"""Tier B — the live board across every interleaving (Mandate 10).

The Tier A scenarios in `slice-03-live-board-reconciliation.feature` enumerate
the orderings somebody thought of: refresh twice, rollover-then-refresh,
refuse-then-retry. This machine generates the rest.

WHY THIS EXISTS RATHER THAN MORE EXAMPLES. The duplicate that this feature is
most afraid of is produced by an ORDERING, not by an input — a rollover landing
in the same cycle as a newly registered tier, a refusal landing between a send
and its persist. Orderings are what state-machine PBT covers and examples do
not. `guild-key-integrity` learned this the expensive way: its one Tier B
invariant was silently short-circuited by a sibling rule and executed its
assertion body zero times across 200 examples, with a green tick where the
property should have been. `test_the_invariants_actually_assert_something`
below exists because of that, and it is not optional.

THE INVARIANTS, in plain words:
  * one message per tier, ever — a second send for a tier orphans the first,
    visibly, in a channel every guild member reads
  * nothing is ever deleted — reconciliation is additive only (ADR-009 D8)
  * a tier with a message keeps it — including after it leaves the registry
"""
from __future__ import annotations

import os

import pytest

hypothesis = pytest.importorskip(
    "hypothesis",
    reason="Tier B needs hypothesis; it is in requirements.txt for exactly this",
)

import hypothesis.strategies as st  # noqa: E402
from hypothesis.stateful import (  # noqa: E402
    RuleBasedStateMachine,
    initialize,
    invariant,
    rule,
)

from tier_b.in_memory_composition import InMemoryComposition  # noqa: E402

RED = pytest.mark.skipif(
    os.getenv("SCRAPCODE_RED_GATE") != "1",
    reason="RED scaffold — enable in DELIVER once reconciliation exists",
)

TIER_KEYS = st.sampled_from([
    "Legendary_0", "Legendary_1", "Legendary_2", "Legendary_3", "Legendary_4",
    "Mythic", "Mythic_1", "Mythic_2", "Mythic_3",
])

# Counts how many times an invariant body actually RAN. A property that never
# executes its assertion is worse than no property: it occupies the space where
# a real one would go and reports success.
_ASSERTIONS_EXECUTED = {"one_message_per_tier": 0, "nothing_is_deleted": 0}


class LiveBoardJourney(RuleBasedStateMachine):
    """The board under every ordering of the four things that can happen to it."""

    @initialize()
    def setup(self):
        self.composition = InMemoryComposition()
        self.board = self.composition.board
        self.board.registered = ("Legendary_0", "Mythic", "Mythic_1")
        self.board.messages = {"Legendary_0": 1, "Mythic": 2, "Mythic_1": 3}

    @rule(tier=TIER_KEYS)
    def a_tier_is_registered(self, tier):
        """Slice 02 lands a new tier. From the board's view this is an input."""
        self.board.register_tier(tier)

    @rule()
    def the_board_refreshes(self):
        """The hourly refresh. The only command that may send anything."""
        self.board.refresh()

    @rule()
    def the_season_rolls_over(self):
        """The rollover path — which rewrites the remembered ids wholesale."""
        self.board.roll_season(self.board.season + 1)

    @rule(refuse=st.booleans())
    def discord_starts_or_stops_refusing(self, refuse):
        """Rate limits arrive and clear. Refusal is a state, not an event."""
        self.board.refuse_sends = refuse

    # -- invariants -------------------------------------------------------

    @invariant()
    def one_message_per_tier(self):
        """No tier is ever sent twice.

        THE invariant. A tier sent twice overwrites its own remembered id and
        leaves an orphan message in the channel that no later refresh will ever
        edit — permanently stale, publicly, showing last season's numbers.

        Asserted on the SEND LOG rather than on the messages map, because the
        map cannot hold two ids for one tier and would therefore report success
        for exactly the failure being looked for.
        """
        _ASSERTIONS_EXECUTED["one_message_per_tier"] += 1
        sends = self.composition.capture_universe()["board.sends_per_tier"]
        offenders = {k: n for k, n in sends.items() if n > 1}
        assert not offenders, f"tiers sent more than once: {offenders}"

    @invariant()
    def nothing_is_deleted(self):
        """Reconciliation is additive only (ADR-009 D8).

        A frozen board is a visible oddity somebody asks about. A deleted board
        is a season of data that silently stopped being shown — and deleting on
        a vendor-shaped input contradicts the operator's stated anti-goal that
        a problem should stop and wait for a human.

        Note this invariant does NOT call any command. That is deliberate:
        `guild-key-integrity`'s Tier B was defeated by a sibling invariant that
        mutated the state before the real one ran, and hypothesis runs
        invariants in name order. An invariant that only reads cannot do that
        to its neighbours.
        """
        _ASSERTIONS_EXECUTED["nothing_is_deleted"] += 1
        assert self.composition.capture_universe()["board.deleted"] == ()


TestLiveBoardJourney = LiveBoardJourney.TestCase
TestLiveBoardJourney = pytest.mark.property(TestLiveBoardJourney)
TestLiveBoardJourney = RED(TestLiveBoardJourney)


@RED
@pytest.mark.property
def test_the_invariants_actually_assert_something():
    """The guard against a green tick where a property should have been.

    `guild-key-integrity` shipped a Tier B invariant that executed its
    assertion body ZERO times across 200 examples × 25 steps, because a sibling
    invariant mutated the state out from under it and hypothesis runs
    invariants in name order. It had the same green tick as a working property.

    Counting executions and failing at zero is the only thing that
    distinguishes "this property holds" from "this property never ran". Run
    AFTER the machine, so the counters have something in them.
    """
    assert all(n > 0 for n in _ASSERTIONS_EXECUTED.values()), (
        f"an invariant never executed its assertion body: {_ASSERTIONS_EXECUTED}"
    )
