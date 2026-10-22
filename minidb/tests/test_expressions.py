import itertools

import pytest

from minidb.execution.errors import PlanError
from minidb.execution.expressions import and3, check_predicate, compile_expr, format_expr, infer_type, or3
from minidb.sql.parser import parse
from minidb.table.schema import Column, Schema

SCHEMA = Schema((Column("id", "INT", False), Column("name", "TEXT"), Column("score", "FLOAT"), Column("ok", "BOOL")))


def where(sql_condition):
    return parse(f"SELECT * FROM t WHERE {sql_condition}").where


def evaluate(condition, row):
    return compile_expr(where(condition), SCHEMA)(row)


@pytest.mark.parametrize("a,b,expected_and,expected_or", [
    (True, True, True, True), (True, False, False, True), (True, None, None, True),
    (False, False, False, False), (False, None, False, None), (None, None, None, None),
])
def test_kleene_tables(a, b, expected_and, expected_or):
    for x, y in ((a, b), (b, a)):
        assert and3(x, y) is expected_and
        assert or3(x, y) is expected_or


def test_null_comparison_is_unknown():
    assert evaluate("name = 'x'", (1, None, 1.0, True)) is None
    assert evaluate("name != 'x'", (1, None, 1.0, True)) is None
    assert evaluate("NOT name = 'x'", (1, None, 1.0, True)) is None


def test_is_null_never_unknown():
    assert evaluate("name IS NULL", (1, None, 1.0, True)) is True
    assert evaluate("name IS NOT NULL", (1, None, 1.0, True)) is False


def test_numeric_mixing():
    assert evaluate("score > 1", (1, "a", 1.5, True)) is True
    assert evaluate("id < 2.5", (2, "a", 1.5, True)) is True


@pytest.mark.parametrize("cond", ["id = 'x'", "name > 1", "ok = 1", "id AND ok", "NOT id", "nosuch = 1", "id"])
def test_type_errors(cond):
    with pytest.raises(PlanError):
        check_predicate(where(cond), SCHEMA)


def test_infer_type():
    assert infer_type(where("id = 1"), SCHEMA) == "BOOL"
    assert infer_type(where("name = NULL"), SCHEMA) == "BOOL"


@pytest.mark.parametrize("cond,text", [
    ("id = 1", "id = 1"),
    ("a = 1 OR b = 2 AND c = 3", "a = 1 OR b = 2 AND c = 3"),
    ("(a = 1 OR b = 2) AND c = 3", "(a = 1 OR b = 2) AND c = 3"),
    ("NOT (a = 1 AND b = 2)", "NOT (a = 1 AND b = 2)"),
    ("name = 'it''s'", "name = 'it''s'"),
    ("x IS NOT NULL", "x IS NOT NULL"),
])
def test_format_roundtrip(cond, text):
    printed = format_expr(where(cond))
    assert printed == text
    assert where(printed) == where(cond)  # printing then re-parsing gives the same AST


def test_precedence_and_before_or():
    row = (1, "a", 1.0, True)
    assert evaluate("id = 1 OR id = 2 AND id = 3", row) is True


def test_and3_exhaustive_matches_definition():
    for a, b in itertools.product((True, False, None), repeat=2):
        assert and3(a, b) is not True or (a is True and b is True)
