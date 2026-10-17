import pytest

from minidb.index.errors import BTreeError, CorruptIndexError
from minidb.index.node import (
    HEADER,
    InternalNode,
    LeafNode,
    decode_node,
    encode_node,
    physical_max_keys,
)
from minidb.storage.record_id import RecordId

PAGE = 512


@pytest.mark.parametrize("page_size,expected", [(4096, 292), (512, 36)])
def test_physical_max_keys(page_size, expected):
    assert physical_max_keys(page_size) == expected


def test_leaf_roundtrip_including_extreme_keys():
    node = LeafNode(
        2,
        [-(2**63), -1, 0, 2**63 - 1],
        [RecordId(1, 0), RecordId(2, 5), RecordId(3, 65535), RecordId(4294967295, 1)],
        next_leaf=7,
    )
    assert decode_node(2, encode_node(node, 128)) == node


def test_internal_roundtrip():
    node = InternalNode(5, [10, 20, 30], [2, 3, 4, 6])
    assert decode_node(5, encode_node(node, 128)) == node


def test_exact_leaf_layout():
    node = LeafNode(2, [7], [RecordId(3, 4)], next_leaf=9)
    expected = (
        b"\x01" + (1).to_bytes(2, "little") + (9).to_bytes(4, "little")  # kind, nkeys, next
        + (7).to_bytes(8, "little")                                       # key
        + (3).to_bytes(4, "little") + (4).to_bytes(2, "little")           # rid page, slot
    )
    data = encode_node(node, 64)
    assert data.startswith(expected)
    assert data[len(expected):] == bytes(64 - len(expected))              # zero padding


def test_exact_internal_layout():
    node = InternalNode(2, [30, 60], [3, 4, 5])
    expected = (
        b"\x02" + (2).to_bytes(2, "little") + (0).to_bytes(4, "little")
        + (30).to_bytes(8, "little") + (60).to_bytes(8, "little")
        + (3).to_bytes(4, "little") + (4).to_bytes(4, "little") + (5).to_bytes(4, "little")
    )
    data = encode_node(node, 64)
    assert data.startswith(expected)
    assert data[len(expected):] == bytes(64 - len(expected))


def test_unformatted_page_is_rejected():
    with pytest.raises(CorruptIndexError, match="node type"):
        decode_node(1, bytes(PAGE))


@pytest.mark.parametrize("kind", [1, 2])
def test_impossible_key_count_is_rejected(kind):
    page = HEADER.pack(kind, 65535, 0).ljust(PAGE, b"\x00")
    with pytest.raises(CorruptIndexError):
        decode_node(1, page)


def test_oversized_node_is_rejected_on_encode():
    node = LeafNode(2, list(range(10)), [RecordId(1, 0)] * 10)
    with pytest.raises(BTreeError, match="needs"):
        encode_node(node, 64)