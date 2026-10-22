"""Executor: runs plans against the storage engine.

    SQL text -> parse -> AST -> Planner -> Plan -> Executor -> Result
"""

from __future__ import annotations

from dataclasses import dataclass, field

from minidb.database import Database
from minidb.execution.errors import PlanError, QueryError
from minidb.execution.operators import run
from minidb.execution.planner import (
    CreateTablePlan, DeletePlan, InsertPlan, Planner, SelectPlan, UpdatePlan,
)
from minidb.sql import nodes as ast
from minidb.sql.parser import parse, parse_script
from minidb.storage.record_id import RecordId
from minidb.table.errors import RowError, TableError
from minidb.table.row_codec import validate_row
from minidb.table.table import Table



@dataclass
class Result:
    columns: tuple[str, ...] = ()
    rows: list[tuple[object, ...]] = field(default_factory=list)
    affected: int = 0
    message: str = ""

    def format(self) -> str:
        if not self.columns:
            return self.message
        cells = [[_show(v) for v in row] for row in self.rows]
        widths = [len(c) for c in self.columns]
        for row in cells:
            widths = [max(w, len(v)) for w, v in zip(widths, row)]
        line = "-+-".join("-" * w for w in widths)
        out = [" | ".join(c.ljust(w) for c, w in zip(self.columns, widths)), line]
        out += [" | ".join(v.ljust(w) for v, w in zip(row, widths)) for row in cells]
        out.append(f"({len(self.rows)} row{'s' if len(self.rows) != 1 else ''})")
        return "\n".join(out)


def _show(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return str(value)


class Executor:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._planner = Planner(db)

    # ---- entry points ----------------------------------------------------------------
    def execute(self, sql: str) -> Result:
        return self.execute_statement(parse(sql))

    def execute_script(self, sql: str) -> list[Result]:
        return [self.execute_statement(s) for s in parse_script(sql)]

    def execute_statement(self, stmt: ast.Statement) -> Result:
        plan = self._planner.plan(stmt)
        if isinstance(plan, SelectPlan):
            return self._select(plan)
        if isinstance(plan, InsertPlan):
            return self._insert(plan)
        if isinstance(plan, UpdatePlan):
            return self._update(plan)
        if isinstance(plan, DeletePlan):
            return self._delete(plan)
        if isinstance(plan, CreateTablePlan):
            self._db.create_table(plan.name, plan.schema)
            return Result(message=f"CREATE TABLE {plan.name}")
        raise QueryError(f"cannot execute {plan!r}")  # pragma: no cover

    def explain(self, sql: str, analyze: bool = False) -> str:
        stmt = parse(sql)
        plan = self._planner.plan(stmt)
        if analyze and not isinstance(plan, SelectPlan):
            raise PlanError("EXPLAIN ANALYZE runs the query, so it is only allowed for SELECT")
        if isinstance(plan, SelectPlan):
            if analyze:
                run(plan.root)
            return plan.root.explain(analyze)
        if isinstance(plan, (UpdatePlan, DeletePlan)):
            kind = "Update" if isinstance(plan, UpdatePlan) else "Delete"
            return f"{kind}({plan.table.name})\n" + "\n".join(
                "  " + line for line in plan.source.explain().splitlines()
            )
        if isinstance(plan, InsertPlan):
            return f"Insert({plan.table.name}, rows={len(plan.rows)})"
        return f"CreateTable({plan.name})"  # type: ignore[union-attr]

    # ---- statements ------------------------------------------------------------------------
    def _select(self, plan: SelectPlan) -> Result:
        rows = [row for _, row in run(plan.root)]
        return Result(columns=plan.columns, rows=rows, affected=len(rows))

    def _insert(self, plan: InsertPlan) -> Result:
        inserted: list[RecordId] = []
        try:
            for row in plan.rows:
                inserted.append(plan.table.insert(row))
        except Exception:
            _undo_inserts(plan.table, inserted)
            raise
        return Result(affected=len(inserted), message=f"INSERT {len(inserted)}")

    def _delete(self, plan: DeletePlan) -> Result:
        # Drain FIRST: never modify the table while the scan over it is still running.
        victims = [rid for rid, _ in run(plan.source) if rid is not None]
        removed: list[tuple[object, ...]] = []
        try:
            for rid in victims:
                row = plan.table.get(rid)
                plan.table.delete(rid)
                removed.append(row)
        except Exception:
            for row in reversed(removed):  # undo: put back what we already removed
                plan.table.insert(row)
            raise
        return Result(affected=len(victims), message=f"DELETE {len(victims)}")

    def _update(self, plan: UpdatePlan) -> Result:
        table = plan.table
        matches = [(rid, row) for rid, row in run(plan.source) if rid is not None]  # drain first
        # compute every new row from the OLD row, and validate all of them BEFORE touching anything
        new_rows = []
        for rid, old in matches:
            values = list(old)
            for position, evaluate in plan.assignments:
                values[position] = evaluate(old)
            try:
                new_rows.append(validate_row(table.schema, values))
            except RowError as exc:
                raise PlanError(f"cannot update row {tuple(old)!r}: {exc}") from None
        applied: list[tuple[RecordId | None, tuple[object, ...], tuple[object, ...]]] = []
        try:
            for (rid, old), new in zip(matches, new_rows):
                table.delete(rid)
                try:
                    new_rid = table.insert(new)
                except Exception:
                    table.insert(old)  # put the old row back before reporting
                    raise
                applied.append((new_rid, old, new))
        except Exception:
            for new_rid, old, _ in reversed(applied):
                if new_rid is not None:
                    table.delete(new_rid)
                table.insert(old)
            raise
        return Result(affected=len(matches), message=f"UPDATE {len(matches)}")


def _undo_inserts(table: Table, rids: list[RecordId]) -> None:
    for rid in reversed(rids):
        table.delete(rid)
