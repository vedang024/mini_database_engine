"""Database: a directory of tables and indexes, plus the catalogs that remember them.

Both catalogs are heap files with fixed schemas. CREATE TABLE and CREATE INDEX
are "committed" when their catalog row has been flushed to disk.
"""

from __future__ import annotations

import contextlib
import logging
from types import TracebackType

from minidb.config import DatabaseConfig
from minidb.index.btree import BPlusTree
from minidb.index.errors import DuplicateKeyError
from minidb.storage.heap_file import HeapFile
from minidb.table.errors import (
    CatalogError,
    IndexExistsError,
    SchemaError,
    TableExistsError,
    TableNotFoundError,
    UniqueViolationError,
)
from minidb.table.schema import Column, ColumnType, Schema, validate_identifier
from minidb.table.table import Table

logger = logging.getLogger(__name__)

CATALOG_FILE = "catalog.dat"
INDEX_CATALOG_FILE = "catalog_indexes.dat"
CATALOG_SCHEMA = Schema(
    [
        Column("table_name", ColumnType.TEXT, nullable=False),
        Column("schema_json", ColumnType.TEXT, nullable=False),
    ]
)
INDEX_CATALOG_SCHEMA = Schema(
    [
        Column("table_name", ColumnType.TEXT, nullable=False),
        Column("column_name", ColumnType.TEXT, nullable=False),
    ]
)


def index_file_name(table: str, column: str) -> str:
    """Identifiers never contain dots, so this name cannot collide across (table, column)."""
    return f"idx.{table}.{column}.dat"


class Database:
    def __init__(self, config: DatabaseConfig | None = None) -> None:
        self._config = config or DatabaseConfig()
        self._config.data_dir.mkdir(parents=True, exist_ok=True)
        self._schemas: dict[str, Schema] = {}            # every table the catalog knows
        self._tables: dict[str, Table] = {}              # tables opened so far (lazily)
        self._index_columns: dict[str, list[str]] = {}   # table -> indexed column names
        self._closed = False
        self._catalog = self._open_table("catalog", CATALOG_SCHEMA, CATALOG_FILE)
        try:
            self._index_catalog = self._open_table(
                "catalog_indexes", INDEX_CATALOG_SCHEMA, INDEX_CATALOG_FILE
            )
        except Exception:
            self._catalog.close()
            raise
        try:
            self._load_catalog()
            self._load_index_catalog()
        except Exception:
            self._index_catalog.close()
            self._catalog.close()
            raise

    # ---- tables ---------------------------------------------------------------------
    def create_table(self, name: str, schema: Schema) -> Table:
        key = validate_identifier(name)
        if key in self._schemas:
            raise TableExistsError(f"table {key!r} already exists")
        table = self._open_table(key, schema, self._table_file(key))  # creates the file
        try:
            if table.heap.page_count != 0:
                raise CatalogError(
                    f"{self._table_file(key)} already holds data but the catalog has no "
                    f"table {key!r}; refusing to adopt it"
                )
            self._catalog.insert((key, schema.to_json()))
            self._catalog.flush()  # COMMIT POINT: the table exists from here on
        except Exception:
            table.close()
            raise
        self._schemas[key] = schema
        self._tables[key] = table
        logger.info("Created table %s (%d columns)", key, len(schema.columns))
        return table

    def get_table(self, name: str) -> Table:
        key = name.lower()
        table = self._tables.get(key)
        if table is None:
            schema = self._schemas.get(key)
            if schema is None:
                raise TableNotFoundError(f"table {name!r} does not exist")
            table = self._open_user_table(key, schema)
            self._tables[key] = table
        return table

    def table_names(self) -> list[str]:
        return sorted(self._schemas)

    # (If you wrote drop_table in Phase 7, paste it here. It must now also drop the
    #  table's indexes: see the exercise.)

    # ---- indexes ---------------------------------------------------------------------
    def create_index(self, table_name: str, column_name: str) -> None:
        """Build a unique B+ tree index on an INT column from the table's current rows."""
        table = self.get_table(table_name)
        key = table.name
        position = table.schema.index_of(column_name)
        column = table.schema.columns[position]
        if column.type is not ColumnType.INT:
            raise SchemaError(
                f"cannot index column {column.name!r}: only INT columns can be indexed"
            )
        if column.name in self._index_columns.get(key, []):
            raise IndexExistsError(f"table {key!r} already has an index on {column.name!r}")

        cfg = self._config
        path = cfg.data_dir / index_file_name(key, column.name)
        path.unlink(missing_ok=True)  # a leftover from a crashed CREATE INDEX: not cataloged, so not valid
        tree = BPlusTree(
            path,
            page_size=cfg.page_size,
            pool_pages=cfg.buffer_pool_pages,
            eviction_policy=cfg.eviction_policy,
        )
        try:
            for rid, row in table.scan():
                value = row[position]
                if value is not None:  # NULLs are not indexed
                    tree.insert(value, rid)
            tree.flush()
            self._index_catalog.insert((key, column.name))
            self._index_catalog.flush()  # COMMIT POINT: the index exists from here on
        except DuplicateKeyError as exc:
            self._discard_index(tree, path)
            raise UniqueViolationError(
                f"cannot build a unique index on {key}.{column.name}: {exc}"
            ) from exc
        except Exception:
            self._discard_index(tree, path)
            raise
        table.attach_index(column.name, tree)
        self._index_columns.setdefault(key, []).append(column.name)
        logger.info("Created index on %s.%s", key, column.name)

    def index_columns(self, table_name: str) -> list[str]:
        """Names of the indexed columns of a table, in creation order."""
        return list(self._index_columns.get(table_name.lower(), []))

    # ---- lifecycle ----------------------------------------------------------------------
    def flush(self) -> None:
        for table in self._tables.values():
            table.flush()
        self._catalog.flush()
        self._index_catalog.flush()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        first_error: Exception | None = None
        for table in (*self._tables.values(), self._index_catalog, self._catalog):
            try:
                table.close()
            except Exception as exc:  # keep closing the rest, report the first failure
                first_error = first_error or exc
        if first_error is not None:
            raise first_error

    def __enter__(self) -> "Database":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # ---- internals ------------------------------------------------------------------------
    @staticmethod
    def _table_file(name: str) -> str:
        return f"tbl_{name}.dat"

    def _open_table(self, name: str, schema: Schema, file_name: str) -> Table:
        cfg = self._config
        heap = HeapFile(
            cfg.data_dir / file_name,
            page_size=cfg.page_size,
            pool_pages=cfg.buffer_pool_pages,
            eviction_policy=cfg.eviction_policy,
        )
        return Table(name, schema, heap)

    def _open_user_table(self, key: str, schema: Schema) -> Table:
        """Open a table's heap file and attach every cataloged index to it."""
        table = self._open_table(key, schema, self._table_file(key))
        try:
            for column in self._index_columns.get(key, []):
                table.attach_index(column, self._open_index(key, column))
        except Exception:
            table.close()
            raise
        return table

    def _open_index(self, table: str, column: str) -> BPlusTree:
        cfg = self._config
        path = cfg.data_dir / index_file_name(table, column)
        if not path.exists():
            # BPlusTree would silently create a fresh EMPTY tree here: refuse instead.
            raise CatalogError(f"index file {path.name} is missing but the catalog lists it")
        return BPlusTree(
            path,
            page_size=cfg.page_size,
            pool_pages=cfg.buffer_pool_pages,
            eviction_policy=cfg.eviction_policy,
        )

    @staticmethod
    def _discard_index(tree: BPlusTree, path) -> None:
        with contextlib.suppress(Exception):
            tree.close()  # Windows cannot delete an open file
        with contextlib.suppress(OSError):
            path.unlink()

    def _load_catalog(self) -> None:
        for _rid, (name, schema_json) in self._catalog.scan():
            if name in self._schemas:
                raise CatalogError(f"duplicate catalog entry for table {name!r}")
            self._schemas[name] = Schema.from_json(schema_json)

    def _load_index_catalog(self) -> None:
        for _rid, (table_name, column_name) in self._index_catalog.scan():
            schema = self._schemas.get(table_name)
            if schema is None:
                raise CatalogError(f"index catalog refers to unknown table {table_name!r}")
            schema.index_of(column_name)  # raises if the column no longer exists
            columns = self._index_columns.setdefault(table_name, [])
            if column_name in columns:
                raise CatalogError(f"duplicate index entry for {table_name}.{column_name}")
            columns.append(column_name)