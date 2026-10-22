"""Operators: the iterator (Volcano) model.

Every operator has the same three-method interface:

    open()   get ready (may allocate, may open a child)
    next()   return the next item, or None when there are no more
    close()  release resources (always called, even after an error)

An operator pulls from its child, so the plan is a tree and the ROOT drives the
whole thing: asking the root for one row makes each operator below it do just
enough work to produce that row. Nothing is computed ahead of time (except in
the index operators, see their notes).

An item is (rid, row): the row's address, and its values. Scans know the RID,
which UPDATE and DELETE need. Project drops it, because a projected row no
longer corresponds to one stored row.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator

from minidb.storage.record_id import RecordId
from minidb.table.table import Table

Row = tuple[object, ...]
Item = tuple[RecordId | None, Row]


class Operator(ABC):
    def __init__(self) -> None:
        self.rows_out = 0  # how many items next() has returned: shown by EXPLAIN ANALYZE

    # ---- the three-method protocol ---------------------------------------
    def open(self) -> None:
        self.rows_out = 0
        self._open()

    def next(self) -> Item | None:
        item = self._next()
        if item is not None:
            self.rows_out += 1
        return item

    def close(self) -> None:
        self._close()

    @abstractmethod
    def _open(self) -> None: ...

    @abstractmethod
    def _next(self) -> Item | None: ...

    @abstractmethod
    def _close(self) -> None: ...

    # ---- plan display -----------------------------------------------------------
    @abstractmethod
    def label(self) -> str: ...

    def children(self) -> list["Operator"]:
        return []

    def explain(self, analyze: bool = False, depth: int = 0) -> str:
        suffix = f"   [rows={self.rows_out}]" if analyze else ""
        lines = [f"{'  ' * depth}{self.label()}{suffix}"]
        for child in self.children():
            lines.append(child.explain(analyze, depth + 1))
        return "\n".join(lines)


def run(root: Operator) -> list[Item]:
    """Drive a plan to completion: open, pull until None, always close."""
    root.open()
    try:
        items: list[Item] = []
        while (item := root.next()) is not None:
            items.append(item)
        return items
    finally:
        root.close()


# ---- leaf operators: where rows come from ------------------------------------------------
class SeqScan(Operator):
    """Read every row of the table, in heap (RID) order. O(pages), whatever the query."""

    def __init__(self, table: Table) -> None:
        super().__init__()
        self._table = table
        self._rows: Iterator[Item] | None = None

    def _open(self) -> None:
        self._rows = self._table.scan()  # a generator: no work happens until next()

    def _next(self) -> Item | None:
        assert self._rows is not None, "next() before open()"
        return next(self._rows, None)

    def _close(self) -> None:
        if self._rows is not None and hasattr(self._rows, "close"):
            self._rows.close()  # type: ignore[union-attr]
        self._rows = None

    def label(self) -> str:
        return f"SeqScan({self._table.name})"


class _MaterialisedScan(Operator):
    """Index operators fetch their matches in open() and hand them out one by one.

    Table.index_equal / index_range return lists, so this is the one place where
    work happens before the first next(). A trade-off noted in the lesson.
    """

    def __init__(self) -> None:
        super().__init__()
        self._matches: Iterator[Item] | None = None

    @abstractmethod
    def _fetch(self) -> list[Item]: ...

    def _open(self) -> None:
        self._matches = iter(self._fetch())

    def _next(self) -> Item | None:
        assert self._matches is not None, "next() before open()"
        return next(self._matches, None)

    def _close(self) -> None:
        self._matches = None


class IndexLookup(_MaterialisedScan):
    """WHERE column = key through the B+ tree: tree height + one heap page."""

    def __init__(self, table: Table, column: str, key: int) -> None:
        super().__init__()
        self._table, self._column, self._key = table, column, key

    def _fetch(self) -> list[Item]:
        return self._table.index_equal(self._column, self._key)

    def label(self) -> str:
        return f"IndexLookup({self._table.name}.{self._column} = {self._key})"


class IndexRangeScan(_MaterialisedScan):
    """low <= column <= high through the leaf chain. Rows come back in key order."""

    def __init__(self, table: Table, column: str, low: int | None, high: int | None) -> None:
        super().__init__()
        self._table, self._column, self._low, self._high = table, column, low, high

    def _fetch(self) -> list[Item]:
        return self._table.index_range(self._column, self._low, self._high)

    def label(self) -> str:
        low = "-inf" if self._low is None else self._low
        high = "+inf" if self._high is None else self._high
        return f"IndexRangeScan({self._table.name}.{self._column} in [{low}, {high}])"


# ---- inner operators: they pull from a child ------------------------------------------------
class Filter(Operator):
    """Keep the items whose predicate is exactly True (UNKNOWN is dropped too)."""

    def __init__(self, child: Operator, predicate: Callable[[Row], object], text: str) -> None:
        super().__init__()
        self._child, self._predicate, self._text = child, predicate, text

    def _open(self) -> None:
        self._child.open()

    def _next(self) -> Item | None:
        while (item := self._child.next()) is not None:
            if self._predicate(item[1]) is True:
                return item
        return None

    def _close(self) -> None:
        self._child.close()

    def label(self) -> str:
        return f"Filter({self._text})"

    def children(self) -> list[Operator]:
        return [self._child]


class Project(Operator):
    """Keep only some columns, in the order given. The result has no RID."""

    def __init__(self, child: Operator, positions: tuple[int, ...], names: tuple[str, ...]) -> None:
        super().__init__()
        self._child, self._positions, self._names = child, positions, names

    def _open(self) -> None:
        self._child.open()

    def _next(self) -> Item | None:
        item = self._child.next()
        if item is None:
            return None
        row = item[1]
        return None, tuple(row[p] for p in self._positions)

    def _close(self) -> None:
        self._child.close()

    def label(self) -> str:
        return f"Project({', '.join(self._names)})"

    def children(self) -> list[Operator]:
        return [self._child]
