"""Rehearse a trusted migration on a disposable SQLite database snapshot."""

import argparse
import json
import math
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path


SAMPLE_LIMIT = 10
MAX_SAFE_JSON_INTEGER = (1 << 53) - 1


def readonly(path):
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def backup(source, destination, timeout):
    deadline = time.monotonic() + timeout

    def check_deadline(_status, _remaining, _total):
        if time.monotonic() > deadline:
            raise TimeoutError("SQLite backup timed out")

    with closing(readonly(source)) as original, closing(sqlite3.connect(destination)) as copy:
        original.backup(copy, pages=256, progress=check_deadline, sleep=0.05)


def quote(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def load_rules(path):
    if path is None:
        return [], []
    rules = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(rules, dict) or rules.keys() - {"preserve", "assertions"}:
        raise ValueError("Rules must be an object with preserve and/or assertions arrays")
    preserve = rules.get("preserve", [])
    assertions = rules.get("assertions", [])
    if not isinstance(preserve, list) or not isinstance(assertions, list):
        raise ValueError("preserve and assertions must be arrays")
    for rule in preserve:
        if not isinstance(rule, dict) or rule.keys() - {"table", "key", "columns"}:
            raise ValueError("A preserve rule needs table, key and optional columns")
        if not identifier(rule.get("table")) or not identifiers(rule.get("key")) or not rule["key"]:
            raise ValueError("A preserve rule needs a table and at least one key column")
        if "columns" in rule and not identifiers(rule["columns"]):
            raise ValueError("columns must be an array of distinct column names")
    for rule in assertions:
        if not isinstance(rule, dict) or rule.keys() != {"name", "query", "equals"}:
            raise ValueError("An assertion needs name, query and equals")
        if not identifier(rule["name"]) or not identifier(rule["query"]):
            raise ValueError("An assertion needs nonempty name and query strings")
        if type(rule["equals"]) not in (str, int, float, type(None)):
            raise ValueError("equals must be a string, number or null")
        if isinstance(rule["equals"], float) and not math.isfinite(rule["equals"]):
            raise ValueError("equals must be finite")
    return preserve, assertions


def identifier(value):
    return isinstance(value, str) and bool(value.strip()) and "\0" not in value


def identifiers(values):
    return (isinstance(values, list) and all(identifier(value) for value in values)
            and len({value.lower() for value in values}) == len(values))


def run_migration(candidate, args):
    if args.sql is not None:
        deadline = time.monotonic() + args.timeout
        with closing(sqlite3.connect(candidate)) as db:
            db.execute("PRAGMA foreign_keys = ON")
            db.set_progress_handler(lambda: time.monotonic() > deadline, 1000)
            db.set_authorizer(lambda action, *_: (sqlite3.SQLITE_DENY
                              if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH)
                              else sqlite3.SQLITE_OK))
            db.executescript(args.sql.read_text(encoding="utf-8-sig"))
            if db.in_transaction:
                raise ValueError("SQL migration left a transaction open. End it with COMMIT.")
        return
    command = [argument.replace("{db}", str(candidate)) for argument in args.command]
    result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=sys.stderr,
                            timeout=args.timeout, check=False, cwd=candidate.parent)
    if result.returncode:
        raise ValueError(f"Migration command exited with code {result.returncode}")


def schema(db, database):
    return {(kind, name): sql for kind, name, sql in db.execute(
        f"SELECT type, name, sql FROM {database}.sqlite_master "
        "WHERE name NOT GLOB 'sqlite_*' ORDER BY type, name"
    )}


def compare_schema(db):
    before = schema(db, "baseline")
    after = schema(db, "main")
    changes = []
    for kind, name in sorted(before.keys() | after.keys()):
        key = kind, name
        if key not in before:
            status = "added"
        elif key not in after:
            status = "removed"
        elif before[key] != after[key]:
            status = "changed"
        else:
            continue
        changes.append({"type": kind, "name": name, "change": status})
    return {
        "before_version": db.execute("PRAGMA baseline.user_version").fetchone()[0],
        "after_version": db.execute("PRAGMA main.user_version").fetchone()[0],
        "changes": changes,
    }


def check(name, passed, **details):
    return {"name": name, "status": "passed" if passed else "failed", **details}


def table_columns(db, database, table):
    row = db.execute(f"SELECT type FROM {database}.sqlite_master WHERE name = ? COLLATE NOCASE",
                     (table,)).fetchone()
    if row is None or row[0] != "table":
        raise ValueError(f"Table {table!r} is missing in {database}")
    return [row[1] for row in db.execute(f"PRAGMA {database}.table_xinfo({quote(table)})")
            if row[6] != 1]


def key_data(db, relation, keys):
    fields = ", ".join(f"r.{quote(key)} COLLATE BINARY" for key in keys)
    null_filter = " OR ".join(f"r.{quote(key)} IS NULL" for key in keys)
    invalid = db.execute(
        f"SELECT 1 FROM {relation} AS r GROUP BY {fields} "
        f"HAVING COUNT(*) > 1 OR {null_filter} LIMIT 1"
    ).fetchone()
    if invalid is not None:
        raise ValueError(f"Key columns must be non-null and unique in {relation}")
    return db.execute(f"SELECT COUNT(*) FROM {relation}").fetchone()[0]


def json_value(value):
    if isinstance(value, bytes):
        return {"blob_hex": value.hex()}
    if isinstance(value, int) and abs(value) > MAX_SAFE_JSON_INTEGER:
        return str(value)
    if isinstance(value, float) and value in (float("inf"), float("-inf")):
        return str(value)
    return value


def diff_rows(db, query, keys):
    count = db.execute(f"SELECT COUNT(*) FROM ({query})").fetchone()[0]
    if count == 0:
        return {"count": 0, "keys": []}
    key_list = ", ".join(str(index + 1) for index in range(len(keys)))
    sample = db.execute(f"{query} ORDER BY {key_list} LIMIT ?", (SAMPLE_LIMIT,))
    return {"count": count, "keys": [dict(zip(keys, map(json_value, row))) for row in sample]}


def preserve_rows(db, rule):
    table, keys = rule["table"], rule["key"]
    name = f"Preserve {table}"
    try:
        before_columns = table_columns(db, "baseline", table)
        columns = rule.get("columns", before_columns)
        after_columns = table_columns(db, "main", table)
        for database, available in (("baseline", before_columns), ("main", after_columns)):
            known = {column.lower() for column in available}
            missing = [column for column in keys + columns if column.lower() not in known]
            if missing:
                raise ValueError(f"Missing columns in {database}: {', '.join(dict.fromkeys(missing))}")

        old = f"baseline.{quote(table)}"
        new = f"main.{quote(table)}"
        before_count = key_data(db, old, keys)
        after_count = key_data(db, new, keys)
        key_match = " AND ".join(f"a.{quote(key)} COLLATE BINARY IS b.{quote(key)} COLLATE BINARY "
                                 f"AND typeof(a.{quote(key)}) = typeof(b.{quote(key)})" for key in keys)
        old_keys = ", ".join(f"b.{quote(key)} COLLATE BINARY" for key in keys)
        new_keys = ", ".join(f"a.{quote(key)} COLLATE BINARY" for key in keys)
        removed = diff_rows(db, f"SELECT {old_keys} FROM {old} b WHERE NOT EXISTS "
                            f"(SELECT 1 FROM {new} a WHERE {key_match})", keys)
        added = diff_rows(db, f"SELECT {new_keys} FROM {new} a WHERE NOT EXISTS "
                          f"(SELECT 1 FROM {old} b WHERE {key_match})", keys)
        value_diff = " OR ".join(f"a.{quote(column)} COLLATE BINARY IS NOT b.{quote(column)} COLLATE BINARY "
                                 f"OR typeof(a.{quote(column)}) != typeof(b.{quote(column)})"
                                 for column in columns) or "0"
        changed = diff_rows(db, f"SELECT {old_keys} FROM {old} b JOIN {new} a ON {key_match} "
                            f"WHERE {value_diff}", keys)
    except (ValueError, sqlite3.Error) as error:
        return check(name, False, detail=str(error))
    return check(name, not (removed["count"] or added["count"] or changed["count"]),
                 before_count=before_count, after_count=after_count,
                 removed=removed, added=added, changed=changed)


def assert_scalar(db, rule):
    expected = rule["equals"]
    if not rule["query"].lstrip().upper().startswith(("SELECT", "WITH")):
        return check(rule["name"], False, detail="Use a read-only SELECT or WITH query")
    try:
        rows = db.execute(rule["query"]).fetchmany(2)
        if len(rows) != 1 or len(rows[0]) != 1:
            return check(rule["name"], False, detail="Expected exactly one row and one column")
        actual = rows[0][0]
        return check(rule["name"], actual == expected, expected=json_value(expected), actual=json_value(actual))
    except sqlite3.Error as error:
        return check(rule["name"], False, detail=str(error))


def inspect(candidate, baseline, preserve, assertions):
    with closing(readonly(candidate)) as db:
        db.execute("ATTACH DATABASE ? AS baseline", (f"{baseline.as_uri()}?mode=ro",))
        db.execute("PRAGMA query_only = ON")
        integrity = [row[0] for row in db.execute("PRAGMA main.integrity_check(10)")]
        checks = [check("SQLite integrity", integrity == ["ok"], detail="; ".join(integrity))]
        if checks[0]["status"] == "failed":
            return None, checks
        try:
            foreign_keys = db.execute("PRAGMA main.foreign_key_check").fetchmany(SAMPLE_LIMIT)
            checks.append(check("Foreign keys", not foreign_keys,
                                violations=[list(map(json_value, row)) for row in foreign_keys]))
        except sqlite3.Error as error:
            checks.append(check("Foreign keys", False, detail=str(error)))
        schema_result = compare_schema(db)
        checks.extend(preserve_rows(db, rule) for rule in preserve)
        allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
                   sqlite3.SQLITE_RECURSIVE}
        db.set_authorizer(lambda action, *_: sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY)
        checks.extend(assert_scalar(db, rule) for rule in assertions)
        return schema_result, checks


def rehearse(args):
    report = {"format_version": 1, "status": "error", "checks": [], "schema": None}
    try:
        if not args.database.is_file() or args.database.stat().st_size == 0:
            raise ValueError("Source must be an existing, nonempty SQLite database file")
        preserve, assertions = load_rules(args.rules)
        with tempfile.TemporaryDirectory(prefix="sqlite-rehearsal-") as workspace:
            baseline = Path(workspace).resolve() / "before.sqlite"
            candidate = baseline.with_name("candidate.sqlite")
            backup(args.database, baseline, args.timeout)
            backup(baseline, candidate, args.timeout)
            run_migration(candidate, args)
            if candidate.stat().st_size == 0:
                raise ValueError("Migration left an empty database file")
            report["schema"], report["checks"] = inspect(candidate, baseline, preserve, assertions)
        report["status"] = ("failed" if any(item["status"] == "failed" for item in report["checks"])
                            else "passed")
    except subprocess.TimeoutExpired:
        report["error"] = f"Migration command exceeded {args.timeout:g} seconds"
    except (OSError, sqlite3.Error, ValueError) as error:
        report["error"] = str(error)
    return report


def print_report(report):
    print(f"SQLite Rehearsal  /  {report['status'].upper()}")
    if "error" in report:
        print(report["error"], file=sys.stderr)
    if report["schema"] is not None:
        schema_result = report["schema"]
        print(f"Schema user_version: {schema_result['before_version']} -> {schema_result['after_version']}")
        for change in schema_result["changes"]:
            print(f"  {change['change']:7} {change['type']} {change['name']}")
    for item in report["checks"]:
        print(f"[{item['status'].upper()}] {item['name']}")
        if "before_count" in item:
            print(f"  Rows: {item['before_count']} -> {item['after_count']}")
            for category in ("removed", "added", "changed"):
                diff = item[category]
                if diff["count"]:
                    print(f"  {category}: {diff['count']} | {json.dumps(diff['keys'])}")
        elif "expected" in item:
            print(f"  Expected {item['expected']!r}, actual {item['actual']!r}")
        elif "detail" in item and item["status"] == "failed":
            print(f"  {item['detail']}")
        elif item.get("violations"):
            print(f"  Violation samples: {item['violations']!r}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path, help="existing SQLite database")
    parser.add_argument("--rules", type=Path, help="JSON rules for row preservation and SQL assertions")
    parser.add_argument("--report", type=Path, help="write JSON to a new file")
    parser.add_argument("--timeout", type=int, default=30, help="seconds per backup or migration (default: 30)")
    migration = parser.add_mutually_exclusive_group(required=True)
    migration.add_argument("--sql", type=Path, help="run a migration SQL file")
    migration.add_argument("--command", nargs=argparse.REMAINDER,
                           help="run a trusted executable; place this option last and pass {db}")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")
    if args.command is not None and (not args.command or not any("{db}" in part for part in args.command[1:])):
        parser.error("--command needs an executable and a {db} placeholder in its arguments")
    if args.report is not None:
        source = str(args.database.resolve())
        if args.report.resolve() in {Path(source + suffix) for suffix in ("", "-journal", "-shm", "-wal")}:
            parser.error("--report must be separate from the source database and its sidecar files")

    report = rehearse(args)
    print_report(report)
    if args.report is not None:
        try:
            with args.report.open("x", encoding="utf-8") as file:
                json.dump(report, file, ensure_ascii=False, indent=2, allow_nan=False)
                file.write("\n")
        except OSError as error:
            print(f"Could not write report: {error}", file=sys.stderr)
            return 1
    return {"passed": 0, "error": 1, "failed": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
