"""Phase 10 demo: the same WHERE id = X through a full scan and through the index.

Page accesses are exact counts, not timings, so they are the same on every machine.
"""

from __future__ import annotations

import random
import tempfile
from pathlib import Path

from minidb.config import DatabaseConfig
from minidb.database import Database
from minidb.table.schema import Column, Schema
from minidb.table.table import Table

USERS = Schema([Column("id", "INT", nullable=False), Column("name", "TEXT", nullable=False)])
N = 2000
TARGET = 1234


def accesses(table: Table) -> int:
    """Total page requests so far, across the heap's pool and every index's pool."""
    pools = [table.heap.pool_stats]
    index = table.index_for("id")
    if index is not None:
        pools.append(index.tree.pool_stats)
    return sum(p.hits + p.misses for p in pools)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg = DatabaseConfig(data_dir=Path(tmp) / "d", page_size=4096, buffer_pool_pages=16)
        ids = list(range(1, N + 1))
        random.Random(1).shuffle(ids)

        with Database(cfg) as db:
            users = db.create_table("users", USERS)
            for i in ids:
                users.insert((i, f"user{i}"))
            print("plan before the index:", users.explain_equal("id"))
            db.create_index("users", "id")
            print("plan after  the index:", users.explain_equal("id"))
            pages = users.heap.page_count
            height = users.index_for("id").tree.check().height
        print(f"table: {N} rows in {pages} heap pages; index height {height}")

        queries = (
            ("full scan", lambda t: t.scan_equal("id", TARGET)),
            ("index    ", lambda t: t.index_equal("id", TARGET)),
        )
        for label, query in queries:
            with Database(cfg) as db:  # reopened: fresh buffer pools each time
                table = db.get_table("users")
                before = accesses(table)
                rows = query(table)
                print(f"{label} -> {rows[0][1]}   page accesses: {accesses(table) - before}")


if __name__ == "__main__":
    main()