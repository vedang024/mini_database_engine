"""Row codec: turns a row (a tuple of Python values) into record bytes and back.

Layout (little-endian):
    null bitmap : ceil(ncols / 8) bytes; bit i set means column i is NULL
    then, for each non-NULL column in order:
        INT   int64          8 bytes
        FLOAT float64        8 bytes
        BOOL  one byte       0 or 1
        TEXT  uint16 byte length, then UTF-8 bytes
NULL columns take no bytes beyond their bitmap bit. A row is never empty
(the bitmap is at least one byte), which SlottedPage requires.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence

from minidb.table.errors import RowError
from minidb.table.schema import Column, ColumnType, Schema

_INT = struct.Struct("<q")
_FLOAT = struct.Struct("<d")
_LEN = struct.Struct("<H")
MAX_TEXT_BYTES = 0xFFFF


def encode_row(schema: Schema, values: Sequence[object]) -> bytes:
    columns = schema.columns
    if len(values) != len(columns):
        raise RowError(f"expected {len(columns)} values, got {len(values)}")
    bitmap = bytearray((len(columns) + 7) // 8)
    parts: list[bytes] = []
    for i, (column, value) in enumerate(zip(columns, values)):
        if value is None:
            if not column.nullable:
                raise RowError(f"column {column.name!r} is NOT NULL")
            bitmap[i // 8] |= 1 << (i % 8)
        else:
            parts.append(_encode_value(column, value))
    return bytes(bitmap) + b"".join(parts)


def decode_row(schema: Schema, data: bytes) -> tuple[object, ...]:
    columns = schema.columns
    bitmap_len = (len(columns) + 7) // 8
    if len(data) < bitmap_len:
        raise RowError("row is shorter than its null bitmap")
    offset = bitmap_len
    values: list[object] = []
    try:
        for i, column in enumerate(columns):
            if data[i // 8] & (1 << (i % 8)):
                if not column.nullable:
                    raise RowError(f"column {column.name!r} is NOT NULL but stored as NULL")
                values.append(None)
                continue
            value, offset = _decode_value(column, data, offset)
            values.append(value)
    except (struct.error, UnicodeDecodeError) as exc:
        raise RowError(f"cannot decode row: {exc}") from exc
    if offset != len(data):
        raise RowError(f"{len(data) - offset} unexpected trailing bytes")
    return tuple(values)


def _encode_value(column: Column, value: object) -> bytes:
    kind = column.type
    try:
        # bool is a subclass of int in Python; we refuse it for INT/FLOAT on purpose.
        if kind is ColumnType.INT and isinstance(value, int) and not isinstance(value, bool):
            return _INT.pack(value)
        if kind is ColumnType.FLOAT and isinstance(value, (int, float)) and not isinstance(value, bool):
            return _FLOAT.pack(float(value))
        if kind is ColumnType.BOOL and isinstance(value, bool):
            return b"\x01" if value else b"\x00"
        if kind is ColumnType.TEXT and isinstance(value, str):
            raw = value.encode("utf-8")
            if len(raw) > MAX_TEXT_BYTES:
                raise RowError(
                    f"column {column.name!r}: text of {len(raw)} bytes exceeds {MAX_TEXT_BYTES}"
                )
            return _LEN.pack(len(raw)) + raw
    except (struct.error, OverflowError, UnicodeEncodeError) as exc:
        raise RowError(f"column {column.name!r}: cannot encode {value!r}: {exc}") from exc
    raise RowError(
        f"column {column.name!r} expects {kind.value}, got {type(value).__name__} {value!r}"
    )


def _decode_value(column: Column, data: bytes, offset: int) -> tuple[object, int]:
    kind = column.type
    if kind is ColumnType.INT:
        (value,) = _INT.unpack_from(data, offset)
        return value, offset + _INT.size
    if kind is ColumnType.FLOAT:
        (value,) = _FLOAT.unpack_from(data, offset)
        return value, offset + _FLOAT.size
    if kind is ColumnType.BOOL:
        if offset >= len(data):
            raise RowError("row truncated inside a BOOL")
        flag = data[offset]
        if flag > 1:
            raise RowError(f"invalid BOOL byte {flag}")
        return bool(flag), offset + 1
    (length,) = _LEN.unpack_from(data, offset)  # TEXT
    offset += _LEN.size
    raw = data[offset : offset + length]
    if len(raw) != length:
        raise RowError("row truncated inside a TEXT value")
    return raw.decode("utf-8"), offset + length