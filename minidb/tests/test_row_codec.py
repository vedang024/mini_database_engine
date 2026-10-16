import pytest

from minidb.table.errors import RowError
from minidb.table.row_codec import decode_row, encode_row
from minidb.table.schema import Column, Schema

ALL = Schema(
    [
        Column("id", "INT", nullable=False),
        Column("name", "TEXT"),
        Column("active", "BOOL"),
        Column("score", "FLOAT"),
    ]
)
STRICT = Schema(
    [
        Column("id", "INT", False),
        Column("name", "TEXT", False),
        Column("active", "BOOL", False),
        Column("score", "FLOAT", False),
    ]
)


def test_exact_byte_layout():
    data = encode_row(ALL, (1, "ab", True, 1.5))
    assert data == (
        b"\x00"                                  # null bitmap: nothing is NULL
        b"\x01\x00\x00\x00\x00\x00\x00\x00"      # id = 1 (int64, little-endian)
        b"\x02\x00ab"                            # name: length 2, then bytes
        b"\x01"                                  # active = True
        b"\x00\x00\x00\x00\x00\x00\xf8\x3f"      # score = 1.5 (float64)
    )


def test_nulls_cost_no_bytes():
    data = encode_row(ALL, (2, None, None, None))
    assert data == b"\x0e" + (2).to_bytes(8, "little")  # bits 1, 2, 3 set; only the id follows


def test_row_is_never_empty():
    assert encode_row(Schema([Column("a", "INT")]), (None,)) == b"\x01"


@pytest.mark.parametrize(
    "row",
    [
        (1, "ab", True, 1.5),
        (0, "", False, 0.0),
        (-5, "Zoë 名前", None, -2.5),
        (2**63 - 1, None, None, None),
        (-(2**63), "x", True, float("inf")),
    ],
)
def test_roundtrip(row):
    assert decode_row(ALL, encode_row(ALL, row)) == row


def test_int_in_a_float_column_comes_back_as_float():
    row = decode_row(ALL, encode_row(ALL, (1, None, None, 3)))
    assert row[3] == 3.0
    assert isinstance(row[3], float)


def test_text_length_counts_bytes_not_characters():
    schema = Schema([Column("t", "TEXT", nullable=False)])
    assert len(encode_row(schema, ("Zoë",))) == 1 + 2 + 4  # bitmap + length + 4 UTF-8 bytes


def test_more_than_eight_columns_use_a_wider_bitmap():
    schema = Schema([Column(f"c{i}", "INT") for i in range(10)])
    row = tuple(i if i % 2 == 0 else None for i in range(10))
    data = encode_row(schema, row)
    assert data[:2] == b"\xaa\x02"       # NULL bits 1,3,5,7 in byte 0 and bit 9 in byte 1
    assert len(data) == 2 + 5 * 8
    assert decode_row(schema, data) == row


@pytest.mark.parametrize(
    "col_type,value",
    [
        ("INT", "5"),
        ("INT", 1.5),
        ("INT", True),
        ("FLOAT", "1.0"),
        ("FLOAT", True),
        ("BOOL", 1),
        ("BOOL", "yes"),
        ("TEXT", 5),
        ("TEXT", b"x"),
    ],
)
def test_wrong_python_type_rejected(col_type, value):
    with pytest.raises(RowError):
        encode_row(Schema([Column("c", col_type)]), (value,))


@pytest.mark.parametrize(
    "col_type,value",
    [
        pytest.param("INT", 2**63, id="int-too-big"),
        pytest.param("INT", -(2**63) - 1, id="int-too-small"),
        pytest.param("FLOAT", 10**400, id="float-overflow"),
        pytest.param("TEXT", "x" * 65536, id="text-too-long"),
        pytest.param("TEXT", "\ud800", id="lone-surrogate"),
    ],
)
def test_unencodable_values_rejected(col_type, value):
    with pytest.raises(RowError):
        encode_row(Schema([Column("c", col_type)]), (value,))


def test_not_null_enforced():
    with pytest.raises(RowError, match="NOT NULL"):
        encode_row(ALL, (None, "a", True, 1.0))


@pytest.mark.parametrize("row", [(1,), (1, "a", True, 1.0, 9)])
def test_wrong_arity_rejected(row):
    with pytest.raises(RowError, match="expected 4"):
        encode_row(ALL, row)


def test_every_truncation_is_detected():
    data = encode_row(STRICT, (7, "hé", True, 2.5))
    for cut in range(len(data)):
        with pytest.raises(RowError):
            decode_row(STRICT, data[:cut])


def test_trailing_bytes_rejected():
    data = encode_row(STRICT, (7, "a", True, 2.5))
    with pytest.raises(RowError, match="trailing"):
        decode_row(STRICT, data + b"x")


def test_corrupt_stored_values_rejected():
    not_null = Schema([Column("id", "INT", nullable=False)])
    with pytest.raises(RowError, match="NOT NULL"):
        decode_row(not_null, b"\x01")        # bitmap says NULL for a NOT NULL column
    flag = Schema([Column("b", "BOOL", nullable=False)])
    with pytest.raises(RowError, match="BOOL"):
        decode_row(flag, b"\x00\x02")         # 2 is not a valid BOOL byte