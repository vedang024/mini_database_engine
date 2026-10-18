"""Phase 10 benchmark: full table scan vs B+ tree index.

Every figure is measured when you run this. Nothing is hard-coded or estimated.

    python benchmarks\\index_benchmark.py
    python benchmarks\\index_benchmark.py --sizes 1000 10000 --repeats 5

Experiment A  point lookups: index vs scan vs an unindexed column
Experiment B  selectivity: index range scan vs full scan as the result grows
Experiment C  what the index costs: load time and file size

Each measurement REOPENS the database, so buffer pools start cold. (Opening a
heap file rebuilds its free-space map, which leaves its last POOL_PAGES pages
cached; the effect is small for large tables and larger for tiny ones. Page
"accesses" do not depend on caching at all.) Rows are inserted in shuffled id
order, so neighbouring ids are NOT on neighbouring heap pages.
"""

from __future__ import annotations

import argparse
import platform
import random
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from minidb.config import DatabaseConfig
from minidb.database import Database, index_file_name
from minidb.table.schema import Column, Schema
from minidb.table.table import Table

SCHEMA = Schema(
    [
        Column("id", "INT", nullable=False),    # indexed, unique
        Column("code", "INT", nullable=False),  # unique too, but NOT indexed
        Column("name", "TEXT", nullable=False),
    ]
)
PAGE_SIZE = 4096
POOL_PAGES = 16
SEED = 2026
FRACTIONS = (0.001, 0.01, 0.05, 0.10, 0.25, 0.50, 1.00)


@dataclass
class Result:
    seconds: float
    accesses: int   # page requests to the buffer pools (cache independent)
    reads: int      # buffer pool misses = actual page reads from the files
    rows: int       # what the query returned, for sanity checks


@dataclass
class BuildInfo:
    heap_pages: int
    heap_bytes: int
    index_bytes: int
    tree_height: int
    load_plain_s: float
    load_indexed_s: float


def _totals(table: Table) -> tuple[int, int]:
    pools = [table.heap.pool_stats]
    index = table.index_for("id")
    if index is not None:
        pools.append(index.tree.pool_stats)
    return sum(p.hits + p.misses for p in pools), sum(p.misses for p in pools)


def measure(cfg: DatabaseConfig, work: Callable[[Table], int]) -> Result:
    with Database(cfg) as db:  # a fresh Database means fresh, cold buffer pools
        table = db.get_table("bench")
        accesses0, reads0 = _totals(table)
        start = time.perf_counter()
        rows = work(table)
        seconds = time.perf_counter() - start
        accesses1, reads1 = _totals(table)
    return Result(seconds, accesses1 - accesses0, reads1 - reads0, rows)


def best_of(cfg: DatabaseConfig, work: Callable[[Table], int], repeats: int) -> Result:
    """Repeat the measurement; keep the fastest run (least disturbed by other processes)."""
    return min((measure(cfg, work) for _ in range(repeats)), key=lambda r: r.seconds)


def build(cfg: DatabaseConfig, n: int) -> BuildInfo:
    rng = random.Random(SEED)
    ids = list(range(1, n + 1))
    codes = list(range(1, n + 1))
    rng.shuffle(ids)
    rng.shuffle(codes)
    rows = [(i, c, f"user{i}") for i, c in zip(ids, codes)]

    with Database(cfg) as db:
        plain = db.create_table("bench_plain", SCHEMA)  # same rows, no index
        start = time.perf_counter()
        for row in rows:
            plain.insert(row)
        plain.flush()
        load_plain = time.perf_counter() - start

        table = db.create_table("bench", SCHEMA)
        db.create_index("bench", "id")
        start = time.perf_counter()
        for row in rows:
            table.insert(row)
        table.flush()
        load_indexed = time.perf_counter() - start

        heap_pages = table.heap.page_count
        height = table.index_for("id").tree.check().height

    return BuildInfo(
        heap_pages=heap_pages,
        heap_bytes=(cfg.data_dir / "tbl_bench.dat").stat().st_size,
        index_bytes=(cfg.data_dir / index_file_name("bench", "id")).stat().st_size,
        tree_height=height,
        load_plain_s=load_plain,
        load_indexed_s=load_indexed,
    )


def experiment_a(cfg: DatabaseConfig, n: int, args: argparse.Namespace) -> None:
    rng = random.Random(SEED + 1)
    keys = rng.sample(range(1, n + 1), min(args.lookups, n))
    few = keys[: min(args.scan_lookups, len(keys))]  # scans are slow: fewer of them
    cases = [
        ("index lookup on id", keys, lambda t, k: t.index_equal("id", k)),
        ("full scan on id (index ignored)", few, lambda t, k: t.scan_equal("id", k)),
        ("find() on code (no index: scans)", few, lambda t, k: t.find("code", k)),
    ]
    print("\nExperiment A: WHERE col = value   (per lookup, averaged; each case starts cold)")
    print(f"{'method':<36}{'lookups':>8}{'ms':>11}{'accesses':>10}{'reads':>8}")
    for label, probe_keys, query in cases:
        def work(table: Table, probe_keys=probe_keys, query=query) -> int:
            return sum(len(query(table, k)) for k in probe_keys)

        result = best_of(cfg, work, args.repeats)
        count = len(probe_keys)
        assert result.rows == count, f"{label}: expected {count} matches, got {result.rows}"
        print(
            f"{label:<36}{count:>8}{result.seconds / count * 1000:>11.3f}"
            f"{result.accesses / count:>10.1f}{result.reads / count:>8.1f}"
        )


def experiment_b(cfg: DatabaseConfig, n: int, args: argparse.Namespace) -> None:
    print(f"\nExperiment B: WHERE id BETWEEN 1 AND k   (n = {n:,}; one query per cell, cold)")
    print(
        f"{'selectivity':>11}{'rows':>9} | {'index ms':>9}{'scan ms':>9} | "
        f"{'index acc':>10}{'scan acc':>9} | {'index rd':>9}{'scan rd':>8} | "
        f"{'faster':>7}{'fewer acc':>10}"
    )
    for fraction in FRACTIONS:
        matches = max(1, int(n * fraction))
        via_index = best_of(cfg, lambda t: len(t.index_range("id", 1, matches)), args.repeats)
        via_scan = best_of(cfg, lambda t: len(t.scan_range("id", 1, matches)), args.repeats)
        assert via_index.rows == via_scan.rows == matches
        print(
            f"{matches / n:>11.2%}{matches:>9,} | "
            f"{via_index.seconds * 1000:>9.1f}{via_scan.seconds * 1000:>9.1f} | "
            f"{via_index.accesses:>10,}{via_scan.accesses:>9,} | "
            f"{via_index.reads:>9,}{via_scan.reads:>8,} | "
            f"{'index' if via_index.seconds < via_scan.seconds else 'scan':>7}"
            f"{'index' if via_index.accesses < via_scan.accesses else 'scan':>10}"
        )


def run_size(root: Path, n: int, args: argparse.Namespace, run_b: bool) -> None:
    cfg = DatabaseConfig(
        data_dir=root / f"n{n}", page_size=PAGE_SIZE, buffer_pool_pages=POOL_PAGES,
        eviction_policy="lru",
    )
    info = build(cfg, n)
    print(f"\n{'=' * 78}\nn = {n:,} rows")
    print(
        f"heap: {info.heap_pages:,} pages ({info.heap_bytes / 1024:,.0f} KiB)   "
        f"index: height {info.tree_height}, {info.index_bytes // PAGE_SIZE:,} pages "
        f"({info.index_bytes / 1024:,.0f} KiB)"
    )
    print(
        f"Experiment C: load {n:,} rows   without index {info.load_plain_s:.2f} s   "
        f"with index {info.load_indexed_s:.2f} s   "
        f"(x{info.load_indexed_s / info.load_plain_s:.2f}; one run, includes a final flush)"
    )
    experiment_a(cfg, n, args)
    if run_b:
        experiment_b(cfg, n, args)


def main() -> None:
    parser = argparse.ArgumentParser(description="MiniDB index benchmark")
    parser.add_argument("--sizes", type=int, nargs="+", default=[1000, 10000, 50000])
    parser.add_argument("--lookups", type=int, default=200, help="index lookups per size")
    parser.add_argument("--scan-lookups", type=int, default=3, help="full-scan lookups per size")
    parser.add_argument("--repeats", type=int, default=3, help="keep the fastest of this many runs")
    args = parser.parse_args()

    print(f"python {platform.python_version()} on {platform.platform()}")
    print(f"page size {PAGE_SIZE} B, buffer pool {POOL_PAGES} pages per file, repeats {args.repeats}")
    largest = max(args.sizes)
    with tempfile.TemporaryDirectory() as tmp:
        for n in args.sizes:
            run_size(Path(tmp), n, args, run_b=(n == largest))


if __name__ == "__main__":
    main()