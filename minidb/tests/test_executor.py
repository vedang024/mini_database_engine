import random

import pytest

from minidb.config import DatabaseConfig
from minidb.database import Database
from minidb.execution.errors import PlanError
from minidb.execution.executor import Executor
from minidb.table.errors import UniqueViolationError


@pytest.fixture
def db(tmp_path):
    database = Database(DatabaseConfig(data_dir=tmp_path))
    yield database
    database.close()


@pytest.fixture
def ex(db):
    executor = Executor(db)
    executor.execute("CREATE TABLE users (id INT NOT NULL, name TEXT, age INT, active BOOL)")
    return executor


def rows(ex, sql):
    return ex.execute(sql).rows


def test_insert_and_select_star(ex):
    assert ex.execute("INSERT INTO users VALUES (1, 'ann', 30, TRUE), (2, 'bob', 25, FALSE)").message == "INSERT 2"
    assert rows(ex, "SELECT * FROM users") == [(1, "ann", 30, True), (2, "bob", 25, False)]


def test_insert_column_list_pads_nulls(ex):
    ex.execute("INSERT INTO users (id, name) VALUES (1, 'ann')")
    assert rows(ex, "SELECT * FROM users") == [(1, "ann", None, None)]


def test_projection_reorders(ex):
    ex.execute("INSERT INTO users VALUES (1, 'ann', 30, TRUE)")
    result = ex.execute("SELECT age, name FROM users")
    assert result.columns == ("age", "name")
    assert result.rows == [(30, "ann")]


def test_where_null_semantics(ex):
    ex.execute("INSERT INTO users VALUES (1, 'a', 10, TRUE), (2, 'b', NULL, TRUE), (3, 'c', 30, FALSE)")
    assert rows(ex, "SELECT id FROM users WHERE age > 5") == [(1,), (3,)]
    assert rows(ex, "SELECT id FROM users WHERE NOT age > 5") == []  # NOT UNKNOWN is UNKNOWN
    assert rows(ex, "SELECT id FROM users WHERE age IS NULL") == [(2,)]
    assert rows(ex, "SELECT id FROM users WHERE age = NULL") == []
    assert rows(ex, "SELECT id FROM users WHERE age > 5 OR active = TRUE") == [(1,), (2,), (3,)]


def test_update_and_delete(ex):
    ex.execute("INSERT INTO users VALUES (1, 'a', 10, TRUE), (2, 'b', 20, TRUE), (3, 'c', 30, FALSE)")
    assert ex.execute("UPDATE users SET age = 99, name = 'z' WHERE id = 2").message == "UPDATE 1"
    assert rows(ex, "SELECT * FROM users WHERE id = 2") == [(2, "z", 99, True)]
    assert ex.execute("DELETE FROM users WHERE age < 50 AND active = TRUE").message == "DELETE 1"
    assert sorted(rows(ex, "SELECT id FROM users")) == [(2,), (3,)]
    assert ex.execute("DELETE FROM users").affected == 2
    assert rows(ex, "SELECT * FROM users") == []


def test_update_sees_old_row(ex):
    ex.execute("INSERT INTO users VALUES (1, 'a', 10, TRUE)")
    ex.execute("UPDATE users SET age = id, id = age")  # swap: both read the OLD row
    assert rows(ex, "SELECT id, age FROM users") == [(10, 1)]


def test_halloween(ex):
    for i in range(300):
        ex.execute(f"INSERT INTO users VALUES ({i}, 'n', {i}, TRUE)")
    ex.execute_script("")  # empty script is fine
    assert ex.execute("UPDATE users SET age = 1 WHERE age >= 0").affected == 300


def test_halloween_on_indexed_column(db, ex):
    for i in range(200):
        ex.execute(f"INSERT INTO users VALUES ({i}, 'n', 1, TRUE)")
    db.create_index("users", "id")
    assert ex.execute("UPDATE users SET id = id WHERE id >= 0").affected == 200
    assert len(rows(ex, "SELECT * FROM users")) == 200


def test_insert_is_all_or_nothing_on_duplicate(db, ex):
    db.create_index("users", "id")
    ex.execute("INSERT INTO users VALUES (1, 'a', 1, TRUE)")
    with pytest.raises(UniqueViolationError):
        ex.execute("INSERT INTO users VALUES (2, 'b', 1, TRUE), (3, 'c', 1, TRUE), (1, 'dup', 1, TRUE)")
    assert sorted(rows(ex, "SELECT id FROM users")) == [(1,)]
    assert rows(ex, "SELECT * FROM users WHERE id = 2") == []  # index rolled back too


def test_update_is_all_or_nothing_on_duplicate(db, ex):
    db.create_index("users", "id")
    ex.execute("INSERT INTO users VALUES (1, 'a', 1, TRUE), (2, 'b', 1, TRUE), (3, 'c', 1, TRUE)")
    with pytest.raises(UniqueViolationError):
        ex.execute("UPDATE users SET id = 9")  # every row would become id 9
    assert sorted(rows(ex, "SELECT id FROM users")) == [(1,), (2,), (3,)]
    for i in (1, 2, 3):
        assert rows(ex, f"SELECT name FROM users WHERE id = {i}") != []
    assert rows(ex, "SELECT * FROM users WHERE id = 9") == []


def test_update_validation_failure_changes_nothing(ex):
    ex.execute("INSERT INTO users VALUES (1, 'a', 1, TRUE), (2, NULL, 1, TRUE)")
    with pytest.raises(PlanError):
        ex.execute("UPDATE users SET id = NULL")
    assert sorted(rows(ex, "SELECT id FROM users")) == [(1,), (2,)]


def test_int_to_float_assignment(db):
    ex = Executor(db)
    ex.execute("CREATE TABLE m (id INT, x FLOAT)")
    ex.execute("INSERT INTO m VALUES (1, 2)")
    ex.execute("UPDATE m SET x = 3")
    assert rows(ex, "SELECT x FROM m") == [(3.0,)]


def test_persistence_across_reopen(tmp_path):
    cfg = DatabaseConfig(data_dir=tmp_path)
    with Database(cfg) as db:
        ex = Executor(db)
        ex.execute("CREATE TABLE t (id INT NOT NULL, v TEXT)")
        db.create_index("t", "id")
        ex.execute("INSERT INTO t VALUES (1, 'a'), (2, 'b'), (3, 'c')")
        ex.execute("UPDATE t SET v = 'B' WHERE id = 2")
        ex.execute("DELETE FROM t WHERE id = 3")
    with Database(cfg) as db:
        ex = Executor(db)
        assert sorted(rows(ex, "SELECT * FROM t")) == [(1, "a"), (2, "B")]
        assert "IndexLookup" in ex.explain("SELECT * FROM t WHERE id = 1")


def test_explain_analyze_counts(db, ex):
    for i in range(50):
        ex.execute(f"INSERT INTO users VALUES ({i}, 'n', {i}, TRUE)")
    text = ex.explain("SELECT * FROM users WHERE age >= 40", analyze=True)
    assert text.splitlines() == ["Filter(age >= 40)   [rows=10]", "  SeqScan(users)   [rows=50]"]
    with pytest.raises(PlanError):
        ex.explain("DELETE FROM users", analyze=True)
    assert len(rows(ex, "SELECT * FROM users")) == 50  # explain never changed data


def test_script_stops_at_first_error(ex):
    with pytest.raises(PlanError):
        ex.execute_script("INSERT INTO users VALUES (1,'a',1,TRUE); SELECT * FROM nope; INSERT INTO users VALUES (2,'b',1,TRUE);")
    assert rows(ex, "SELECT id FROM users") == [(1,)]


def test_result_format(ex):
    ex.execute("INSERT INTO users VALUES (1, 'ann', NULL, TRUE)")
    text = ex.execute("SELECT * FROM users").format()
    assert text.splitlines() == [
        "id | name | age  | active",
        "---+------+------+-------",
        "1  | ann  | NULL | TRUE  ",
        "(1 row)",
    ]


def _build(tmp_path, name, indexed, seed):
    db = Database(DatabaseConfig(data_dir=tmp_path / name))
    ex = Executor(db)
    ex.execute("CREATE TABLE t (id INT NOT NULL, a INT, b TEXT)")
    if indexed:
        db.create_index("t", "id")
    rng = random.Random(seed)
    ids = rng.sample(range(-50, 400), 250)
    for i in ids:
        ex.execute(f"INSERT INTO t VALUES ({i}, {rng.choice(['NULL', str(rng.randint(0, 9))])}, 'x{i % 7}')")
    return db, ex, rng


def test_differential_index_vs_scan(tmp_path):
    """The same random statements on an indexed and an unindexed database must agree."""
    db1, indexed, rng = _build(tmp_path, "with", True, 1)
    db2, plain, _ = _build(tmp_path, "without", False, 1)
    try:
        for step in range(150):
            lo, hi = sorted(rng.sample(range(-60, 420), 2))
            where = rng.choice([
                f"id = {lo}", f"id > {lo}", f"id >= {lo}", f"id < {hi}", f"id <= {hi}",
                f"id > {lo} AND id < {hi}", f"{lo} <= id AND a IS NOT NULL",
                f"id >= {lo} AND id <= {hi} AND a > 4", f"id = {lo} OR id = {hi}",
                f"NOT id < {lo}", f"id != {lo} AND id < {hi}",
            ])
            kind = rng.choice(["select", "select", "select", "update", "delete"])
            if kind == "select":
                sql = f"SELECT id, a FROM t WHERE {where}"
            elif kind == "update":
                sql = f"UPDATE t SET a = {rng.randint(0, 9)} WHERE {where}"
            else:
                if rng.random() < 0.7:
                    continue
                sql = f"DELETE FROM t WHERE {where}"
            r1, r2 = indexed.execute(sql), plain.execute(sql)
            assert r1.affected == r2.affected, sql
            if kind == "select":
                assert sorted(r1.rows) == sorted(r2.rows), sql
        final1 = sorted(indexed.execute("SELECT * FROM t").rows)
        final2 = sorted(plain.execute("SELECT * FROM t").rows)
        assert final1 == final2
    finally:
        db1.close()
        db2.close()
