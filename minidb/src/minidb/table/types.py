"""Data types: how each column type is validated, encoded and decoded.

validate(column, value) returns the NORMALISED value or raises ValueError with a
short message (the caller adds the column name). encode() assumes the value has
already been validated. decode() raises on bytes that cannot be valid.
"""

from __future__ import annotations

import struct
from abc import ABC, abstractmethod

from minidb.table.errors import RowError
from minidb.table.schema import Column, ColumnType, Schema

MAX_TEXT_BYTES = 0xFFFF
_INT = struct.Struct("<q")
_FLOAT = struct.Struct("<d")
_LEN = struct.Struct("<H")
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1


def _show(value: object) -> str:
    """repr() capped at 40 characters, so a 60 KB string cannot flood an error message."""
    text = repr(value)
    return text if len(text) <= 40 else text[:37] + "..."


def _wrong_type(expected: str, value: object) -> ValueError:
    return ValueError(f"expects {expected}, got {type(value).__name__} {_show(value)}")


class DataType(ABC):
    name: str
    fixed_size: int | None  # bytes for fixed-width types, None for variable length

    @abstractmethod
    def validate(self, column: Column, value: object) -> object: ...

    @abstractmethod
    def encode(self, value: object) -> bytes: ...

    @abstractmethod
    def decode(self, data: bytes, offset: int) -> tuple[object, int]: ...


class IntType(DataType):
    name = "INT"
    fixed_size = 8

    def validate(self, column: Column, value: object) -> object:
        # bool is a subclass of int in Python; refusing it avoids silently storing 1.
        if not isinstance(value, int) or isinstance(value, bool):
            raise _wrong_type("INT", value)
        if not (_INT64_MIN <= value <= _INT64_MAX):
            raise ValueError(f"{_show(value)} is outside the int64 range")
        return value

    def encode(self, value: object) -> bytes:
        return _INT.pack(value)

    def decode(self, data: bytes, offset: int) -> tuple[object, int]:
        (value,) = _INT.unpack_from(data, offset)
        return value, offset + _INT.size


class FloatType(DataType):
    name = "FLOAT"
    fixed_size = 8

    def validate(self, column: Column, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _wrong_type("FLOAT", value)
        try:
            return float(value)  # the one implicit conversion: int -> float
        except OverflowError:
            raise ValueError(f"{_show(value)} cannot be represented as a float64") from None

    def encode(self, value: object) -> bytes:
        return _FLOAT.pack(value)

    def decode(self, data: bytes, offset: int) -> tuple[object, int]:
        (value,) = _FLOAT.unpack_from(data, offset)
        return value, offset + _FLOAT.size


class BoolType(DataType):
    name = "BOOL"
    fixed_size = 1

    def validate(self, column: Column, value: object) -> object:
        if not isinstance(value, bool):
            raise _wrong_type("BOOL", value)
        return value

    def encode(self, value: object) -> bytes:
        return b"\x01" if value else b"\x00"

    def decode(self, data: bytes, offset: int) -> tuple[object, int]:
        if offset >= len(data):
            raise RowError("row truncated inside a BOOL")
        flag = data[offset]
        if flag > 1:
            raise RowError(f"invalid BOOL byte {flag}")
        return bool(flag), offset + 1


class TextType(DataType):
    name = "TEXT"
    fixed_size = None

    def validate(self, column: Column, value: object) -> object:
        if not isinstance(value, str):
            raise _wrong_type("TEXT", value)
        if column.max_length is not None and len(value) > column.max_length:
            raise ValueError(
                f"{len(value)} characters exceeds max_length {column.max_length}"
            )
        try:
            raw = value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("is not valid Unicode text (cannot be encoded as UTF-8)") from None
        if len(raw) > MAX_TEXT_BYTES:
            raise ValueError(f"{len(raw)} bytes exceeds the {MAX_TEXT_BYTES}-byte limit")
        return value

    def encode(self, value: object) -> bytes:
        raw = value.encode("utf-8")  # type: ignore[attr-defined]
        return _LEN.pack(len(raw)) + raw

    def decode(self, data: bytes, offset: int) -> tuple[object, int]:
        (length,) = _LEN.unpack_from(data, offset)
        offset += _LEN.size
        raw = data[offset : offset + length]
        if len(raw) != length:
            raise RowError("row truncated inside a TEXT value")
        return raw.decode("utf-8"), offset + length


TYPES: dict[ColumnType, DataType] = {
    ColumnType.INT: IntType(),
    ColumnType.FLOAT: FloatType(),
    ColumnType.BOOL: BoolType(),
    ColumnType.TEXT: TextType(),
}


def type_of(column: Column) -> DataType:
    return TYPES[column.type]


def row_size_bounds(schema: Schema) -> tuple[int, int]:
    """(smallest, largest) possible encoded row size in bytes for this schema.

    Smallest: only NOT NULL columns present, TEXT empty. A nullable column can be
    absent entirely. Largest: every column present, TEXT at its maximum, where one
    character can take up to 4 UTF-8 bytes.
    """
    bitmap = (len(schema.columns) + 7) // 8
    low = high = bitmap
    for column in schema.columns:
        fixed = type_of(column).fixed_size
        if fixed is not None:
            col_low = col_high = fixed
        else:
            col_low = _LEN.size
            max_bytes = MAX_TEXT_BYTES
            if column.max_length is not None:
                max_bytes = min(4 * column.max_length, MAX_TEXT_BYTES)
            col_high = _LEN.size + max_bytes
        if not column.nullable:
            low += col_low
        high += col_high
    return low, high


def is_fixed_width(schema: Schema) -> bool:
    """True when every row of this schema encodes to exactly the same size."""
    return all(
        type_of(c).fixed_size is not None and not c.nullable for c in schema.columns
    )