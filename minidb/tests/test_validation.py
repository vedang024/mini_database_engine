import math
import random

import pytest

from minidb.table.errors import RowError, RowValidationError
from minidb.table.row_codec import decode_row, encode_row, validate_row
from minidb.table.schema import Column, Schema

SCHEMA = Schema(
    [
        Column("id", "INTEGER", nullable=False),
        Column("name", "STRING", max_length=5),
        Column("score", "FLOAT"),
        Column("active", "BOOLEAN"),
    ]
)


def test_valid_row_is_normalised():
    clean = validate_row(SCHEMA, (1, "Ann", 3, True))
    assert clean == (1, "Ann", 3.0, True)
    assert isinstance(clean[2], float)  # the int became a float


def test_all_problems_are_reported_at_once():
    with pytest.raises(RowValidationError) as exc:
        validate_row(SCHEMA, (None, "toolongname", "high", 1))
    assert exc.value.problems == [
        "column 'id' is NOT NULL",
        "column 'name': 11 characters exceeds max_length 5",
        "column 'score': expects FLOAT, got str 'high'",
        "column 'active': expects BOOL, got int 1",
    ]
    assert isinstance(exc.value, RowError)  # old `except RowError` code still works


def test_wrong_value_count_is_a_plain_row_error():
    with pytest.raises(RowError, match="expected 4 values, got 2") as exc:
        validate_row(SCHEMA, (1, "a"))
    assert not isinstance(exc.value, RowValidationError)


def test_max_length_counts_characters_not_bytes():
    assert validate_row(SCHEMA, (1, "ééééé", None, None))[1] == "ééééé"  # 5 chars, 10 bytes
    with pytest.raises(RowValidationError):
        validate_row(SCHEMA, (1, "éééééé", None, None))                  # 6 chars


def test_null_empty_string_zero_and_false_are_four_different_things():
    nulls = (1, None, None, None)
    zeros = (1, "", 0.0, False)
    assert encode_row(SCHEMA, nulls) != encode_row(SCHEMA, zeros)
    assert decode_row(SCHEMA, encode_row(SCHEMA, nulls)) == nulls
    assert decode_row(SCHEMA, encode_row(SCHEMA, zeros)) == zeros


def test_nan_round_trips():
    row = decode_row(SCHEMA, encode_row(SCHEMA, (1, None, float("nan"), None)))
    assert math.isnan(row[2])


def test_text_over_the_byte_limit_is_reported():
    schema = Schema([Column("t", "TEXT")])
    with pytest.raises(RowValidationError) as exc:
        validate_row(schema, ("x" * 65536,))  # built here, so the test id stays short
    assert exc.value.problems == ["column 't': 65536 bytes exceeds the 65535-byte limit"]


def test_text_that_is_not_valid_unicode_is_reported():
    schema = Schema([Column("t", "TEXT")])
    with pytest.raises(RowValidationError) as exc:
        validate_row(schema, ("\ud800",))  # a lone surrogate cannot become UTF-8
    assert "UTF-8" in exc.value.problems[0]


def test_random_valid_rows_round_trip():
    rng = random.Random(11)
    alphabet = "aé😀"
    for _ in range(500):
        row = (
            rng.randint(-(2**63), 2**63 - 1),
            None if rng.random() < 0.3 else "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 5))),
            None if rng.random() < 0.3 else rng.uniform(-1e9, 1e9),
            rng.choice([None, True, False]),
        )
        assert decode_row(SCHEMA, encode_row(SCHEMA, row)) == validate_row(SCHEMA, row)