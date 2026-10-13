"""SlottedPage: variable-length records inside one fixed-size page.

Layout (little-endian):
    [header: num_slots u32, free_end u32]
    [slot 0: offset u16, length u16] [slot 1] ...   grows toward the end
    ...free space...
    [record data]                                   grows toward the start

A slot whose offset is 0 is deleted (offset 0 lies inside the header, so no
live record can have it). Slot numbers never change, so (page_id, slot) is a
stable record id even when compaction moves the bytes.
"""

from __future__ import annotations

import logging
import struct
from collections.abc import Iterator

from minidb.storage.errors import (
    CorruptPageError,
    InvalidSlotError,
    PageFullError,
    RecordFormatError,
    RecordTooLargeError,
)
from minidb.storage.page import Page
from minidb.storage.record_id import RecordId

logger = logging.getLogger(__name__)

HEADER = struct.Struct("<II")  # num_slots, free_end
SLOT = struct.Struct("<HH")    # offset, length
DELETED = 0


class SlottedPage:
    def __init__(self, page: Page) -> None:
        self.page = page
        num_slots, free_end = page.unpack_from(HEADER, 0)
        if free_end == 0:  # never formatted: all zeros
            page.pack_into(HEADER, 0, 0, page.size)
        else:
            dir_end = HEADER.size + num_slots * SLOT.size
            if not (dir_end <= free_end <= page.size):
                raise CorruptPageError(
                    f"page {page.page_id}: bad header "
                    f"(num_slots={num_slots}, free_end={free_end})"
                )

    # ---- header state (always read from the page bytes) -----------------
    @property
    def page_id(self) -> int:
        return self.page.page_id

    @property
    def num_slots(self) -> int:
        return self.page.unpack_from(HEADER, 0)[0]

    @property
    def free_end(self) -> int:
        return self.page.unpack_from(HEADER, 0)[1]

    @property
    def max_record_size(self) -> int:
        """Largest record that could ever fit: an empty page, one new slot."""
        return self.page.size - HEADER.size - SLOT.size

    def rid(self, slot: int) -> RecordId:
        return RecordId(self.page_id, slot)

    # ---- space accounting -----------------------------------------------
    def contiguous_free(self) -> int:
        """Bytes in the gap between the slot directory and the record area."""
        return self.free_end - (HEADER.size + self.num_slots * SLOT.size)

    def total_free(self) -> int:
        """Gap plus the holes left by deleted records (reclaimable by compact)."""
        live_bytes = sum(length for _, _, length in self._live())
        dead_bytes = (self.page.size - self.free_end) - live_bytes
        return self.contiguous_free() + dead_bytes

    # ---- operations -------------------------------------------------------
    def insert(self, data: bytes) -> int:
        """Store a record and return its slot number."""
        if not data:
            raise RecordFormatError("cannot store an empty record")
        if len(data) > self.max_record_size:
            raise RecordTooLargeError(
                f"record of {len(data)} bytes can never fit "
                f"(max {self.max_record_size} for this page size)"
            )

        reuse = next(
            (s for s in range(self.num_slots) if self._slot_entry(s)[0] == DELETED), None
        )
        needed = len(data) + (0 if reuse is not None else SLOT.size)

        if needed > self.contiguous_free():
            if needed > self.total_free():
                raise PageFullError(
                    f"page {self.page_id}: need {needed} bytes, "
                    f"only {self.total_free()} reclaimable"
                )
            self.compact()

        new_end = self.free_end - len(data)
        self.page.write(new_end, data)
        if reuse is None:
            slot = self.num_slots
            self._set_header(slot + 1, new_end)
        else:
            slot = reuse
            self._set_header(self.num_slots, new_end)
        self._set_slot(slot, new_end, len(data))
        return slot

    def get(self, slot: int) -> bytes:
        offset, length = self._live_entry(slot)
        return self.page.read(offset, length)

    def delete(self, slot: int) -> None:
        """Mark the record deleted. Its bytes become a hole until compaction."""
        self._live_entry(slot)  # validates the slot
        self._set_slot(slot, DELETED, 0)

    def iter_records(self) -> Iterator[tuple[int, bytes]]:
        """Yield (slot, record bytes) for every live record, in slot order."""
        for slot, offset, length in self._live():
            yield slot, self.page.read(offset, length)

    def compact(self) -> None:
        """Slide all live records to the end of the page, merging all holes.

        Slot numbers (and so record ids) are unchanged; only offsets change.
        """
        live = [(slot, self.get(slot)) for slot, _, _ in self._live()]  # read ALL first
        free_end = self.page.size
        for slot, data in live:
            free_end -= len(data)
            self.page.write(free_end, data)
            self._set_slot(slot, free_end, len(data))
        self._set_header(self.num_slots, free_end)
        logger.debug("Compacted page %d: %d live records", self.page_id, len(live))

    # ---- internals ----------------------------------------------------------
    @staticmethod
    def _slot_pos(slot: int) -> int:
        return HEADER.size + slot * SLOT.size

    def _slot_entry(self, slot: int) -> tuple[int, int]:
        if not (0 <= slot < self.num_slots):
            raise InvalidSlotError(
                f"page {self.page_id}: slot {slot} out of range [0, {self.num_slots})"
            )
        return self.page.unpack_from(SLOT, self._slot_pos(slot))

    def _live_entry(self, slot: int) -> tuple[int, int]:
        offset, length = self._slot_entry(slot)
        if offset == DELETED:
            raise InvalidSlotError(f"page {self.page_id}: slot {slot} is deleted")
        dir_end = HEADER.size + self.num_slots * SLOT.size
        if offset < dir_end or offset + length > self.page.size:
            raise CorruptPageError(
                f"page {self.page_id}: slot {slot} points outside the record area"
            )
        return offset, length

    def _live(self) -> Iterator[tuple[int, int, int]]:
        for slot in range(self.num_slots):
            offset, length = self.page.unpack_from(SLOT, self._slot_pos(slot))
            if offset != DELETED:
                yield slot, offset, length

    def _set_slot(self, slot: int, offset: int, length: int) -> None:
        self.page.pack_into(SLOT, self._slot_pos(slot), offset, length)

    def _set_header(self, num_slots: int, free_end: int) -> None:
        self.page.pack_into(HEADER, 0, num_slots, free_end)