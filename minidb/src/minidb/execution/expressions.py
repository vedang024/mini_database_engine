"""Expressions: type checking, compilation to Python closures, and printing.

Three steps, each used at a different time:

    infer_type(expr, schema)    PLAN time   semantic check; returns the expression's type
    compile_expr(expr, schema)  PLAN time   resolves column names ONCE into row positions
    fn(row)                     RUN time    evaluates the expression for one row

SQL uses three-valued logic: a comparison involving NULL is neither true nor
false but UNKNOWN. We represent UNKNOWN as Python's None. A WHERE clause keeps a
row only when its predicate is exactly True, so UNKNOWN rows are dropped.
"""

from __future__ import annotations

import operator
from collections.abc import Callable

from minidb.execution.errors import PlanError
from minidb.sql import nodes as ast
from minidb.table.errors import SchemaError
from minidb.table.schema import Schema

Row = tuple[object, ...]
Evaluator = Callable[[Row], object]

NUMERIC = frozenset({"INT", "FLOAT"})
COMPARE_OPS: dict[str, Callable[[object, object], bool]] = {
    "=": operator.eq, "!=": operator.ne, "<": operator.lt,
    "<=": operator.le, ">": operator.gt, ">=": operator.ge,
}


# ---- type checking (plan time) -------------------------------------------------
def literal_type(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):  # bool before int: True is an int in Python
        return "BOOL"
    if isinstance(value, int):
        return "INT"
    if isinstance(value, float):
        return "FLOAT"
    return "TEXT"


def comparable(a: str, b: str) -> bool:
    """NULL compares with anything, numbers with numbers, everything else only with its own type."""
    return "NULL" in (a, b) or a == b or (a in NUMERIC and b in NUMERIC)


def infer_type(expr: ast.Expr, schema: Schema) -> str:
    """Return INT, FLOAT, BOOL, TEXT or NULL, or raise PlanError."""
    if isinstance(expr, ast.Literal):
        return literal_type(expr.value)
    if isinstance(expr, ast.ColumnRef):
        try:
            return schema.columns[schema.index_of(expr.name)].type.value
        except SchemaError:
            raise PlanError(f"no such column: {expr.name!r}") from None
    if isinstance(expr, ast.IsNull):
        infer_type(expr.operand, schema)
        return "BOOL"
    if isinstance(expr, ast.Not):
        _require_boolean(infer_type(expr.operand, schema), "NOT", expr.operand)
        return "BOOL"
    if isinstance(expr, ast.BinaryOp):
        left = infer_type(expr.left, schema)
        right = infer_type(expr.right, schema)
        if expr.op in ("AND", "OR"):
            _require_boolean(left, expr.op, expr.left)
            _require_boolean(right, expr.op, expr.right)
        elif not comparable(left, right):
            raise PlanError(
                f"cannot compare {left} with {right} in {format_expr(expr)}"
            )
        return "BOOL"
    raise PlanError(f"unsupported expression {expr!r}")  # pragma: no cover


def _require_boolean(kind: str, what: str, operand: ast.Expr) -> None:
    if kind not in ("BOOL", "NULL"):
        raise PlanError(f"{what} needs a boolean, but {format_expr(operand)} is {kind}")


def check_predicate(expr: ast.Expr, schema: Schema) -> None:
    """A WHERE clause must be boolean."""
    kind = infer_type(expr, schema)
    if kind not in ("BOOL", "NULL"):
        raise PlanError(f"WHERE needs a boolean condition, but {format_expr(expr)} is {kind}")


# ---- compilation (plan time) ------------------------------------------------------
def compile_expr(expr: ast.Expr, schema: Schema) -> Evaluator:
    """Turn an (already type-checked) expression into a function of one row."""
    if isinstance(expr, ast.Literal):
        value = expr.value
        return lambda row: value
    if isinstance(expr, ast.ColumnRef):
        position = schema.index_of(expr.name)  # resolved once, not per row
        return lambda row: row[position]
    if isinstance(expr, ast.IsNull):
        inner = compile_expr(expr.operand, schema)
        negated = expr.negated
        return lambda row: (inner(row) is None) != negated  # never UNKNOWN
    if isinstance(expr, ast.Not):
        inner = compile_expr(expr.operand, schema)

        def evaluate_not(row: Row) -> object:
            value = inner(row)
            return None if value is None else not value

        return evaluate_not
    if isinstance(expr, ast.BinaryOp):
        left = compile_expr(expr.left, schema)
        right = compile_expr(expr.right, schema)
        if expr.op == "AND":
            return lambda row: and3(left(row), right(row))
        if expr.op == "OR":
            return lambda row: or3(left(row), right(row))
        compare = COMPARE_OPS[expr.op]

        def evaluate_comparison(row: Row) -> object:
            a, b = left(row), right(row)
            if a is None or b is None:
                return None  # UNKNOWN
            return compare(a, b)

        return evaluate_comparison
    raise PlanError(f"unsupported expression {expr!r}")  # pragma: no cover


def and3(a: object, b: object) -> object:
    """Three-valued AND: False beats everything, then UNKNOWN, then True."""
    if a is False or b is False:
        return False
    if a is None or b is None:
        return None
    return True


def or3(a: object, b: object) -> object:
    """Three-valued OR: True beats everything, then UNKNOWN, then False."""
    if a is True or b is True:
        return True
    if a is None or b is None:
        return None
    return False


# ---- printing (for EXPLAIN) ------------------------------------------------------------
_PRECEDENCE = {"OR": 1, "AND": 2, "NOT": 3}  # comparisons are 4, atoms 5


def format_literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return repr(value)


def format_expr(expr: ast.Expr, parent: int = 0) -> str:
    """The expression as SQL text, with parentheses only where precedence needs them."""
    if isinstance(expr, ast.Literal):
        return format_literal(expr.value)
    if isinstance(expr, ast.ColumnRef):
        return expr.name
    if isinstance(expr, ast.IsNull):
        text = f"{format_expr(expr.operand, 4)} IS {'NOT ' if expr.negated else ''}NULL"
        return f"({text})" if parent > 4 else text
    if isinstance(expr, ast.Not):
        text = f"NOT {format_expr(expr.operand, 3)}"
        return f"({text})" if parent > 3 else text
    precedence = _PRECEDENCE.get(expr.op, 4)
    text = f"{format_expr(expr.left, precedence)} {expr.op} {format_expr(expr.right, precedence + 1)}"
    return f"({text})" if parent > precedence else text
