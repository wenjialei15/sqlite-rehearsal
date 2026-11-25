"""Create a new synthetic AgriPulse v0.1.0 database for the quickstart."""

import argparse
import sqlite3
from contextlib import closing
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    script = Path(__file__).with_name("agripulse_legacy.sql").read_text(encoding="utf-8")
    args.output.touch(exist_ok=False)
    with closing(sqlite3.connect(args.output)) as db:
        db.executescript(script)


if __name__ == "__main__":
    main()
