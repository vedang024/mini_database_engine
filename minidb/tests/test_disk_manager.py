import pytest

from minidb.storage.disk_manager import DiskManager
from minidb.storage.errors import DiskManagerError, InvalidPageError
from minidb.storage.disk_manager import page_offset

PAGE = 4096


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.db"


def test_new_file_has_header_page_only(db_path):
    with DiskManager(db_path, PAGE) as dm:
        assert dm.num_pages == 1
    assert db_path.stat().st_size == PAGE


def test_allocate_returns_sequential_ids_starting_at_1(db_path):
    with DiskManager(db_path, PAGE) as dm:
        assert [dm.allocate_page() for _ in range(3)] == [1, 2, 3]
        assert dm.num_pages == 4


def test_new_page_is_zero_filled(db_path):
    with DiskManager(db_path, PAGE) as dm:
        pid = dm.allocate_page()
        assert dm.read_page(pid) == bytes(PAGE)


def test_write_then_read_roundtrip(db_path):
    with DiskManager(db_path, PAGE) as dm:
        pid = dm.allocate_page()
        data = bytes(range(256)) * (PAGE // 256)
        dm.write_page(pid, data)
        assert dm.read_page(pid) == data


def test_data_persists_after_reopen(db_path):
    data = b"\xab" * PAGE
    with DiskManager(db_path, PAGE) as dm:
        pid = dm.allocate_page()
        dm.write_page(pid, data)
    with DiskManager(db_path, PAGE) as dm:
        assert dm.num_pages == 2
        assert dm.read_page(pid) == data


def test_pages_do_not_overlap(db_path):
    with DiskManager(db_path, PAGE) as dm:
        a, b = dm.allocate_page(), dm.allocate_page()
        dm.write_page(a, b"A" * PAGE)
        dm.write_page(b, b"B" * PAGE)
        assert dm.read_page(a) == b"A" * PAGE
        assert dm.read_page(b) == b"B" * PAGE


def test_wrong_size_write_rejected(db_path):
    with DiskManager(db_path, PAGE) as dm:
        pid = dm.allocate_page()
        with pytest.raises(InvalidPageError):
            dm.write_page(pid, b"too short")


@pytest.mark.parametrize("bad_id", [-1, 0, 5])  # 0 is the header page
def test_invalid_page_id_rejected(db_path, bad_id):
    with DiskManager(db_path, PAGE) as dm:
        with pytest.raises(InvalidPageError):
            dm.read_page(bad_id)


@pytest.mark.parametrize("content", [b"x" * 5, b"x" * 100, b"x" * PAGE])
def test_non_minidb_file_rejected(db_path, content):
    db_path.write_bytes(content)
    with pytest.raises(DiskManagerError, match="not a MiniDB file"):
        DiskManager(db_path, PAGE)


def test_page_size_mismatch_rejected(db_path):
    with DiskManager(db_path, 4096):
        pass
    with pytest.raises(DiskManagerError, match="page size mismatch"):
        DiskManager(db_path, 8192)


def test_truncated_file_rejected(db_path):
    with DiskManager(db_path, PAGE) as dm:
        dm.allocate_page()
    with open(db_path, "r+b") as f:
        f.truncate(2 * PAGE - 10)
    with pytest.raises(DiskManagerError, match="multiple"):
        DiskManager(db_path, PAGE)


def test_empty_existing_file_is_initialised(db_path):
    db_path.touch()
    with DiskManager(db_path, PAGE) as dm:
        assert dm.num_pages == 1


def test_file_size_matches_page_count(db_path):
    with DiskManager(db_path, PAGE) as dm:
        for _ in range(3):
            dm.allocate_page()
    assert db_path.stat().st_size == 4 * PAGE

@pytest.mark.parametrize("pid,expected", [(0, 0), (1, 4096), (3, 12288)])
def test_page_offset_formula(pid, expected):
    assert page_offset(pid, 4096) == expected


def test_negative_page_offset_rejected():
    with pytest.raises(InvalidPageError):
        page_offset(-1, 4096)


def test_offset_of_matches_physical_location(db_path):
    with DiskManager(db_path, PAGE) as dm:
        pid = dm.allocate_page()
        dm.write_page(pid, b"Z" * PAGE)
        dm.sync()
        with open(db_path, "rb") as f:
            f.seek(dm.offset_of(pid))
            assert f.read(PAGE) == b"Z" * PAGE


def test_fetch_modify_flush_persists(db_path):
    with DiskManager(db_path, PAGE) as dm:
        pid = dm.allocate_page()
        page = dm.fetch_page(pid)
        page.write(0, b"abc")
        assert page.dirty
        dm.flush_page(page)
        assert not page.dirty
    with DiskManager(db_path, PAGE) as dm:
        assert dm.fetch_page(pid).read(0, 3) == b"abc"


def test_fetch_page_is_a_copy_until_flushed(db_path):
    with DiskManager(db_path, PAGE) as dm:
        pid = dm.allocate_page()
        page = dm.fetch_page(pid)
        page.write(0, b"xyz")  # modified in memory only
        assert dm.fetch_page(pid).read(0, 3) == b"\x00\x00\x00"