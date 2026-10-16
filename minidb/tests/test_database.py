import subprocess
import sys
from pathlib import Path

import pytest

from minidb.config import DatabaseConfig
from minidb.database import CATALOG_FILE, CATALOG_SCHEMA, Database
from minidb.storage.heap_file import HeapFile
from minidb.table.errors import (
    CatalogError,
    SchemaError,
    TableExistsError,
    TableNotFoundError,
)
from minidb.table.schema import Column, Schema
from minidb.table.table import Table

REPO_ROOT = Path(__file__).resolve().parent.parent

USERS = Schema(
    [
        Column("id", "INT", nullable=False),
        Column("name", "TEXT", nullable=False),
        Column("email", "TEXT"),
        Column("active", "BOOL"),
    ]
)
ORDERS = Schema([Column("order_id", "INT", nullable=False), Column("total", "FLOAT")])


def make_config(tmp_path) -> DatabaseConfig:
    return DatabaseConfig(data_dir=tmp_path / "data", page_size=512, buffer_pool_pages=4)


def test_tables_and_rows_survive_reopen(tmp_path):
    cfg = make_config(tmp_path)
    with Database(cfg) as db:
        rid = db.create_table("users", USERS).insert((1, "Ann", "ann@x.com", True))
    with Database(cfg) as db:
        assert db.table_names() == ["users"]
        users = db.get_table("users")
        assert users.schema == USERS
        assert users.get(rid) == (1, "Ann", "ann@x.com", True)


def test_duplicate_table_rejected_case_insensitively(tmp_path):
    with Database(make_config(tmp_path)) as db:
        db.create_table("users", USERS)
        with pytest.raises(TableExistsError):
            db.create_table("USERS", USERS)


def test_unknown_table_raises(tmp_path):
    with Database(make_config(tmp_path)) as db:
        with pytest.raises(TableNotFoundError):
            db.get_table("nope")


def test_table_lookup_is_case_insensitive_and_cached(tmp_path):
    with Database(make_config(tmp_path)) as db:
        db.create_table("users", USERS)
        assert db.get_table("USERS") is db.get_table("users")


@pytest.mark.parametrize("bad", ["", "1x", "a b", "../evil", "x" * 65])
def test_invalid_table_names_rejected_and_no_file_created(tmp_path, bad):
    cfg = make_config(tmp_path)
    with Database(cfg) as db:
        with pytest.raises(SchemaError):
            db.create_table(bad, USERS)
    assert list(cfg.data_dir.glob("tbl_*")) == []


def test_tables_are_isolated_and_names_sorted(tmp_path):
    with Database(make_config(tmp_path)) as db:
        users = db.create_table("users", USERS)
        orders = db.create_table("orders", ORDERS)
        users.insert((1, "Ann", None, None))
        assert list(orders.scan()) == []
        assert db.table_names() == ["orders", "users"]


def test_create_table_is_durable_immediately(tmp_path):
    cfg = make_config(tmp_path)
    db = Database(cfg)
    db.create_table("users", USERS)
    raw = (cfg.data_dir / CATALOG_FILE).read_bytes()   # read while the database is still open
    assert b"users" in raw                              # the catalog row is already on disk
    db.close()


def test_create_refuses_to_adopt_a_non_empty_orphan_file(tmp_path):
    cfg = make_config(tmp_path)
    with Database(cfg) as db:
        db.create_table("users", USERS).insert((1, "Ann", None, None))
    (cfg.data_dir / CATALOG_FILE).unlink()              # lose the catalog, keep the table file
    with Database(cfg) as db:
        assert db.table_names() == []
        with pytest.raises(CatalogError, match="refusing"):
            db.create_table("users", USERS)


def test_create_adopts_an_empty_orphan_file(tmp_path):
    cfg = make_config(tmp_path)
    with Database(cfg) as db:
        db.create_table("users", USERS)                 # no rows inserted
    (cfg.data_dir / CATALOG_FILE).unlink()
    with Database(cfg) as db:
        db.create_table("users", USERS)                 # empty leftover is fine
        assert db.table_names() == ["users"]


def test_duplicate_catalog_entry_is_detected(tmp_path):
    cfg = make_config(tmp_path)
    with Database(cfg) as db:
        db.create_table("users", USERS)
    catalog = Table(
        "catalog",
        CATALOG_SCHEMA,
        HeapFile(cfg.data_dir / CATALOG_FILE, page_size=512, pool_pages=2),
    )
    [(_, row)] = list(catalog.scan())
    catalog.insert(row)                                 # the same entry, twice
    catalog.close()
    with pytest.raises(CatalogError, match="duplicate"):
        Database(cfg)


def run_demo(data_dir, command):
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "table_demo.py"),
         "--data-dir", str(data_dir), command],
        capture_output=True,
        text=True,
    )


def test_data_survives_a_real_process_restart(tmp_path):
    first = run_demo(tmp_path / "d", "write")
    assert first.returncode == 0, first.stderr
    second = run_demo(tmp_path / "d", "read")      # a different OS process
    assert second.returncode == 0, second.stderr
    assert "(1, 'Ann', 'ann@x.com', True)" in second.stdout
    assert "(3, 'Cy', 'cy@x.com', None)" in second.stdout
    assert "Bob" not in second.stdout               # deleted before the restart
    assert "(2 row(s))" in second.stdout