from __future__ import annotations
import struct
from dataclasses import dataclass
from minidb.storage.errors import InvalidPageError

@dataclass
class Page:
    page_id:int
    data:bytearray
    dirty:bool=False

    @property
    def size(self) -> int:
        return len(self.data)

    def read(self, offset: int, length: int) -> bytes:
        self._check(offset, length)
        return bytes(self.data[offset : offset + length])

    def write(self, offset: int, payload: bytes) -> None:
        self._check(offset, len(payload))
        self.data[offset : offset + len(payload)] = payload
        self.dirty = True

    def unpack_from(self, fmt: struct.Struct, offset: int = 0) -> tuple:
        self._check(offset, fmt.size)
        return fmt.unpack_from(self.data, offset)

    def pack_into(self, fmt: struct.Struct, offset: int, *values: object) -> None:
        self._check(offset, fmt.size)
        fmt.pack_into(self.data, offset, *values)
        self.dirty = True

    def _check(self, offset: int, length: int) -> None:
        if offset < 0 or length < 0 or offset + length > len(self.data):
            raise InvalidPageError(
                f"page {self.page_id}: access [{offset}, {offset + length}) "
                f"outside page of {len(self.data)} bytes"
            )