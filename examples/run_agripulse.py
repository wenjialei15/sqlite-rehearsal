"""Run the actual AgriPulse Store migration against a disposable database."""

import argparse
import sqlite3
import sys
from contextlib import closing
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("agripulse_checkout", type=Path)
    parser.add_argument("--simulate-loss", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, str(args.agripulse_checkout.resolve()))
    from agripulse.core import Store

    Store(args.candidate)
    if args.simulate_loss:
        with closing(sqlite3.connect(args.candidate)) as db, db:
            db.execute("DELETE FROM readings WHERE id = 102")


if __name__ == "__main__":
    main()
