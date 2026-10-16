"""Phase 6 benchmark: FIFO vs LRU vs Clock on four access patterns.

Run from the repo root:   python benchmarks\\replacement_benchmark.py
Each cell shows: hit ratio (disk reads). Every policy sees the IDENTICAL trace.
"""

from __future__ import annotations

import random
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from minidb.storage.buffer_pool import BufferPool, BufferPoolStats
from minidb.storage.disk_manager import DiskManager
from minidb.storage.eviction import make_policy

PAGES = 200
ACCESSES = 20_000
CAPACITIES = (10, 20, 40, 80)
POLICY_NAMES = ("fifo", "lru", "clock")
HIT_US, MISS_US = 0.1, 100.0  # latency model from Phase 5: RAM hit vs SSD read


def hot_cold(rng: random.Random) -> list[int]:
    """Skewed: 80% of accesses hit 10 hot pages, 20% hit 90 cold pages."""
    return [
        rng.randint(1, 10) if rng.random() < 0.8 else rng.randint(11, 100)
        for _ in range(ACCESSES)
    ]


def uniform(rng: random.Random) -> list[int]:
    """No locality at all: every one of 100 pages is equally likely."""
    return [rng.randint(1, 100) for _ in range(ACCESSES)]


def loop(rng: random.Random) -> list[int]:
    """Cycle through pages 1..40 forever (a repeated sequential scan)."""
    return [1 + i % 40 for i in range(ACCESSES)]


def scan_pollution(rng: random.Random) -> list[int]:
    """Hot pages, interrupted over and over by a one-pass scan of 100 other pages."""
    trace: list[int] = []
    while len(trace) < ACCESSES:
        trace += [rng.randint(1, 10) for _ in range(50)]   # 50 hot accesses
        trace += range(101, 201)                            # then a scan
    return trace[:ACCESSES]


WORKLOADS: dict[str, Callable[[random.Random], list[int]]] = {
    "hot/cold 80/20 over 100 pages": hot_cold,
    "uniform random over 100 pages": uniform,
    "loop over 40 pages": loop,
    "hot pages + repeated 100-page scan": scan_pollution,
}


def run(disk: DiskManager, policy: str, capacity: int, trace: list[int]) -> BufferPoolStats:
    pool = BufferPool(disk, capacity, policy=make_policy(policy))
    for page_id in trace:
        pool.fetch_page(page_id)
        pool.unpin_page(page_id)
    return pool.stats


def estimated_ms(stats: BufferPoolStats) -> float:
    return (stats.hits * HIT_US + stats.misses * MISS_US) / 1000


def cpu_cost_per_hit_us(disk: DiskManager, policy: str) -> float:
    """Time per access when EVERY access is a hit, so only policy bookkeeping differs."""
    pool = BufferPool(disk, 64, policy=make_policy(policy))
    for page_id in range(1, 51):  # warm up: load pages 1..50
        pool.fetch_page(page_id)
        pool.unpin_page(page_id)
    best = float("inf")
    for _ in range(3):  # best of 3 reduces timing noise
        start = time.perf_counter()
        for i in range(ACCESSES):
            page_id = 1 + i % 50
            pool.fetch_page(page_id)
            pool.unpin_page(page_id)
        best = min(best, time.perf_counter() - start)
    return best / ACCESSES * 1e6


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        with DiskManager(Path(tmp) / "bench.db", 4096) as disk:
            for _ in range(PAGES):
                disk.allocate_page()

            results: dict[tuple[str, int, str], BufferPoolStats] = {}
            for name, make_trace in WORKLOADS.items():
                trace = make_trace(random.Random(42))
                print(f"\nworkload: {name}   ({len(trace)} accesses)")
                print(f"{'frames':<8}" + "".join(f"{p.upper():<19}" for p in POLICY_NAMES))
                for capacity in CAPACITIES:
                    cells = []
                    for policy in POLICY_NAMES:
                        stats = run(disk, policy, capacity, trace)
                        results[(name, capacity, policy)] = stats
                        cells.append(f"{stats.hit_ratio:6.1%} ({stats.misses:>5})  ")
                    print(f"{capacity:<8}" + "".join(cells))

            first = next(iter(WORKLOADS))
            print(f"\nEstimated I/O time, '{first}', 20 frames "
                  f"(hit {HIT_US} us, miss {MISS_US} us):")
            for policy in POLICY_NAMES:
                print(f"  {policy:<6} {estimated_ms(results[(first, 20, policy)]):8.1f} ms")

            print("\nPolicy CPU overhead (all-hit workload, microseconds per access):")
            for policy in POLICY_NAMES:
                print(f"  {policy:<6} {cpu_cost_per_hit_us(disk, policy):.2f} us")


if __name__ == "__main__":
    main()