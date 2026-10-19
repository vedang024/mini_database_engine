"""Phase 11 demo: SQL text -> tokens -> AST, and what an error looks like.

    python scripts\\sql_demo.py                      # built-in examples
    python scripts\\sql_demo.py "SELECT * FROM t"    # your own statement
"""

from __future__ import annotations

import sys

from minidb.sql.errors import SqlError
from minidb.sql.lexer import tokenize
from minidb.sql.nodes import dump
from minidb.sql.parser import parse

EXAMPLES = [
    "SELECT * FROM users WHERE id = 10;",
    "SELECT * FROM t WHERE a = 1 OR b = 2 AND c = 3",
    "INSERT INTO users (id, name) VALUES (1, 'Ann'), (2, 'it''s')",
    "UPDATE users SET name = 'Bob' WHERE id = 2",
    "DELETE FROM users WHERE email IS NULL",
    "CREATE TABLE users (id INT NOT NULL, name VARCHAR(20), active BOOL)",
    "SELECT * FROM users WHERE id = = 10",
]


def show(sql: str) -> None:
    print("=" * 70)
    print(sql)
    print("-- tokens")
    print("  " + " ".join(f"{t.type.name}:{t.value}" for t in tokenize(sql) if t.value or t.type.name != "EOF"))
    print("-- AST")
    try:
        print(dump(parse(sql)))
    except SqlError as exc:
        print(exc.render(sql))


def main() -> None:
    for sql in sys.argv[1:] or EXAMPLES:
        show(sql)


if __name__ == "__main__":
    main()