"""Row codec: turns a row (a tuple of Python values) into record bytes and back.

Layout (little-endian):
    null bitmap : ceil(ncols / 8) bytes; bit i set means column i is NULL
    then, for each non-NULL column in order, that column type's encoding
    (see types.py). NULL columns take no bytes beyond their bitmap bit.
A row is never empty (the bitmap is at least one byte), which SlottedPage requires.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence

from minidb.table.errors import RowError, RowValidationError
from minidb.table.schema import Schema
from minidb.table.types import type_of


def validate_row(schema: Schema, values: Sequence[object]) -> tuple[object, ...]:
    """Check a row against the schema and return it normalised (e.g. 3 -> 3.0 for FLOAT).

    Raises RowError for a wrong value count, otherwise RowValidationError listing
    EVERY violation at once.
    """
    columns = schema.columns
    if len(values) != len(columns):
        raise RowError(f"expected {len(columns)} values, got {len(values)}")

    problems: list[str] = []
    clean: list[object] = []
    for column, value in zip(columns, values):
        if value is None:
            if not column.nullable:
                problems.append(f"column {column.name!r} is NOT NULL")
            clean.append(None)
            continue
        try:
            clean.append(type_of(column).validate(column, value))
        except ValueError as exc:
            problems.append(f"column {column.name!r}: {exc}")
            clean.append(None)
    if problems:
        raise RowValidationError(problems)
    return tuple(clean)


def encode_row(schema: Schema, values: Sequence[object]) -> bytes:
    clean = validate_row(schema, values)  # nothing is encoded unless the whole row is valid
    columns = schema.columns
    bitmap = bytearray((len(columns) + 7) // 8)
    parts: list[bytes] = []
    for i, (column, value) in enumerate(zip(columns, clean)):
        if value is None:
            bitmap[i // 8] |= 1 << (i % 8)
        else:
            parts.append(type_of(column).encode(value))
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
            value, offset = type_of(column).decode(data, offset)
            values.append(value)
    except (struct.error, UnicodeDecodeError) as exc:
        raise RowError(f"cannot decode row: {exc}") from exc
    if offset != len(data):
        raise RowError(f"{len(data) - offset} unexpected trailing bytes")
    return tuple(values)