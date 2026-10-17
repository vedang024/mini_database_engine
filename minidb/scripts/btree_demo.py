"""Phase 9 demo: watch a B+ tree grow and split, then see what an index buys."""

from __future__ import annotations

import random
import tempfile
import textwrap
from pathlib import Path

from minidb.index.btree import BPlusTree
from minidb.index.errors import DuplicateKeyError
from minidb.storage.record_id import RecordId

SEQUENCE = [10, 20, 30, 40, 60, 70, 50, 80, 90, 100, 110]


def small_tree(tmp: Path) -> None:
    print("== building a tree where a node holds at most 3 keys")
    with BPlusTree(tmp / "small.idx", page_size=512, max_keys=3) as tree:
        last = tree.check()
        for key in SEQUENCE:
            tree.insert(key, RecordId(1, key // 10))
            now = tree.check()
            if now.node_count != last.node_count:  # a split happened
                print(
                    f"\ninsert {key}: split! nodes {last.node_count} -> {now.node_count}, "
                    f"height {last.height} -> {now.height}"
                )
                print(textwrap.indent(tree.render(show_pages=True), "   "))
                last = now

        print("\n== search walks one page per level")
        for key in (70, 75):
            path = " -> ".join(f"p{p}" for p in tree.search_path(key))
            print(f"search {key}: {path}  found {tree.search(key)}")

        print("\n== range scan 35..90 follows the leaf chain")
        print([key for key, _ in tree.range_scan(35, 90)])

        print("\n== duplicate keys are rejected")
        try:
            tree.insert(70, RecordId(1, 7))
        except DuplicateKeyError:
            print("insert 70 again -> DuplicateKeyError")


def rid(key: int) -> RecordId:
    return RecordId(1 + key // 100, key % 100)


def at_scale(tmp: Path) -> None:
    n = 20_000
    shuffled = list(range(1, n + 1))
    random.Random(1).shuffle(shuffled)
    print(f"\n== scale: {n:,} keys, 4096-byte pages (takes a few seconds)")
    for label, keys in (("ascending", sorted(shuffled)), ("shuffled", shuffled)):
        with BPlusTree(tmp / f"{label}.idx", 4096) as tree:
            for key in keys:
                tree.insert(key, rid(key))
            s = tree.check()
            fill = s.key_count / (s.leaf_count * tree.max_keys)
        print(
            f"{label:<10} height={s.height} leaves={s.leaf_count:<4} "
            f"nodes={s.node_count:<4} leaf fill={fill:.1%}"
        )

    path = tmp / "ascending.idx"
    with BPlusTree(path, 4096) as cold:  # a fresh buffer pool: every read is a real miss
        base = cold.pool_stats.misses
        found = cold.search(12_345)
        lookup = cold.pool_stats.misses - base
    with BPlusTree(path, 4096) as cold:
        base = cold.pool_stats.misses
        scanned = sum(1 for _ in cold.range_scan())
        scan = cold.pool_stats.misses - base
    print(f"point lookup of key 12345: {lookup} page reads (found {found})")
    print(f"scan of all {scanned} keys: {scan} page reads")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        small_tree(Path(tmp))
        at_scale(Path(tmp))


if __name__ == "__main__":
    main()