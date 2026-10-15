import pytest

from minidb.storage.buffer_pool import BufferPool
from minidb.storage.disk_manager import DiskManager
from minidb.storage.errors import BufferPoolError, BufferPoolFullError, InvalidPageError
from minidb.storage.slotted_page import SlottedPage

PAGE = 512
NUM_PAGES = 6


@pytest.fixture
def disk(tmp_path):
    """A disk whose page i (1..6) is filled with the byte value i."""
    with DiskManager(tmp_path / "bp.db", PAGE) as dm:
        for i in range(1, NUM_PAGES + 1):
            pid = dm.allocate_page()
            dm.write_page(pid, bytes([i]) * PAGE)
        yield dm


def touch(pool: BufferPool, page_id: int):
    """Fetch then immediately unpin: a quick read."""
    page = pool.fetch_page(page_id)
    pool.unpin_page(page_id)
    return page


def test_first_fetch_is_a_miss_second_is_a_hit(disk):
    pool = BufferPool(disk, 2)
    touch(pool, 1)
    touch(pool, 1)
    assert (pool.stats.misses, pool.stats.hits) == (1, 1)


def test_cached_page_is_the_same_object(disk):
    pool = BufferPool(disk, 2)
    a = pool.fetch_page(1)
    b = pool.fetch_page(1)
    assert a is b


def test_fetch_loads_page_contents(disk):
    pool = BufferPool(disk, 2)
    assert pool.fetch_page(3).read(0, 4) == b"\x03" * 4


def test_fifo_evicts_the_oldest_loaded_page(disk):
    pool = BufferPool(disk, 2)
    touch(pool, 1)
    touch(pool, 2)
    touch(pool, 3)
    assert not pool.is_cached(1)
    assert pool.is_cached(2) and pool.is_cached(3)
    assert pool.stats.evictions == 1


def test_fifo_ignores_recent_use(disk):
    pool = BufferPool(disk, 2)
    touch(pool, 1)
    touch(pool, 2)
    touch(pool, 1)  # a hit; page 1 was just used
    touch(pool, 3)
    assert not pool.is_cached(1)  # FIFO evicts it anyway
    assert pool.is_cached(2)


def test_pinned_page_is_never_evicted(disk):
    pool = BufferPool(disk, 2)
    pool.fetch_page(1)  # stays pinned
    touch(pool, 2)
    touch(pool, 3)
    assert pool.is_cached(1)
    assert not pool.is_cached(2)
    assert pool.is_cached(3)


def test_all_pinned_raises_and_pool_recovers_after_unpin(disk):
    pool = BufferPool(disk, 2)
    pool.fetch_page(1)
    pool.fetch_page(2)
    with pytest.raises(BufferPoolFullError):
        pool.fetch_page(3)
    assert pool.is_cached(1) and pool.is_cached(2)  # nothing was disturbed
    pool.unpin_page(1)
    pool.fetch_page(3)
    assert not pool.is_cached(1)


def test_dirty_page_is_written_back_on_eviction(disk):
    pool = BufferPool(disk, 1)
    page = pool.fetch_page(1)
    page.write(0, b"abc")
    pool.unpin_page(1)
    assert disk.read_page(1)[:3] == b"\x01\x01\x01"  # change exists only in memory
    touch(pool, 2)                                    # evicts page 1
    assert pool.stats.dirty_evictions == 1
    assert disk.read_page(1)[:3] == b"abc"


def test_clean_page_eviction_does_not_count_as_dirty(disk):
    pool = BufferPool(disk, 1)
    touch(pool, 1)
    touch(pool, 2)
    assert pool.stats.evictions == 1
    assert pool.stats.dirty_evictions == 0


def test_unpin_with_dirty_flag_marks_page_dirty(disk):
    pool = BufferPool(disk, 1)
    pool.fetch_page(1)
    pool.unpin_page(1, dirty=True)
    touch(pool, 2)
    assert pool.stats.dirty_evictions == 1


def test_unpin_misuse_is_rejected(disk):
    pool = BufferPool(disk, 2)
    with pytest.raises(BufferPoolError):
        pool.unpin_page(1)  # never fetched
    touch(pool, 1)
    with pytest.raises(BufferPoolError):
        pool.unpin_page(1)  # already at pin count 0


def test_pin_count_tracks_every_fetch(disk):
    pool = BufferPool(disk, 2)
    pool.fetch_page(1)
    pool.fetch_page(1)
    assert pool.pin_count(1) == 2
    pool.unpin_page(1)
    assert pool.pin_count(1) == 1  # still pinned by the other user
    pool.fetch_page(2)
    with pytest.raises(BufferPoolFullError):
        pool.fetch_page(3)


def test_flush_page_writes_and_cleans(disk):
    pool = BufferPool(disk, 2)
    page = pool.fetch_page(1)
    page.write(0, b"zz")
    assert page.dirty
    assert pool.flush_page(1) is True
    assert not page.dirty
    assert disk.read_page(1)[:2] == b"zz"
    assert pool.flush_page(1) is False  # already clean
    assert pool.flush_page(5) is False  # not cached


def test_flush_all_makes_changes_visible_after_reopen(tmp_path):
    path = tmp_path / "f.db"
    ids = []
    with DiskManager(path, PAGE) as dm:
        pool = BufferPool(dm, 3)
        for _ in range(3):
            page = pool.new_page()
            page.write(0, bytes([page.page_id]) * 4)
            ids.append(page.page_id)
            pool.unpin_page(page.page_id)
        assert pool.flush_all() == 3
    with DiskManager(path, PAGE) as dm:
        for pid in ids:
            assert dm.read_page(pid)[:4] == bytes([pid]) * 4


def test_new_page_allocates_pins_and_is_zeroed(tmp_path):
    with DiskManager(tmp_path / "n.db", PAGE) as dm:
        pool = BufferPool(dm, 2)
        a = pool.new_page()
        b = pool.new_page()
        assert (a.page_id, b.page_id) == (1, 2)
        assert dm.num_pages == 3
        assert bytes(a.data) == bytes(PAGE)
        assert pool.pin_count(1) == 1


def test_new_page_on_a_full_pool_does_not_grow_the_file(tmp_path):
    with DiskManager(tmp_path / "n.db", PAGE) as dm:
        pool = BufferPool(dm, 1)
        pool.new_page()  # stays pinned
        with pytest.raises(BufferPoolFullError):
            pool.new_page()
        assert dm.num_pages == 2  # header + one page; nothing leaked


@pytest.mark.parametrize("bad_id", [0, -1, 999])  # 0 is the header page
def test_invalid_page_id_raises_and_pool_stays_usable(disk, bad_id):
    pool = BufferPool(disk, 1)
    with pytest.raises(InvalidPageError):
        pool.fetch_page(bad_id)
    assert pool.fetch_page(1).read(0, 1) == b"\x01"  # the frame was not leaked


def test_hit_ratio(disk):
    pool = BufferPool(disk, 2)
    assert pool.stats.hit_ratio == 0.0
    for _ in range(4):
        touch(pool, 1)  # 1 miss + 3 hits
    assert pool.stats.hit_ratio == 0.75


@pytest.mark.parametrize("bad", [0, -1])
def test_capacity_must_be_positive(disk, bad):
    with pytest.raises(ValueError):
        BufferPool(disk, bad)


def test_slotted_page_through_the_pool(tmp_path):
    with DiskManager(tmp_path / "s.db", PAGE) as dm:
        pool = BufferPool(dm, 1)
        page = pool.new_page()
        pid = page.page_id
        slot = SlottedPage(page).insert(b"hello")
        pool.unpin_page(pid)
        other = pool.new_page()  # capacity 1: evicts and writes back the first page
        pool.unpin_page(other.page_id)
        assert pool.stats.dirty_evictions == 1
        again = pool.fetch_page(pid)  # a miss: read back from disk
        assert SlottedPage(again).get(slot) == b"hello"


def test_cyclic_scan_larger_than_pool_never_hits(disk):
    pool = BufferPool(disk, 3)
    for _ in range(3):
        for pid in (1, 2, 3, 4):  # working set of 4 pages, only 3 frames
            touch(pool, pid)
    assert pool.stats.hits == 0
    assert pool.stats.misses == 12


def test_context_manager_flushes_on_exit(disk):
    with BufferPool(disk, 2) as pool:
        page = pool.fetch_page(1)
        page.write(0, b"q")
        pool.unpin_page(1)
    assert disk.read_page(1)[:1] == b"q"