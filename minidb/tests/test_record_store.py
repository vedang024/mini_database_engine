import pytest

from minidb.storage.disk_manager import DiskManager
from minidb.storage.errors import CorruptPageError, RecordTooLargeError
from minidb.storage.record import Record
from minidb.storage.record_store import PAGE_HEADER, RecordStore

PAGE = 512  # small pages make multi-page behaviour easy to trigger


@pytest.fixture
def disk(tmp_path):
    with DiskManager(tmp_path / "t.db", PAGE) as dm:
        yield dm


def make(i: int, name_len: int = 20) -> Record:
    return Record(i, "n" * name_len, f"user{i}@x.com")


def test_empty_store_scans_nothing(disk):
    assert list(RecordStore(disk).scan()) == []


def test_insert_then_scan_returns_records_in_order(disk):
    store = RecordStore(disk)
    records = [Record(1, "Ann", "a@x.com"), Record(2, "Bob", "b@x.com")]
    for r in records:
        store.insert(r)
    assert list(store.scan()) == records


def test_first_insert_lands_on_page_1(disk):
    assert RecordStore(disk).insert(make(1)) == 1


def test_many_records_span_multiple_pages(disk):
    store = RecordStore(disk)
    records = [make(i) for i in range(100)]
    for r in records:
        store.insert(r)
    assert disk.num_pages > 3
    assert list(store.scan()) == records


def test_records_persist_after_reopen_and_appending_continues(tmp_path):
    path = tmp_path / "p.db"
    records = [make(i) for i in range(25)]
    with DiskManager(path, PAGE) as dm:
        store = RecordStore(dm)
        for r in records:
            store.insert(r)
        store.flush()
    with DiskManager(path, PAGE) as dm:
        store = RecordStore(dm)
        assert list(store.scan()) == records
        store.insert(make(99))
        assert list(store.scan()) == records + [make(99)]


def test_find_by_id(disk):
    store = RecordStore(disk)
    for i in range(5):
        store.insert(make(i))
    assert store.find(3) == make(3)
    assert store.find(99) is None


def test_record_larger_than_a_page_rejected(disk):
    store = RecordStore(disk)
    with pytest.raises(RecordTooLargeError):
        store.insert(Record(1, "x" * 600, ""))
    assert disk.num_pages == 1  # nothing was allocated
    assert list(store.scan()) == []


def test_record_exactly_filling_a_page_then_next_goes_to_new_page(disk):
    store = RecordStore(disk)
    # usable = 512 - 8 header = 504; entry = 4 prefix + (8 + 2 + n + 2) -> n = 488
    assert store.insert(Record(1, "x" * 488, "")) == 1
    assert store.insert(make(2)) == 2


def test_corrupt_page_header_detected(disk):
    pid = disk.allocate_page()
    disk.write_page(pid, PAGE_HEADER.pack(1, 99999).ljust(PAGE, b"\x00"))
    with pytest.raises(CorruptPageError):
        list(RecordStore(disk).scan())
        
def test_page_stats_across_several_pages(disk):
    store = RecordStore(disk)
    for i in range(100):
        store.insert(make(i))

    stats = list(store.page_stats())

    assert disk.num_pages > 3
    assert [s[0] for s in stats] == list(range(1, disk.num_pages))  # pages 1..N, in order
    assert sum(s[1] for s in stats) == 100                          # every record is counted once
    assert all(s[1] > 0 for s in stats)                             # no empty pages in this scenario


def test_page_stats_used_plus_free_equals_page_size(disk):
    store = RecordStore(disk)
    for i in range(30):
        store.insert(make(i))
    disk.allocate_page()  # an untouched all-zero page must be reported correctly too

    stats = list(store.page_stats())

    assert len(stats) > 1
    for page_id, count, used, free in stats:
        assert used + free == PAGE, f"page {page_id}"
        assert 8 <= used <= PAGE  # 8 = page header size
    assert stats[-1][1:] == (0, 8, PAGE - 8)  # empty page: 0 records, only the header used