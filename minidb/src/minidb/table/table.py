"""Table: a schema, a heap file, and optional unique indexes on INT columns.

It speaks in rows. insert/delete keep every index in sync; find/find_range use
an index when one exists and fall back to a full scan otherwise. The explicit
scan_* and index_* methods exist so the two paths can be compared.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from minidb.index.btree import BPlusTree
from minidb.storage.errors import InvalidPageError, InvalidSlotError
from minidb.storage.heap_file import HeapFile
from minidb.storage.record_id import RecordId
from minidb.table.errors import (
    IndexExistsError,
    IndexInconsistentError,
    IndexNotFoundError,
    RowError,
    RowNotFoundError,
    SchemaError,
    UniqueViolationError,
)
from minidb.table.row_codec import decode_row, encode_row
from minidb.table.schema import ColumnType, Schema
from minidb.table.types import type_of

Row = tuple[object, ...]
Match = tuple[RecordId, Row]


@dataclass(frozen=True)
class ColumnIndex:
    column: str
    position: int      # the column's position in the row
    tree: BPlusTree


class Table:
    def __init__(self, name: str, schema: Schema, heap: HeapFile) -> None:
        self._name = name
        self._schema = schema
        self._heap = heap
        self._indexes: dict[str, ColumnIndex] = {}

    @property
    def name(self) -> str:
        return self._name

    @property
    def schema(self) -> Schema:
        return self._schema

    @property
    def heap(self) -> HeapFile:
        return self._heap

    # ---- indexes -----------------------------------------------------------------
    def attach_index(self, column: str, tree: BPlusTree) -> ColumnIndex:
        """Register an already-built tree as the index of a column."""
        position = self._schema.index_of(column)
        col = self._schema.columns[position]
        if col.type is not ColumnType.INT:
            raise SchemaError(f"cannot index column {col.name!r}: only INT columns can be indexed")
        if col.name in self._indexes:
            raise IndexExistsError(f"table {self._name!r} already has an index on {col.name!r}")
        index = ColumnIndex(col.name, position, tree)
        self._indexes[col.name] = index
        return index

    def index_for(self, column: str) -> ColumnIndex | None:
        return self._indexes.get(column.lower())

    def indexed_columns(self) -> list[str]:
        return sorted(self._indexes)

    # ---- writes ----------------------------------------------------------------------
    def insert(self, values: Sequence[object]) -> RecordId:
        """Validate and store a row, then add it to every index. All or nothing."""
        data = encode_row(self._schema, values)  # validates the whole row first
        keys = [
            (index, values[index.position])
            for index in self._indexes.values()
            if values[index.position] is not None  # NULLs are not indexed
        ]
        for index, key in keys:  # check uniqueness BEFORE touching the heap
            if index.tree.search(key) is not None:
                raise UniqueViolationError(
                    f"duplicate key {key} for unique index on {self._name}.{index.column}"
                )
        rid = self._heap.insert(data)
        done: list[tuple[ColumnIndex, int]] = []
        try:
            for index, key in keys:
                index.tree.insert(key, rid)
                done.append((index, key))
        except Exception:
            for index, key in done:  # undo: no row without its index entries
                index.tree.delete(key)
            self._heap.delete(rid)
            raise
        return rid

    def delete(self, rid: RecordId) -> None:
        row = self.get(rid)  # raises RowNotFoundError; we need the keys to unindex
        try:
            self._heap.delete(rid)
        except (InvalidPageError, InvalidSlotError) as exc:
            raise RowNotFoundError(f"table {self._name!r}: no row at {rid}") from exc
        for index in self._indexes.values():
            key = row[index.position]
            if key is not None:
                index.tree.delete(key)

    # ---- reads by address ---------------------------------------------------------------
    def get(self, rid: RecordId) -> Row:
        try:
            data = self._heap.get(rid)
        except (InvalidPageError, InvalidSlotError) as exc:
            raise RowNotFoundError(f"table {self._name!r}: no row at {rid}") from exc
        return decode_row(self._schema, data)

    def scan(self) -> Iterator[tuple[RecordId, Row]]:
        for rid, data in self._heap.scan():
            yield rid, decode_row(self._schema, data)

    # ---- lookups: the two access paths ------------------------------------------------
    def explain_equal(self, column: str) -> str:
        """Which access path find() will use (a one-line query plan)."""
        name = self._schema.columns[self._schema.index_of(column)].name
        if name in self._indexes:
            return f"IndexLookup({self._name}.{name})"
        return f"FullScan({self._name})"

    def explain_range(self, column: str) -> str:
        name = self._schema.columns[self._schema.index_of(column)].name
        if name in self._indexes:
            return f"IndexRangeScan({self._name}.{name})"
        return f"FullScan({self._name})"

    def find(self, column: str, value: object) -> list[Match]:
        """WHERE column = value. Uses the index when there is one."""
        if self.index_for(column) is not None:
            return self.index_equal(column, value)
        return self.scan_equal(column, value)

    def find_range(
        self, column: str, low: object = None, high: object = None
    ) -> list[Match]:
        """WHERE low <= column <= high (either bound may be None).

        Index results come in key order, scan results in heap order.
        """
        if self.index_for(column) is not None:
            return self.index_range(column, low, high)
        return self.scan_range(column, low, high)

    def scan_equal(self, column: str, value: object) -> list[Match]:
        """Full table scan: O(n) page accesses, whatever the column."""
        position = self._schema.index_of(column)
        if value is None:
            return []  # "= NULL" is never true in SQL
        target = self._coerce(position, value)
        return [(rid, row) for rid, row in self.scan() if row[position] == target]

    def index_equal(self, column: str, value: object) -> list[Match]:
        """Index lookup: tree height + one heap page. Requires an index."""
        index = self._require_index(column)
        if value is None:
            return []
        key = self._coerce(index.position, value)
        rid = index.tree.search(key)
        if rid is None:
            return []
        return [(rid, self._fetch_indexed(index, rid, key))]

    def scan_range(
        self, column: str, low: object = None, high: object = None
    ) -> list[Match]:
        position = self._schema.index_of(column)
        lo, hi = self._bounds(position, low, high)
        matches: list[Match] = []
        for rid, row in self.scan():
            value = row[position]
            if value is None:
                continue
            if lo is not None and not value >= lo:  # "not >=" also skips NaN
                continue
            if hi is not None and not value <= hi:
                continue
            matches.append((rid, row))
        return matches

    def index_range(
        self, column: str, low: object = None, high: object = None
    ) -> list[Match]:
        index = self._require_index(column)
        lo, hi = self._bounds(index.position, low, high)
        return [
            (rid, self._fetch_indexed(index, rid, key))
            for key, rid in index.tree.range_scan(lo, hi)
        ]

    # ---- lifecycle ------------------------------------------------------------------------
    def flush(self) -> None:
        self._heap.flush()
        for index in self._indexes.values():
            index.tree.flush()

    def close(self) -> None:
        first_error: Exception | None = None
        closers = [self._heap.close, *(ix.tree.close for ix in self._indexes.values())]
        for close in closers:  # keep closing the rest, report the first failure
            try:
                close()
            except Exception as exc:
                first_error = first_error or exc
        if first_error is not None:
            raise first_error

    # ---- internals ----------------------------------------------------------------------------
    def _require_index(self, column: str) -> ColumnIndex:
        index = self._indexes.get(column.lower())
        if index is None:
            raise IndexNotFoundError(f"table {self._name!r} has no index on {column!r}")
        return index

    def _coerce(self, position: int, value: object) -> object:
        """Validate a search value against the column's type (3 -> 3.0 for FLOAT)."""
        column = self._schema.columns[position]
        try:
            return type_of(column).validate(column, value)
        except ValueError as exc:
            raise RowError(f"column {column.name!r}: {exc}") from exc

    def _bounds(self, position: int, low: object, high: object) -> tuple[object, object]:
        return (
            None if low is None else self._coerce(position, low),
            None if high is None else self._coerce(position, high),
        )

    def _fetch_indexed(self, index: ColumnIndex, rid: RecordId, key: int) -> Row:
        """Fetch the row an index entry points at, and check the entry told the truth."""
        try:
            row = self.get(rid)
        except RowNotFoundError as exc:
            raise IndexInconsistentError(
                f"index on {self._name}.{index.column} maps {key} to {rid}, which holds no row"
            ) from exc
        if row[index.position] != key:
            raise IndexInconsistentError(
                f"index on {self._name}.{index.column} maps {key} to {rid}, "
                f"but that row has {row[index.position]!r}"
            )
        return row