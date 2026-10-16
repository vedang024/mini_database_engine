"""Table: a schema plus a heap file. It speaks in rows; the heap file speaks in bytes."""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from minidb.storage.errors import InvalidPageError, InvalidSlotError
from minidb.storage.heap_file import HeapFile
from minidb.storage.record_id import RecordId
from minidb.table.errors import RowNotFoundError
from minidb.table.row_codec import decode_row, encode_row
from minidb.table.schema import Schema

Row = tuple[object, ...]


class Table:
    def __init__(self, name: str, schema: Schema, heap: HeapFile) -> None:
        self._name = name
        self._schema = schema
        self._heap = heap

    @property
    def name(self) -> str:
        return self._name

    @property
    def schema(self) -> Schema:
        return self._schema

    @property
    def heap(self) -> HeapFile:
        return self._heap

    def insert(self, values: Sequence[object]) -> RecordId:
        """Validate and store a row. A row that fails validation writes nothing."""
        return self._heap.insert(encode_row(self._schema, values))

    def get(self, rid: RecordId) -> Row:
        try:
            data = self._heap.get(rid)
        except (InvalidPageError, InvalidSlotError) as exc:
            raise RowNotFoundError(f"table {self._name!r}: no row at {rid}") from exc
        return decode_row(self._schema, data)

    def delete(self, rid: RecordId) -> None:
        try:
            self._heap.delete(rid)
        except (InvalidPageError, InvalidSlotError) as exc:
            raise RowNotFoundError(f"table {self._name!r}: no row at {rid}") from exc

    def scan(self) -> Iterator[tuple[RecordId, Row]]:
        for rid, data in self._heap.scan():
            yield rid, decode_row(self._schema, data)

    def flush(self) -> None:
        self._heap.flush()

    def close(self) -> None:
        self._heap.close()