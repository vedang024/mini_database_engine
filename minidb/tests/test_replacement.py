import random

import pytest

from minidb.storage.buffer_pool import BufferPool
from minidb.storage.disk_manager import DiskManager
from minidb.storage.errors import BufferPoolFullError
from minidb.storage.eviction import make_policy

PAGE = 512
NAMES = ["fifo", "lru", "clock"]
BELADY = [1, 2, 3, 4, 1, 2, 5, 1, 2, 3, 4, 5]


@pytest.fixture
def disk(tmp_path):
    """Pages 1..6, where page i is filled with the byte value i."""
    with DiskManager(tmp_path / "r.db", PAGE) as dm:
        for _ in range(6):
            pid = dm.allocate_page()
            dm.write_page(pid, bytes([pid]) * PAGE)
        yield dm


def touch(pool, page_id):
    pool.fetch_page(page_id)
    pool.unpin_page(page_id)


def run_trace(disk, policy, capacity, trace):
    pool = BufferPool(disk, capacity, policy=make_policy(policy))
    for page_id in trace:
        touch(pool, page_id)
    return pool.stats


@pytest.mark.parametrize("policy,hits", [("fifo", 1), ("lru", 2), ("clock", 2)])
def test_phase5_trace_hits_per_policy(disk, policy, hits):
    stats = run_trace(disk, policy, 2, [1, 2, 1, 3, 1, 2])
    assert stats.hits == hits
    assert stats.hits + stats.misses == 6


def test_fifo_suffers_beladys_anomaly(disk):
    assert run_trace(disk, "fifo", 3, BELADY).misses == 9
    assert run_trace(disk, "fifo", 4, BELADY).misses == 10  # MORE frames, MORE misses


def test_lru_does_not_get_worse_with_more_frames(disk):
    assert run_trace(disk, "lru", 3, BELADY).misses == 10
    assert run_trace(disk, "lru", 4, BELADY).misses == 8


@pytest.mark.parametrize("policy", NAMES)
def test_cyclic_scan_larger_than_pool_never_hits(disk, policy):
    stats = run_trace(disk, policy, 3, [1, 2, 3, 4] * 3)
    assert (stats.hits, stats.misses) == (0, 12)


@pytest.mark.parametrize("policy", NAMES)
def test_pinned_page_survives_under_every_policy(disk, policy):
    pool = BufferPool(disk, 2, policy=make_policy(policy))
    pool.fetch_page(1)  # stays pinned
    for page_id in (2, 3, 4, 5, 2, 3):
        touch(pool, page_id)
    assert pool.is_cached(1)


@pytest.mark.parametrize("policy", NAMES)
def test_dirty_page_is_written_back_under_every_policy(disk, policy):
    pool = BufferPool(disk, 1, policy=make_policy(policy))
    page = pool.fetch_page(1)
    page.write(0, b"abc")
    pool.unpin_page(1)
    touch(pool, 2)  # evicts page 1
    assert disk.read_page(1)[:3] == b"abc"


@pytest.mark.parametrize("policy", NAMES)
def test_all_pinned_raises_under_every_policy(disk, policy):
    pool = BufferPool(disk, 2, policy=make_policy(policy))
    pool.fetch_page(1)
    pool.fetch_page(2)
    with pytest.raises(BufferPoolFullError):
        pool.fetch_page(3)


@pytest.mark.parametrize("policy", NAMES)
def test_random_workload_keeps_pool_consistent(disk, policy):
    rng = random.Random(7)
    pool = BufferPool(disk, 4, policy=make_policy(policy))
    for _ in range(3000):
        page_id = rng.randint(1, 6)
        page = pool.fetch_page(page_id)
        assert page.read(0, 1) == bytes([page_id])  # right page, right contents
        pool.unpin_page(page_id)
        assert len(pool.cached_page_ids()) <= 4
    assert pool.stats.hits + pool.stats.misses == 3000
    assert pool.stats.evictions == pool.stats.misses - 4  # each miss past warm-up evicts one