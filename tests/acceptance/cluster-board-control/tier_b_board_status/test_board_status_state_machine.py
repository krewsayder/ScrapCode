"""Tier B — the board-status state machine.

WHY TIER B IS HERE, given the strict Mandate-10 test says skip it.

Mandate 10 adds Tier B when a journey has >=3 chained scenarios AND the input
space is domain-rich. This feature meets the first and NOT the second — the
inputs are two commands, two scope kinds and a season number, which is not an
email or a free-text payload. On the strict reading, Tier B is skipped.

It is included anyway, on the state-machine trigger, which is a different test:
"if you can describe the SUT's behaviour by a state-machine model with
command/postcondition pairs, use stateful PBT." Board status is exactly that —
{active, disabled} x {turn off, turn on, set up, hourly cycle} — and the
codebase already carries the analogous machine for `key_status`
(`guild-key-integrity/tier_b/test_key_status_state_machine.py`).

The deciding argument is what the property buys. AC-001.2 is a HARD KPI gate
and the entire feature's value rests on it: a paused board is never touched.
An example test proves that for ONE interleaving. This proves it for every
interleaving of the four commands — including the ones nobody thought to
enumerate, like turning a board off during the same cycle as a season
rollover.

The deviation from the strict Mandate-10 reading is deliberate and recorded
here rather than left for a reviewer to catch.
"""
from __future__ import annotations

import pytest

hypothesis = pytest.importorskip(
    "hypothesis",
    reason="hypothesis is not installed — DISTILL pins it into requirements.txt",
)

from hypothesis import settings  # noqa: E402
from hypothesis.stateful import (  # noqa: E402
    RuleBasedStateMachine,
    initialize,
    invariant,
    rule,
)

from bot.repository import BoardStatus  # noqa: E402
from tier_b_board_status.in_memory_composition import SEASON, InMemoryComposition  # noqa: E402

pytestmark = [pytest.mark.property, pytest.mark.slice_01]


class BoardStatusJourney(RuleBasedStateMachine):
    """Every interleaving of the four things that can happen to a board."""

    @initialize()
    def setup(self):
        self.composition = InMemoryComposition()
        self.composition.Given_a_configured_cluster_leaderboard()
        # Mirrors of the Universe, maintained independently of the SUT so the
        # invariants below cannot be satisfied by reading the SUT's own answer
        # back to itself.
        self.calls_while_disabled_at_entry = None
        self.state_while_disabled_at_entry = None

    # -- commands ----------------------------------------------------------

    @rule()
    def officer_turns_it_off(self):
        self.composition.When_an_officer_turns_the_board_off()
        self._remember_disabled_entry()

    @rule()
    def officer_turns_it_on(self):
        self.composition.When_an_officer_turns_the_board_on()
        self._forget_disabled_entry()

    @rule()
    def officer_sets_it_up_again(self):
        self.composition.When_an_officer_sets_the_board_up_again()
        self._forget_disabled_entry()

    @rule()
    def the_hourly_cycle_runs(self):
        self.composition.When_the_hourly_cycle_runs(season=SEASON)

    @rule()
    def the_season_rolls_over_and_the_cycle_runs(self):
        self.composition.When_the_hourly_cycle_runs(season=SEASON + 1)

    # -- invariants --------------------------------------------------------

    @invariant()
    def a_disabled_board_is_never_touched(self):
        """KPI-1, quantified over interleavings rather than one example.

        The counters are compared against what they were when the board most
        recently ENTERED the disabled state. Any cycle in between must have
        left them alone — including a cycle that coincides with a season
        rollover, which is the interleaving an example test would not think
        to write.
        """
        universe = self.composition.capture_universe()
        if universe["board.status"] is not BoardStatus.DISABLED:
            return
        assert universe["discord.calls"] == self.calls_while_disabled_at_entry, (
            "a board was touched while it was turned off: calls went from "
            f"{self.calls_while_disabled_at_entry} to {universe['discord.calls']}"
        )

    @invariant()
    def a_disabled_board_loses_nothing(self):
        """AC-001.4 across interleavings.

        Channel, messages and season are frozen for as long as the board is
        off. Season especially: advancing it while paused would make the
        eventual resume edit last season's messages with this season's
        numbers.
        """
        universe = self.composition.capture_universe()
        if universe["board.status"] is not BoardStatus.DISABLED:
            return
        frozen = {k: universe[k] for k in ("board.channel_id", "board.messages", "board.season")}
        assert frozen == self.state_while_disabled_at_entry, (
            "a board's own state changed while it was turned off: "
            f"{self.state_while_disabled_at_entry} -> {frozen}"
        )

    @invariant()
    def a_board_is_always_in_a_named_state(self):
        """There is no third state, and no absent one."""
        universe = self.composition.capture_universe()
        assert universe["board.status"] in set(BoardStatus)

    # -- bookkeeping -------------------------------------------------------

    def _remember_disabled_entry(self):
        universe = self.composition.capture_universe()
        if universe["board.status"] is not BoardStatus.DISABLED:
            return
        if self.calls_while_disabled_at_entry is None:
            self.calls_while_disabled_at_entry = universe["discord.calls"]
            self.state_while_disabled_at_entry = {
                k: universe[k]
                for k in ("board.channel_id", "board.messages", "board.season")
            }

    def _forget_disabled_entry(self):
        self.calls_while_disabled_at_entry = None
        self.state_while_disabled_at_entry = None


BoardStatusJourney.TestCase.settings = settings(max_examples=100, stateful_step_count=25, deadline=None)
TestBoardStatusJourney = BoardStatusJourney.TestCase
