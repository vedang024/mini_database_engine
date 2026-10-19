"""AST node types. Plain frozen dataclasses: data only, no behaviour.

Expressions : Literal, ColumnRef, BinaryOp, Not, IsNull
Statements  : CreateTable, Insert, Select, Update, Delete
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass

# ---- expressions -------------------------------------------------------------


@dataclass(frozen=True)
class Literal:
    value: int | float | str | bool | None


@dataclass(frozen=True)
class ColumnRef:
    name: str


@dataclass(frozen=True)
class BinaryOp:
    op: str  # = != < <= > >= AND OR
    left: "Expr"
    right: "Expr"


@dataclass(frozen=True)
class Not:
    operand: "Expr"


@dataclass(frozen=True)
class IsNull:
    operand: "Expr"
    negated: bool  # True for IS NOT NULL


Expr = Literal | ColumnRef | BinaryOp | Not | IsNull

# ---- statements ----------------------------------------------------------------


@dataclass(frozen=True)
class ColumnDef:
    name: str
    type_name: str            # as written, upper-cased: INT, VARCHAR, ... (checked later)
    nullable: bool = True
    max_length: int | None = None


@dataclass(frozen=True)
class CreateTable:
    table: str
    columns: tuple[ColumnDef, ...]


@dataclass(frozen=True)
class Insert:
    table: str
    columns: tuple[str, ...] | None            # None: all columns, in table order
    rows: tuple[tuple[Literal, ...], ...]


@dataclass(frozen=True)
class Select:
    table: str
    columns: tuple[str, ...] | None            # None means *
    where: Expr | None = None


@dataclass(frozen=True)
class Assignment:
    column: str
    value: Expr


@dataclass(frozen=True)
class Update:
    table: str
    assignments: tuple[Assignment, ...]
    where: Expr | None = None


@dataclass(frozen=True)
class Delete:
    table: str
    where: Expr | None = None


Statement = CreateTable | Insert | Select | Update | Delete

# ---- display ----------------------------------------------------------------------


def _composite(value: object) -> bool:
    if is_dataclass(value):
        return True
    return isinstance(value, tuple) and any(_composite(v) for v in value)


def _lines(value: object, label: str, depth: int) -> list[str]:
    pad = "  " * depth
    if is_dataclass(value):
        simple = [
            f"{f.name}={getattr(value, f.name)!r}"
            for f in fields(value)
            if not _composite(getattr(value, f.name))
        ]
        prefix = f"{label}: " if label else ""
        out = [f"{pad}{prefix}{type(value).__name__}({', '.join(simple)})"]
        for f in fields(value):
            child = getattr(value, f.name)
            if _composite(child):
                out += _lines(child, f.name, depth + 1)
        return out
    assert isinstance(value, tuple)
    out = [f"{pad}{label}:"]
    for i, item in enumerate(value):
        out += _lines(item, f"[{i}]", depth + 1)
    return out


def dump(node: object) -> str:
    """The tree as indented text, for learning and debugging."""
    return "\n".join(_lines(node, "", 0))