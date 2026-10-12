import pytest

from minidb.storage.errors import RecordFormatError
from minidb.storage.record import Record


def test_exact_byte_layout():
    data = Record(1, "ab", "c").to_bytes()
    assert data == (
        b"\x01\x00\x00\x00\x00\x00\x00\x00"  # id = 1, int64 little-endian
        b"\x02\x00" b"ab"                      # name: length 2, then the bytes
        b"\x01\x00" b"c"                       # email: length 1, then the bytes
    )


def test_roundtrip():
    r = Record(42, "Ann", "ann@x.com")
    assert Record.from_bytes(r.to_bytes()) == r


def test_unicode_roundtrip_and_length_counts_bytes():
    r = Record(1, "Zoë", "名前@x.com")
    data = r.to_bytes()
    assert Record.from_bytes(data) == r
    expected = 8 + 2 + len(r.name.encode()) + 2 + len(r.email.encode())
    assert len(data) == expected
    assert len(r.name.encode()) != len(r.name)  # bytes != characters


def test_empty_strings_roundtrip():
    r = Record(0, "", "")
    assert Record.from_bytes(r.to_bytes()) == r


@pytest.mark.parametrize("rid", [0, -1, 2**63 - 1, -(2**63)])
def test_extreme_ids_roundtrip(rid):
    r = Record(rid, "a", "b")
    assert Record.from_bytes(r.to_bytes()).id == rid


def test_id_outside_int64_rejected():
    with pytest.raises(RecordFormatError):
        Record(2**63, "a", "b").to_bytes()


def test_string_too_long_rejected():
    with pytest.raises(RecordFormatError):
        Record(1, "x" * 65536, "").to_bytes()


def test_every_truncation_is_detected():
    data = Record(1, "ab", "c").to_bytes()
    for cut in range(len(data)):
        with pytest.raises(RecordFormatError):
            Record.from_bytes(data[:cut])


def test_trailing_bytes_rejected():
    data = Record(1, "ab", "c").to_bytes()
    with pytest.raises(RecordFormatError):
        Record.from_bytes(data + b"x")