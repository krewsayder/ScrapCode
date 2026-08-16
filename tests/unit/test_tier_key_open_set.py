"""The tier key set is OPEN, and the historical keys are FROZEN (Mythic 3).

WHY-NEW-FILE: tests/unit/test_tier_key_open_set.py
  CLOSEST-EXISTING: bot/tests/test_tracker_tiebreak.py
  EXTENSION-COST: that module's universe is `try_insert`'s ordering contract —
    a list of entries carrying `damage` / `completed_on` / `hero_details`, with
    no `rarity` or `set` field anywhere in it, and a docstring that names the
    tie-break sort as the reason it exists. Hosting these would put a parser's
    total-function claim inside a comparator's regression pin, and `try_insert`
    is explicitly on its way out (it is retained only for the JSON rollback
    impl), so the host is scheduled to be deleted out from under them.
  PARALLEL-RATIONALE: different observable and no shared fixture. This is about
    WHICH ENTRIES SURVIVE INGEST AT ALL; that is about how survivors are
    ordered once they have.

WHY A PROPERTY AND NOT ONLY EXAMPLES. The defect was an enumeration: both
branches of `get_tier_key` listed the tiers that existed the day they were
written (`Mythic` 0-1, `Legendary` 0-4) and silently dropped everything past
the end. An example suite is the same mistake in the test file — it would pin
`Mythic_2` and go quiet again on Mythic 4. The openness claim is quantified
over the index, so it fails for the NEXT tier too, not just this one.

The frozen half must stay examples. `Legendary_0` and bare `Mythic` are keys
already written to `battle_hits` / `bomb_hits` rows and to
`live_leaderboards.messages`, so they are facts about data at rest, not
consequences of a rule — a derivation that "tidied" the per-rarity skew would
satisfy any property phrased over the rule while orphaning every historical
row. They are enumerated here on purpose.

DECLARED UNIVERSE. `get_tier_key` is total: every entry lands in exactly one
of three buckets, and the file asserts all three, so a fix that opened the set
by also accepting garbage fails here.

    accepted   — a tracked rarity with a non-negative integer `set`
    rejected   — untracked rarity, negative, non-integer, absent
    frozen     — the seven keys that already exist in the database
"""
import os

import pytest

hypothesis = pytest.importorskip(
    "hypothesis",
    reason="hypothesis is not installed — DISTILL pins it into requirements.txt",
)

import hypothesis.strategies as st  # noqa: E402
from hypothesis import given, settings  # noqa: E402

# `config.py` casts UPDATE_CHANNEL_ID / REPLAY_INDEX_CHANNEL_ID with
# `int(os.getenv(...))` at import time and raises TypeError when either is
# unset. `bot.tracker` does not import config, but the TIER_CHOICES coherence
# test below does. Precedent: tests/unit/test_auto_update_cycle_containment.py.
os.environ.setdefault("UPDATE_CHANNEL_ID", "0")
os.environ.setdefault("REPLAY_INDEX_CHANNEL_ID", "0")
os.environ.setdefault("SCRAPCODE_REPO_BACKEND", "json")

from bot.tracker import TRACKED_RARITIES, get_tier_key  # noqa: E402

_SETTINGS = settings(max_examples=100, deadline=None)


def _entry(rarity, tier_set):
    """The two fields `get_tier_key` reads, and nothing else.

    Deliberately not a full Tacticus entry: a parser that started consulting
    `damageType` or `damageDealt` to decide a tier key would be a routing bug
    wearing a parser's name, and this shape makes that a TypeError here rather
    than a silent pass.
    """
    return {"rarity": rarity, "set": tier_set}


def _expected_key(rarity: str, index: int) -> str:
    """The storage convention, stated once.

    The skew is real and per-rarity: `Mythic` index 0 stores BARE, every other
    index of either rarity stores suffixed. This helper is the only place that
    knows it.
    """
    if rarity == "Mythic" and index == 0:
        return "Mythic"
    return f"{rarity}_{index}"


# ===========================================================================
# Open — the regression. Mythic 3 is `set=2`, and there will be a Mythic 4.
# ===========================================================================

@given(
    rarity=st.sampled_from(sorted(TRACKED_RARITIES)),
    index=st.integers(min_value=0, max_value=64),
)
@_SETTINGS
def test_every_non_negative_index_of_a_tracked_rarity_is_ingested(rarity, index):
    """No tracked rarity has an upper bound on `set`.

    This is the whole bug: `{"rarity": "Mythic", "set": 2}` returned None, and
    `process_api_response` dropped it with a bare `continue`. The guildRaid
    endpoint serves a rolling window, so each dropped hour was permanent.

    Quantified over BOTH rarities because the identical enumeration existed one
    rarity over (`0 <= tier <= 4` capped Legendary at `Legendary_4`). Fixing
    only the rarity that happened to break re-arms the same defect for the
    next Legendary tier.
    """
    key = get_tier_key(_entry(rarity, index))

    assert key == _expected_key(rarity, index), (
        f"a tracked rarity at index {index} did not produce its storage key: "
        f"{key!r} (expected {_expected_key(rarity, index)!r})"
    )


@given(
    rarity=st.sampled_from(sorted(TRACKED_RARITIES)),
    index=st.integers(min_value=0, max_value=64),
)
@_SETTINGS
def test_the_api_string_form_of_set_is_ingested_identically(rarity, index):
    """`set` arriving as a JSON string is the same tier as the int.

    `int(entry.get("set"))` accepted `"2"` before this change and must keep
    accepting it. An implementation that reached for `isinstance(x, int)` while
    generalising the bounds would pass every property above and silently
    resume dropping hits the day the vendor quotes the field.
    """
    assert get_tier_key(_entry(rarity, str(index))) == _expected_key(rarity, index)


# ===========================================================================
# Frozen — keys that already exist in rows nobody may orphan.
# ===========================================================================

@pytest.mark.parametrize(
    ("rarity", "tier_set", "expected"),
    [
        ("Legendary", 0, "Legendary_0"),
        ("Legendary", 1, "Legendary_1"),
        ("Legendary", 2, "Legendary_2"),
        ("Legendary", 3, "Legendary_3"),
        ("Legendary", 4, "Legendary_4"),
        ("Mythic",    0, "Mythic"),
        ("Mythic",    1, "Mythic_1"),
        ("Mythic",    2, "Mythic_2"),
    ],
)
def test_the_stored_key_of_every_shipped_tier_is_unchanged(rarity, tier_set, expected):
    """The seven pre-existing keys still map exactly where they always did.

    Enumerated rather than derived: these are the strings sitting in
    `battle_hits.tier_key`, `bomb_hits.tier_key` and the
    `live_leaderboards.messages` dict of every server running today. A
    "cleanup" that made `Mythic` index 0 store as `Mythic_0` for symmetry with
    Legendary would read as an improvement and would orphan every Mythic row
    ever written, plus silently strand the live Discord message keyed under
    the old value in a channel members are watching.

    `Mythic_2` rides along as the eighth: same rule, newly reachable.
    """
    assert get_tier_key(_entry(rarity, tier_set)) == expected


# ===========================================================================
# Rejected — opening the index must not open anything else.
# ===========================================================================

@pytest.mark.parametrize(
    ("entry", "why"),
    [
        ({"rarity": "Epic",  "set": 0},    "untracked rarity — a product decision, not a parser fix"),
        ({"rarity": "Rare",  "set": 3},    "untracked rarity at a valid index"),
        ({"rarity": None,    "set": 0},    "absent rarity"),
        ({"rarity": "Mythic", "set": -1},  "negative index would store as 'Mythic_-1'"),
        ({"rarity": "Legendary", "set": -7}, "negative index, other rarity"),
        ({"rarity": "Mythic", "set": None}, "absent set"),
        ({"rarity": "Mythic", "set": "x"},  "unparseable set"),
        ({"rarity": "Mythic"},              "no set field at all"),
        ({},                                "empty entry"),
    ],
)
def test_what_stays_out(entry, why):
    """Generalising the index must not generalise the rarity or the bound.

    Two distinct traps, one assertion. Unbounding `rarity` would change what
    the leaderboard MEANS — low-rarity hits arrive in far greater volume and
    would be stored forever. Dropping the LOWER bound is the original defect
    inverted: `set=-1` parses as an int perfectly well and would write a row
    under a name no picker offers and no label rule produces, which is data
    lost to invisibility rather than to a `continue`.
    """
    assert get_tier_key(entry) is None, f"{why}: {entry!r} was ingested"


# ===========================================================================
# Coherence — ingest and the picker have to agree, or nothing renders.
# ===========================================================================

def test_every_offered_tier_is_a_key_ingest_can_actually_produce():
    """`TIER_CHOICES` and `get_tier_key` are two halves of one contract.

    Fixing the parser alone stores Mythic 3 hits that no command can display;
    adding the picker entry alone offers a tier that is never written. This is
    the assertion that fails if a future tier lands on one side only.

    The labels are pinned in the same breath because `bot/cogs/replay_cog.py`
    stores `tier.name` — the DISPLAY string — while everything else keys off
    `tier.value`. Renaming a label silently drops historical replays out of
    `/get_replay` while leaving them in the database, so the label is as much
    a stored key as the value is.
    """
    from config import TIER_CHOICES

    expected = [
        (f"{rarity} {index + 1}", _expected_key(rarity, index))
        for rarity, count in (("Legendary", 5), ("Mythic", 3))
        for index in range(count)
    ]

    assert [(choice.name, choice.value) for choice in TIER_CHOICES] == expected, (
        "the tier picker drifted from the ingest convention — every value must "
        "be a key get_tier_key produces, and every label must read one ahead "
        "of its index"
    )

    for choice in TIER_CHOICES:
        rarity = choice.value.split("_")[0]
        index = int(choice.value.split("_")[1]) if "_" in choice.value else 0
        assert get_tier_key(_entry(rarity, index)) == choice.value, (
            f"the picker offers {choice.value!r} but ingest never writes it"
        )
