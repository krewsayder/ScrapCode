"""Slice 03 — the always-on board grows a message for a new tier. Implements
`acceptance/slice-03-live-board-reconciliation.feature`.

Covers US-005. TK-3 is measured from the `live_board.reconciled` records
asserted here.

THE FAILURE MODE OF THIS SLICE IS PUBLIC. A duplicated board is visible to
every member of the guild, not just to the operator, and it is worse than the
bug being fixed. `test_a_second_refresh_adds_nothing` and
`test_rollover_and_a_new_tier_produce_one_set_of_messages` should be the first
two written in DELIVER, not the last.

Driving port: `bot.cogs.tasks_cog.TasksCog._refresh_live_leaderboards`, driven
by awaiting the loop body directly with the schedule bypassed — the schedule is
`discord.py`'s concern, the cycle body is ours
(docs/architecture/atdd-infrastructure-policy.md).
"""
from __future__ import annotations

import os

import pytest

from tier_types import MYTHIC_3_KEY, LiveConfigShape, SendFailure
from tier_types import GUILD_WB, PROD_SERVER_ID, SEASON  # noqa: F401

RECONCILED_EVENT = "live_board.reconciled"
RECONCILE_FAILED_EVENT = "live_board.reconcile.failed"

RED = pytest.mark.skipif(
    os.getenv("SCRAPCODE_RED_GATE") != "1",
    reason="RED scaffold — enable one at a time in DELIVER",
)


# ===========================================================================
# The behaviour being added
# ===========================================================================

@RED
@pytest.mark.driving_port
@pytest.mark.kpi
async def test_the_board_gains_a_message_for_the_tier_it_was_missing(
    sqlite_repo, registered_guilds, live_channel, live_config_missing_mythic_3,
    cycle_events, seed_hits,
):
    """AC-005.1 + TK-3's instrument.

    Three assertions and all three are needed: the message was SENT (not
    edited), its id was REMEMBERED (or the next cycle sends it again), and the
    record NAMES the tier (or TK-3 has no numerator).
    """
    raise AssertionError("RED scaffold — refresh once, assert send + persist + record")


@RED
@pytest.mark.driving_port
async def test_a_second_refresh_adds_nothing(
    sqlite_repo, registered_guilds, live_channel, live_config_missing_mythic_3,
    cycle_events, seed_hits,
):
    """AC-005.2 — THE SINGLE PROPERTY THAT MAKES THIS SAFE TO RUN HOURLY.

    Idempotence keyed on the stored tier value, so running twice in one hour
    and running an hour apart are the same thing.

    The silence assertion is deliberate and matches the observability contract:
    `live_board.reconciled` is emitted ONLY when something was added. A record
    per server per hour saying "nothing to do" would be 24 lines a day of noise
    around the one line that matters, and would train the operator to ignore
    the event that carries TK-3.
    """
    raise AssertionError("RED scaffold — refresh twice, assert one send total")


@RED
@pytest.mark.driving_port
async def test_the_new_message_sits_in_tier_order(
    sqlite_repo, registered_guilds, live_channel, live_config_missing_mythic_3,
    seed_hits,
):
    """AC-005.4.

    Note what this does NOT claim. Discord message order is chronological, so a
    tier inserted BETWEEN two existing ones appears at the bottom of the
    channel and stays there. Rewriting history to fix that is out of scope
    (slice brief, OUT of scope). What is asserted is that the reconciliation
    loop visits tiers in registry order, which is what makes the common case —
    a tier appended at the end — come out right.
    """
    raise AssertionError("RED scaffold — assert send order follows registry order")


@RED
@pytest.mark.driving_port
@pytest.mark.parametrize("shape", list(LiveConfigShape), ids=lambda s: s.value)
async def test_both_config_shapes_reconcile(
    sqlite_repo, registered_guilds, live_channel, live_config, seed_hits, shape
):
    """AC-005.7. Two branches of the same loop.

    `guild:{id}` and `cluster` are handled by separate arms of the dispatch in
    `_refresh_live_leaderboards`. Fixing one and missing the other is the
    realistic failure — and the cluster board is the one the operator looks at,
    while the per-guild boards are the ones there are more of.
    """
    raise AssertionError("RED scaffold — parametrized over both config shapes")


# ===========================================================================
# Error paths — where the public damage lives
# ===========================================================================

@RED
@pytest.mark.driving_port
@pytest.mark.error
@pytest.mark.parametrize("failure", list(SendFailure), ids=lambda f: f.value)
async def test_a_refused_send_leaves_the_board_exactly_as_it_was(
    sqlite_repo, registered_guilds, live_channel, live_config_missing_mythic_3,
    cycle_events, seed_hits, failure,
):
    """AC-005.3 — retain unchanged, NOT write back what we got.

    Under rate limiting this is the normal case, not an edge case.

    The requirement is precise and the wrong version is tempting: persisting a
    partial `messages` map that omits an already-sent message is what produces
    the duplicate on the following cycle. Best-effort partial writes are how a
    board doubles in public.

    `SENT_THEN_PERSIST_FAILED` is the honest case, and the reason the slice
    brief demands a production run as well as this test: the real failure shape
    is a Discord send SUCCEEDING after local state has concluded it failed, and
    a fake channel simulates the consequence rather than the cause.

    The `error_type` assertion is not incidental — `discord.HTTPException`
    carries the response body, and this project's standing rule is that no
    record carries material nobody chose to log (see `_emit_server_failed`).
    """
    raise AssertionError(
        "RED scaffold — program the failure, assert messages map byte-identical"
    )


@RED
@pytest.mark.driving_port
@pytest.mark.error
async def test_rollover_and_a_new_tier_produce_one_set_of_messages(
    sqlite_repo, registered_guilds, live_channel, live_config, cycle_events, seed_hits
):
    """AC-005.6 — THE RACE.

    The rollover path rewrites `config["messages"]` WHOLESALE
    (`tasks_cog.py:701`). A reconciliation branch that runs beside it
    duplicates the entire board — every tier, twice, in a channel every guild
    member reads.

    The assertion is on the COUNT PER TIER, not on the total. A total of
    sixteen for eight tiers is also produced by an implementation that sends
    two messages for one tier and none for another, and that failure is harder
    to see and harder to clean up.
    """
    raise AssertionError("RED scaffold — rollover + missing tier in one cycle")


@RED
@pytest.mark.driving_port
@pytest.mark.error
async def test_a_tier_that_leaves_the_registry_keeps_its_board(
    sqlite_repo, registered_guilds, live_channel, live_config, seed_hits
):
    """AC-005.5 — additive only (ADR-009 D8).

    Reconciliation never deletes. Deleting a board automatically on a
    vendor-shaped input contradicts the operator's stated anti-goal: a problem
    should stop and wait for a human rather than resolve itself destructively.

    A frozen board is a visible oddity somebody asks about. A deleted board is
    a season of data that silently stopped being shown.
    """
    raise AssertionError("RED scaffold — registry-absent tier, assert no delete")


@RED
@pytest.mark.driving_port
@pytest.mark.error
async def test_one_broken_board_does_not_stop_the_others(
    sqlite_repo, registered_guilds, live_channel, live_config, seed_hits
):
    """The blast-radius rule, applied to boards.

    `_refresh_live_leaderboards` already removes the configuration of a board
    whose channel has gone. Reconciliation must not become a new way for one
    broken board to end the loop before the others are reached — which is
    exactly what happened at the server level in `guild-key-integrity` (KPI-5)
    and is the reason `auto_update.cycle` records reasons at all.
    """
    raise AssertionError("RED scaffold — one dead channel, assert the rest reconcile")
