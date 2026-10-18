"""BPlusTree: a disk-resident B+ tree mapping unique int64 keys to RecordIds.

It owns one DiskManager and one BufferPool. Every node is one page. Pages are
only pinned for the duration of a single _read or _write, never across a
yield or between operations, so scans cannot leak pins.

Insert is "split after overflow": a node is edited in memory, and if it then
holds more than max_keys keys it is split before anything is written.
"""

from __future__ import annotations

import logging
import struct
from bisect import bisect_left, bisect_right
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from minidb.index.errors import (
    BTreeError,
    CorruptIndexError,
    DuplicateKeyError,
    InvalidKeyError,
)
from minidb.index.node import (
    NO_PAGE,
    InternalNode,
    LeafNode,
    Node,
    decode_node,
    encode_node,
    physical_max_keys,
)
from minidb.storage.buffer_pool import BufferPool, BufferPoolStats
from minidb.storage.disk_manager import FIRST_DATA_PAGE_ID, DiskManager, PageId
from minidb.storage.errors import InvalidPageError
from minidb.storage.eviction import make_policy
from minidb.storage.record_id import RecordId

logger = logging.getLogger(__name__)

META = struct.Struct("<4sII")  # magic, root page id, max_keys
META_MAGIC = b"BPT1"
META_PAGE_ID: PageId = FIRST_DATA_PAGE_ID
MIN_MAX_KEYS = 3
INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1
MAX_PAGE_ID = 0xFFFFFFFF
MAX_SLOT = 0xFFFF


@dataclass(frozen=True)
class TreeStats:
    height: int       # number of levels; 1 means the root is a leaf
    node_count: int
    leaf_count: int
    key_count: int


class BPlusTree:
    def __init__(
        self,
        path: str | Path,
        page_size: int = 4096,
        pool_pages: int = 64,
        eviction_policy: str = "lru",
        max_keys: int | None = None,
    ) -> None:
        capacity = physical_max_keys(page_size)
        if capacity < MIN_MAX_KEYS:
            raise ValueError(f"page size {page_size} is too small for a B+ tree node")
        if max_keys is not None and not (MIN_MAX_KEYS <= max_keys <= capacity):
            raise ValueError(
                f"max_keys must be between {MIN_MAX_KEYS} and {capacity} for page size "
                f"{page_size}, got {max_keys!r}"
            )
        self._closed = False
        self._root: PageId = NO_PAGE
        self._max_keys = max_keys or capacity
        self._disk = DiskManager(path, page_size)
        try:
            self._pool = BufferPool(self._disk, pool_pages, policy=make_policy(eviction_policy))
            if self._disk.num_pages == FIRST_DATA_PAGE_ID:  # only the header page: a new tree
                self._create()
            else:
                self._load_meta(max_keys, capacity)
        except Exception:
            self._disk.close()  # do not leak the file handle if setup fails
            raise
        logger.info("Opened B+ tree %s: root page %d, max_keys %d", path, self._root, self._max_keys)

    # ---- inspection ---------------------------------------------------------
    @property
    def root_page_id(self) -> PageId:
        return self._root

    @property
    def max_keys(self) -> int:
        return self._max_keys

    @property
    def pool_stats(self) -> BufferPoolStats:
        return self._pool.stats

    # ---- search ----------------------------------------------------------------
    def search(self, key: int) -> RecordId | None:
        """Exact lookup. Costs one page read per tree level."""
        key = self._check_key(key)
        leaf = self._descend(key)
        pos = bisect_left(leaf.keys, key)
        if pos < len(leaf.keys) and leaf.keys[pos] == key:
            return leaf.rids[pos]
        return None

    def search_path(self, key: int) -> list[PageId]:
        """Page ids visited from the root down to the leaf that would hold key."""
        key = self._check_key(key)
        node = self._read(self._root)
        path = [node.page_id]
        while isinstance(node, InternalNode):
            node = self._read(node.children[bisect_right(node.keys, key)])
            path.append(node.page_id)
        return path

    def range_scan(self, low: int | None = None, high: int | None = None) -> Iterator[tuple[int, RecordId]]:
        """Yield (key, rid) with low <= key <= high in key order (None = unbounded).

        Descends once, then follows the leaf chain. Do not modify the tree while
        iterating: the result is not defined.
        """
        if low is not None:
            low = self._check_key(low)
        if high is not None:
            high = self._check_key(high)
        return self._scan(low, high)

    def _scan(self, low: int | None, high: int | None) -> Iterator[tuple[int, RecordId]]:
        node = self._read(self._root)
        while isinstance(node, InternalNode):
            index = 0 if low is None else bisect_right(node.keys, low)
            node = self._read(node.children[index])
        start = 0 if low is None else bisect_left(node.keys, low)
        while True:
            for i in range(start, len(node.keys)):
                if high is not None and node.keys[i] > high:
                    return
                yield node.keys[i], node.rids[i]
            if node.next_leaf == NO_PAGE:
                return
            node = self._read(node.next_leaf)
            if not isinstance(node, LeafNode):
                raise CorruptIndexError("leaf chain points at a non-leaf page")
            start = 0

    # ---- insert --------------------------------------------------------------------
    def insert(self, key: int, rid: RecordId) -> None:
        key = self._check_key(key)
        self._check_rid(rid)

        path: list[tuple[InternalNode, int]] = []  # ancestors, and which child we took
        leaf = self._descend(key, path)
        pos = bisect_left(leaf.keys, key)
        if pos < len(leaf.keys) and leaf.keys[pos] == key:
            raise DuplicateKeyError(f"key {key} already exists")  # nothing has been modified

        leaf.keys.insert(pos, key)
        leaf.rids.insert(pos, rid)
        if len(leaf.keys) <= self._max_keys:
            self._write(leaf)  # the common case: it fits
            return

        separator, right_id = self._split_leaf(leaf)
        left_id = leaf.page_id
        while path:  # push the separator up, splitting ancestors that overflow
            parent, index = path.pop()
            parent.keys.insert(index, separator)
            parent.children.insert(index + 1, right_id)
            if len(parent.keys) <= self._max_keys:
                self._write(parent)
                return
            separator, right_id = self._split_internal(parent)
            left_id = parent.page_id

        # The root itself split: grow the tree by one level.
        new_root = InternalNode(self._new_page_id(), [separator], [left_id, right_id])
        self._write(new_root)
        self._root = new_root.page_id
        self._write_meta()
        logger.debug("Root split: new root is page %d", self._root)

    def _split_leaf(self, leaf: LeafNode) -> tuple[int, PageId]:
        """Split an overflowing leaf; return (separator key, new right page id).

        The separator is COPIED up: the key stays in the right leaf.
        """
        mid = len(leaf.keys) // 2
        right = LeafNode(
            self._new_page_id(), leaf.keys[mid:], leaf.rids[mid:], leaf.next_leaf
        )
        leaf.keys = leaf.keys[:mid]
        leaf.rids = leaf.rids[:mid]
        leaf.next_leaf = right.page_id  # splice the new leaf into the chain
        self._write(right)  # the new page first, then the page that points at it
        self._write(leaf)
        logger.debug("Split leaf %d -> new leaf %d, separator %d", leaf.page_id, right.page_id, right.keys[0])
        return right.keys[0], right.page_id

    def _split_internal(self, node: InternalNode) -> tuple[int, PageId]:
        """Split an overflowing internal node; return (promoted key, new right page id).

        The promoted key MOVES up: it stays in neither half.
        """
        mid = len(node.keys) // 2
        promoted = node.keys[mid]
        right = InternalNode(self._new_page_id(), node.keys[mid + 1 :], node.children[mid + 1 :])
        node.keys = node.keys[:mid]
        node.children = node.children[: mid + 1]
        self._write(right)
        self._write(node)
        logger.debug("Split internal %d -> new node %d, promoted %d", node.page_id, right.page_id, promoted)
        return promoted, right.page_id

    # ---- verification and display ---------------------------------------------------
    def check(self, strict_fill: bool = True) -> TreeStats:
        """Verify every invariant; raise CorruptIndexError on the first violation.

        strict_fill also enforces the half-full rule, which holds for insert-only
        trees. Pass False once keys can be deleted without rebalancing.
        """
        max_keys = self._max_keys
        leaf_order: list[PageId] = []
        leaf_depths: set[int] = set()
        counts = [0, 0]  # nodes, keys

        def walk(page_id: PageId, low: int | None, high: int | None, depth: int) -> None:
            node = self._read(page_id)
            counts[0] += 1
            keys = node.keys
            if any(a >= b for a, b in zip(keys, keys[1:])):
                raise CorruptIndexError(f"page {page_id}: keys are not strictly increasing")
            if keys and (
                (low is not None and keys[0] < low) or (high is not None and keys[-1] >= high)
            ):
                raise CorruptIndexError(
                    f"page {page_id}: keys fall outside [{low}, {high}) allowed by the parent"
                )
            if len(keys) > max_keys:
                raise CorruptIndexError(f"page {page_id}: {len(keys)} keys exceeds max_keys {max_keys}")
            is_root = depth == 0
            if isinstance(node, LeafNode):
                minimum = 0 if is_root else (max_keys + 1) // 2
                if strict_fill and len(keys) < minimum:
                    raise CorruptIndexError(f"page {page_id}: leaf has {len(keys)} keys, minimum {minimum}")
                leaf_order.append(page_id)
                leaf_depths.add(depth)
                counts[1] += len(keys)
                return
            if not keys:
                raise CorruptIndexError(f"page {page_id}: internal node without keys")
            minimum = 1 if is_root else max(1, max_keys // 2)
            if strict_fill and len(keys) < minimum:
                raise CorruptIndexError(f"page {page_id}: internal node has {len(keys)} keys, minimum {minimum}")
            bounds = [low, *keys, high]
            for i, child in enumerate(node.children):
                walk(child, bounds[i], bounds[i + 1], depth + 1)

        walk(self._root, None, None, 0)
        if len(leaf_depths) != 1:
            raise CorruptIndexError(f"leaves are at different depths: {sorted(leaf_depths)}")

        chain: list[PageId] = []
        page_id = leaf_order[0]
        while page_id != NO_PAGE:
            if page_id in chain:
                raise CorruptIndexError("leaf chain contains a cycle")
            chain.append(page_id)
            leaf = self._read(page_id)
            if not isinstance(leaf, LeafNode):
                raise CorruptIndexError(f"leaf chain reaches non-leaf page {page_id}")
            page_id = leaf.next_leaf
        if chain != leaf_order:
            raise CorruptIndexError(f"leaf chain {chain} does not match the tree order {leaf_order}")

        return TreeStats(
            height=next(iter(leaf_depths)) + 1,
            node_count=counts[0],
            leaf_count=len(leaf_order),
            key_count=counts[1],
        )

    def render(self, show_pages: bool = False) -> str:
        """The tree as text, one line per level. Leaves are joined with '->' (the chain)."""

        def label(node: Node) -> str:
            body = f"[{' | '.join(str(k) for k in node.keys)}]"
            return f"p{node.page_id}{body}" if show_pages else body

        lines: list[str] = []
        level = [self._root]
        depth = 0
        while level:
            nodes = [self._read(page_id) for page_id in level]
            is_leaf = isinstance(nodes[0], LeafNode)
            separator = " -> " if is_leaf else "  "
            lines.append(f"level {depth}: " + separator.join(label(n) for n in nodes))
            if is_leaf:
                break
            level = [child for n in nodes for child in n.children]  # type: ignore[union-attr]
            depth += 1
        return "\n".join(lines)

    # ---- lifecycle ----------------------------------------------------------------------
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

    def __enter__(self) -> "BPlusTree":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # ---- internals ----------------------------------------------------------------------
    def _descend(
        self, key: int, path: list[tuple[InternalNode, int]] | None = None
    ) -> LeafNode:
        node = self._read(self._root)
        while isinstance(node, InternalNode):
            index = bisect_right(node.keys, key)  # equal keys go RIGHT
            if path is not None:
                path.append((node, index))
            node = self._read(node.children[index])
        return node

    def _read(self, page_id: PageId) -> Node:
        try:
            page = self._pool.fetch_page(page_id)
        except InvalidPageError as exc:
            raise CorruptIndexError(f"index points at page {page_id}, which does not exist") from exc
        try:
            data = bytes(page.data)  # a copy, safe to use after unpinning
        finally:
            self._pool.unpin_page(page_id)
        return decode_node(page_id, data)

    def _write(self, node: Node) -> None:
        data = encode_node(node, self._disk.page_size)
        page = self._pool.fetch_page(node.page_id)
        try:
            page.write(0, data)  # marks the page dirty
        finally:
            self._pool.unpin_page(node.page_id)

    def _new_page_id(self) -> PageId:
        page = self._pool.new_page()
        self._pool.unpin_page(page.page_id)
        return page.page_id

    def _create(self) -> None:
        meta = self._pool.new_page()
        self._pool.unpin_page(meta.page_id)
        assert meta.page_id == META_PAGE_ID
        root_id = self._new_page_id()
        self._write(LeafNode(root_id))
        self._root = root_id
        self._write_meta()
        self._pool.flush_all()  # a brand-new tree is valid on disk immediately

    def _write_meta(self) -> None:
        page = self._pool.fetch_page(META_PAGE_ID)
        try:
            raw = META.pack(META_MAGIC, self._root, self._max_keys)
            page.write(0, raw.ljust(self._disk.page_size, b"\x00"))
        finally:
            self._pool.unpin_page(META_PAGE_ID)

    def _load_meta(self, requested: int | None, capacity: int) -> None:
        page = self._pool.fetch_page(META_PAGE_ID)
        try:
            raw = bytes(page.read(0, META.size))
        finally:
            self._pool.unpin_page(META_PAGE_ID)
        magic, root, stored = META.unpack(raw)
        if magic != META_MAGIC:
            raise CorruptIndexError("not a B+ tree index file (bad magic in the meta page)")
        if not (MIN_MAX_KEYS <= stored <= capacity):
            raise CorruptIndexError(f"meta page stores an invalid max_keys {stored}")
        if requested is not None and requested != stored:
            raise BTreeError(
                f"index was created with max_keys={stored}, but max_keys={requested} was requested"
            )
        if not (META_PAGE_ID < root < self._disk.num_pages):
            raise CorruptIndexError(
                f"meta page points at root page {root}, but the file has {self._disk.num_pages} pages"
            )
        self._root = root
        self._max_keys = stored

    @staticmethod
    def _check_key(key: object) -> int:
        if isinstance(key, bool) or not isinstance(key, int) or not (INT64_MIN <= key <= INT64_MAX):
            raise InvalidKeyError(f"key must be an int64, got {type(key).__name__} {repr(key)[:40]}")
        return key

    @staticmethod
    def _check_rid(rid: object) -> None:
        if (
            not isinstance(rid, RecordId)
            or not (0 < rid.page_id <= MAX_PAGE_ID)
            or not (0 <= rid.slot <= MAX_SLOT)
        ):
            raise BTreeError(f"invalid record id: {rid!r}")

    def delete(self, key: int) -> bool:
        """Remove a key. Returns False if it was not present (nothing is written).

        Lazy: leaves may become underfull or empty and are never merged. Searches
        and scans stay correct; check() needs strict_fill=False afterwards.
        """
        key = self._check_key(key)
        leaf = self._descend(key)
        pos = bisect_left(leaf.keys, key)
        if pos >= len(leaf.keys) or leaf.keys[pos] != key:
            return False
        del leaf.keys[pos]
        del leaf.rids[pos]
        self._write(leaf)
        return True