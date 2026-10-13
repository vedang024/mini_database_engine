import random

import pytest

from minidb.storage.disk_manager import DiskManager
from minidb.storage.errors import (
    CorruptPageError,
    InvalidSlotError,
    PageFullError,
    RecordFormatError,
    RecordTooLargeError,
)
from minidb.storage.page import Page
from minidb.storage.record_id import RecordId
from minidb.storage.slotted_page import HEADER, SlottedPage

PAGE = 512


def make_page() -> SlottedPage:
    return SlottedPage(Page(1, bytearray(PAGE)))


def test_fresh_page_state():
    sp = make_page()
    assert sp.num_slots == 0
    assert sp.free_end == PAGE
    assert sp.contiguous_free() == PAGE - 8          # minus the 8-byte header
    assert sp.total_free() == PAGE - 8
    assert sp.max_record_size == PAGE - 8 - 4        # header and one slot entry


def test_insert_get_roundtrip_with_sequential_slots():
    sp = make_page()
    data = [b"alpha", b"beta", b"gamma-gamma"]
    slots = [sp.insert(d) for d in data]
    assert slots == [0, 1, 2]
    assert [sp.get(s) for s in slots] == data


def test_records_grow_from_the_end_of_the_page():
    sp = make_page()
    sp.insert(b"AAAA")
    assert sp.free_end == PAGE - 4
    assert sp.contiguous_free() == (PAGE - 4) - (8 + 4)  # one slot entry in the directory
    assert sp.page.read(PAGE - 4, 4) == b"AAAA"


def test_delete_invalidates_slot_but_not_neighbours():
    sp = make_page()
    a, b, c = sp.insert(b"aaa"), sp.insert(b"bbb"), sp.insert(b"ccc")
    sp.delete(b)
    with pytest.raises(InvalidSlotError):
        sp.get(b)
    assert sp.get(a) == b"aaa"
    assert sp.get(c) == b"ccc"       # same slot number as before
    assert sp.num_slots == 3         # directory entry stays (it's a tombstone)


def test_delete_twice_rejected():
    sp = make_page()
    s = sp.insert(b"x")
    sp.delete(s)
    with pytest.raises(InvalidSlotError):
        sp.delete(s)


@pytest.mark.parametrize("bad", [-1, 0, 99])
def test_out_of_range_slot_rejected_on_empty_page(bad):
    sp = make_page()
    with pytest.raises(InvalidSlotError):
        sp.get(bad)
    with pytest.raises(InvalidSlotError):
        sp.delete(bad)


def test_deleted_slot_is_reused():
    sp = make_page()
    sp.insert(b"a")
    middle = sp.insert(b"b")
    sp.insert(b"c")
    sp.delete(middle)
    assert sp.insert(b"new") == middle
    assert sp.get(middle) == b"new"
    assert sp.num_slots == 3         # directory did not grow


def test_delete_leaves_a_hole_that_total_free_counts():
    sp = make_page()
    s = sp.insert(b"x" * 100)
    assert sp.free_end == PAGE - 100
    assert sp.contiguous_free() == 400
    assert sp.total_free() == 400
    sp.delete(s)
    assert sp.free_end == PAGE - 100          # record area did not shrink
    assert sp.contiguous_free() == 400        # the hole is NOT in the gap...
    assert sp.total_free() == 500             # ...but it is reclaimable


def test_insert_triggers_compaction_when_only_fragmented_space_fits():
    sp = make_page()
    recs = [bytes([i]) * 100 for i in range(4)]
    slots = [sp.insert(r) for r in recs]
    sp.delete(slots[0])
    sp.delete(slots[2])
    new = b"N" * 150
    assert sp.contiguous_free() == 88 < 150 <= sp.total_free() == 288

    slot = sp.insert(new)                     # must compact to fit

    assert slot == 0                          # lowest deleted slot is reused
    assert sp.get(0) == new
    assert sp.get(1) == recs[1]               # survivors intact, same slot numbers
    assert sp.get(3) == recs[3]
    assert sp.contiguous_free() == 138


def test_page_full_error_leaves_page_unchanged():
    sp = make_page()
    recs = [bytes([i]) * 100 for i in range(4)]
    for r in recs:
        sp.insert(r)
    with pytest.raises(PageFullError):
        sp.insert(b"y" * 100)
    assert [d for _, d in sp.iter_records()] == recs


def test_max_record_size_boundaries():
    sp = make_page()
    with pytest.raises(RecordTooLargeError):
        sp.insert(b"x" * (sp.max_record_size + 1))
    slot = sp.insert(b"x" * sp.max_record_size)   # exactly fills the page
    assert sp.contiguous_free() == 0
    assert len(sp.get(slot)) == sp.max_record_size


def test_empty_record_rejected():
    with pytest.raises(RecordFormatError):
        make_page().insert(b"")


def test_iter_records_skips_deleted():
    sp = make_page()
    sp.insert(b"a")
    gone = sp.insert(b"b")
    sp.insert(b"c")
    sp.delete(gone)
    assert list(sp.iter_records()) == [(0, b"a"), (2, b"c")]


def test_corrupt_header_detected():
    page = Page(1, bytearray(PAGE))
    page.pack_into(HEADER, 0, 1000, PAGE)     # 1000 slots cannot fit in 512 bytes
    with pytest.raises(CorruptPageError):
        SlottedPage(page)


def test_survives_flush_and_reopen(tmp_path):
    path = tmp_path / "s.db"
    with DiskManager(path, PAGE) as disk:
        pid = disk.allocate_page()
        sp = SlottedPage(disk.fetch_page(pid))
        first = sp.insert(b"alpha")
        second = sp.insert(b"beta")
        sp.delete(first)
        disk.flush_page(sp.page)
    with DiskManager(path, PAGE) as disk:
        sp = SlottedPage(disk.fetch_page(pid))   # must NOT re-format a used page
        assert sp.get(second) == b"beta"
        with pytest.raises(InvalidSlotError):
            sp.get(first)


def test_random_operations_match_a_dict_model():
    rng = random.Random(42)
    sp = make_page()
    model: dict[int, bytes] = {}
    for _ in range(2000):
        if model and rng.random() < 0.4:
            slot = rng.choice(list(model))
            sp.delete(slot)
            del model[slot]
        else:
            data = bytes([rng.randrange(256)]) * rng.randint(1, 60)
            try:
                slot = sp.insert(data)
            except PageFullError:
                continue
            assert slot not in model
            model[slot] = data
        assert dict(sp.iter_records()) == model
        assert sp.total_free() >= sp.contiguous_free() >= 0


def test_record_id_ordering_and_str():
    assert RecordId(1, 5) < RecordId(2, 0)
    assert RecordId(1, 1) < RecordId(1, 2)
    assert str(RecordId(7, 2)) == "RID(page=7, slot=2)"
    assert {RecordId(1, 1): "x"}[RecordId(1, 1)] == "x"   # hashable