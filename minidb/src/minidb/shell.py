"""Interactive SQL shell and script runner.

    python -m minidb.shell --config config\\minidb.toml              (interactive)
    python -m minidb.shell --config config\\minidb.toml -e "SELECT ..."
    Get-Content script.sql | python -m minidb.shell --config config\\minidb.toml
"""

from __future__ import annotations

import argparse
import sys
from typing import TextIO

from minidb.config import ConfigError, load_config
from minidb.database import Database
from minidb.execution.errors import QueryError
from minidb.execution.executor import Executor
from minidb.logging_setup import configure_logging
from minidb.sql.errors import SqlError
from minidb.sql.parser import parse_script
from minidb.storage.errors import StorageError
from minidb.table.errors import TableError

HELP = """\
SQL: CREATE TABLE, INSERT, SELECT, UPDATE, DELETE   (end each statement with ;)
.tables                list tables
.schema [table]        show columns
.index TABLE COLUMN    create a unique index on an INT column
.explain SQL           show the plan without running it
.help  .quit"""


def run_script(executor: Executor, sql: str, out: TextIO) -> bool:
    """Run every statement; return False if one failed. Syntax is checked for ALL first."""
    try:
        statements = parse_script(sql)
    except SqlError as exc:
        print(exc.render(sql), file=out)
        return False
    for statement in statements:
        try:
            print(executor.execute_statement(statement).format(), file=out)
        except (QueryError, TableError, StorageError) as exc:
            print(f"error: {exc}", file=out)
            return False
    return True


def run_meta(db: Database, executor: Executor, line: str, out: TextIO) -> bool:
    """Handle a dot-command. Returns False for .quit."""
    parts = line.split(None, 1)
    command, rest = parts[0], (parts[1] if len(parts) > 1 else "")
    try:
        if command in (".quit", ".exit"):
            return False
        if command == ".help":
            print(HELP, file=out)
        elif command == ".tables":
            print("\n".join(db.table_names()) or "(no tables)", file=out)
        elif command == ".schema":
            names = [rest.strip()] if rest.strip() else db.table_names()
            for name in names:
                table = db.get_table(name)
                indexed = set(db.index_columns(name))
                cols = ", ".join(
                    f"{c.name} {c.type.value}{'' if c.nullable else ' NOT NULL'}{' [indexed]' if c.name in indexed else ''}"
                    for c in table.schema.columns
                )
                print(f"{table.name} ({cols})", file=out)
        elif command == ".index":
            args = rest.split()
            if len(args) != 2:
                print("usage: .index TABLE COLUMN", file=out)
            else:
                db.create_index(args[0], args[1])
                print(f"index on {args[0]}.{args[1]} created", file=out)
        elif command == ".explain":
            print(executor.explain(rest.strip().rstrip(";")), file=out)
        else:
            print(f"unknown command {command!r}; try .help", file=out)
    except SqlError as exc:
        print(exc.render(rest), file=out)
    except (QueryError, TableError, StorageError) as exc:
        print(f"error: {exc}", file=out)
    return True


def repl(db: Database, executor: Executor, inp: TextIO, out: TextIO) -> None:
    buffer: list[str] = []
    interactive = inp.isatty()
    while True:
        if interactive:
            out.write("minidb> " if not buffer else "   ...> ")
            out.flush()
        line = inp.readline()
        if not line:
            break
        stripped = line.strip()
        if not buffer and stripped.startswith("."):
            if not run_meta(db, executor, stripped, out):
                break
            continue
        if not stripped and not buffer:
            continue
        buffer.append(line)
        if stripped.endswith(";"):
            run_script(executor, "".join(buffer), out)
            buffer.clear()
    if buffer:
        run_script(executor, "".join(buffer), out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="minidb.shell")
    parser.add_argument("--config", default=None)
    parser.add_argument("-e", "--execute", default=None, help="run this SQL and exit")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
    except (ConfigError, OSError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    configure_logging(config.logging)
    with Database(config.database) as db:
        executor = Executor(db)
        if args.execute is not None:
            return 0 if run_script(executor, args.execute, sys.stdout) else 1
        repl(db, executor, sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
