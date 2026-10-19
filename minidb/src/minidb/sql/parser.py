"""Recursive descent parser: tokens -> AST.

Grammar (keywords upper case, [ ] optional, ( )* repeat, | choice):

    script       := ( [statement] ';' )* [statement]
    statement    := create_table | insert | select | update | delete
    create_table := CREATE TABLE ident '(' column_def (',' column_def)* ')'
    column_def   := ident type_name [ '(' INTEGER ')' ] [ NOT NULL | NULL ]
    insert       := INSERT INTO ident [ '(' ident (',' ident)* ')' ]
                    VALUES row (',' row)*
    row          := '(' literal (',' literal)* ')'
    select       := SELECT ( '*' | ident (',' ident)* ) FROM ident [ WHERE expr ]
    update       := UPDATE ident SET assignment (',' assignment)* [ WHERE expr ]
    assignment   := ident '=' primary
    delete       := DELETE FROM ident [ WHERE expr ]

    expr         := or_expr
    or_expr      := and_expr ( OR and_expr )*
    and_expr     := not_expr ( AND not_expr )*
    not_expr     := NOT not_expr | comparison
    comparison   := primary [ ('=' | '!=' | '<>' | '<' | '<=' | '>' | '>=') primary
                            | IS [NOT] NULL ]
    primary      := literal | ident | '(' expr ')'
    literal      := ['-'] INTEGER | ['-'] FLOAT | STRING | TRUE | FALSE | NULL

Each grammar rule is one method. Precedence comes from the rule order: the
rule that binds loosest (OR) calls the next tighter one, and so on down to
primary.
"""

from __future__ import annotations

import math

from minidb.sql.errors import ParseError
from minidb.sql.lexer import Token, TokenType, tokenize
from minidb.sql.nodes import (
    Assignment,
    BinaryOp,
    ColumnDef,
    ColumnRef,
    CreateTable,
    Delete,
    Expr,
    Insert,
    IsNull,
    Literal,
    Not,
    Select,
    Statement,
    Update,
)

TYPE_NAMES = frozenset({"INT", "INTEGER", "FLOAT", "BOOL", "BOOLEAN", "TEXT", "STRING", "VARCHAR"})
LENGTH_TYPES = frozenset({"TEXT", "STRING", "VARCHAR"})
COMPARISONS = {"=": "=", "!=": "!=", "<>": "!=", "<": "<", "<=": "<=", ">": ">", ">=": ">="}
MAX_DEPTH = 50  # nesting limit for parentheses and NOT chains (protects the Python stack)
INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1


class Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self._tokens = tokens
        self._pos = 0
        self._depth = 0

    # ---- token helpers --------------------------------------------------------
    def _peek(self) -> Token:
        return self._tokens[self._pos]

    def _next(self) -> Token:
        token = self._tokens[self._pos]
        if token.type is not TokenType.EOF:  # never move past EOF
            self._pos += 1
        return token

    def at_eof(self) -> bool:
        return self._peek().type is TokenType.EOF

    def _fail(self, expected: str) -> ParseError:
        token = self._peek()
        return ParseError(f"expected {expected}, found {token.describe()}", token.line, token.col)

    def _is_keyword(self, name: str) -> bool:
        token = self._peek()
        return token.type is TokenType.KEYWORD and token.value == name

    def _accept_keyword(self, name: str) -> bool:
        if self._is_keyword(name):
            self._next()
            return True
        return False

    def _expect_keyword(self, name: str) -> None:
        if not self._accept_keyword(name):
            raise self._fail(name)

    def _is_symbol(self, symbol: str) -> bool:
        token = self._peek()
        return token.type is TokenType.SYMBOL and token.value == symbol

    def accept_symbol(self, symbol: str) -> bool:
        if self._is_symbol(symbol):
            self._next()
            return True
        return False

    def _expect_symbol(self, symbol: str) -> None:
        if not self.accept_symbol(symbol):
            raise self._fail(repr(symbol))

    def _identifier(self, what: str) -> str:
        token = self._peek()
        if token.type is not TokenType.IDENT:
            raise self._fail(what)
        self._next()
        return token.value

    def _identifier_list(self, what: str) -> tuple[str, ...]:
        names = [self._identifier(what)]
        while self.accept_symbol(","):
            names.append(self._identifier(what))
        return tuple(names)

    def _enter(self) -> None:
        self._depth += 1
        if self._depth > MAX_DEPTH:
            token = self._peek()
            raise ParseError(
                f"expression nested more than {MAX_DEPTH} levels deep", token.line, token.col
            )

    def _leave(self) -> None:
        self._depth -= 1

    # ---- statements --------------------------------------------------------------
    def statement(self) -> Statement:
        token = self._peek()
        if token.type is TokenType.KEYWORD:
            handler = {
                "CREATE": self._create_table,
                "INSERT": self._insert,
                "SELECT": self._select,
                "UPDATE": self._update,
                "DELETE": self._delete,
            }.get(token.value)
            if handler is not None:
                return handler()
        raise self._fail("a statement (CREATE, INSERT, SELECT, UPDATE or DELETE)")

    def _create_table(self) -> CreateTable:
        self._expect_keyword("CREATE")
        self._expect_keyword("TABLE")
        table = self._identifier("a table name")
        self._expect_symbol("(")
        columns = [self._column_def()]
        while self.accept_symbol(","):
            columns.append(self._column_def())
        self._expect_symbol(")")
        return CreateTable(table, tuple(columns))

    def _column_def(self) -> ColumnDef:
        name = self._identifier("a column name")
        token = self._peek()
        type_name = token.value.upper()
        if token.type is not TokenType.IDENT or type_name not in TYPE_NAMES:
            raise self._fail("a column type (INT, FLOAT, BOOL, TEXT, VARCHAR, ...)")
        self._next()
        max_length = None
        if self._is_symbol("("):
            paren = self._peek()
            if type_name not in LENGTH_TYPES:
                raise ParseError(
                    f"a length is only allowed on TEXT, STRING and VARCHAR, not {type_name}",
                    paren.line,
                    paren.col,
                )
            self._next()
            length = self._peek()
            if length.type is not TokenType.INTEGER:
                raise self._fail("a length (an integer)")
            self._next()
            max_length = self._to_int(length)
            self._expect_symbol(")")
        nullable = True
        if self._accept_keyword("NOT"):
            self._expect_keyword("NULL")
            nullable = False
        elif self._accept_keyword("NULL"):
            nullable = True
        return ColumnDef(name, type_name, nullable, max_length)

    def _insert(self) -> Insert:
        self._expect_keyword("INSERT")
        self._expect_keyword("INTO")
        table = self._identifier("a table name")
        columns = None
        if self.accept_symbol("("):
            columns = self._identifier_list("a column name")
            self._expect_symbol(")")
        self._expect_keyword("VALUES")
        rows = [self._row()]
        while self.accept_symbol(","):
            rows.append(self._row())
        return Insert(table, columns, tuple(rows))

    def _row(self) -> tuple[Literal, ...]:
        self._expect_symbol("(")
        values = [self._literal()]
        while self.accept_symbol(","):
            values.append(self._literal())
        self._expect_symbol(")")
        return tuple(values)

    def _select(self) -> Select:
        self._expect_keyword("SELECT")
        if self.accept_symbol("*"):
            columns = None
        else:
            columns = self._identifier_list("'*' or a column name")
        self._expect_keyword("FROM")
        table = self._identifier("a table name")
        return Select(table, columns, self._where())

    def _update(self) -> Update:
        self._expect_keyword("UPDATE")
        table = self._identifier("a table name")
        self._expect_keyword("SET")
        assignments = [self._assignment()]
        while self.accept_symbol(","):
            assignments.append(self._assignment())
        return Update(table, tuple(assignments), self._where())

    def _assignment(self) -> Assignment:
        column = self._identifier("a column name")
        self._expect_symbol("=")
        return Assignment(column, self._primary())

    def _delete(self) -> Delete:
        self._expect_keyword("DELETE")
        self._expect_keyword("FROM")
        table = self._identifier("a table name")
        return Delete(table, self._where())

    def _where(self) -> Expr | None:
        return self.expression() if self._accept_keyword("WHERE") else None

    # ---- expressions: one method per precedence level ------------------------------
    def expression(self) -> Expr:
        return self._or()

    def _or(self) -> Expr:
        left = self._and()
        while self._accept_keyword("OR"):
            left = BinaryOp("OR", left, self._and())
        return left

    def _and(self) -> Expr:
        left = self._not()
        while self._accept_keyword("AND"):
            left = BinaryOp("AND", left, self._not())
        return left

    def _not(self) -> Expr:
        if self._accept_keyword("NOT"):
            self._enter()
            operand = self._not()
            self._leave()
            return Not(operand)
        return self._comparison()

    def _comparison(self) -> Expr:
        left = self._primary()
        token = self._peek()
        if token.type is TokenType.SYMBOL and token.value in COMPARISONS:
            self._next()
            return BinaryOp(COMPARISONS[token.value], left, self._primary())
        if self._accept_keyword("IS"):
            negated = self._accept_keyword("NOT")
            self._expect_keyword("NULL")
            return IsNull(left, negated)
        return left

    def _primary(self) -> Expr:
        token = self._peek()
        if token.type is TokenType.SYMBOL and token.value == "(":
            self._next()
            self._enter()
            inner = self.expression()
            self._expect_symbol(")")
            self._leave()
            return inner
        if token.type is TokenType.IDENT:
            self._next()
            return ColumnRef(token.value)
        return self._literal("an expression")

    def _literal(self, expected: str = "a literal value") -> Literal:
        token = self._peek()
        negative = False
        if token.type is TokenType.SYMBOL and token.value == "-":
            self._next()
            negative = True
            token = self._peek()
            if token.type not in (TokenType.INTEGER, TokenType.FLOAT):
                raise self._fail("a number after '-'")
        if token.type is TokenType.INTEGER:
            self._next()
            value = self._to_int(token)
            if negative:
                value = -value
            if not INT64_MIN <= value <= INT64_MAX:
                raise ParseError("integer literal out of int64 range", token.line, token.col)
            return Literal(value)
        if token.type is TokenType.FLOAT:
            self._next()
            number = float(token.value)
            if not math.isfinite(number):
                raise ParseError("float literal out of range", token.line, token.col)
            return Literal(-number if negative else number)
        if token.type is TokenType.STRING:
            self._next()
            return Literal(token.value)
        if token.type is TokenType.KEYWORD and token.value in ("TRUE", "FALSE", "NULL"):
            self._next()
            return Literal({"TRUE": True, "FALSE": False, "NULL": None}[token.value])
        raise self._fail(expected)

    @staticmethod
    def _to_int(token: Token) -> int:
        try:
            return int(token.value)
        except ValueError:  # Python refuses absurdly long digit strings
            raise ParseError("integer literal too large", token.line, token.col) from None


def parse_script(sql: str) -> list[Statement]:
    """Parse zero or more statements separated by semicolons."""
    parser = Parser(tokenize(sql))
    statements: list[Statement] = []
    while not parser.at_eof():
        if parser.accept_symbol(";"):  # empty statement
            continue
        statements.append(parser.statement())
        if not parser.at_eof() and not parser.accept_symbol(";"):
            raise parser._fail("';' or end of input")
    return statements


def parse(sql: str) -> Statement:
    """Parse exactly one statement (a trailing ';' is optional)."""
    statements = parse_script(sql)
    if len(statements) != 1:
        raise ParseError(f"expected exactly one statement, found {len(statements)}")
    return statements[0]