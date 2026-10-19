"""Lexer: turns SQL text into a list of tokens.

    "SELECT * FROM users WHERE id = 10;"
      -> KEYWORD SELECT, SYMBOL *, KEYWORD FROM, IDENT users, KEYWORD WHERE,
         IDENT id, SYMBOL =, INTEGER 10, SYMBOL ;, EOF

Rules: keywords are case-insensitive (value is upper-cased); identifiers are
case-insensitive (value is lower-cased); strings are 'single quoted' with ''
for a quote; "--" starts a comment; every token records its line and column.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from minidb.sql.errors import LexError


class TokenType(Enum):
    KEYWORD = "keyword"
    IDENT = "identifier"
    INTEGER = "integer"
    FLOAT = "float"
    STRING = "string"
    SYMBOL = "symbol"
    EOF = "end of input"


KEYWORDS = frozenset(
    {
        "CREATE", "TABLE", "INSERT", "INTO", "VALUES", "SELECT", "FROM", "WHERE",
        "DELETE", "UPDATE", "SET", "AND", "OR", "NOT", "NULL", "IS", "TRUE", "FALSE",
    }
)
TWO_CHAR_SYMBOLS = ("<=", ">=", "!=", "<>")
ONE_CHAR_SYMBOLS = "(),;*=<>-"
DIGITS = "0123456789"


@dataclass(frozen=True)
class Token:
    type: TokenType
    value: str
    line: int
    col: int

    def describe(self) -> str:
        """How this token is named in an error message."""
        if self.type is TokenType.EOF:
            return "end of input"
        shown = self.value if len(self.value) <= 30 else self.value[:27] + "..."
        if self.type is TokenType.KEYWORD:
            return f"keyword {shown}"
        if self.type is TokenType.IDENT:
            return f"identifier {shown!r}"
        if self.type is TokenType.STRING:
            return f"string {shown!r}"
        return repr(shown)


def tokenize(sql: str) -> list[Token]:
    """Split SQL text into tokens. The last token is always EOF."""
    tokens: list[Token] = []
    n = len(sql)
    i = 0
    line = 1
    line_start = 0  # index where the current line begins

    while i < n:
        ch = sql[i]
        col = i - line_start + 1

        if ch == "\n":
            i += 1
            line += 1
            line_start = i
            continue
        if ch in " \t\r":
            i += 1
            continue
        if sql.startswith("--", i):  # comment to end of line
            while i < n and sql[i] != "\n":
                i += 1
            continue

        if ch.isascii() and (ch.isalpha() or ch == "_"):
            j = i + 1
            while j < n and sql[j].isascii() and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            word = sql[i:j]
            if word.upper() in KEYWORDS:
                tokens.append(Token(TokenType.KEYWORD, word.upper(), line, col))
            else:
                tokens.append(Token(TokenType.IDENT, word.lower(), line, col))
            i = j
            continue

        if ch in DIGITS:
            j = i
            while j < n and sql[j] in DIGITS:
                j += 1
            is_float = False
            if j + 1 < n and sql[j] == "." and sql[j + 1] in DIGITS:
                is_float = True
                j += 1
                while j < n and sql[j] in DIGITS:
                    j += 1
            if j < n and sql[j] in "eE":  # exponent only counts if digits follow
                k = j + 1
                if k < n and sql[k] in "+-":
                    k += 1
                if k < n and sql[k] in DIGITS:
                    is_float = True
                    while k < n and sql[k] in DIGITS:
                        k += 1
                    j = k
            if j < n and (sql[j].isalpha() or sql[j] == "_"):
                raise LexError("invalid number", line, col)
            kind = TokenType.FLOAT if is_float else TokenType.INTEGER
            tokens.append(Token(kind, sql[i:j], line, col))
            i = j
            continue

        if ch == "'":
            start_line, start_col = line, col
            parts: list[str] = []
            j = i + 1
            while True:
                if j >= n:
                    raise LexError("unterminated string", start_line, start_col)
                c = sql[j]
                if c == "'":
                    if j + 1 < n and sql[j + 1] == "'":  # '' is an escaped quote
                        parts.append("'")
                        j += 2
                        continue
                    break
                if c == "\n":
                    line += 1
                    line_start = j + 1
                parts.append(c)
                j += 1
            tokens.append(Token(TokenType.STRING, "".join(parts), start_line, start_col))
            i = j + 1
            continue

        two = sql[i : i + 2]
        if two in TWO_CHAR_SYMBOLS:
            tokens.append(Token(TokenType.SYMBOL, two, line, col))
            i += 2
            continue
        if ch in ONE_CHAR_SYMBOLS:
            tokens.append(Token(TokenType.SYMBOL, ch, line, col))
            i += 1
            continue

        raise LexError(f"unexpected character {ch!r}", line, col)

    tokens.append(Token(TokenType.EOF, "", line, n - line_start + 1))
    return tokens