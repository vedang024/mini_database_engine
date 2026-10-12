import struct

import pytest

from minidb.storage.errors import InvalidPageError
from minidb.storage.page import Page

PAGE = 4096


def new_page() -> Page:
    return Page(1, bytearray(PAGE))


def test_write_marks_dirty_and_read_returns_bytes():
    p = new_page()
    assert not p.dirty
    p.write(10, b"hello")
    assert p.dirty
    assert p.read(10, 5) == b"hello"
    assert p.size == PAGE


@pytest.mark.parametrize("offset,length", [(-1, 1), (4090, 10), (4096, 1)])
def test_out_of_bounds_access_rejected(offset, length):
    with pytest.raises(InvalidPageError):
        new_page().read(offset, length)


def test_struct_helpers_roundtrip():
    fmt = struct.Struct("<II")
    p = new_page()
    p.pack_into(fmt, 100, 7, 9)
    assert p.dirty
    assert p.unpack_from(fmt, 100) == (7, 9)