"""Database: a directory of tables plus the catalog that remembers their schemas.

The catalog is itself a heap file with a fixed schema. CREATE TABLE is
"committed" when its catalog row has been flushed to disk.
"""

from __future__ import annotations

import logging
from types import TracebackType

from minidb.config import DatabaseConfig
from minidb.storage.heap_file import HeapFile
from minidb.table.errors import CatalogError, TableExistsError, TableNotFoundError
from minidb.table.schema import Column, ColumnType, Schema, validate_identifier
from minidb.table.table import Table

logger = logging.getLogger(__name__)

CATALOG_FILE = "catalog.dat"
CATALOG_SCHEMA = Schema(
    [
        Column("table_name", ColumnType.TEXT, nullable=False),
        Column("schema_json", ColumnType.TEXT, nullable=False),
    ]
)


class Database:
    def __init__(self, config: DatabaseConfig | None = None) -> None:
        self._config = config or DatabaseConfig()
        self._config.data_dir.mkdir(parents=True, exist_ok=True)
        self._schemas: dict[str, Schema] = {}   # every table the catalog knows
        self._tables: dict[str, Table] = {}     # tables opened so far (opened lazily)
        self._closed = False
        self._catalog = self._open_table("catalog", CATALOG_SCHEMA, CATALOG_FILE)
        try:
            self._load_catalog()
        except Exception:
            self._catalog.close()
            raise

    # ---- API ---------------------------------------------------------------------
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
            table = self._open_table(key, schema, self._table_file(key))
            self._tables[key] = table
        return table

    def table_names(self) -> list[str]:
        return sorted(self._schemas)

    def flush(self) -> None:
        for table in self._tables.values():
            table.flush()
        self._catalog.flush()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        first_error: Exception | None = None
        for table in (*self._tables.values(), self._catalog):
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

    # ---- internals -------------------------------------------------------------------
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

    def _load_catalog(self) -> None:
        for _rid, (name, schema_json) in self._catalog.scan():
            if name in self._schemas:
                raise CatalogError(f"duplicate catalog entry for table {name!r}")
            self._schemas[name] = Schema.from_json(schema_json)