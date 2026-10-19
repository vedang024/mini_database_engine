import pytest

from minidb.config import VALID_EVICTION_POLICIES
from minidb.storage.eviction import (
    POLICIES,
    ClockPolicy,
    FIFOPolicy,
    LRUPolicy,
    make_policy,
)

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

def loaded_with(policy, *ids: int):
    for page_id in ids:
        policy.on_load(page_id)
    return policy


# ---- LRU ------------------------------------------------------------------
def test_lru_evicts_least_recently_used():
    policy = loaded_with(LRUPolicy(), 1, 2, 3)
    policy.on_access(1)  # order is now 2, 3, 1
    assert policy.victim(always) == 2


def test_lru_without_accesses_behaves_like_fifo():
    assert loaded_with(LRUPolicy(), 5, 3, 9).victim(always) == 5


def test_lru_skips_unevictable_pages_and_can_find_none():
    policy = loaded_with(LRUPolicy(), 1, 2, 3)
    assert policy.victim(lambda pid: pid != 1) == 2
    assert policy.victim(lambda pid: False) is None


def test_lru_remove_drops_a_page():
    policy = loaded_with(LRUPolicy(), 1, 2)
    policy.remove(1)
    assert policy.victim(always) == 2
    policy.remove(99)  # unknown page: harmless


# ---- Clock ----------------------------------------------------------------
def test_clock_gives_referenced_page_a_second_chance():
    policy = loaded_with(ClockPolicy(), 1, 2, 3)
    policy.on_access(1)
    assert policy.victim(always) == 2  # FIFO would have chosen 1


def test_clock_evicts_first_unreferenced_page_at_the_hand():
    assert loaded_with(ClockPolicy(), 1, 2, 3).victim(always) == 1


def test_clock_all_referenced_still_finds_a_victim():
    policy = loaded_with(ClockPolicy(), 1, 2)
    policy.on_access(1)
    policy.on_access(2)
    assert policy.victim(always) == 1  # first lap clears the bits, second lap evicts


def test_clock_skips_unevictable_pages_and_can_find_none():
    policy = loaded_with(ClockPolicy(), 1, 2, 3)
    assert policy.victim(lambda pid: pid == 3) == 3
    assert policy.victim(lambda pid: False) is None  # must terminate


def test_clock_remove_reuses_slots_and_handles_an_empty_ring():
    policy = loaded_with(ClockPolicy(), 1, 2)
    policy.remove(1)
    assert policy.victim(always) == 2
    policy.remove(2)
    assert policy.victim(always) is None  # nothing left
    policy.remove(99)                     # unknown page: harmless
    policy.on_load(7)
    assert policy.victim(always) == 7


# ---- factory ----------------------------------------------------------------
@pytest.mark.parametrize(
    "name,cls", [("fifo", FIFOPolicy), ("lru", LRUPolicy), ("clock", ClockPolicy)]
)
def test_make_policy_builds_each_named_policy(name, cls):
    assert isinstance(make_policy(name), cls)
    assert isinstance(make_policy(name.upper()), cls)


def test_make_policy_rejects_unknown_name():
    with pytest.raises(ValueError, match="mru"):
        make_policy("mru")


def test_policy_names_match_config():
    assert set(POLICIES) == VALID_EVICTION_POLICIES  # guards the duplicated list