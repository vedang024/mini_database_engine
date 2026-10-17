"""B+ tree nodes and their byte layout on a page (little-endian).

Leaf page    : [kind=1 u8][nkeys u16][next_leaf u32] ([key i64][rid.page u32][rid.slot u16]) * nkeys
Internal page: [kind=2 u8][nkeys u16][unused u32]     [key i64] * nkeys   [child u32] * (nkeys + 1)

Page id 0 is the file header and never a node, so 0 doubles as "no next leaf".
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from minidb.index.errors import BTreeError, CorruptIndexError
from minidb.storage.disk_manager import PageId
from minidb.storage.record_id import RecordId

NO_PAGE: PageId = 0
LEAF_KIND = 1
INTERNAL_KIND = 2

HEADER = struct.Struct("<BHI")        # kind, number of keys, next leaf
LEAF_ENTRY = struct.Struct("<qIH")    # key, rid.page_id, rid.slot
KEY_SIZE = 8
CHILD_SIZE = 4


@dataclass
class LeafNode:
    page_id: PageId
    keys: list[int] = field(default_factory=list)
    rids: list[RecordId] = field(default_factory=list)
    next_leaf: PageId = NO_PAGE


@dataclass
class InternalNode:
    page_id: PageId
    keys: list[int] = field(default_factory=list)
    children: list[PageId] = field(default_factory=list)


Node = LeafNode | InternalNode


def physical_max_keys(page_size: int) -> int:
    """Most keys a node can physically hold on a page of this size."""
    leaf = (page_size - HEADER.size) // LEAF_ENTRY.size
    internal = (page_size - HEADER.size - CHILD_SIZE) // (KEY_SIZE + CHILD_SIZE)
    return min(leaf, internal)


def encode_node(node: Node, page_size: int) -> bytes:
    """Serialise a node into exactly one page (zero padded)."""
    if isinstance(node, LeafNode):
        if len(node.keys) != len(node.rids):
            raise BTreeError(f"leaf {node.page_id}: {len(node.keys)} keys but {len(node.rids)} rids")
        head = HEADER.pack(LEAF_KIND, len(node.keys), node.next_leaf)
        body = b"".join(
            LEAF_ENTRY.pack(k, r.page_id, r.slot) for k, r in zip(node.keys, node.rids)
        )
    else:
        n = len(node.keys)
        if len(node.children) != n + 1:
            raise BTreeError(f"internal {node.page_id}: {n} keys but {len(node.children)} children")
        head = HEADER.pack(INTERNAL_KIND, n, NO_PAGE)
        body = struct.pack(f"<{n}q", *node.keys) + struct.pack(f"<{n + 1}I", *node.children)
    data = head + body
    if len(data) > page_size:
        raise BTreeError(
            f"node on page {node.page_id} needs {len(data)} bytes but a page holds {page_size}"
        )
    return data.ljust(page_size, b"\x00")


def decode_node(page_id: PageId, data: bytes) -> Node:
    """Parse a page into a node, rejecting anything that cannot be valid."""
    kind, n, next_leaf = HEADER.unpack_from(data, 0)
    if kind == LEAF_KIND:
        end = HEADER.size + n * LEAF_ENTRY.size
        if end > len(data):
            raise CorruptIndexError(f"page {page_id}: leaf claims {n} entries, more than fit")
        keys: list[int] = []
        rids: list[RecordId] = []
        for key, rid_page, rid_slot in LEAF_ENTRY.iter_unpack(data[HEADER.size : end]):
            keys.append(key)
            rids.append(RecordId(rid_page, rid_slot))
        return LeafNode(page_id, keys, rids, next_leaf)
    if kind == INTERNAL_KIND:
        keys_end = HEADER.size + n * KEY_SIZE
        end = keys_end + (n + 1) * CHILD_SIZE
        if end > len(data):
            raise CorruptIndexError(f"page {page_id}: internal node claims {n} keys, more than fit")
        keys = list(struct.unpack_from(f"<{n}q", data, HEADER.size))
        children = list(struct.unpack_from(f"<{n + 1}I", data, keys_end))
        return InternalNode(page_id, keys, children)
    raise CorruptIndexError(f"page {page_id}: unknown node type {kind}")