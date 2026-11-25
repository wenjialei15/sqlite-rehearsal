import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


CLI = Path(__file__).resolve().parents[1] / "sqlite_rehearsal.py"


class RehearsalTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.source = self.root / "old #%.sqlite"
        self.report = self.root / "output.json"

    def setup_db(self, sql):
        with closing(sqlite3.connect(self.source)) as db:
            db.executescript(sql)

    def run_cli(self, migration_args, rules=None):
        args = [sys.executable, str(CLI), str(self.source), "--report", str(self.report)]
        if rules is not None:
            rule_file = self.root / "rules.json"
            rule_file.write_text(json.dumps(rules), encoding="utf-8")
            args.extend(["--rules", str(rule_file)])
        return subprocess.run(args + migration_args, capture_output=True, text=True, timeout=15)

    def run_sql(self, sql, rules=None):
        script = self.root / "migration.sql"
        script.write_text(sql, encoding="utf-8")
        return self.run_cli(["--sql", str(script)], rules)

    def read_report(self):
        return json.loads(self.report.read_text(encoding="utf-8"))

    def test_sql_applies_only_to_copy_and_reports_schema(self):
        self.setup_db("CREATE TABLE readings (id INTEGER PRIMARY KEY, value TEXT); INSERT INTO readings VALUES (1, 'a');")
        before = self.source.read_bytes()
        rules = {"preserve": [{"table": "readings", "key": ["id"]}], "assertions": [{
            "name": "Same total", "query": "SELECT (SELECT COUNT(*) FROM main.readings) = "
                                          "(SELECT COUNT(*) FROM baseline.readings)", "equals": 1,
        }]}
        result = self.run_sql("ALTER TABLE readings ADD COLUMN note TEXT; PRAGMA user_version = 7;", rules)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(self.read_report()["schema"], {
            "before_version": 0, "after_version": 7,
            "changes": [{"type": "table", "name": "readings", "change": "changed"}],
        })

    def test_equal_row_counts_still_find_loss_addition_and_edit(self):
        self.setup_db('CREATE TABLE "sensor logs" ("site name" TEXT, stamp INT, value TEXT COLLATE NOCASE);'
                      "INSERT INTO \"sensor logs\" VALUES ('A', 1, 'old'), ('A', 2, 'UPPER');")
        result = self.run_sql("DELETE FROM \"sensor logs\" WHERE stamp = 1;"
                              "INSERT INTO \"sensor logs\" VALUES ('A', 3, 'new');"
                              "UPDATE \"sensor logs\" SET value = 'upper' WHERE stamp = 2;", {
                                  "preserve": [{"table": "sensor logs", "key": ["site name", "stamp"]}],
                              })
        self.assertEqual(result.returncode, 2, result.stderr)
        item = self.read_report()["checks"][2]
        self.assertEqual((item["before_count"], item["after_count"]), (2, 2))
        for change, stamp in (("removed", 1), ("changed", 2), ("added", 3)):
            self.assertEqual(item[change], {"count": 1, "keys": [{"site name": "A", "stamp": stamp}]})

    def test_command_snapshot_includes_committed_wal(self):
        with closing(sqlite3.connect(self.source)) as source:
            self.assertEqual(source.execute("PRAGMA journal_mode = WAL").fetchone()[0], "wal")
            source.executescript("CREATE TABLE items (id INTEGER PRIMARY KEY); INSERT INTO items VALUES (42);")
            source.commit()
            self.assertTrue(Path(str(self.source) + "-wal").exists())
            program = "import sqlite3, sys; db = sqlite3.connect(sys.argv[1]); db.execute('PRAGMA user_version = 3')"
            result = self.run_cli(["--command", sys.executable, "-c", program, "{db}"], {
                "assertions": [{"name": "WAL row", "query": "SELECT id FROM main.items", "equals": 42}],
            })
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.read_report()["schema"]["after_version"], 3)
            self.assertEqual(source.execute("PRAGMA user_version").fetchone()[0], 0)

    def test_sql_cannot_attach_another_database(self):
        self.setup_db("CREATE TABLE items (id INTEGER PRIMARY KEY);")
        before = self.source.read_bytes()
        source = self.source.as_posix().replace("'", "''")
        result = self.run_sql(f"ATTACH DATABASE '{source}' AS original; DROP TABLE original.items;")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(self.read_report()["status"], "error")
        self.assertEqual(self.source.read_bytes(), before)

    def test_reports_foreign_key_violations_and_invalid_assertion(self):
        self.setup_db("CREATE TABLE parent (id INTEGER PRIMARY KEY);"
                      "CREATE TABLE child (parent_id INTEGER REFERENCES parent(id));")
        result = self.run_sql("PRAGMA foreign_keys = OFF; INSERT INTO child VALUES (99);", {
            "assertions": [{"name": "Write attempt", "query": "WITH x AS (SELECT 1) DELETE FROM child", "equals": 0}],
        })
        self.assertEqual(result.returncode, 2, result.stderr)
        checks = self.read_report()["checks"]
        self.assertEqual(checks[1]["violations"], [["child", 1, "parent", 0]])
        self.assertEqual(checks[2]["status"], "failed")

    def test_preserve_rule_rejects_duplicate_key(self):
        self.setup_db("CREATE TABLE items (id INTEGER); INSERT INTO items VALUES (1), (1);")
        result = self.run_sql("SELECT 1;", {"preserve": [{"table": "items", "key": ["id"]}]})
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("non-null and unique", self.read_report()["checks"][2]["detail"])

    def test_report_keeps_full_precision_for_large_sqlite_primary_keys(self):
        key = 9223372036854775807
        self.setup_db(f"CREATE TABLE items (id INTEGER PRIMARY KEY); INSERT INTO items VALUES ({key});")
        result = self.run_sql("DELETE FROM items;", {
            "preserve": [{"table": "items", "key": ["id"]}],
            "assertions": [{"name": "Largest id", "query": "SELECT MAX(id) FROM baseline.items", "equals": key}],
        })
        self.assertEqual(result.returncode, 2, result.stderr)
        checks = self.read_report()["checks"]
        self.assertEqual(checks[2]["removed"]["keys"], [{"id": str(key)}])
        self.assertEqual(checks[3]["status"], "passed")
        self.assertEqual(checks[3]["actual"], str(key))

    def test_does_not_overwrite_report(self):
        self.setup_db("CREATE TABLE items (id INTEGER);")
        self.report.write_text("keep this", encoding="utf-8")
        result = self.run_sql("SELECT 1;")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.report.read_text(encoding="utf-8"), "keep this")


if __name__ == "__main__":
    unittest.main()
