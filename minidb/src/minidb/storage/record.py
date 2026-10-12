"""
Byte layout (all integers little-endian):
    id     : int64               8 bytes
    name   : uint16 length + UTF-8 bytes
    email  : uint16 length + UTF-8 bytes
"""

from __future__ import annotations
import struct
from dataclasses import dataclass
from minidb.storage.errors import RecordFormatError

_ID = struct.Struct("<q")
_STR_LEN = struct.Struct("<H")
MAX_STR_BYTES = 0xFFFF


@dataclass(frozen=True)
class Record:
    id: int
    name: str
    email: str

    def to_bytes(self) -> bytes:
        try:
            parts = [_ID.pack(self.id)]
        except struct.error as exc:
            raise RecordFormatError(f"id {self.id} does not fit in int64") from exc

        for text in (self.name, self.email):
            raw = text.encode("utf-8")
            if len(raw) > MAX_STR_BYTES:
                raise RecordFormatError(f"string too long: {len(raw)} bytes (max {MAX_STR_BYTES})")
            parts.append(_STR_LEN.pack(len(raw)))
            parts.append(raw)
        return b"".join(parts)

    @classmethod
    def from_bytes(cls, data: bytes) -> "Record":
        try:
            (record_id,) = _ID.unpack_from(data, 0)
            offset = _ID.size
            strings: list[str] = []
            for _ in range(2):
                (length,) = _STR_LEN.unpack_from(data, offset)
                offset += _STR_LEN.size
                raw = data[offset : offset + length]
                if len(raw) != length:
                    raise RecordFormatError("record truncated inside a string")
                strings.append(raw.decode("utf-8"))
                offset += length
        except (struct.error, UnicodeDecodeError) as exc:
            raise RecordFormatError(f"cannot decode record: {exc}") from exc

        if offset != len(data):
            raise RecordFormatError(f"{len(data) - offset} unexpected trailing bytes")
        return cls(record_id, strings[0], strings[1])