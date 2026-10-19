import pytest

from minidb.sql.errors import LexError
from minidb.sql.lexer import TokenType, tokenize

K, I, N, F, S, Y, E = (
    TokenType.KEYWORD, TokenType.IDENT, TokenType.INTEGER, TokenType.FLOAT,
    TokenType.STRING, TokenType.SYMBOL, TokenType.EOF,
)


def kinds(sql):
    return [(t.type, t.value) for t in tokenize(sql)]


def test_the_lesson_query():
    assert kinds("SELECT * FROM users WHERE id = 10;") == [
        (K, "SELECT"), (Y, "*"), (K, "FROM"), (I, "users"), (K, "WHERE"),
        (I, "id"), (Y, "="), (N, "10"), (Y, ";"), (E, ""),
    ]


def test_keywords_and_identifiers_are_case_insensitive():
    assert kinds("select Foo_1 FROM") == [(K, "SELECT"), (I, "foo_1"), (K, "FROM"), (E, "")]


def test_empty_and_blank_input_give_only_eof():
    assert kinds("") == [(E, "")]
    assert kinds("  \n\t ") == [(E, "")]


@pytest.mark.parametrize(
    "text,kind",
    [("0", N), ("42", N), ("3.14", F), ("1e5", F), ("2.5E-3", F), ("7e+2", F)],
)
def test_numbers(text, kind):
    assert kinds(text)[0] == (kind, text)


def test_dot_without_digits_is_not_part_of_a_number():
    with pytest.raises(LexError, match="unexpected character '.'"):
        tokenize("1.")


@pytest.mark.parametrize("bad", ["12abc", "1e", "3_x"])
def test_number_followed_by_letters_is_rejected(bad):
    with pytest.raises(LexError, match="invalid number"):
        tokenize(bad)


def test_strings_and_escaped_quotes():
    assert kinds("'it''s'")[0] == (S, "it's")
    assert kinds("''")[0] == (S, "")
    assert kinds("'a  b'")[0] == (S, "a  b")
    assert kinds("'Zoë 名前'")[0] == (S, "Zoë 名前")


def test_string_may_span_lines_and_later_positions_stay_right():
    toks = tokenize("'a\nb' x")
    assert toks[0].value == "a\nb"
    assert (toks[1].line, toks[1].col) == (2, 4)


def test_unterminated_string_points_at_the_opening_quote():
    with pytest.raises(LexError) as exc:
        tokenize("SELECT 'abc")
    assert (exc.value.line, exc.value.col) == (1, 8)


def test_two_character_operators():
    assert [v for _, v in kinds("<= >= != <> < > =")[:-1]] == ["<=", ">=", "!=", "<>", "<", ">", "="]


def test_comments_are_skipped():
    assert kinds("SELECT -- the rest is ignored\n 1") == [(K, "SELECT"), (N, "1"), (E, "")]


def test_minus_is_a_symbol_not_part_of_the_number():
    assert kinds("-5")[:2] == [(Y, "-"), (N, "5")]


def test_positions_are_line_and_column_1_based():
    toks = tokenize("SELECT\n  id")
    assert (toks[0].line, toks[0].col) == (1, 1)
    assert (toks[1].line, toks[1].col) == (2, 3)


@pytest.mark.parametrize("ch", ["@", "!", "#", "é", "\"", "."])
def test_unexpected_characters_are_rejected_with_a_position(ch):
    with pytest.raises(LexError) as exc:
        tokenize("a " + ch)
    assert (exc.value.line, exc.value.col) == (1, 3)


def test_eof_is_always_last():
    assert tokenize("a b")[-1].type is E