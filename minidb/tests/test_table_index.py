import random

import pytest

from minidb.config import DatabaseConfig
from minidb.database import INDEX_CATALOG_FILE, Database, index_file_name
from minidb.table.errors import (
    CatalogError,
    IndexExistsError,
    IndexInconsistentError,
    IndexNotFoundError,
    RowError,
    SchemaError,
    TableNotFoundError,
    UniqueViolationError,
)
from minidb.table.schema import Column, Schema

PEOPLE = Schema(
    [
        Column("id", "INT", nullable=False),
        Column("name", "TEXT", nullable=False),
        Column("age", "INT"),
    ]
)


def make_config(tmp_path, pool=4) -> DatabaseConfig:
    return DatabaseConfig(data_dir=tmp_path / "data", page_size=512, buffer_pool_pages=pool)


@pytest.fixture
def cfg(tmp_path):
    return make_config(tmp_path)


def test_create_index_backfills_existing_rows(cfg):
    with Database(cfg) as db:
        people = db.create_table("people", PEOPLE)
        rids = {i: people.insert((i, f"u{i}", None)) for i in range(1, 60)}
        assert people.explain_equal("id") == "FullScan(people)"
        db.create_index("people", "id")
        assert people.explain_equal("id") == "IndexLookup(people.id)"
        assert people.explain_range("id") == "IndexRangeScan(people.id)"
        for i, rid in rids.items():
            assert people.index_equal("id", i) == [(rid, (i, f"u{i}", None))]
        assert people.index_equal("id", 999) == []
        assert people.index_for("id").tree.check().key_count == 59
        assert db.index_columns("people") == ["id"]


def test_insert_maintains_the_index(cfg):
    with Database(cfg) as db:
        people = db.create_table("people", PEOPLE)
        db.create_index("people", "id")
        rid = people.insert((7, "Ann", 30))
        assert people.find("id", 7) == [(rid, (7, "Ann", 30))]
        assert people.find("id", 8) == []


def test_delete_maintains_the_index_and_the_key_can_be_reused(cfg):
    with Database(cfg) as db:
        people = db.create_table("people", PEOPLE)
        db.create_index("people", "id")
        rids = {i: people.insert((i, f"u{i}", None)) for i in range(1, 11)}
        people.delete(rids[3])
        assert people.index_equal("id", 3) == []
        assert people.index_for("id").tree.search(3) is None
        again = people.insert((3, "again", None))
        assert people.find("id", 3) == [(again, (3, "again", None))]


def test_unique_violation_writes_nothing(cfg):
    with Database(cfg) as db:
        people = db.create_table("people", PEOPLE)
        db.create_index("people", "id")
        people.insert((1, "a", None))
        before = list(people.scan())
        pages = people.heap.page_count
        with pytest.raises(UniqueViolationError):
            people.insert((1, "b", None))
        assert list(people.scan()) == before
        assert people.heap.page_count == pages
        assert people.index_for("id").tree.check().key_count == 1


def test_create_index_over_duplicate_values_fails_and_leaves_nothing_behind(cfg):
    with Database(cfg) as db:
        people = db.create_table("people", PEOPLE)
        people.insert((1, "a", 30))
        people.insert((2, "b", 30))
        with pytest.raises(UniqueViolationError):
            db.create_index("people", "age")
        assert people.index_for("age") is None
        assert db.index_columns("people") == []
        assert not (cfg.data_dir / index_file_name("people", "age")).exists()
    with Database(cfg) as db:                       # and nothing was cataloged
        assert db.index_columns("people") == []


def test_nulls_are_not_indexed(cfg):
    with Database(cfg) as db:
        people = db.create_table("people", PEOPLE)
        people.insert((1, "a", None))
        people.insert((2, "b", None))
        people.insert((3, "c", 30))
        db.create_index("people", "age")
        people.insert((4, "d", None))               # more NULLs: no unique violation
        assert people.index_for("age").tree.check().key_count == 1
        assert [row for _, row in people.index_equal("age", 30)] == [(3, "c", 30)]
        assert people.find("age", None) == []        # "= NULL" never matches
        assert people.scan_equal("age", None) == []


def test_index_creation_errors(cfg):
    with Database(cfg) as db:
        db.create_table("people", PEOPLE)
        with pytest.raises(SchemaError):
            db.create_index("people", "name")        # TEXT cannot be indexed
        with pytest.raises(SchemaError):
            db.create_index("people", "nope")
        with pytest.raises(TableNotFoundError):
            db.create_index("ghost", "id")
        db.create_index("people", "id")
        with pytest.raises(IndexExistsError):
            db.create_index("PEOPLE", "ID")          # names are case-insensitive


def test_index_survives_reopen_and_keeps_being_maintained(cfg):
    with Database(cfg) as db:
        people = db.create_table("people", PEOPLE)
        db.create_index("people", "id")
        rids = {i: people.insert((i, f"u{i}", None)) for i in range(1, 41)}
    with Database(cfg) as db:
        assert db.index_columns("people") == ["id"]
        people = db.get_table("people")
        assert people.explain_equal("id") == "IndexLookup(people.id)"
        assert people.find("id", 17) == [(rids[17], (17, "u17", None))]
        people.insert((100, "new", None))
    with Database(cfg) as db:
        assert len(db.get_table("people").find("id", 100)) == 1


def test_missing_index_file_is_detected_not_silently_recreated(cfg):
    with Database(cfg) as db:
        db.create_table("people", PEOPLE)
        db.create_index("people", "id")
    (cfg.data_dir / index_file_name("people", "id")).unlink()
    with Database(cfg) as db:
        with pytest.raises(CatalogError, match="missing"):
            db.get_table("people")


def test_orphan_index_file_is_rebuilt(cfg):
    with Database(cfg) as db:
        people = db.create_table("people", PEOPLE)
        for i in range(1, 21):
            people.insert((i, f"u{i}", None))
        db.create_index("people", "id")
    (cfg.data_dir / INDEX_CATALOG_FILE).unlink()     # the index file is now an uncataloged orphan
    with Database(cfg) as db:
        assert db.index_columns("people") == []
        db.create_index("people", "id")
        assert db.get_table("people").find("id", 13)[0][1] == (13, "u13", None)


def test_index_and_scan_agree_after_random_inserts_and_deletes(cfg):
    rng = random.Random(8)
    with Database(cfg) as db:
        people = db.create_table("people", PEOPLE)
        db.create_index("people", "id")
        live = {}
        for _ in range(600):
            i = rng.randrange(150)
            if i in live:
                people.delete(live.pop(i))
            else:
                live[i] = people.insert((i, f"u{i}", None))
        for i in range(150):
            assert people.index_equal("id", i) == people.scan_equal("id", i)
        wanted = sorted(k for k in live if 20 <= k <= 90)
        assert [row[0] for _, row in people.index_range("id", 20, 90)] == wanted
        assert sorted(rid for rid, _ in people.scan_range("id", 20, 90)) == sorted(
            live[k] for k in wanted
        )
        assert people.index_for("id").tree.check(strict_fill=False).key_count == len(live)


def test_range_bounds_and_unindexed_columns(cfg):
    with Database(cfg) as db:
        people = db.create_table("people", PEOPLE)
        for i in range(1, 11):
            people.insert((i, f"u{i}", i * 10 if i % 2 else None))
        db.create_index("people", "id")
        assert [r[0] for _, r in people.find_range("id", 4, 6)] == [4, 5, 6]   # inclusive
        assert [r[0] for _, r in people.find_range("id", None, 2)] == [1, 2]
        assert [r[0] for _, r in people.find_range("id", 9, None)] == [9, 10]
        # "age" has no index: scan path, NULLs excluded
        assert people.explain_range("age") == "FullScan(people)"
        assert sorted(r[2] for _, r in people.find_range("age", 30, 70)) == [30, 50, 70]
        with pytest.raises(IndexNotFoundError):
            people.index_range("age", 30, 70)
        with pytest.raises(IndexNotFoundError):
            people.index_equal("age", 30)


def test_wrong_typed_search_values_are_rejected_on_both_paths(cfg):
    with Database(cfg) as db:
        people = db.create_table("people", PEOPLE)
        people.insert((1, "a", None))
        with pytest.raises(RowError):
            people.find("id", "5")                   # scan path
        db.create_index("people", "id")
        with pytest.raises(RowError):
            people.find("id", "5")                   # index path
        with pytest.raises(RowError):
            people.find("id", True)                  # bool is not an INT
        with pytest.raises(SchemaError):
            people.find("nope", 1)


def test_stale_index_entry_is_detected(cfg):
    with Database(cfg) as db:
        people = db.create_table("people", PEOPLE)
        db.create_index("people", "id")
        rid = people.insert((1, "a", None))
        people.heap.delete(rid)                      # bypass Table: the index entry is now stale
        with pytest.raises(IndexInconsistentError):
            people.index_equal("id", 1)


def test_failed_index_insert_rolls_the_row_back(cfg, monkeypatch):
    with Database(cfg) as db:
        people = db.create_table("people", PEOPLE)
        db.create_index("people", "id")
        db.create_index("people", "age")

        def boom(key, rid):
            raise RuntimeError("disk full")

        monkeypatch.setattr(people.index_for("age").tree, "insert", boom)
        with pytest.raises(RuntimeError):
            people.insert((1, "a", 30))
        assert list(people.scan()) == []                       # the heap row is gone
        assert people.index_for("id").tree.search(1) is None   # and the earlier index entry too


def test_everything_works_with_a_single_frame_per_pool(tmp_path):
    cfg = make_config(tmp_path, pool=1)
    with Database(cfg) as db:
        people = db.create_table("people", PEOPLE)
        for i in range(1, 50):
            people.insert((i, "x" * 20, None))
        db.create_index("people", "id")              # backfill from a scan
        for i in range(50, 100):
            people.insert((i, "x" * 20, None))
        assert people.find("id", 77)[0][1][0] == 77
        assert len(people.index_range("id", 10, 90)) == 81
        people.delete(people.find("id", 77)[0][0])
        assert people.find("id", 77) == []