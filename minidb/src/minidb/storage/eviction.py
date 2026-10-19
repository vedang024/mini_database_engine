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

        Must NOT forget the page; the pool calls remove() after the eviction
        succeeds. May update private bookkeeping (e.g. clear reference bits).
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


class LRUPolicy:
    """Least recently used: evict the page whose last use is furthest in the past.

    The OrderedDict is a hash table plus a doubly linked list, so moving a page
    to the "most recent" end is O(1).
    """

    def __init__(self) -> None:
        self._order: OrderedDict[PageId, None] = OrderedDict()  # oldest use first

    def on_load(self, page_id: PageId) -> None:
        self._order[page_id] = None
        self._order.move_to_end(page_id)

    def on_access(self, page_id: PageId) -> None:
        if page_id in self._order:
            self._order.move_to_end(page_id)  # now the most recently used

    def victim(self, is_evictable: Callable[[PageId], bool]) -> PageId | None:
        for page_id in self._order:  # least recently used first
            if is_evictable(page_id):
                return page_id
        return None

    def remove(self, page_id: PageId) -> None:
        self._order.pop(page_id, None)


class ClockPolicy:
    """Clock (second chance): one reference bit per page and a sweeping hand.

    A hit sets the page's bit. The hand clears bits as it passes and evicts the
    first evictable page whose bit is already clear. New pages start with the
    bit clear: loading a page is not evidence that it will be used again.
    """

    def __init__(self) -> None:
        self._slots: list[PageId | None] = []   # the circle; None = empty slot
        self._ref: list[bool] = []              # reference bit per slot
        self._index: dict[PageId, int] = {}     # page id -> slot
        self._free: list[int] = []              # empty slots to reuse
        self._hand = 0

    def on_load(self, page_id: PageId) -> None:
        slot = self._index.get(page_id)
        if slot is not None:  # defensive: already tracked
            self._ref[slot] = True
            return
        if self._free:
            slot = self._free.pop()
            self._slots[slot] = page_id
            self._ref[slot] = False
        else:
            slot = len(self._slots)
            self._slots.append(page_id)
            self._ref.append(False)
        self._index[page_id] = slot

    def on_access(self, page_id: PageId) -> None:
        slot = self._index.get(page_id)
        if slot is not None:
            self._ref[slot] = True

    def victim(self, is_evictable: Callable[[PageId], bool]) -> PageId | None:
        n = len(self._slots)
        # At most two laps: the first clears every reference bit, the second
        # must then find a clear one, unless no page is evictable at all.
        for _ in range(2 * n):
            slot = self._hand
            page_id = self._slots[slot]
            if page_id is not None and is_evictable(page_id):
                if not self._ref[slot]:
                    return page_id          # the hand stays here; remove() moves it
                self._ref[slot] = False     # second chance
            self._hand = (slot + 1) % n
        return None

    def remove(self, page_id: PageId) -> None:
        slot = self._index.pop(page_id, None)
        if slot is None:
            return
        self._slots[slot] = None
        self._ref[slot] = False
        self._free.append(slot)
        if self._hand == slot:
            self._hand = (slot + 1) % len(self._slots)


POLICIES: dict[str, Callable[[], EvictionPolicy]] = {
    "fifo": FIFOPolicy,
    "lru": LRUPolicy,
    "clock": ClockPolicy,
}


def make_policy(name: str) -> EvictionPolicy:
    """Build a policy from its config name (case-insensitive)."""
    factory = POLICIES.get(name.lower())
    if factory is None:
        raise ValueError(f"unknown eviction policy {name!r}; choose from {sorted(POLICIES)}")
    return factory()