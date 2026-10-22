"""Phase 12 demo: SQL text in, rows out, with plans shown.

Run:  python scripts/executor_demo.py
"""
import shutil
import tempfile
from pathlib import Path

from minidb.config import DatabaseConfig
from minidb.database import Database
from minidb.execution.executor import Executor


def show(ex: Executor, sql: str) -> None:
    print(f"\nminidb> {sql}")
    print(ex.execute(sql).format())


def main() -> None:
    folder = Path(tempfile.mkdtemp(prefix="minidb_phase12_"))
    try:
        with Database(DatabaseConfig(data_dir=folder)) as db:
            ex = Executor(db)
            show(ex, "CREATE TABLE users (id INT NOT NULL, name TEXT, age INT)")
            show(ex, "INSERT INTO users VALUES (1,'ann',30),(2,'bob',NULL),(3,'cy',25),(4,'di',41)")
            show(ex, "SELECT name, age FROM users WHERE age > 26")
            show(ex, "SELECT name FROM users WHERE NOT age > 26")
            show(ex, "SELECT name FROM users WHERE age IS NULL")

            print("\n--- plan without an index ---")
            print(ex.explain("SELECT name FROM users WHERE id = 3", analyze=True))
            db.create_index("users", "id")
            print("\n--- plan with an index on id ---")
            print(ex.explain("SELECT name FROM users WHERE id = 3", analyze=True))
            print("\n--- index + leftover predicate ---")
            print(ex.explain("SELECT * FROM users WHERE id >= 2 AND age < 40", analyze=True))

            show(ex, "UPDATE users SET age = 99 WHERE id = 2")
            show(ex, "DELETE FROM users WHERE age > 40")
            show(ex, "SELECT * FROM users")
            print("\n--- semantic error ---")
            try:
                ex.execute("SELECT * FROM users WHERE name = 5")
            except Exception as exc:
                print(f"{type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
