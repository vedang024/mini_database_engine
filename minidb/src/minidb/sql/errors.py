"""Errors raised while turning SQL text into an AST."""

from __future__ import annotations


class SqlError(Exception):
    """Base class. Carries the 1-based line and column of the problem when known."""

    def __init__(self, message: str, line: int | None = None, col: int | None = None) -> None:
        text = message if line is None else f"{message} (line {line}, column {col})"
        super().__init__(text)
        self.message = message
        self.line = line
        self.col = col

    def render(self, sql: str) -> str:
        """The error plus the offending source line with a caret under the column."""
        if self.line is None or self.col is None:
            return str(self)
        lines = sql.split("\n")
        source = lines[self.line - 1].rstrip("\r") if self.line - 1 < len(lines) else ""
        return f"{self}\n  {source}\n  {' ' * (self.col - 1)}^"


class LexError(SqlError):
    """The text contains something that is not a valid token."""


class ParseError(SqlError):
    """The tokens do not follow the grammar."""