"""Phase 5 demo: watch the buffer pool hit, miss and evict."""

import random
import tempfile
from pathlib import Path

from minidb.storage.buffer_pool import BufferPool
from minidb.storage.disk_manager import DiskManager

with DiskManager(Path(tempfile.mkdtemp()) / "pool.db", 4096) as disk:
    for _ in range(100):
        disk.allocate_page()  # pages 1..100

    print("== Part 1: a 2-frame pool with FIFO")
    pool = BufferPool(disk, capacity=2)
    for pid in (1, 2, 1, 3, 1, 2):
        before = pool.stats.hits
        pool.fetch_page(pid)
        pool.unpin_page(pid)
        kind = "HIT " if pool.stats.hits > before else "MISS"
        print(f"access {pid}: {kind} cache={pool.cached_page_ids()}")
    s = pool.stats
    print(f"hits={s.hits} misses={s.misses} evictions={s.evictions} hit ratio={s.hit_ratio:.1%}")

    print("\n== Part 2: 100 pages, 80% of accesses go to 10 hot pages")
    rng = random.Random(1)
    accesses = [
        rng.randint(1, 10) if rng.random() < 0.8 else rng.randint(11, 100)
        for _ in range(20000)
    ]
    for frames in (5, 10, 20, 50, 100):
        pool = BufferPool(disk, capacity=frames)
        for pid in accesses:
            pool.fetch_page(pid)
            pool.unpin_page(pid)
        print(
            f"frames={frames:<4} hit ratio={pool.stats.hit_ratio:6.1%}  "
            f"disk reads={pool.stats.misses}"
        )