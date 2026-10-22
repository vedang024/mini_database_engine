import pytest

from minidb.config import DatabaseConfig
from minidb.database import Database
from minidb.execution.errors import PlanError
from minidb.execution.executor import Executor


@pytest.fixture
def ex(tmp_path):
    db = Database(DatabaseConfig(data_dir=tmp_path))
    ex = Executor(db)
    ex.execute("CREATE TABLE t (id INT NOT NULL, n INT, s TEXT(5))")
    db.create_index("t", "id")
    yield ex
    db.close()


def plan(ex, where):
    return ex.explain(f"SELECT * FROM t WHERE {where}")


@pytest.mark.parametrize("where,expected", [
    ("id = 10", "IndexLookup(t.id = 10)"),
    ("10 = id", "IndexLookup(t.id = 10)"),
    ("id >= 5", "IndexRangeScan(t.id in [5, +inf])"),
    ("id > 5", "IndexRangeScan(t.id in [6, +inf])"),
    ("id < 5", "IndexRangeScan(t.id in [-inf, 4])"),
    ("5 < id", "IndexRangeScan(t.id in [6, +inf])"),
    ("id > 5 AND id <= 9", "IndexRangeScan(t.id in [6, 9])"),
    ("id > 5 AND id > 8", "IndexRangeScan(t.id in [9, +inf])"),
    ("n = 3", "SeqScan(t)"),
    ("id != 3", "SeqScan(t)"),
    ("id = 1 OR id = 2", "SeqScan(t)"),
    ("NOT id = 1", "SeqScan(t)"),
    ("id = 1.5", "SeqScan(t)"),
])
def test_access_path_leaf(ex, where, expected):
    assert expected in plan(ex, where).splitlines()[-1]


def test_residual_filter(ex):
    text = plan(ex, "id = 7 AND n > 3")
    assert text.splitlines() == ["Filter(n > 3)", "  IndexLookup(t.id = 7)"]


def test_equality_beats_range(ex):
    text = plan(ex, "id > 1 AND id = 5")
    assert text.splitlines() == ["Filter(id > 1)", "  IndexLookup(t.id = 5)"]


def test_int64_edge_stays_in_filter(ex):
    text = plan(ex, f"id > {2**63 - 1}")
    assert text.splitlines()[0].startswith("Filter(")
    assert ex.execute(f"SELECT * FROM t WHERE id > {2**63 - 1}").rows == []


def test_projection_on_top(ex):
    assert ex.explain("SELECT n, id FROM t WHERE id = 1").splitlines()[0] == "Project(n, id)"


@pytest.mark.parametrize("sql", [
    "SELECT * FROM nope",
    "SELECT zzz FROM t",
    "SELECT * FROM t WHERE zzz = 1",
    "SELECT * FROM t WHERE s = 1",
    "SELECT * FROM t WHERE id",
    "INSERT INTO t VALUES (1, 2)",
    "INSERT INTO t (id, id) VALUES (1, 2)",
    "INSERT INTO t (n) VALUES (1)",
    "INSERT INTO t VALUES (NULL, 1, 'a')",
    "INSERT INTO t VALUES (1, 1, 'toolong')",
    "INSERT INTO t VALUES ('x', 1, 'a')",
    "UPDATE t SET zzz = 1",
    "UPDATE t SET n = 'x'",
    "UPDATE t SET n = 1, n = 2",
    "DELETE FROM nope",
    "CREATE TABLE t (a INT)",
])
def test_semantic_errors(ex, sql):
    with pytest.raises(Exception) as info:
        ex.execute(sql)
    assert not isinstance(info.value, (AssertionError, AttributeError, KeyError))


def test_plan_errors_are_plan_errors(ex):
    with pytest.raises(PlanError):
        ex.execute("SELECT * FROM t WHERE s = 1")
