import random

import pytest

from minidb.sql.errors import LexError, ParseError, SqlError
from minidb.sql.nodes import (
    Assignment, BinaryOp, ColumnDef, ColumnRef, CreateTable, Delete, Insert,
    IsNull, Literal, Not, Select, Update, dump,
)
from minidb.sql.parser import parse, parse_script

A, B, C = ColumnRef("a"), ColumnRef("b"), ColumnRef("c")


def lit(v):
    return Literal(v)


def where_of(sql):
    return parse(f"SELECT * FROM t WHERE {sql}").where


# ---- statements ----------------------------------------------------------------
def test_select_star_with_where():
    assert parse("SELECT * FROM users WHERE id = 10;") == Select(
        "users", None, BinaryOp("=", ColumnRef("id"), lit(10))
    )


def test_select_columns_without_where():
    assert parse("select id, Name from Users") == Select("users", ("id", "name"), None)


def test_create_table():
    stmt = parse(
        "CREATE TABLE users (id INT NOT NULL, name VARCHAR(20) NOT NULL, "
        "email TEXT NULL, active BOOLEAN, score float)"
    )
    assert stmt == CreateTable(
        "users",
        (
            ColumnDef("id", "INT", False),
            ColumnDef("name", "VARCHAR", False, 20),
            ColumnDef("email", "TEXT", True),
            ColumnDef("active", "BOOLEAN", True),
            ColumnDef("score", "FLOAT", True),
        ),
    )


def test_insert_with_and_without_column_list_and_many_rows():
    assert parse("INSERT INTO t VALUES (1, 'a', NULL)") == Insert(
        "t", None, ((lit(1), lit("a"), lit(None)),)
    )
    assert parse("INSERT INTO t (id, name) VALUES (1, 'a'), (2, 'it''s')") == Insert(
        "t", ("id", "name"), ((lit(1), lit("a")), (lit(2), lit("it's")))
    )


def test_update():
    assert parse("UPDATE t SET a = 1, b = 'x', c = d WHERE id = 3") == Update(
        "t",
        (Assignment("a", lit(1)), Assignment("b", lit("x")), Assignment("c", ColumnRef("d"))),
        BinaryOp("=", ColumnRef("id"), lit(3)),
    )
    assert parse("UPDATE t SET a = NULL").where is None


def test_delete():
    assert parse("DELETE FROM t") == Delete("t", None)
    assert parse("DELETE FROM t WHERE a > 1") == Delete("t", BinaryOp(">", A, lit(1)))


# ---- expressions -----------------------------------------------------------------
def test_and_binds_tighter_than_or():
    assert where_of("a = 1 OR b = 2 AND c = 3") == BinaryOp(
        "OR",
        BinaryOp("=", A, lit(1)),
        BinaryOp("AND", BinaryOp("=", B, lit(2)), BinaryOp("=", C, lit(3))),
    )


def test_parentheses_override_precedence():
    assert where_of("(a = 1 OR b = 2) AND c = 3") == BinaryOp(
        "AND",
        BinaryOp("OR", BinaryOp("=", A, lit(1)), BinaryOp("=", B, lit(2))),
        BinaryOp("=", C, lit(3)),
    )


def test_and_and_or_are_left_associative():
    assert where_of("a AND b AND c") == BinaryOp("AND", BinaryOp("AND", A, B), C)
    assert where_of("a OR b OR c") == BinaryOp("OR", BinaryOp("OR", A, B), C)


def test_not_binds_tighter_than_and():
    assert where_of("NOT a AND b") == BinaryOp("AND", Not(A), B)
    assert where_of("NOT NOT a") == Not(Not(A))


def test_is_null_and_is_not_null():
    assert where_of("a IS NULL") == IsNull(A, False)
    assert where_of("a IS NOT NULL") == IsNull(A, True)
    assert where_of("NOT a IS NULL") == Not(IsNull(A, False))


@pytest.mark.parametrize("op,expected", [("<>", "!="), ("!=", "!="), ("<=", "<="), (">=", ">=")])
def test_comparison_operators_are_normalised(op, expected):
    assert where_of(f"a {op} 1") == BinaryOp(expected, A, lit(1))


def test_literals():
    assert where_of("a = -5") == BinaryOp("=", A, lit(-5))
    assert where_of("a = 2.5") == BinaryOp("=", A, lit(2.5))
    assert where_of("a = -2.5e1") == BinaryOp("=", A, lit(-25.0))
    assert where_of("a = TRUE") == BinaryOp("=", A, lit(True))
    assert where_of("a = false") == BinaryOp("=", A, lit(False))
    assert where_of("1 = a") == BinaryOp("=", lit(1), A)


def test_int64_boundaries():
    assert parse("INSERT INTO t VALUES (-9223372036854775808)").rows[0][0] == lit(-(2**63))
    assert parse("INSERT INTO t VALUES (9223372036854775807)").rows[0][0] == lit(2**63 - 1)
    with pytest.raises(ParseError, match="int64"):
        parse("INSERT INTO t VALUES (9223372036854775808)")
    with pytest.raises(ParseError, match="int64"):
        parse("INSERT INTO t VALUES (-9223372036854775809)")


def test_absurd_numbers_are_parse_errors_not_crashes():
    with pytest.raises(ParseError, match="too large"):
        parse("INSERT INTO t VALUES (" + "9" * 5000 + ")")
    with pytest.raises(ParseError, match="out of range"):
        parse("INSERT INTO t VALUES (1e999)")


def test_nesting_limit_prevents_stack_overflow():
    deep = "(" * 500 + "a" + ")" * 500
    with pytest.raises(ParseError, match="nested"):
        parse(f"SELECT * FROM t WHERE {deep}")
    with pytest.raises(ParseError, match="nested"):
        parse("SELECT * FROM t WHERE " + "NOT " * 500 + "a")
    assert where_of("(" * 40 + "a" + ")" * 40) == A  # a sensible depth is fine


# ---- scripts ---------------------------------------------------------------------------
def test_script_with_several_statements_and_empty_ones():
    stmts = parse_script(";; SELECT * FROM a; DELETE FROM b;\n;")
    assert stmts == [Select("a", None, None), Delete("b", None)]
    assert parse_script("") == [] == parse_script("-- nothing")


def test_parse_requires_exactly_one_statement():
    with pytest.raises(ParseError, match="found 0"):
        parse("")
    with pytest.raises(ParseError, match="found 2"):
        parse("SELECT * FROM a; SELECT * FROM b")


def test_statement_without_final_semicolon_is_fine():
    assert parse("SELECT * FROM a") == parse("SELECT * FROM a;")


# ---- error messages ---------------------------------------------------------------------
ERRORS = [
    ("SELCT * FROM t", "expected a statement", 1, 1),
    ("SELECT FROM t", "expected '*' or a column name, found keyword FROM", 1, 8),
    ("SELECT * FROM", "expected a table name, found end of input", 1, 14),
    ("SELECT * FROM t WHERE", "expected an expression, found end of input", 1, 22),
    ("SELECT * FROM t WHERE a =", "expected an expression", 1, 26),
    ("SELECT * FROM t garbage", "expected ';' or end of input, found identifier 'garbage'", 1, 17),
    ("SELECT * FROM t WHERE a = b = c", "expected ';' or end of input", 1, 29),
    ("SELECT * FROM t WHERE (a = 1", "expected ')'", 1, 29),
    ("INSERT INTO t VALUES (1,)", "expected a literal value, found ')'", 1, 25),
    ("INSERT INTO t VALUES (a)", "expected a literal value, found identifier 'a'", 1, 23),
    ("INSERT INTO t (1) VALUES (1)", "expected a column name", 1, 16),
    ("DELETE t", "expected FROM", 1, 8),
    ("UPDATE t SET", "expected a column name, found end of input", 1, 13),
    ("UPDATE t SET a 1", "expected '='", 1, 16),
    ("CREATE TABLE t ()", "expected a column name, found ')'", 1, 17),
    ("CREATE TABLE t (a blob)", "expected a column type", 1, 19),
    ("CREATE TABLE t (a INT(5))", "length is only allowed", 1, 22),
    ("CREATE TABLE t (a TEXT(x))", "expected a length", 1, 24),
    ("CREATE TABLE t (a INT NOT)", "expected NULL", 1, 26),
    ("INSERT INTO t VALUES (-x)", "expected a number after '-'", 1, 24),
    ("SELECT *\nFROM t\nWHERE", "expected an expression, found end of input", 3, 6),
]


@pytest.mark.parametrize("sql,message,line,col", ERRORS, ids=[f"e{i}" for i in range(len(ERRORS))])
def test_error_messages_and_positions(sql, message, line, col):
    with pytest.raises(ParseError) as exc:
        parse(sql)
    assert message in exc.value.message
    assert (exc.value.line, exc.value.col) == (line, col)


def test_lex_errors_surface_through_parse():
    with pytest.raises(LexError):
        parse("SELECT @ FROM t")


def test_render_puts_a_caret_under_the_column():
    sql = "SELECT * FROM t\nWHERE a = = 1"
    with pytest.raises(ParseError) as exc:
        parse(sql)
    assert exc.value.render(sql) == (
        f"{exc.value}\n  WHERE a = = 1\n  {' ' * 10}^"
    )


# ---- display and robustness -----------------------------------------------------------
def test_dump_shows_the_tree():
    assert dump(parse("SELECT * FROM users WHERE id = 10")) == "\n".join(
        [
            "Select(table='users', columns=None)",
            "  where: BinaryOp(op='=')",
            "    left: ColumnRef(name='id')",
            "    right: Literal(value=10)",
        ]
    )


def test_dump_of_insert_rows():
    text = dump(parse("INSERT INTO t VALUES (1, 'a')"))
    assert "rows:" in text and "[0]:" in text and "Literal(value='a')" in text


def test_ast_nodes_are_immutable_and_hashable():
    stmt = parse("SELECT * FROM t WHERE a = 1")
    assert hash(stmt) == hash(parse("select * from T where A = 1"))
    with pytest.raises(AttributeError):
        stmt.table = "other"  # type: ignore[misc]


def test_random_garbage_only_ever_raises_sql_errors():
    rng = random.Random(5)
    vocab = [
        "SELECT", "FROM", "WHERE", "INSERT", "INTO", "VALUES", "UPDATE", "SET", "DELETE",
        "CREATE", "TABLE", "AND", "OR", "NOT", "NULL", "IS", "t", "a", "1", "2.5", "'x'",
        "(", ")", ",", ";", "*", "=", "<>", "-", "INT", "TEXT", "@", "'",
    ]
    parsed = 0
    for _ in range(3000):
        sql = " ".join(rng.choice(vocab) for _ in range(rng.randint(0, 12)))
        try:
            parse_script(sql)
            parsed += 1
        except SqlError:
            pass
    for _ in range(1000):
        sql = "".join(chr(rng.randint(0, 255)) for _ in range(rng.randint(0, 30)))
        try:
            parse_script(sql)
        except SqlError:
            pass
    assert parsed > 0  # the generator does sometimes produce valid SQL