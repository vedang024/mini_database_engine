"""BufferPool: a fixed set of in-memory frames caching pages of one DiskManager.

Contract for callers:
  * fetch_page / new_page return a PINNED page.
  * Call unpin_page when done. Pass dirty=True if you modified the page by
    some route other than Page.write / Page.pack_into (those mark it already).
  * Do not use a Page after unpinning it; the pool may evict it at any time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from types import TracebackType

from minidb.storage.disk_manager import DiskManager, PageId
from minidb.storage.errors import BufferPoolError, BufferPoolFullError
from minidb.storage.eviction import EvictionPolicy, FIFOPolicy
from minidb.storage.page import Page

logger = logging.getLogger(__name__)


@dataclass
class Frame:
    page: Page | None = None
    pin_count: int = 0


@dataclass
class BufferPoolStats:
    hits: int = 0
    misses: int = 0            # every miss is exactly one disk read
    evictions: int = 0
    dirty_evictions: int = 0   # evictions that had to write the page first

    @property
    def hit_ratio(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


class BufferPool:
    def __init__(
        self, disk: DiskManager, capacity: int, policy: EvictionPolicy | None = None
    ) -> None:
        if capacity < 1:
            raise ValueError(f"capacity must be >= 1, got {capacity}")
        self._disk = disk
        self._capacity = capacity
        self._policy: EvictionPolicy = policy if policy is not None else FIFOPolicy()
        self._frames = [Frame() for _ in range(capacity)]
        self._free = list(reversed(range(capacity)))  # frame ids holding nothing
        self._page_table: dict[PageId, int] = {}      # page_id -> frame id
        self.stats = BufferPoolStats()

    # ---- inspection -----------------------------------------------------
    @property
    def capacity(self) -> int:
        return self._capacity

    def is_cached(self, page_id: PageId) -> bool:
        return page_id in self._page_table

    def pin_count(self, page_id: PageId) -> int:
        frame_id = self._page_table.get(page_id)
        return 0 if frame_id is None else self._frames[frame_id].pin_count

    def cached_page_ids(self) -> list[PageId]:
        return sorted(self._page_table)

    # ---- main API -------------------------------------------------------
    def fetch_page(self, page_id: PageId) -> Page:
        """Return the page, pinned. A hit costs no I/O; a miss costs one read."""
        frame_id = self._page_table.get(page_id)
        if frame_id is not None:                              # ---- HIT
            frame = self._frames[frame_id]
            assert frame.page is not None
            frame.pin_count += 1
            self._policy.on_access(page_id)
            self.stats.hits += 1
            return frame.page

        frame_id = self._acquire_frame()                      # ---- MISS
        try:
            page = self._disk.fetch_page(page_id)
        except Exception:
            self._free.append(frame_id)  # give the frame back; the pool stays consistent
            raise
        self._install(frame_id, page)
        self.stats.misses += 1
        return page

    def new_page(self) -> Page:
        """Allocate a brand-new page on disk and return it pinned (and zeroed)."""
        frame_id = self._acquire_frame()  # make room BEFORE growing the file
        try:
            page_id = self._disk.allocate_page()
        except Exception:
            self._free.append(frame_id)
            raise
        page = Page(page_id, bytearray(self._disk.page_size))
        self._install(frame_id, page)
        return page

    def unpin_page(self, page_id: PageId, dirty: bool = False) -> None:
        frame_id = self._page_table.get(page_id)
        if frame_id is None:
            raise BufferPoolError(f"page {page_id} is not in the buffer pool")
        frame = self._frames[frame_id]
        if frame.pin_count == 0:
            raise BufferPoolError(f"page {page_id} is not pinned")
        frame.pin_count -= 1
        if dirty:
            assert frame.page is not None
            frame.page.dirty = True

    def flush_page(self, page_id: PageId) -> bool:
        """Write the page to disk if it is cached and dirty. True if a write happened."""
        frame_id = self._page_table.get(page_id)
        if frame_id is None:
            return False
        page = self._frames[frame_id].page
        assert page is not None
        if not page.dirty:
            return False
        self._disk.flush_page(page)
        return True

    def flush_all(self) -> int:
        """Write every dirty page, then fsync. Returns the number of pages written."""
        written = 0
        for frame in self._frames:
            if frame.page is not None and frame.page.dirty:
                self._disk.flush_page(frame.page)
                written += 1
        self._disk.sync()
        return written

    def close(self) -> None:
        pinned = [pid for pid, fid in self._page_table.items() if self._frames[fid].pin_count > 0]
        if pinned:
            logger.warning("Closing buffer pool with pinned pages: %s", sorted(pinned))
        self.flush_all()

    def __enter__(self) -> "BufferPool":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # ---- internals --------------------------------------------------------
    def _install(self, frame_id: int, page: Page) -> None:
        frame = self._frames[frame_id]
        frame.page = page
        frame.pin_count = 1
        self._page_table[page.page_id] = frame_id
        self._policy.on_load(page.page_id)

    def _is_evictable(self, page_id: PageId) -> bool:
        return self._frames[self._page_table[page_id]].pin_count == 0

    def _acquire_frame(self) -> int:
        """Return an empty frame id: a free one, or one made empty by evicting."""
        if self._free:
            return self._free.pop()

        victim = self._policy.victim(self._is_evictable)
        if victim is None:
            raise BufferPoolFullError(
                f"all {self._capacity} frames are pinned; unpin a page or enlarge the pool"
            )
        frame_id = self._page_table[victim]
        frame = self._frames[frame_id]
        assert frame.page is not None

        if frame.page.dirty:  # write-back BEFORE the data is lost
            self._disk.flush_page(frame.page)
            self.stats.dirty_evictions += 1

        del self._page_table[victim]
        self._policy.remove(victim)
        frame.page = None
        frame.pin_count = 0
        self.stats.evictions += 1
        logger.debug("Evicted page %d from frame %d", victim, frame_id)
        return frame_id