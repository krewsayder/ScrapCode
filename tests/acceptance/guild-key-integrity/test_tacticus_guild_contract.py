"""Contract test for `GET /api/v1/guild`. Implements
`acceptance/tacticus-guild-contract.feature`.

DESIGN Open Question 3, and ADR-006 §H's "highest-risk boundary". `guildId`
is UNDOCUMENTED by the vendor and is the single field this feature binds on.
Every other test in this suite is written against the classification, so all
of them keep passing against a fixture while production goes blind. These are
the only tests that look at the payload.

The `@requires_external` tests are the only ones that can detect a vendor
change. They are skipped unless SCRAPCODE_TACTICUS_CONTRACT_KEY is set, and
that is a deliberate trade: they are the highest-value tests here and also
the only ones that need a real credential and network.
"""
from __future__ import annotations

import os

import pytest

from domain_types import WORD_BEARERS, ProbeOutcome

RED = pytest.mark.skip(reason="RED scaffold — enable one at a time in DELIVER")

CONTRACT_KEY = os.getenv("SCRAPCODE_TACTICUS_CONTRACT_KEY")
requires_external = pytest.mark.skipif(
    not CONTRACT_KEY,
    reason="set SCRAPCODE_TACTICUS_CONTRACT_KEY to run the live contract checks",
)


# ===========================================================================
# Recorded-response checks
# ===========================================================================

@RED
@pytest.mark.real_io
def test_a_recorded_response_yields_an_identity_and_a_roster_from_one_read(
    recorded_guild_response,
):
    """DDD-2 — probe and roster come from ONE call.

    Two calls could disagree: the identity check could pass against one
    response while the roster written came from another. Folding them also
    keeps the per-hour Tacticus call count flat, which is what let ADR-003's
    allow-list be amended rather than widened.
    """
    from bot.services.tacticus.guild_client import parse_guild_snapshot

    snapshot = parse_guild_snapshot(recorded_guild_response)

    assert snapshot.identity.uuid == WORD_BEARERS.uuid
    assert snapshot.identity.tag == WORD_BEARERS.tag
    assert snapshot.identity.name == WORD_BEARERS.name
    assert len(snapshot.members) == 5
    assert snapshot.outcome is ProbeOutcome.MATCH or snapshot.identity is not None


@RED
@pytest.mark.real_io
@pytest.mark.error
def test_a_recorded_response_without_the_identifier_is_unverifiable(
    recorded_guild_response,
):
    """DDD-10 asserted at the parser, where the decision is actually made.

    Reading a REAL recorded response with one key removed, rather than a
    hand-written stub: a stub would let the parser pass while reading a
    field name Tacticus does not use.
    """
    from bot.services.tacticus.guild_client import parse_guild_snapshot

    del recorded_guild_response["guild"]["guildId"]
    snapshot = parse_guild_snapshot(recorded_guild_response)

    assert snapshot.outcome is ProbeOutcome.UNVERIFIABLE
    assert snapshot.outcome is not ProbeOutcome.MISMATCH
    assert snapshot.identity is None, (
        "an identity was synthesised from a response with no identifier — "
        "the only field left to build it from is the tag"
    )


@RED
@pytest.mark.real_io
@pytest.mark.error
@pytest.mark.parametrize("field", ["guildTag", "name"])
def test_a_recorded_response_missing_a_display_field_still_yields_an_identity(
    recorded_guild_response, field: str
):
    """AC-001.6 at the parser boundary — display fields are never
    load-bearing, so their absence must not raise."""
    from bot.services.tacticus.guild_client import parse_guild_snapshot

    del recorded_guild_response["guild"][field]
    snapshot = parse_guild_snapshot(recorded_guild_response)

    assert snapshot.identity.uuid == WORD_BEARERS.uuid
    assert getattr(snapshot.identity, {"guildTag": "tag", "name": "name"}[field]) is None


@RED
@pytest.mark.real_io
def test_the_snapshot_members_match_what_the_old_roster_reader_produced(
    recorded_guild_response,
):
    """The regression guard on relocating `_fetch_roster`.

    `PlayerService._fetch_roster` is DELETED by this feature and its callers
    take a snapshot instead. If the member set changes shape in the move,
    every guild's roster diff is wrong on the first cycle after deploy — and
    a wrong roster diff marks real members as departed.
    """
    from bot.services.tacticus.guild_client import parse_guild_snapshot

    legacy = {m["userId"] for m in recorded_guild_response["guild"]["members"]}
    snapshot = parse_guild_snapshot(recorded_guild_response)

    assert set(snapshot.members) == legacy


# ===========================================================================
# Live checks — the only ones that can catch a vendor change
# ===========================================================================

@RED
@requires_external
@pytest.mark.requires_external
@pytest.mark.kpi
async def test_the_live_service_still_returns_a_stable_identifier():
    """The residual risk ADR-008 D1 accepts, made observable.

    Two consecutive reads, because "present" is not enough — a field that
    changes between calls is worse than one that is missing, since it would
    quarantine a healthy guild at random.
    """
    from bot.services.tacticus.guild_client import fetch_guild_snapshot

    first = await fetch_guild_snapshot(CONTRACT_KEY)
    second = await fetch_guild_snapshot(CONTRACT_KEY)

    assert first.identity is not None, (
        "the live service no longer returns guildId — this feature's binding "
        "is unverifiable in production RIGHT NOW"
    )
    assert first.identity.uuid == second.identity.uuid


@RED
@requires_external
@pytest.mark.requires_external
@pytest.mark.error
async def test_the_live_response_still_carries_every_recorded_field(
    recorded_guild_response,
):
    """Drift detector on the recording itself. When this fails, the fix is
    to re-record `fixtures/guild_response_recorded.json` and read the diff —
    that diff is the review artifact for a vendor change."""
    from bot.services.tacticus.guild_client import fetch_guild_snapshot

    live = await fetch_guild_snapshot(CONTRACT_KEY)
    recorded_fields = set(recorded_guild_response["guild"]) - {"_recording_note"}

    assert recorded_fields <= set(live.raw["guild"]), (
        f"the live response dropped {recorded_fields - set(live.raw['guild'])} — "
        "re-record the fixture and review the diff"
    )
