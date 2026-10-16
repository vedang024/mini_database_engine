"""Schema: column types and table definitions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum

from minidb.table.errors import SchemaError

MAX_IDENTIFIER_LENGTH = 64
_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]*")


def validate_identifier(name: str) -> str:
    """Lower-case a table/column name and check it is safe to use (it becomes a file name)."""
    if not isinstance(name, str):
        raise SchemaError(f"identifier must be a string, got {name!r}")
    lowered = name.lower()
    if len(lowered) > MAX_IDENTIFIER_LENGTH or not _IDENTIFIER.fullmatch(lowered):
        raise SchemaError(
            f"invalid identifier {name!r}: use letters, digits and underscores, "
            f"start with a letter or underscore, at most {MAX_IDENTIFIER_LENGTH} characters"
        )
    return lowered


class ColumnType(Enum):
    INT = "INT"
    FLOAT = "FLOAT"
    BOOL = "BOOL"
    TEXT = "TEXT"

    @classmethod
    def parse(cls, value: "ColumnType | str") -> "ColumnType":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).upper())
        except ValueError:
            raise SchemaError(
                f"unknown column type {value!r}; choose from {[t.value for t in cls]}"
            ) from None


@dataclass(frozen=True)
class Column:
    name: str
    type: ColumnType  # a type name such as "int" is accepted and converted
    nullable: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", validate_identifier(self.name))
        object.__setattr__(self, "type", ColumnType.parse(self.type))
        if not isinstance(self.nullable, bool):
            raise SchemaError(f"column {self.name!r}: nullable must be True or False")


@dataclass(frozen=True)
class Schema:
    columns: tuple[Column, ...]

    def __post_init__(self) -> None:
        columns = tuple(self.columns)
        if not columns:
            raise SchemaError("a table needs at least one column")
        if not all(isinstance(c, Column) for c in columns):
            raise SchemaError("schema columns must be Column objects")
        names = [c.name for c in columns]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise SchemaError(f"duplicate column name(s): {duplicates}")
        object.__setattr__(self, "columns", columns)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)

    def index_of(self, name: str) -> int:
        key = name.lower()
        for i, column in enumerate(self.columns):
            if column.name == key:
                return i
        raise SchemaError(f"no such column: {name!r}")

    def to_json(self) -> str:
        return json.dumps(
            [{"name": c.name, "type": c.type.value, "nullable": c.nullable} for c in self.columns],
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, text: str) -> "Schema":
        try:
            raw = json.loads(text)
            return cls(tuple(Column(i["name"], i["type"], i["nullable"]) for i in raw))
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise SchemaError(f"invalid schema JSON: {exc}") from exc