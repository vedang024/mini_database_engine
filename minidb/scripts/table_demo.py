"""Phase 7 demo: CREATE TABLE, INSERT, GET, DELETE, and a real restart.

    python scripts\\table_demo.py write     # one process
    python scripts\\table_demo.py read      # a different process
"""

from __future__ import annotations

import argparse
from pathlib import Path

from minidb.config import DatabaseConfig
from minidb.database import Database
from minidb.table.errors import RowNotFoundError
from minidb.table.schema import Column, Schema

USERS = Schema(
    [
        Column("id", "INT", nullable=False),
        Column("name", "TEXT", nullable=False),
        Column("email", "TEXT"),
        Column("active", "BOOL"),
    ]
)

ROWS = [
    (1, "Ann", "ann@x.com", True),
    (2, "Bob", None, False),
    (3, "Cy", "cy@x.com", None),
]


def write(db: Database) -> None:
    if "users" not in db.table_names():
        db.create_table("users", USERS)
        print("created table users")
    users = db.get_table("users")
    rids = []
    for row in ROWS:
        rid = users.insert(row)
        rids.append(rid)
        print(f"inserted {rid} -> {row}")
    users.delete(rids[1])
    print(f"deleted {rids[1]}")
    try:
        users.get(rids[1])
    except RowNotFoundError:
        print("get after delete: RowNotFoundError")


def read(db: Database) -> None:
    count = 0
    for rid, row in db.get_table("users").scan():
        print(rid, row)
        count += 1
    print(f"({count} row(s))")


def main() -> None:
    parser = argparse.ArgumentParser(description="MiniDB table demo")
    parser.add_argument("--data-dir", default="data/demo")
    parser.add_argument("command", choices=["write", "read"])
    args = parser.parse_args()

    with Database(DatabaseConfig(data_dir=Path(args.data_dir))) as db:  # closing flushes
        if args.command == "write":
            write(db)
        else:
            read(db)


if __name__ == "__main__":
    main()