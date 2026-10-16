"""HeapFile: an unordered collection of records spread over slotted pages.

It owns one DiskManager and one BufferPool, and speaks only in bytes and
RecordIds: it knows nothing about schemas or types.

Pin discipline: every page is pinned only for the duration of one operation,
and NEVER across a yield. A scan copies one page's records out, unpins the
page, and only then hands records to the caller.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType

from minidb.storage.buffer_pool import BufferPool, BufferPoolStats
from minidb.storage.disk_manager import FIRST_DATA_PAGE_ID, DiskManager, PageId
from minidb.storage.errors import RecordFormatError, RecordTooLargeError
from minidb.storage.eviction import make_policy
from minidb.storage.record_id import RecordId
from minidb.storage.slotted_page import HEADER, SLOT, SlottedPage

logger = logging.getLogger(__name__)


class HeapFile:
    def __init__(
        self,
        path: str | Path,
        page_size: int = 4096,
        pool_pages: int = 64,
        eviction_policy: str = "lru",
    ) -> None:
        self._closed = False
        self._hint: PageId | None = None          # page that accepted the last insert
        self._free: dict[PageId, int] = {}        # free-space map: page id -> reclaimable bytes
        self._max_record = page_size - HEADER.size - SLOT.size
        self._disk = DiskManager(path, page_size)
        try:
            self._pool = BufferPool(self._disk, pool_pages, policy=make_policy(eviction_policy))
            self._rebuild_free_space_map()
        except Exception:
            self._disk.close()  # do not leak the file handle if setup fails
            raise
        logger.info("Opened heap file %s: %d data pages", path, self.page_count)

    # ---- inspection -------------------------------------------------------
    @property
    def page_count(self) -> int:
        """Number of data pages (the header page is not counted)."""
        return self._disk.num_pages - FIRST_DATA_PAGE_ID

    @property
    def pool_stats(self) -> BufferPoolStats:
        return self._pool.stats

    def free_space(self) -> dict[PageId, int]:
        """page id -> reclaimable free bytes (a copy of the free-space map)."""
        return dict(self._free)

    # ---- operations ---------------------------------------------------------
    def insert(self, data: bytes) -> RecordId:
        if not data:
            raise RecordFormatError("cannot store an empty record")
        if len(data) > self._max_record:
            raise RecordTooLargeError(
                f"record of {len(data)} bytes can never fit (max {self._max_record})"
            )
        need = len(data) + SLOT.size  # conservative: assume a new slot entry is needed
        page_id = self._find_page_with_space(need)
        page = self._pool.new_page() if page_id is None else self._pool.fetch_page(page_id)
        page_id = page.page_id
        try:
            slotted = SlottedPage(page)
            slot = slotted.insert(data)
            self._free[page_id] = slotted.total_free()
        finally:
            self._pool.unpin_page(page_id)
        self._hint = page_id
        return RecordId(page_id, slot)

    def get(self, rid: RecordId) -> bytes:
        page = self._pool.fetch_page(rid.page_id)
        try:
            return SlottedPage(page).get(rid.slot)  # returns a copy, safe after unpin
        finally:
            self._pool.unpin_page(rid.page_id)

    def delete(self, rid: RecordId) -> None:
        page = self._pool.fetch_page(rid.page_id)
        try:
            slotted = SlottedPage(page)
            slotted.delete(rid.slot)
            self._free[rid.page_id] = slotted.total_free()
        finally:
            self._pool.unpin_page(rid.page_id)

    def scan(self) -> Iterator[tuple[RecordId, bytes]]:
        """Yield every live record in physical (RID) order: a sequential full scan."""
        for page_id in range(FIRST_DATA_PAGE_ID, self._disk.num_pages):
            page = self._pool.fetch_page(page_id)
            try:
                records = [
                    (RecordId(page_id, slot), data)
                    for slot, data in SlottedPage(page).iter_records()
                ]
            finally:
                self._pool.unpin_page(page_id)  # unpin BEFORE yielding
            yield from records

    # ---- lifecycle ------------------------------------------------------------
    def flush(self) -> None:
        """Write every dirty page and fsync."""
        self._pool.flush_all()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._pool.close()  # flushes dirty pages
        finally:
            self._disk.close()

    def __enter__(self) -> "HeapFile":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # ---- internals --------------------------------------------------------------
    def _find_page_with_space(self, need: int) -> PageId | None:
        if self._hint is not None and self._free.get(self._hint, 0) >= need:
            return self._hint  # common case: keep appending to the same page
        for page_id, free in self._free.items():  # first fit, lowest page id first
            if free >= need:
                return page_id
        return None

    def _rebuild_free_space_map(self) -> None:
        for page_id in range(FIRST_DATA_PAGE_ID, self._disk.num_pages):
            page = self._pool.fetch_page(page_id)
            try:
                self._free[page_id] = SlottedPage(page).total_free()
            finally:
                self._pool.unpin_page(page_id)