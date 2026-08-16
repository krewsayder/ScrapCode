"""Slice 01 — capture every tier, report what was discarded. Implements
`acceptance/slice-01-capture-and-report.feature`.

Covers US-001 (capture), US-002 (report), US-007 (a new rarity is named, never
adopted). TK-1, TK-2 and TK-5 are measured from the records asserted here.

Driving ports used, per the port-to-port principle:
  * `bot.cogs.tasks_cog.TasksCog.auto_update`     — the hourly loop
  * `bot.cogs.update_cog` `/update_leaderboard`   — manual ingest
  * `bot.tracker.process_api_response`            — the ingest seam the loop calls
Nothing enters through `bot.tiers` internals for the behavioural scenarios; the
registry is reached through the surfaces that call it. The parse-rule scenarios
DO call `bot.tiers.parse` directly, because the rule is itself a driving surface
for `tracker.get_tier_key` and asserting it through a cycle would tell us the
answer for one entry out of hundreds.
"""
from __future__ import annotations

import os

import pytest

from tier_types import (
    MYTHIC_3_KEY,
    MYTHIC_4_KEY,
    PRE_FEATURE_TIERS,
    MalformedSet,
    SkipReason,
    UntrackedRarity,
)
# MODULE level, never inside a function. Three suites ship a bare `conftest`
# module, so `sys.modules["conftest"]` holds whichever was imported LAST. A
# module-level import binds during THIS file's collection, while the right one
# is still installed; a function-level import resolves when the test RUNS, by
# which point another suite's conftest has replaced it. Same hazard recorded in
# guild-key-integrity/test_slice_01_bind_and_report.py:31.
from tier_types import GUILD_WB, PROD_SERVER_ID, SEASON  # noqa: F401

CYCLE_EVENT = "auto_update.cycle"

# RED scaffolds. `skipif` rather than `skip` so the pre-DELIVER gate can
# actually RUN them: a scaffold that is skipped never fails, and a gate that
# classifies "why did this fail" against a suite of skips classifies nothing.
# DELIVER unskips them one at a time by deleting the marker, not by setting the
# variable.
RED = pytest.mark.skipif(
    os.getenv("SCRAPCODE_RED_GATE") != "1",
    reason="RED scaffold — enable one at a time in DELIVER",
)


# ===========================================================================
# US-001 — capture a tier the bot has never seen
# ===========================================================================

@RED
def test_a_new_tier_index_within_a_tracked_rarity_produces_a_key(make_entry):
    """AC-001.1. The single fact the whole feature rests on.

    `rarity="Mythic", set=2` is the payload shape the operator confirmed on
    2026-08-15 — an observed value, not an inferred one. Today this returns
    None and `process_api_response` walks past it with a bare `continue`.
    """
    from bot import tiers

    assert tiers.parse(make_entry(rarity="Mythic", set_=2)) == "Mythic_2"


@RED
@pytest.mark.parametrize(
    ("rarity", "index", "expected"),
    [
        ("Mythic", 0, "Mythic"),
        ("Mythic", 1, "Mythic_1"),
        ("Mythic", 2, "Mythic_2"),
        ("Mythic", 3, "Mythic_3"),
        ("Mythic", 7, "Mythic_7"),
        ("Legendary", 0, "Legendary_0"),
        ("Legendary", 4, "Legendary_4"),
        ("Legendary", 5, "Legendary_5"),
        ("Legendary", 9, "Legendary_9"),
    ],
)
def test_every_tier_index_within_a_tracked_rarity_parses(
    make_entry, rarity, index, expected
):
    """AC-001.2 + AC-001.3.

    Parametrized well beyond the observed value on purpose. A fix that special-
    cases `set == 2` passes the previous test and fails here, and it is the fix
    somebody under time pressure actually writes.

    `Legendary_5` and `Legendary_9` matter as much as the Mythic rows: the
    upper bound is removed SYMMETRICALLY, so the same bug cannot recur one
    rarity over the next time the game extends Legendary.
    """
    from bot import tiers

    assert tiers.parse(make_entry(rarity=rarity, set_=index)) == expected


@RED
@pytest.mark.kpi
@pytest.mark.parametrize("key", [k for k, _ in PRE_FEATURE_TIERS])
def test_every_pre_existing_tier_key_parses_byte_identically(make_entry, key):
    """AC-001.4 — THE REGRESSION PIN.

    Asserted against `PRE_FEATURE_TIERS`, which is a literal copied from
    `config.py` as it stood before this feature, NOT against a re-derivation.
    A derivation that is wrong the same way twice would agree with itself.

    A subtle change here orphans historical rows: a hit stored under a key
    nobody derives any more is a hit nobody can find, which is this feature's
    own defect reproduced on the way out of it.
    """
    from bot import tiers

    rarity, _, suffix = key.partition("_")
    index = int(suffix) if suffix else 0
    assert tiers.parse(make_entry(rarity=rarity, set_=index)) == key


@RED
@pytest.mark.parametrize("rarity", [r.value for r in UntrackedRarity])
def test_a_rarity_outside_the_allow_list_is_never_stored(make_entry, rarity):
    """AC-001.7 / AC-007.1 — the over-generalisation guard.

    Removing the `set` bound fixes a bug. Removing the rarity allow-list would
    change what the leaderboard MEANS, and an ingest parser does not get to
    make that decision as a side effect of a fix. This is the assertion that
    Slice 01 did the first and not the second.
    """
    from bot import tiers

    assert tiers.parse(make_entry(rarity=rarity, set_=0)) is None


@RED
@pytest.mark.error
def test_a_negative_tier_index_is_refused(make_entry):
    """The boundary a partial fix misses.

    `set = -1` parses as an integer perfectly well, so an implementation that
    deleted the upper bound and nothing else returns `"Mythic_-1"` — a row
    written under a name no picker will ever offer and no label rule will ever
    produce. Silently unreachable data is the defect, whichever direction it
    arrives from.
    """
    from bot import tiers

    assert tiers.parse(make_entry(rarity="Mythic", set_=-1)) is None


@RED
@pytest.mark.parametrize("case", list(MalformedSet), ids=lambda c: c.value)
def test_an_unusable_tier_index_is_refused(make_entry, case):
    """AC-001.6's parse half. Every shape `set` arrives broken in."""
    from bot import tiers

    kwargs = {
        MalformedSet.ABSENT: {},
        MalformedSet.NULL: {"set_": None},
        MalformedSet.NON_NUMERIC: {"set_": "two"},
        MalformedSet.NEGATIVE: {"set_": -1},
    }[case]
    assert tiers.parse(make_entry(rarity="Mythic", **kwargs)) is None


@RED
@pytest.mark.real_io
@pytest.mark.driving_port
@pytest.mark.kpi
def test_a_mythic_3_battle_hit_becomes_a_real_row(
    sqlite_repo, registered_guilds, api_response, make_entry
):
    """AC-001.5 — verified by SQL, not by a mock.

    The learning hypothesis of the whole slice: if rows still do not appear
    after the parser is fixed, something DOWNSTREAM is also rejecting them (the
    unique constraint, the key column width, damage-type routing) and the
    feature is bigger than a parser change. The write path was rebuilt during
    the SQLite cutover and has only ever run against seven enumerated keys.
    """
    from bot.tracker import process_api_response

    process_api_response(
        api_response([make_entry(rarity="Mythic", set_=2, damage_type="Battle")]),
        SEASON, PROD_SERVER_ID, GUILD_WB,
    )

    rows = sqlite_repo.get_battle_hits(PROD_SERVER_ID, GUILD_WB, SEASON)
    assert any(r["tier_key"] == MYTHIC_3_KEY for r in rows), (
        "the parser accepted the entry but no row reached battle_hits — the "
        "gate is downstream of get_tier_key"
    )


@RED
@pytest.mark.real_io
@pytest.mark.driving_port
def test_a_mythic_3_bomb_hit_lands_in_the_bomb_table(
    sqlite_repo, registered_guilds, api_response, make_entry
):
    """AC-001.6 — generalising the tier must not disturb damage-type routing.

    Two assertions, not one. "It reached bomb_hits" passes against an
    implementation that writes it to both.
    """
    from bot.tracker import process_api_response

    process_api_response(
        api_response([make_entry(rarity="Mythic", set_=2, damage_type="Bomb")]),
        SEASON, PROD_SERVER_ID, GUILD_WB,
    )

    bomb = sqlite_repo.get_bomb_hits(PROD_SERVER_ID, GUILD_WB, SEASON)
    battle = sqlite_repo.get_battle_hits(PROD_SERVER_ID, GUILD_WB, SEASON)
    assert any(r["tier_key"] == MYTHIC_3_KEY for r in bomb)
    assert not any(r["tier_key"] == MYTHIC_3_KEY for r in battle)


# ===========================================================================
# US-002 — see what ingest threw away
# ===========================================================================

@RED
@pytest.mark.kpi
def test_discards_are_counted_separately_by_reason(
    sqlite_repo, registered_guilds, api_response, make_entry
):
    """AC-002.1. The counts come out under their OWN keys.

    Three untracked-rarity entries and one unusable index. An implementation
    with a single `skipped` integer satisfies "four were discarded" and fails
    here, which is the distinction TK-5 exists to make.
    """
    from bot.tracker import process_api_response

    report = process_api_response(
        api_response([
            make_entry(rarity="Epic", set_=0),
            make_entry(rarity="Epic", set_=1),
            make_entry(rarity="Rare", set_=0),
            make_entry(rarity="Mythic", set_="two"),
        ]),
        SEASON, PROD_SERVER_ID, GUILD_WB,
    )

    counts = report.counts_by_name()
    assert counts[SkipReason.UNTRACKED_RARITY.value] == 3
    assert counts[SkipReason.MALFORMED_SET.value] == 1


@RED
@pytest.mark.kpi
def test_every_reason_is_present_even_at_zero(
    sqlite_repo, registered_guilds, api_response, make_entry
):
    """The emit-zeroes rule (DEVOPS observability contract).

    An absent key is indistinguishable from a counter nobody built — and this
    entire feature exists because something that left no trace was assumed not
    to be happening. It also makes TK-5's equality checkable without a schema
    lookup.
    """
    from bot.tracker import process_api_response

    report = process_api_response(
        api_response([make_entry(rarity="Mythic", set_=0)]),
        SEASON, PROD_SERVER_ID, GUILD_WB,
    )

    assert set(report.counts_by_name()) == {r.value for r in SkipReason}
    assert all(v == 0 for v in report.counts_by_name().values())


@RED
@pytest.mark.kpi
def test_the_number_discarded_equals_the_sum_of_the_reasons(
    sqlite_repo, registered_guilds, api_response, make_entry
):
    """TK-5, as the invariant rather than as a percentage.

    A total that exceeds the sum of the reasons means a path exists that
    discards an entry without naming itself — the original defect in a new
    location. This is the same shape as `_CycleReport`'s existing rule that
    `skip_reasons` is never empty while `guilds_skipped > 0`.

    Deliberately built from a MIXTURE, including entries that are stored: a
    fixture of nothing but discards would pass against an implementation that
    counts every entry as a discard.
    """
    from bot.tracker import process_api_response

    report = process_api_response(
        api_response([
            make_entry(rarity="Mythic", set_=0),
            make_entry(rarity="Mythic", set_=2),
            make_entry(rarity="Epic", set_=0),
            make_entry(rarity="Mythic", set_=None),
            make_entry(rarity="Mythic", set_="two"),
        ]),
        SEASON, PROD_SERVER_ID, GUILD_WB,
    )

    assert report.entries_skipped == sum(report.counts_by_name().values())
    assert report.entries_total == report.entries_written + report.entries_skipped


@RED
@pytest.mark.driving_port
@pytest.mark.kpi
async def test_the_cycle_record_carries_the_counts_and_the_written_tiers(
    sqlite_repo, registered_guilds, update_channel, cycle_events,
    api_response, make_entry,
):
    """AC-002.1 at the cycle grain + TK-2's instrument.

    `tier_keys_written` is the field DEVOPS fixed rather than left open: TK-2
    measures capture latency as `MIN(completed_on)` against the first cycle
    record carrying the key, and no wording of an acceptance criterion recovers
    that after the fact. See devops/upstream-changes.md item 3.

    Asserted against the EVENT, not the report object, because the event is
    what `docs/product/kpi-contracts.yaml` tells an operator to grep. A renamed
    field must break a test before it breaks their query.
    """
    record = cycle_events.latest(CYCLE_EVENT)

    assert MYTHIC_3_KEY in record.tier_keys_written
    assert set(record.entry_skip_counts) == {r.value for r in SkipReason}
    assert record.entries_skipped == sum(record.entry_skip_counts.values())


@RED
@pytest.mark.driving_port
async def test_the_post_names_the_count_and_the_reason(
    sqlite_repo, registered_guilds, update_channel, api_response, make_entry
):
    """AC-002.2. Both halves, in the operator's only reporting surface.

    The persona has no dashboard and no alerting outside this channel, so a
    count written solely to a structured log does not satisfy the AC. And a
    count with no reason is a number nobody can act on.
    """
    text = update_channel.text
    assert "3" in text
    assert SkipReason.UNTRACKED_RARITY.value in text


@RED
@pytest.mark.driving_port
async def test_a_clean_cycle_posts_no_skip_line(
    sqlite_repo, registered_guilds, update_channel, cycle_events,
    api_response, make_entry,
):
    """AC-002.3 — silence must mean clean.

    The most load-bearing scenario in the slice and the easiest to leave out.
    Every other scenario here passes against an implementation that posts a
    warning on every cycle; this is the one that fails it. An operator who
    learns to scroll past a permanent warning will scroll past the real one.

    The zero-counts assertion sits beside it deliberately: silent to the human,
    explicit to the log, is the intended pair.
    """
    assert "⚠️" not in update_channel.text
    assert "skipped" not in update_channel.text.lower()

    record = cycle_events.latest(CYCLE_EVENT)
    assert record.entries_skipped == 0
    assert all(v == 0 for v in record.entry_skip_counts.values())


@RED
@pytest.mark.driving_port
@pytest.mark.error
async def test_a_captured_but_undisplayable_tier_is_reported(
    sqlite_repo, registered_guilds, update_channel, cycle_events,
    api_response, make_entry,
):
    """AC-002.4′ — SUPERSEDES AC-002.4 per ADR-009 D5.

    DISCUSS wrote this as a first-sighting announcement ("🆕 New tier observed
    … first observation only"). Announcing once requires PERSISTED STATE to
    de-duplicate a condition that is self-clearing by construction: once the
    registry lands, a captured tier is immediately displayable, so the
    condition becomes structurally impossible rather than merely resolved. The
    original criterion buys a schema change for a signal that exists only
    during the transition.

    The replacement reports a CONDITION rather than an event — "I am storing
    data you cannot see" — derived per cycle from that cycle's data, and it
    turns itself off.

    STATUS: proposed by DESIGN, restated by DEVOPS, NOT yet ratified by the
    product owner. Four artifacts describe the replacement (this suite, the
    slice brief, ADR-009, the journey); the DISCUSS user-story text still
    carries the original. See distill/upstream-issues.md UI-1.
    """
    record = cycle_events.latest(CYCLE_EVENT)
    assert MYTHIC_4_KEY in record.tier_keys_undisplayable
    assert "captured" in update_channel.text.lower()
    assert MYTHIC_4_KEY in update_channel.text


@RED
@pytest.mark.driving_port
@pytest.mark.error
async def test_the_condition_repeats_while_true_and_stops_when_resolved(
    sqlite_repo, registered_guilds, update_channel, cycle_events,
    api_response, make_entry,
):
    """AC-002.5′ — the standing-condition half.

    Two claims, and the second is the one that matters: the line STOPS. A
    condition that reports itself forever is the alert fatigue the persona
    already names, and an implementation that never clears it would satisfy
    the first assertion alone.
    """
    raise AssertionError("RED scaffold — two cycles, then registry entry added")


@RED
@pytest.mark.error
def test_the_skip_record_carries_no_player_data(
    sqlite_repo, registered_guilds, api_response, make_entry
):
    """AC-002.7 — the report is about shape, not about people.

    Asserted as an absence over the WHOLE record rather than by naming the
    fields we expect, because the failure mode is a field somebody added
    without thinking about it. There is no analytical question about a
    discarded entry worth the exposure.
    """
    from bot.tracker import process_api_response

    report = process_api_response(
        api_response([make_entry(rarity="Epic", set_=0, user_id="tacticus-uid-999")]),
        SEASON, PROD_SERVER_ID, GUILD_WB,
    )

    rendered = repr(report)
    assert "tacticus-uid-999" not in rendered
    assert "Aethana" not in rendered
    assert "12000" not in rendered


# ===========================================================================
# US-007 — a new rarity is reported, never silently adopted
# ===========================================================================

@RED
@pytest.mark.driving_port
def test_an_unrecognised_rarity_is_named_verbatim(
    sqlite_repo, registered_guilds, api_response, make_entry
):
    """AC-007.2. `"Divine"` exactly as it arrived.

    Verbatim rather than classified, so a brand-new rarity is identifiable
    without opening a shell on the VM. The operator's next decision — add it to
    the allow-list, or confirm it is correctly ignored — needs the string.
    """
    from bot.tracker import process_api_response

    report = process_api_response(
        api_response([make_entry(rarity="Divine", set_=0) for _ in range(12)]),
        SEASON, PROD_SERVER_ID, GUILD_WB,
    )

    assert "Divine" in report.unrecognised_rarities
    assert report.counts_by_name()[SkipReason.UNTRACKED_RARITY.value] == 12


@RED
@pytest.mark.driving_port
def test_routine_volume_cannot_bury_a_novel_rarity(
    sqlite_repo, registered_guilds, api_response, make_entry
):
    """AC-007.3 — the rate limit, and why it is structural.

    Four hundred Epic entries and one Divine. `unrecognised_rarities` is a SET
    per cycle, so it cannot repeat within a cycle and nothing has to be reset —
    a counter somebody has to remember to clear is a counter that eventually
    is not cleared.

    The assertion is on the SET, not on the count: the count is expected to be
    401 and is not the thing at risk.
    """
    from bot.tracker import process_api_response

    entries = [make_entry(rarity="Epic", set_=0) for _ in range(400)]
    entries.append(make_entry(rarity="Divine", set_=0))
    report = process_api_response(
        api_response(entries), SEASON, PROD_SERVER_ID, GUILD_WB
    )

    assert report.unrecognised_rarities == {"Epic", "Divine"}


@RED
def test_adding_a_rarity_to_the_allow_list_needs_no_other_change(
    monkeypatch, make_entry
):
    """AC-007.4 — the tier dimension is already general.

    Proves the two decisions are separable: adding a rarity is a one-line
    product decision, and every tier index within it is captured without a
    second edit. If this fails, D1's split between "generalise the index" and
    "keep the rarity list closed" was not actually achieved.
    """
    from bot import tiers

    monkeypatch.setattr(
        tiers, "TRACKED_RARITIES", frozenset(tiers.TRACKED_RARITIES | {"Divine"})
    )
    for index in (0, 1, 2, 5):
        expected = "Divine" if index == 0 else f"Divine_{index}"
        assert tiers.parse(make_entry(rarity="Divine", set_=index)) == expected


# ===========================================================================
# The day-one readable board
# ===========================================================================

@RED
@pytest.mark.real_io
@pytest.mark.driving_port
def test_the_captured_tier_is_selectable_on_day_one(seed_hits):
    """The one hand-written picker entry Slice 01 adds.

    Operator decision, 2026-08-15 (design/upstream-changes.md §2): gating a
    minutes-long edit behind a day of registry work bought tidier slice
    boundaries at the cost of a day of captured-but-unreadable data.

    Slice 02 DELETES this literal and replaces it with the derived entry —
    which is why the widened byte-identity pin in the Slice 02 suite covers
    eight entries rather than seven.
    """
    import config

    assert any(
        c.value == MYTHIC_3_KEY and c.name == "Mythic 3" for c in config.TIER_CHOICES
    )
