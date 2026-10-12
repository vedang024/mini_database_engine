"""RecordStore: appends records into pages and scans them back.
"""

from __future__ import annotations

import logging
import struct
from collections.abc import Iterator

from minidb.storage.disk_manager import FIRST_DATA_PAGE_ID, DiskManager, PageId
from minidb.storage.errors import CorruptPageError, RecordTooLargeError
from minidb.storage.record import Record

logger = logging.getLogger(__name__)

PAGE_HEADER = struct.Struct("<II")  # record count, bytes used (header included)
LEN_PREFIX = struct.Struct("<I")


class RecordStore:
    def __init__(self, disk: DiskManager) -> None:
        self._disk = disk

    def insert(self, record: Record) -> PageId:
        """Append a record; return the id of the page it landed on."""
        payload = record.to_bytes()
        entry = LEN_PREFIX.pack(len(payload)) + payload

        capacity = self._disk.page_size - PAGE_HEADER.size
        if len(entry) > capacity:
            raise RecordTooLargeError(
                f"record needs {len(entry)} bytes but a page holds at most {capacity}"
            )

        # Pick the tail page (allocating the very first one if needed).
        if self._disk.num_pages == FIRST_DATA_PAGE_ID:
            page_id = self._disk.allocate_page()
        else:
            page_id = self._disk.num_pages - 1
        buf, count, used = self._load_page(page_id)

        # Tail full? Start a fresh page.
        if used + len(entry) > self._disk.page_size:
            page_id = self._disk.allocate_page()
            buf, count, used = self._load_page(page_id)

        buf[used : used + len(entry)] = entry
        PAGE_HEADER.pack_into(buf, 0, count + 1, used + len(entry))
        self._disk.write_page(page_id, bytes(buf))
        logger.debug("Inserted record id=%d into page %d", record.id, page_id)
        return page_id

    def scan(self) -> Iterator[Record]:
        """Yield every record in insertion order (a sequential full scan)."""
        for page_id in range(FIRST_DATA_PAGE_ID, self._disk.num_pages):
            buf, count, used = self._load_page(page_id)
            offset = PAGE_HEADER.size
            for _ in range(count):
                if offset + LEN_PREFIX.size > used:
                    raise CorruptPageError(f"page {page_id}: record header overruns page")
                (length,) = LEN_PREFIX.unpack_from(buf, offset)
                offset += LEN_PREFIX.size
                end = offset + length
                if end > used:
                    raise CorruptPageError(f"page {page_id}: record overruns used area")
                yield Record.from_bytes(bytes(buf[offset:end]))
                offset = end

    def find(self, record_id: int) -> Record | None:
        """First record with this id, or None. O(N): reads every page until found."""
        return next((r for r in self.scan() if r.id == record_id), None)

    def flush(self) -> None:
        """Make everything written so far durable."""
        self._disk.sync()

    def _load_page(self, page_id: PageId) -> tuple[bytearray, int, int]:
        buf = bytearray(self._disk.read_page(page_id))
        count, used = PAGE_HEADER.unpack_from(buf, 0)
        if used == 0:  # never-written page: all zeros
            used = PAGE_HEADER.size
        if not (PAGE_HEADER.size <= used <= self._disk.page_size):
            raise CorruptPageError(f"page {page_id}: invalid used-bytes value {used}")
        return buf, count, used
    
    def page_stats(self) -> Iterator[tuple[int, int, int, int]]:
        """Yield (page_id, record_count, used_bytes, free_bytes) for each data page."""
        for page_id in range(FIRST_DATA_PAGE_ID, self._disk.num_pages):
            _, count, used = self._load_page(page_id)
            yield page_id, count, used, self._disk.page_size - used