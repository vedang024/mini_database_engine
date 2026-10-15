from minidb.storage.eviction import FIFOPolicy


def always(_page_id: int) -> bool:
    return True


def loaded(*ids: int) -> FIFOPolicy:
    policy = FIFOPolicy()
    for page_id in ids:
        policy.on_load(page_id)
    return policy


def test_fifo_victim_is_the_oldest_loaded():
    assert loaded(5, 3, 9).victim(always) == 5


def test_fifo_ignores_accesses():
    policy = loaded(1, 2)
    policy.on_access(1)
    assert policy.victim(always) == 1


def test_fifo_skips_unevictable_pages_and_can_find_none():
    policy = loaded(1, 2, 3)
    assert policy.victim(lambda pid: pid != 1) == 2
    assert policy.victim(lambda pid: False) is None


def test_remove_drops_a_page_from_consideration():
    policy = loaded(1, 2)
    policy.remove(1)
    assert policy.victim(always) == 2
    policy.remove(99)  # removing an unknown page is harmless