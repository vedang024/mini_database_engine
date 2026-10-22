"""Planner: semantic analysis + access-path selection.

The parser proved the SQL is well-formed. The planner proves it is meaningful
for THIS database (tables and columns exist, types fit) and decides HOW to read
the rows: through an index, or by scanning everything.

The strategy is rule-based, not cost-based: if an indexed INT column has a usable
condition, use the index. No statistics are consulted.
"""

from __future__ import annotations

from dataclasses import dataclass

from minidb.database import Database
from minidb.execution.errors import PlanError
from minidb.execution.expressions import (
    COMPARE_OPS, Evaluator, check_predicate, compile_expr, format_expr, infer_type, literal_type,
)
from minidb.execution.operators import Filter, IndexLookup, IndexRangeScan, Operator, Project, SeqScan
from minidb.sql import nodes as ast
from minidb.sql.parser import INT64_MAX, INT64_MIN
from minidb.table.errors import RowError, SchemaError, TableNotFoundError
from minidb.table.row_codec import validate_row
from minidb.table.schema import Column, Schema
from minidb.table.table import Table

_FLIP = {"<": ">", "<=": ">=", ">": "<", ">=": "<=", "=": "=", "!=": "!="}


@dataclass
class SelectPlan:
    root: Operator
    columns: tuple[str, ...]


@dataclass
class InsertPlan:
    table: Table
    rows: list[tuple[object, ...]]  # already validated and padded


@dataclass
class UpdatePlan:
    table: Table
    source: Operator
    assignments: list[tuple[int, Evaluator]]  # (column position, value expression)


@dataclass
class DeletePlan:
    table: Table
    source: Operator


@dataclass
class CreateTablePlan:
    name: str
    schema: Schema


Plan = SelectPlan | InsertPlan | UpdatePlan | DeletePlan | CreateTablePlan


class Planner:
    def __init__(self, db: Database) -> None:
        self._db = db

    def plan(self, stmt: ast.Statement) -> Plan:
        if isinstance(stmt, ast.Select):
            return self._select(stmt)
        if isinstance(stmt, ast.Insert):
            return self._insert(stmt)
        if isinstance(stmt, ast.Update):
            return self._update(stmt)
        if isinstance(stmt, ast.Delete):
            return self._delete(stmt)
        if isinstance(stmt, ast.CreateTable):
            return self._create(stmt)
        raise PlanError(f"unsupported statement {stmt!r}")  # pragma: no cover

    # ---- statements ------------------------------------------------------------
    def _table(self, name: str) -> Table:
        try:
            return self._db.get_table(name)
        except TableNotFoundError as exc:
            raise PlanError(str(exc)) from None

    def _create(self, stmt: ast.CreateTable) -> CreateTablePlan:
        try:
            schema = Schema(tuple(
                Column(c.name, c.type_name, c.nullable, c.max_length) for c in stmt.columns
            ))
        except SchemaError as exc:
            raise PlanError(str(exc)) from None
        return CreateTablePlan(stmt.table.lower(), schema)

    def _select(self, stmt: ast.Select) -> SelectPlan:
        table = self._table(stmt.table)
        schema = table.schema
        root = self._access_path(table, stmt.where)
        if stmt.columns is None:
            return SelectPlan(root, schema.names)
        names = []
        for name in stmt.columns:
            try:
                schema.index_of(name)
            except SchemaError:
                raise PlanError(f"no such column: {name!r}") from None
            names.append(name.lower())
        positions = tuple(schema.index_of(n) for n in names)
        return SelectPlan(Project(root, positions, tuple(names)), tuple(names))

    def _insert(self, stmt: ast.Insert) -> InsertPlan:
        table = self._table(stmt.table)
        schema = table.schema
        if stmt.columns is None:
            positions = list(range(len(schema.columns)))
        else:
            positions = []
            for name in stmt.columns:
                try:
                    position = schema.index_of(name)
                except SchemaError:
                    raise PlanError(f"no such column: {name!r}") from None
                if position in positions:
                    raise PlanError(f"column {name!r} listed twice")
                positions.append(position)
            for position, column in enumerate(schema.columns):
                if position not in positions and not column.nullable:
                    raise PlanError(f"column {column.name!r} is NOT NULL and has no value")
        rows = []
        for number, literals in enumerate(stmt.rows, start=1):
            if len(literals) != len(positions):
                raise PlanError(f"row {number}: expected {len(positions)} values, got {len(literals)}")
            values: list[object] = [None] * len(schema.columns)
            for position, literal in zip(positions, literals):
                values[position] = literal.value
            try:
                rows.append(validate_row(schema, values))
            except RowError as exc:
                raise PlanError(f"row {number}: {exc}") from None
        return InsertPlan(table, rows)

    def _update(self, stmt: ast.Update) -> UpdatePlan:
        table = self._table(stmt.table)
        schema = table.schema
        seen: set[int] = set()
        assignments: list[tuple[int, Evaluator]] = []
        for assignment in stmt.assignments:
            try:
                position = schema.index_of(assignment.column)
            except SchemaError:
                raise PlanError(f"no such column: {assignment.column!r}") from None
            if position in seen:
                raise PlanError(f"column {assignment.column!r} assigned twice")
            seen.add(position)
            column = schema.columns[position]
            value_type = infer_type(assignment.value, schema)
            target = column.type.value
            ok = value_type == "NULL" or value_type == target or (value_type == "INT" and target == "FLOAT")
            if not ok:
                raise PlanError(f"cannot assign {value_type} to column {column.name!r} ({target})")
            assignments.append((position, compile_expr(assignment.value, schema)))
        return UpdatePlan(table, self._access_path(table, stmt.where), assignments)

    def _delete(self, stmt: ast.Delete) -> DeletePlan:
        table = self._table(stmt.table)
        return DeletePlan(table, self._access_path(table, stmt.where))

    # ---- access-path selection -----------------------------------------------------------
    def _access_path(self, table: Table, where: ast.Expr | None) -> Operator:
        if where is None:
            return SeqScan(table)
        check_predicate(where, table.schema)
        conjuncts = _conjuncts(where)
        source, remaining = self._choose_source(table, conjuncts)
        if not remaining:
            return source
        residual = _and_all(remaining)
        return Filter(source, compile_expr(residual, table.schema), format_expr(residual))

    def _choose_source(self, table: Table, conjuncts: list[ast.Expr]) -> tuple[Operator, list[ast.Expr]]:
        indexed = {c for c in self._db.index_columns(table.name)}
        candidates = [(c, _index_condition(c, indexed)) for c in conjuncts]
        # 1. an equality on an indexed column beats everything
        for i, (_, cond) in enumerate(candidates):
            if cond is not None and cond[1] == "=":
                column, _, value = cond
                rest = [c for j, (c, _) in enumerate(candidates) if j != i]
                return IndexLookup(table, column, value), rest
        # 2. otherwise combine range conditions on the first indexed column that has any
        for column in sorted({cond[0] for _, cond in candidates if cond is not None}):
            low: int | None = None
            high: int | None = None
            used: set[int] = set()
            for i, (_, cond) in enumerate(candidates):
                if cond is None or cond[0] != column or cond[1] == "!=":
                    continue
                _, op, value = cond
                bound_low, bound_high = _to_inclusive(op, value)
                if bound_low is not None and not (INT64_MIN <= bound_low <= INT64_MAX):
                    continue  # leave it to the residual Filter
                if bound_high is not None and not (INT64_MIN <= bound_high <= INT64_MAX):
                    continue
                if bound_low is not None:
                    low = bound_low if low is None else max(low, bound_low)
                if bound_high is not None:
                    high = bound_high if high is None else min(high, bound_high)
                used.add(i)
            if used:
                rest = [c for j, (c, _) in enumerate(candidates) if j not in used]
                return IndexRangeScan(table, column, low, high), rest
        return SeqScan(table), conjuncts


def _conjuncts(expr: ast.Expr) -> list[ast.Expr]:
    """Flatten a AND b AND c into [a, b, c]. OR and NOT are left whole."""
    if isinstance(expr, ast.BinaryOp) and expr.op == "AND":
        return _conjuncts(expr.left) + _conjuncts(expr.right)
    return [expr]


def _and_all(parts: list[ast.Expr]) -> ast.Expr:
    result = parts[0]
    for part in parts[1:]:
        result = ast.BinaryOp("AND", result, part)
    return result


def _index_condition(expr: ast.Expr, indexed: set[str]) -> tuple[str, str, int] | None:
    """If expr is `indexed_col <op> int_literal` (either way round), return (col, op, int)."""
    if not isinstance(expr, ast.BinaryOp) or expr.op not in COMPARE_OPS:
        return None
    left, right, op = expr.left, expr.right, expr.op
    if isinstance(left, ast.Literal) and isinstance(right, ast.ColumnRef):
        left, right, op = right, left, _FLIP[op]
    if not (isinstance(left, ast.ColumnRef) and isinstance(right, ast.Literal)):
        return None
    if literal_type(right.value) != "INT" or left.name.lower() not in indexed:
        return None
    return left.name.lower(), op, int(right.value)  # type: ignore[arg-type]


def _to_inclusive(op: str, value: int) -> tuple[int | None, int | None]:
    """Convert a comparison into inclusive (low, high) bounds on integers."""
    if op == ">=":
        return value, None
    if op == ">":
        return value + 1, None
    if op == "<=":
        return None, value
    if op == "<":
        return None, value - 1
    raise AssertionError(op)  # '=' and '!=' never reach here
