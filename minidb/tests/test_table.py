import pytest

from minidb.storage.errors import RecordTooLargeError
from minidb.storage.heap_file import HeapFile
from minidb.storage.record_id import RecordId
from minidb.table.errors import RowError, RowNotFoundError
from minidb.table.schema import Column, Schema
from minidb.table.table import Table

USERS = Schema(
    [
        Column("id", "INT", nullable=False),
        Column("name", "TEXT", nullable=False),
        Column("email", "TEXT"),
        Column("active", "BOOL"),
    ]
)


@pytest.fixture
def users(tmp_path):
    table = Table("users", USERS, HeapFile(tmp_path / "u.heap", page_size=512, pool_pages=4))
    yield table
    table.close()


def test_insert_and_get_roundtrip_including_nulls(users):
    rid = users.insert((1, "Ann", None, True))
    assert users.get(rid) == (1, "Ann", None, True)
    assert users.get(users.insert([2, "Bob", "b@x.com", False])) == (2, "Bob", "b@x.com", False)


def test_scan_returns_rids_and_rows_in_order(users):
    rows = [(i, f"user{i}", None, None) for i in range(30)]
    rids = [users.insert(r) for r in rows]
    assert list(users.scan()) == list(zip(rids, rows))


def test_delete_then_get_raises_and_scan_excludes(users):
    keep = users.insert((1, "Ann", None, None))
    gone = users.insert((2, "Bob", None, None))
    users.delete(gone)
    with pytest.raises(RowNotFoundError):
        users.get(gone)
    assert [rid for rid, _ in users.scan()] == [keep]


def test_delete_twice_raises(users):
    rid = users.insert((1, "Ann", None, None))
    users.delete(rid)
    with pytest.raises(RowNotFoundError):
        users.delete(rid)


@pytest.mark.parametrize("rid", [RecordId(0, 0), RecordId(99, 0), RecordId(1, 99), RecordId(1, -1)])
def test_unknown_rids_raise_row_not_found(users, rid):
    users.insert((1, "Ann", None, None))
    with pytest.raises(RowNotFoundError):
        users.get(rid)


@pytest.mark.parametrize(
    "row",
    [
        (None, "x", None, None),   # NOT NULL violated
        ("1", "x", None, None),    # wrong type
        (1, "x"),                  # wrong arity
    ],
)
def test_invalid_rows_rejected_and_nothing_is_written(users, row):
    with pytest.raises(RowError):
        users.insert(row)
    assert users.heap.page_count == 0


def test_row_larger_than_a_page_rejected(users):
    with pytest.raises(RecordTooLargeError):
        users.insert((1, "x" * 600, None, None))
    assert users.heap.page_count == 0


def test_reused_slot_gives_a_stale_rid_a_new_row(users):
    old = users.insert((1, "Ann", None, None))
    users.delete(old)
    new = users.insert((2, "Bob", None, None))
    assert new == old                                   # the slot was reused
    assert users.get(old) == (2, "Bob", None, None)     # the stale RID now reads a DIFFERENT row


def test_rows_persist_after_reopen(tmp_path):
    path = tmp_path / "p.heap"
    table = Table("users", USERS, HeapFile(path, page_size=512, pool_pages=4))
    rid = table.insert((1, "Ann", None, True))
    table.close()
    table = Table("users", USERS, HeapFile(path, page_size=512, pool_pages=4))
    assert table.get(rid) == (1, "Ann", None, True)
    table.close()