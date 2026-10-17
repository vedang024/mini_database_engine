import random

import pytest

from minidb.index.btree import META, META_MAGIC, BPlusTree, TreeStats
from minidb.index.errors import (
    BTreeError,
    CorruptIndexError,
    DuplicateKeyError,
    InvalidKeyError,
)
from minidb.index.node import InternalNode
from minidb.storage.disk_manager import DiskManager
from minidb.storage.record_id import RecordId

PAGE = 512


def rid(k: int) -> RecordId:
    return RecordId(1 + k // 100, k % 100)


@pytest.fixture
def make_tree(tmp_path):
    """Open (or reopen) a tree in tmp_path. Pass max_keys explicitly when creating one."""
    opened = []

    def factory(name="t.idx", **kwargs):
        kwargs.setdefault("page_size", PAGE)
        kwargs.setdefault("pool_pages", 8)
        tree = BPlusTree(tmp_path / name, **kwargs)
        opened.append(tree)
        return tree

    yield factory
    for tree in opened:
        tree.close()  # close() is idempotent


def build(tree, keys):
    for k in keys:
        tree.insert(k, rid(k))


def test_empty_tree(make_tree):
    tree = make_tree(max_keys=3)
    assert tree.search(1) is None
    assert list(tree.range_scan()) == []
    assert tree.render() == "level 0: []"
    assert tree.check() == TreeStats(height=1, node_count=1, leaf_count=1, key_count=0)


def test_insert_search_roundtrip(make_tree):
    tree = make_tree(max_keys=3)
    build(tree, [5, 1, 3])
    assert tree.search(3) == rid(3)
    assert tree.search(4) is None


def test_duplicate_rejected_and_tree_unchanged(make_tree):
    tree = make_tree(max_keys=3)
    build(tree, range(10))
    before = tree.render(show_pages=True)
    with pytest.raises(DuplicateKeyError):
        tree.insert(4, rid(99))
    assert tree.search(4) == rid(4)            # the original rid survived
    assert tree.render(show_pages=True) == before


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param("5", id="str"),
        pytest.param(1.5, id="float"),
        pytest.param(True, id="bool"),
        pytest.param(2**63, id="too-big"),
        pytest.param(None, id="none"),
    ],
)
def test_invalid_keys_rejected(make_tree, bad):
    tree = make_tree(max_keys=3)
    with pytest.raises(InvalidKeyError):
        tree.insert(bad, rid(1))
    with pytest.raises(InvalidKeyError):
        tree.search(bad)


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(RecordId(1, 70000), id="slot-too-big"),
        pytest.param(RecordId(0, 0), id="page-zero"),
        pytest.param("x", id="not-a-rid"),
    ],
)
def test_invalid_rids_rejected(make_tree, bad):
    tree = make_tree(max_keys=3)
    with pytest.raises(BTreeError):
        tree.insert(1, bad)
    assert tree.search(1) is None


def test_leaf_split_creates_a_root(make_tree):
    tree = make_tree(max_keys=3)
    build(tree, [10, 20, 30])
    assert tree.render() == "level 0: [10 | 20 | 30]"
    tree.insert(40, rid(40))
    # 30 is COPIED up as the separator: it is still in the right leaf.
    assert tree.render() == "level 0: [30]\nlevel 1: [10 | 20] -> [30 | 40]"
    assert tree.check().height == 2


def test_the_example_tree_from_the_lesson(make_tree):
    tree = make_tree(max_keys=3)
    build(tree, [10, 20, 30, 40, 60, 70])
    assert tree.render() == (
        "level 0: [30 | 60]\n"
        "level 1: [10 | 20] -> [30 | 40] -> [60 | 70]"
    )


def test_internal_split_cascade_and_new_root(make_tree):
    tree = make_tree(max_keys=3)
    build(tree, [10, 20, 30, 40, 60, 70, 50, 80, 90, 100, 110])
    assert tree.render(show_pages=True) == (
        "level 0: p9[80]\n"
        "level 1: p4[30 | 60]  p8[100]\n"
        "level 2: p2[10 | 20] -> p3[30 | 40 | 50] -> p5[60 | 70] -> p6[80 | 90] -> p7[100 | 110]"
    )
    assert tree.root_page_id == 9
    assert tree.check() == TreeStats(height=3, node_count=8, leaf_count=5, key_count=11)
    assert tree.search_path(70) == [9, 4, 5]


def keys_in(order, n, rng):
    keys = list(range(n))
    if order == "descending":
        keys.reverse()
    elif order == "shuffled":
        rng.shuffle(keys)
    return keys


@pytest.mark.parametrize("max_keys", [3, 4, 5])
@pytest.mark.parametrize("order", ["ascending", "descending", "shuffled"])
def test_invariants_hold_after_every_insert(make_tree, order, max_keys):
    tree = make_tree(max_keys=max_keys)
    keys = keys_in(order, 120, random.Random(5))
    for count, k in enumerate(keys, 1):
        tree.insert(k, rid(k))
        assert tree.check().key_count == count   # sorted, balanced, half full, chain intact
    assert [k for k, _ in tree.range_scan()] == sorted(keys)
    assert all(tree.search(k) == rid(k) for k in keys)
    assert tree.search(-1) is None and tree.search(120) is None


@pytest.mark.parametrize(
    "low,high,expected",
    [
        (None, None, list(range(0, 100, 10))),
        (25, 55, [30, 40, 50]),
        (30, 50, [30, 40, 50]),       # both ends are inclusive
        (95, None, []),
        (None, 5, [0]),
        (50, 40, []),
        (-100, 1000, list(range(0, 100, 10))),
    ],
)
def test_range_scan_bounds(make_tree, low, high, expected):
    tree = make_tree(max_keys=3)
    keys = list(range(0, 100, 10))
    random.Random(2).shuffle(keys)
    build(tree, keys)
    assert [k for k, _ in tree.range_scan(low, high)] == expected


def test_range_scan_matches_a_model_across_many_leaves(make_tree):
    rng = random.Random(9)
    tree = make_tree(max_keys=4)
    keys = rng.sample(range(10_000), 400)
    build(tree, keys)
    ordered = sorted(keys)
    for _ in range(50):
        low, high = sorted(rng.sample(range(-100, 10_100), 2))
        assert [k for k, _ in tree.range_scan(low, high)] == [k for k in ordered if low <= k <= high]


def test_height_grows_by_at_most_one_per_insert(make_tree):
    tree = make_tree(max_keys=3)
    heights = [tree.check().height]
    for k in range(1, 200):
        tree.insert(k, rid(k))
        heights.append(tree.check().height)
    steps = {b - a for a, b in zip(heights, heights[1:])}
    assert steps <= {0, 1}
    assert heights[-1] >= 4


def test_persistence_after_reopen(make_tree):
    keys = random.Random(1).sample(range(5000), 300)
    tree = make_tree(max_keys=4)
    build(tree, keys)
    shape, stats = tree.render(show_pages=True), tree.check()
    tree.close()

    again = make_tree()                       # max_keys is read back from the file
    assert again.max_keys == 4
    assert again.render(show_pages=True) == shape
    assert again.check() == stats
    assert all(again.search(k) == rid(k) for k in keys)
    again.insert(99_999, rid(999))
    assert again.search(99_999) == rid(999)


def test_reopen_with_a_different_max_keys_is_rejected(make_tree):
    make_tree(max_keys=4).close()
    with pytest.raises(BTreeError, match="max_keys"):
        make_tree(max_keys=5)
    assert make_tree(max_keys=4).max_keys == 4


def test_pool_of_one_frame_proves_no_pin_leaks(make_tree):
    tree = make_tree(pool_pages=1, max_keys=3)
    keys = list(range(60))
    random.Random(4).shuffle(keys)
    build(tree, keys)
    assert tree.check().key_count == 60
    assert [k for k, _ in tree.range_scan()] == sorted(keys)
    assert all(tree.search(k) == rid(k) for k in keys)


def test_scan_does_not_hold_a_pin_between_yields(make_tree):
    tree = make_tree(pool_pages=1, max_keys=3)
    build(tree, range(30))
    it = tree.range_scan()
    next(it)
    assert tree.search(29) == rid(29)         # needs the only frame
    assert len(list(it)) == 29


@pytest.mark.parametrize("bad", [2, 1000])
def test_invalid_max_keys_rejected_before_creating_a_file(tmp_path, bad):
    path = tmp_path / "x.idx"
    with pytest.raises(ValueError):
        BPlusTree(path, page_size=PAGE, max_keys=bad)
    assert not path.exists()


def test_non_index_file_rejected_and_file_released(tmp_path):
    path = tmp_path / "n.idx"
    with DiskManager(path, PAGE) as dm:
        dm.allocate_page()                    # page 1 exists but is all zeros
    with pytest.raises(CorruptIndexError, match="bad magic"):
        BPlusTree(path, page_size=PAGE)
    path.unlink()                             # on Windows this fails if the handle leaked


def test_corrupt_root_pointer_rejected(make_tree, tmp_path):
    tree = make_tree(max_keys=3)
    tree.insert(1, rid(1))
    tree.close()
    with DiskManager(tmp_path / "t.idx", PAGE) as dm:
        page = dm.fetch_page(1)
        page.write(0, META.pack(META_MAGIC, 999, 3))
        dm.flush_page(page)
    with pytest.raises(CorruptIndexError, match="root"):
        make_tree()


def test_lookup_touches_exactly_height_pages_on_a_cold_cache(make_tree):
    tree = make_tree(max_keys=3)
    build(tree, [k * 2 for k in range(50)])
    height = tree.check().height
    tree.close()

    cold = make_tree(pool_pages=8)
    before = cold.pool_stats.misses           # the meta page, read while opening
    assert cold.search(40) == rid(40)
    assert cold.pool_stats.misses - before == height


def test_sequential_inserts_leave_leaves_half_full(make_tree):
    tree = make_tree(page_size=4096, pool_pages=16)   # max_keys = 292
    build(tree, range(1, 1001))
    stats = tree.check()
    assert tree.max_keys == 292
    # Every left leaf is frozen at 146 keys; the last leaf holds the other 270.
    assert (stats.height, stats.leaf_count, stats.key_count) == (2, 6, 1000)


def test_random_operations_match_a_dict_model(make_tree):
    rng = random.Random(21)
    tree = make_tree(max_keys=4, pool_pages=4)
    model: dict[int, RecordId] = {}
    for i in range(2000):
        k = rng.randrange(600)
        if k in model:
            with pytest.raises(DuplicateKeyError):
                tree.insert(k, rid(k + 1))
            assert tree.search(k) == model[k]
        else:
            tree.insert(k, rid(k))
            model[k] = rid(k)
        if i % 100 == 0:
            tree.check()
    assert dict(tree.range_scan()) == model
    tree.check()


def test_check_detects_unsorted_keys(make_tree):
    tree = make_tree(max_keys=3)
    build(tree, [1, 2, 3])
    leaf = tree._read(tree.root_page_id)
    leaf.keys.reverse()
    leaf.rids.reverse()
    tree._write(leaf)
    with pytest.raises(CorruptIndexError, match="increasing"):
        tree.check()


def test_check_detects_a_broken_leaf_chain(make_tree):
    tree = make_tree(max_keys=3)
    build(tree, range(10))
    node = tree._read(tree.root_page_id)
    while isinstance(node, InternalNode):
        node = tree._read(node.children[0])
    node.next_leaf = 0                        # cut the chain after the first leaf
    tree._write(node)
    with pytest.raises(CorruptIndexError, match="chain"):
        tree.check()