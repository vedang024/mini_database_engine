"""Phase 8 demo: the type system, validation, and row sizes."""

from minidb.table.errors import RowValidationError
from minidb.table.row_codec import decode_row, encode_row, validate_row
from minidb.table.schema import Column, Schema
from minidb.table.types import TYPES, is_fixed_width, row_size_bounds

SCHEMA = Schema(
    [
        Column("id", "INTEGER", nullable=False),
        Column("name", "STRING", max_length=5),
        Column("score", "FLOAT"),
        Column("active", "BOOLEAN"),
    ]
)

print("== column types")
for column_type, data_type in TYPES.items():
    size = "variable" if data_type.fixed_size is None else f"{data_type.fixed_size} B"
    print(f"{column_type.value:<6}{size}")

print("\n== a valid row: encoded, then decoded")
data = encode_row(SCHEMA, (7, "Ann", 2.5, True))
print(len(data), "bytes:", data.hex(" "))
print(decode_row(SCHEMA, data))
print("normalised:", validate_row(SCHEMA, (1, None, 3, None)))

print("\n== NULL costs a bit, not bytes")
for row in [(1, None, None, None), (1, "", 0.0, False)]:
    encoded = encode_row(SCHEMA, row)
    print(f"{len(encoded):>2} bytes, bitmap={encoded[0]:08b}  <- {row}")

print("\n== an invalid row: every problem at once")
try:
    validate_row(SCHEMA, (None, "toolongname", "high", 1))
except RowValidationError as exc:
    for problem in exc.problems:
        print("  -", problem)

print("\n== size bounds")
low, high = row_size_bounds(SCHEMA)
print(f"min {low} B, max {high} B, fixed width: {is_fixed_width(SCHEMA)}")