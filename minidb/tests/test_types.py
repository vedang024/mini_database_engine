import pytest

from minidb.table.row_codec import encode_row
from minidb.table.schema import Column, ColumnType, Schema
from minidb.table.types import TYPES, is_fixed_width, row_size_bounds, type_of

MIXED = Schema(
    [
        Column("id", "INT", nullable=False),
        Column("name", "TEXT", max_length=5),
        Column("score", "FLOAT"),
        Column("active", "BOOL"),
    ]
)
FIXED = Schema([Column("a", "INT", False), Column("b", "FLOAT", False)])


def test_every_column_type_has_a_data_type():
    assert set(TYPES) == set(ColumnType)


@pytest.mark.parametrize(
    "column_type,size",
    [
        (ColumnType.INT, 8),
        (ColumnType.FLOAT, 8),
        (ColumnType.BOOL, 1),
        (ColumnType.TEXT, None),
    ],
)
def test_fixed_sizes(column_type, size):
    assert TYPES[column_type].fixed_size == size


def test_type_of_follows_the_column():
    assert type_of(Column("x", "boolean")) is TYPES[ColumnType.BOOL]


def test_size_bounds_of_a_mixed_schema():
    assert row_size_bounds(MIXED) == (9, 40)  # 1+8  ..  1+8+(2+4*5)+8+1
    assert not is_fixed_width(MIXED)


def test_all_fixed_not_null_schema_has_one_row_size():
    assert row_size_bounds(FIXED) == (17, 17)
    assert is_fixed_width(FIXED)


def test_a_nullable_fixed_column_makes_the_row_size_vary():
    schema = Schema([Column("a", "INT")])
    assert row_size_bounds(schema) == (1, 9)  # the value may be absent entirely
    assert not is_fixed_width(schema)


def test_unbounded_text_bounds():
    schema = Schema([Column("t", "TEXT", nullable=False)])
    assert row_size_bounds(schema) == (3, 3 + 65535)


def test_every_encoded_row_is_within_the_bounds():
    low, high = row_size_bounds(MIXED)
    rows = [
        (1, None, None, None),
        (1, "", 0.0, False),
        (-5, "abc", 1.5, True),
        (2**63 - 1, "é" * 5, None, True),
    ]
    for row in rows:
        assert low <= len(encode_row(MIXED, row)) <= high


def test_widest_row_hits_the_upper_bound_exactly():
    row = (2**63 - 1, "😀" * 5, 1.5, True)  # each emoji is 4 UTF-8 bytes
    assert len(encode_row(MIXED, row)) == row_size_bounds(MIXED)[1] == 40