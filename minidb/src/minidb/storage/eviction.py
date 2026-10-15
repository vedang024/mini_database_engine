"""Eviction policies: decide WHICH cached page to throw out when the pool is full.

A policy only tracks page ids. It never touches pages, pins or the disk; the
buffer pool tells it what happened and asks it for a victim.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from typing import Protocol

from minidb.storage.disk_manager import PageId


class EvictionPolicy(Protocol):
    def on_load(self, page_id: PageId) -> None:
        """A page was just brought into the pool."""

    def on_access(self, page_id: PageId) -> None:
        """A page already in the pool was used again (a cache hit)."""

    def victim(self, is_evictable: Callable[[PageId], bool]) -> PageId | None:
        """Choose a page to evict among those where is_evictable(page_id) is True.

        Must NOT remove it; the pool calls remove() after the eviction succeeds.
        """

    def remove(self, page_id: PageId) -> None:
        """Forget a page (it left the pool)."""


class FIFOPolicy:
    """First in, first out: evict the page that has been in the pool the longest."""

    def __init__(self) -> None:
        self._order: OrderedDict[PageId, None] = OrderedDict()

    def on_load(self, page_id: PageId) -> None:
        self._order[page_id] = None

    def on_access(self, page_id: PageId) -> None:
        pass  # FIFO ignores usage. That is its weakness.

    def victim(self, is_evictable: Callable[[PageId], bool]) -> PageId | None:
        for page_id in self._order:  # oldest first
            if is_evictable(page_id):
                return page_id
        return None

    def remove(self, page_id: PageId) -> None:
        self._order.pop(page_id, None)