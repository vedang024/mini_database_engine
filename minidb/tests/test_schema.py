import pytest

from minidb.table.errors import SchemaError
from minidb.table.schema import Column, ColumnType, Schema, validate_identifier


@pytest.mark.parametrize("text", ["int", "INT", "Int"])
def test_column_type_parse_is_case_insensitive(text):
    assert ColumnType.parse(text) is ColumnType.INT


def test_unknown_column_type_rejected():
    with pytest.raises(SchemaError, match="blob"):
        ColumnType.parse("blob")


def test_identifiers_are_lower_cased():
    assert validate_identifier("Users_2") == "users_2"
    assert validate_identifier("x" * 64) == "x" * 64  # exactly the limit is fine
    col = Column("ID", "int")
    assert col.name == "id"
    assert col.type is ColumnType.INT


@pytest.mark.parametrize(
    "bad", ["", "1abc", "a b", "a-b", "../evil", "tab\tle", "x" * 65, "naïve", 42]
)
def test_bad_identifiers_rejected(bad):
    with pytest.raises(SchemaError):
        validate_identifier(bad)


def test_column_nullable_must_be_a_bool():
    with pytest.raises(SchemaError):
        Column("a", "INT", nullable="yes")


def test_empty_schema_rejected():
    with pytest.raises(SchemaError):
        Schema([])


def test_duplicate_column_names_rejected_case_insensitively():
    with pytest.raises(SchemaError, match="duplicate"):
        Schema([Column("a", "INT"), Column("A", "TEXT")])


def test_names_and_index_of():
    schema = Schema([Column("id", "INT"), Column("name", "TEXT")])
    assert schema.names == ("id", "name")
    assert schema.index_of("NAME") == 1
    with pytest.raises(SchemaError):
        schema.index_of("missing")


def test_json_roundtrip():
    schema = Schema(
        [
            Column("id", "INT", nullable=False),
            Column("name", "TEXT"),
            Column("ok", "BOOL"),
            Column("score", "FLOAT"),
        ]
    )
    assert Schema.from_json(schema.to_json()) == schema


@pytest.mark.parametrize(
    "bad",
    [
        "not json",
        "[]",
        "42",
        '[{"name": "a"}]',
        '[{"name": "a", "type": "BLOB", "nullable": true}]',
    ],
)
def test_bad_schema_json_rejected(bad):
    with pytest.raises(SchemaError):
        Schema.from_json(bad)