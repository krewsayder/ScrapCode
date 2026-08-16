"""Slice 02 — the registry, and Mythic 3 on every view command. Implements
`acceptance/slice-02-registry-and-display.feature`.

Covers US-004 (the registry, as the precursor commit) and US-003 (the value
story that makes the slice releasable).

The user-visible outcome of this slice is that NOTHING CHANGES. The same eight
tiers, the same labels, the same order — which is exactly how the derivation is
known to be right, and why `test_the_derived_list_reproduces_the_literal_list`
is the load-bearing assertion rather than any of the rendering ones.

Driving ports:
  * `config.TIER_CHOICES`                       — what every slash command offers
  * `bot.cogs.view_cog` `/view_*_leaderboard`   — the three view commands
  * `bot.cogs.replay_cog` `/get_replay`         — the label round trip
"""
from __future__ import annotations

import os

import pytest

from tier_types import (
    MYTHIC_3_KEY,
    MYTHIC_3_LABEL,
    PRE_FEATURE_TIERS,
)
from tier_types import GUILD_WB, PROD_SERVER_ID, SEASON  # noqa: F401

RED = pytest.mark.skipif(
    os.getenv("SCRAPCODE_RED_GATE") != "1",
    reason="RED scaffold — enable one at a time in DELIVER",
)

VIEW_COMMANDS = [
    "/view_leaderboard",
    "/view_bomb_leaderboard",
    "/view_cluster_leaderboard",
]


# ===========================================================================
# US-004 — one place that knows what a tier is (precursor commit)
# ===========================================================================

@RED
def test_the_registry_owns_all_four_rules(make_entry):
    """AC-004.1. Parse, label, order, override — all four, exercised.

    THIS TEST WAS WRONG WHEN FIRST WRITTEN, and the pre-DELIVER gate caught it.
    It asserted `callable(tiers.parse)` and friends, which is true of a scaffold
    whose every body raises — so it PASSED against a module that does nothing.
    A test that cannot fail is worse than a missing one: it occupies the space
    where the real assertion would go and reports success.

    Each rule is now EXERCISED. One value each is enough here — this is the
    structural claim that all four live in one place, and their behaviour is
    pinned in detail by the tests below.
    """
    from bot import tiers

    assert tiers.parse(make_entry(rarity="Mythic", set_=2)) == MYTHIC_3_KEY
    assert tiers.label(MYTHIC_3_KEY) == MYTHIC_3_LABEL
    assert tiers.order_key("Legendary_0") < tiers.order_key("Mythic")
    assert isinstance(tiers.LABEL_OVERRIDES, dict)


@RED
@pytest.mark.architecture
def test_the_choice_list_is_derived_from_the_registry():
    """AC-004.2. Every offered choice traces back to a registered tier.

    Set equality in BOTH directions. "Every choice is registered" passes
    against a list that silently drops a tier; "every registered tier is
    offered" passes against a list with extras. The failure this feature
    started from was a tier that existed in one place and not the other.
    """
    import config
    from bot import tiers

    offered = {(c.value, c.name) for c in config.TIER_CHOICES}
    registered = {(t.value, t.name) for t in tiers.registered()}
    assert offered == registered


@RED
@pytest.mark.architecture
def test_the_ingest_parser_defers_to_the_registry(make_entry):
    """AC-004.3. `tracker.get_tier_key` and `tiers.parse` cannot disagree.

    Asserted over the whole input domain the two share rather than on one
    value. Two functions that agree about `Mythic_2` and disagree about
    `Legendary_5` is precisely the state this feature exists to eliminate —
    it is the original bug in miniature, with the disagreement moved inside
    the codebase instead of between two files.
    """
    from bot import tiers
    from bot.tracker import get_tier_key

    for rarity in ("Legendary", "Mythic", "Epic"):
        for index in (0, 1, 2, 3, 4, 5, 9, -1):
            entry = make_entry(rarity=rarity, set_=index)
            assert get_tier_key(entry) == tiers.parse(entry), (
                f"the two rules disagree about {rarity} {index}"
            )


@RED
def test_an_override_wins_over_the_derived_label(monkeypatch):
    """AC-004.5. The escape hatch for a tier the game names irregularly.

    Empty today, because every current key derives correctly. It exists so a
    future "Mythic Prime" is a one-line data edit rather than a change to the
    shape of the rule — the difference between a config change and a release.
    """
    from bot import tiers

    monkeypatch.setitem(tiers.LABEL_OVERRIDES, "Mythic_4", "Mythic Prime")
    assert tiers.label("Mythic_4") == "Mythic Prime"


@RED
def test_tiers_sort_by_rarity_then_index():
    """AC-004.6. Read by three consumers, so pinned once here.

    The replay grouping, the live-board message order and the picker order all
    read this. If they diverged, a tier would appear in one order on the pinned
    board and another in the dropdown, and nothing would say which was right.
    """
    from bot import tiers

    keys = [t.value for t in tiers.registered()]
    assert keys == sorted(keys, key=tiers.order_key)
    assert keys.index("Legendary_4") < keys.index("Mythic")
    assert keys.index("Mythic") < keys.index("Mythic_1") < keys.index(MYTHIC_3_KEY)


# ===========================================================================
# US-003 — read a Mythic 3 leaderboard on demand
# ===========================================================================

@RED
@pytest.mark.kpi
def test_the_derived_list_reproduces_the_literal_list_and_adds_the_new_tier():
    """AC-003.2, WIDENED TO EIGHT ENTRIES.

    The whole hypothesis of the slice, expressed as one assertion: labels can
    be derived from stored keys without touching a single stored row.

    Asserted against `PRE_FEATURE_TIERS` — a LITERAL copied from `config.py` as
    it stood before this feature — not against a re-derivation. A rule that is
    wrong the same way twice would agree with itself and pass.

    WHY EIGHT AND NOT SEVEN. The AC text as written in DISCUSS pins the first
    seven; the Slice 02 brief says eight. The eighth is the entry Slice 02
    DELETES and replaces — the operator has been using it since Slice 01
    shipped — which makes it the only entry with a live regression surface, and
    it was the one outside the pin. See devops/upstream-changes.md item 2.

    ORDER IS PART OF THE ASSERTION. `replay_cog.tier_order` and the live-board
    message order both read this sequence.
    """
    import config

    actual = [(c.value, c.name) for c in config.TIER_CHOICES]
    assert actual[:7] == list(PRE_FEATURE_TIERS)
    assert actual[7] == (MYTHIC_3_KEY, MYTHIC_3_LABEL)
    assert len(actual) == 8


@RED
@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("Legendary_0", "Legendary 1"),
        ("Legendary_4", "Legendary 5"),
        ("Mythic", "Mythic 1"),
        ("Mythic_1", "Mythic 2"),
        ("Mythic_2", "Mythic 3"),
    ],
)
def test_a_label_derives_from_the_stored_key(key, expected):
    """AC-003.6. The off-by-one is FROZEN, in both rarities.

    `Mythic_1` displays as "Mythic 2" and `Legendary_0` as "Legendary 1". It is
    wrong, it has always been wrong, and correcting it would rewrite the live
    board's message keys and orphan every historical replay row — which is
    stored under the LABEL, not the key.

    Freezing a known-wrong mapping is the decision (ADR-009 D4); this is where
    it is enforced so nobody tidies it up later without reading the ADR.
    """
    from bot import tiers

    assert tiers.label(key) == expected


@RED
@pytest.mark.real_io
@pytest.mark.driving_port
@pytest.mark.parametrize("command", VIEW_COMMANDS)
def test_every_view_command_offers_and_renders_the_new_tier(
    sqlite_repo, seed_hits, command
):
    """AC-003.3 + AC-003.5.

    Parametrized across all three commands because fixing one surface and
    missing the others is the realistic failure, not a hypothetical one — they
    are three separate handlers reading the same choice list.
    """
    raise AssertionError("RED scaffold — drive the command, assert title + rows")


@RED
@pytest.mark.driving_port
@pytest.mark.error
def test_a_tier_with_no_hits_gives_an_empty_board_not_an_error(sqlite_repo):
    """AC-003.4. An empty board is a legitimate answer; an error is not.

    The distinction matters because it is the one the operator has to make:
    "nobody cleared this tier" and "the bot cannot show me this tier" look the
    same from the outside, and this feature exists to separate them.
    """
    raise AssertionError("RED scaffold — no rows seeded, assert the no-data response")


@RED
@pytest.mark.real_io
@pytest.mark.error
def test_a_stored_tier_nobody_named_still_renders_under_its_own_name(seed_hits):
    """AC-003.7 — ADR-009 D2 applied to the read path.

    A row is never hidden because its name could not be worked out. Hiding it
    is what this feature's original defect looks like from the read side: data
    present, invisible, and nothing anywhere saying so.
    """
    from bot import tiers

    seed_hits("Mythic_9")
    assert tiers.tier_for("Mythic_9").name == "Mythic_9"


@RED
@pytest.mark.error
def test_more_tiers_than_a_command_can_offer_refuses_at_startup(monkeypatch):
    """AC-003.6's cap half — loud refusal, not a warning.

    Discord REJECTS an oversized command sync rather than failing locally. The
    result would be the OLD choice list live in front of NEW code, with nothing
    anywhere saying the two disagree — which is the failure shape of the whole
    feature, arriving through the deployment path instead of the parser.

    Synthetic by necessity (the game has not shipped 25 tiers) and structural
    in value: it guards the failure mode Slice 04 exists to make impossible.
    """
    raise AssertionError("RED scaffold — seed >25 tiers, assert startup refuses")


@RED
@pytest.mark.real_io
@pytest.mark.driving_port
@pytest.mark.error
def test_replays_recorded_under_the_old_labels_are_still_found(sqlite_repo):
    """The cheapest possible check that labels did not shift.

    Replay rows are keyed by DISPLAY LABEL (`replay_cog.py:208` writes
    `tier.name`) and rendering FILTERS on that label (`replay_cog.py:54`). A
    derivation producing "Mythic I" or "Mythic-1" drops every historical replay
    out of `/get_replay` while leaving the rows in the database — this
    feature's own failure shape, reproduced in a new place on the way out.

    This is also why no Alembic revision is needed: nothing is rewritten, so
    nothing needs migrating.
    """
    raise AssertionError("RED scaffold — seed replays at old labels, assert /get_replay")
