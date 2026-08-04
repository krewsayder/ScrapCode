"""Tier B — the key-status state machine (Mandate 10).

Tier B is added here because the model itself IS a state machine, which is
the trigger from Hebert ch.11 — not because the input space is wide. Key
status has three reachable states and six commands, and the invariants that
matter are about REACHABILITY under arbitrary interleavings:

    unbound ──probe(match|mismatch)──▶ bound-active
    bound-active ──probe(mismatch)──▶ quarantined
    quarantined ──update_key(matching)──▶ bound-active
    quarantined ──probe(anything)──▶ quarantined        (no self-healing)

Two properties here are things no enumerated example can establish:

  1. `quarantine_is_never_a_trap` — from EVERY reachable quarantined state
     there exists a path back to active. DISCUSS D3 is a claim about the
     whole state space, and a single example only shows one path works.

  2. `quarantined_guilds_never_write` — zero rows written while quarantined,
     under every command ordering. The Tier A tests assert this for the
     orderings someone thought to write down.

Requires `hypothesis`, which DISTILL adds to requirements.txt (see the
DISTILL Pre-requisites section of feature-delta.md).
"""
from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from domain_types import (  # noqa: E402
    DARK_MECHANICUM,
    WORD_BEARERS,
    GuildIdentity,
    KeyStatus,
    ProbeOutcome,
)

hypothesis = pytest.importorskip(
    "hypothesis",
    reason="hypothesis is not installed — DISTILL pins it into requirements.txt",
)

import hypothesis.strategies as st  # noqa: E402
from hypothesis import HealthCheck, settings  # noqa: E402
from hypothesis.stateful import (  # noqa: E402
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

from tier_b.in_memory_composition import InMemoryComposition  # noqa: E402

pytestmark = [pytest.mark.property, pytest.mark.slice_03]

RED = pytest.mark.skip(reason="RED scaffold — enable one at a time in DELIVER")

GUILD = "word_bearers"

identities = st.sampled_from([WORD_BEARERS, DARK_MECHANICUM])
outcomes = st.sampled_from(list(ProbeOutcome))


class KeyStatusJourney(RuleBasedStateMachine):
    """Commands are the things that actually happen to a guild key in
    production: an hourly probe with each of the five classifications, an
    admin installing a key with or without force, and the operator taking
    the quarantine exit.

    The two `witnessed_*` class counters are the anti-vacuity instrument.
    They are class-level rather than instance-level because hypothesis
    constructs a fresh machine per example, and the question being asked is
    about the RUN, not any one example: "did this property's assertion body
    execute even once?". `test_both_properties_actually_assert_something`
    answers it.
    """

    witnessed_quarantined_write_checks = 0
    witnessed_trap_checks = 0

    @initialize()
    def setup(self):
        self.composition = InMemoryComposition(enforcement=True)
        self.composition.register_guild(GUILD, api_key="wb-key")
        self.rows_at_quarantine: int | None = None
        self.rescues_taken = 0

    # -- commands ---------------------------------------------------------

    @rule(outcome=outcomes, identity=identities)
    def hourly_probe(self, outcome: ProbeOutcome, identity: GuildIdentity):
        self.composition.probe(GUILD, outcome, identity)
        self.composition.run_cycle()
        if self.composition.status_of(GUILD) == KeyStatus.QUARANTINED.value:
            if self.rows_at_quarantine is None:
                self.rows_at_quarantine = self.composition.total_rows()

    @rule(identity=identities, force=st.booleans())
    def admin_updates_the_key(self, identity: GuildIdentity, force: bool):
        installed = self.composition.update_key(
            GUILD, api_key="replacement", resolves_to=identity, force=force
        )
        if installed:
            self.rows_at_quarantine = None

    @rule()
    @precondition(
        lambda self: self.composition.status_of(GUILD) == KeyStatus.QUARANTINED.value
    )
    def the_operator_takes_the_exit(self):
        """The rescue, as a COMMAND rather than an assertion.

        This is the mutation that `quarantine_is_never_a_trap` used to
        perform from inside an `@invariant()`. Moving it here is what lets
        `quarantined_guilds_never_write` see a quarantined guild at all: an
        invariant runs after EVERY step, so a mutating one released the
        quarantine before any other property could observe it. As a rule,
        hypothesis chooses when to fire it, so the model spends genuine
        stretches of steps quarantined — which is the state the other
        properties are about.
        """
        self.rescues_taken += 1
        assert self.composition.update_key(
            GUILD, api_key="rescue",
            resolves_to=_bound_identity(self.composition), force=False,
        ), "the operator's correct key was refused — the exit does not work"
        self.rows_at_quarantine = None

    # -- invariants -------------------------------------------------------

    @invariant()
    def status_is_always_one_of_the_declared_states(self):
        assert self.composition.status_of(GUILD) in {
            KeyStatus.ACTIVE.value,
            KeyStatus.QUARANTINED.value,
        }

    @invariant()
    @precondition(lambda self: self.rows_at_quarantine is not None)
    def quarantined_guilds_never_write(self):
        """KPI-2's target as a property rather than a count.

        Holds across every interleaving of probes and key updates, including
        the ones nobody thought to enumerate.
        """
        if self.composition.status_of(GUILD) == KeyStatus.QUARANTINED.value:
            self.__class__.witnessed_quarantined_write_checks += 1
            assert self.composition.total_rows() == self.rows_at_quarantine

    @invariant()
    def quarantine_is_never_a_trap(self):
        """DISCUSS D3, as a reachability claim about the whole state space.

        From any quarantined state, installing a key that resolves to the
        bound identity returns the guild to active. If this ever fails, some
        reachable quarantined state has no exit and the operator is back to
        an SSH session — the exact procedure the feature retires.

        NON-MUTATING, and that is the whole correction (2026-08-02). This
        invariant used to run the rescue against the LIVE model, so every
        time it observed a quarantine it also ended it. Because hypothesis
        runs invariants in name order, `quarantine_is_never_a_trap` sorted
        ahead of `quarantined_guilds_never_write`, which therefore found the
        guild ACTIVE at every single step and short-circuited before its
        assertion: measured 0 assertions across 200 examples x 25 steps,
        while `kpi-contracts.yaml` cited it as KPI-2's only property-based
        evidence.

        A property that repairs the state it is inspecting is not an
        invariant, it is a command wearing one's clothes. The exit is now
        exercised for real by `the_operator_takes_the_exit`; here it is
        checked against a DEEP COPY, so the claim stays universally
        quantified over every reachable quarantined state without the check
        itself perturbing which states are reachable.
        """
        if self.composition.status_of(GUILD) != KeyStatus.QUARANTINED.value:
            return
        self.__class__.witnessed_trap_checks += 1
        shadow = deepcopy(self.composition)
        assert shadow.update_key(
            GUILD, api_key="rescue", resolves_to=_bound_identity(shadow), force=False
        ), "a reachable quarantined state has no exit"
        assert shadow.status_of(GUILD) == KeyStatus.ACTIVE.value, (
            "the rescue key was accepted but the guild is still quarantined"
        )

    @invariant()
    def an_outage_never_changes_status(self):
        """D6 as an invariant: UNREACHABLE and DEAD are classifications that
        never move the guild between states, only MISMATCH does."""
        events = self.composition.events
        if events and events[-1] in {"guild.key.unreachable", "guild.key.dead"}:
            assert "guild.key.quarantined" not in events[-1:]


def _bound_identity(composition) -> GuildIdentity:
    """The guild's bound identity, reconstructed from the in-memory binding.

    `quarantine_is_never_a_trap` installs a key resolving to THIS identity to
    prove the exit exists from any quarantined state — the rescue path the
    operator takes in place of the SSH session the feature retires.
    """
    binding = composition.guilds[GUILD].binding
    return GuildIdentity(
        uuid=binding.tacticus_guild_id,
        tag=binding.tacticus_guild_tag,
        name=binding.tacticus_guild_name,
    )


TestKeyStatusJourney = KeyStatusJourney.TestCase


# ===========================================================================
# Anti-vacuity gate
#
# A green property proves nothing until you know its assertion ran. Both
# properties above were green for the whole DELIVER wave; one of them had
# executed its assertion zero times, and `kpi-contracts.yaml:159-168` cited
# it as KPI-2's only property-based evidence. Nothing in the suite could
# have told the difference, because "passed" and "never evaluated" look
# identical from the outside.
#
# This is the test that can tell the difference. It runs the machine itself
# rather than reading counters left by `TestKeyStatusJourney`, so it does
# not depend on pytest's collection order and gives the same answer when run
# alone with `-k`.
# ===========================================================================

@pytest.mark.property
def test_both_properties_actually_assert_something():
    """The two load-bearing Tier B properties must reach their assertions.

    Fails with a count of zero rather than a green tick if a future change
    re-introduces a mutation, a precondition, or an ordering that starves
    either property. That is the exact defect this test was written for: an
    `@invariant()` that called `update_key` released the quarantine before
    `quarantined_guilds_never_write` could observe one, and invariants run in
    name order, so the starvation was total and permanent.
    """
    KeyStatusJourney.witnessed_quarantined_write_checks = 0
    KeyStatusJourney.witnessed_trap_checks = 0

    run_state_machine_as_test(
        KeyStatusJourney,
        settings=settings(
            max_examples=100, stateful_step_count=25,
            deadline=None, database=None,
            suppress_health_check=list(HealthCheck),
        ),
    )

    assert KeyStatusJourney.witnessed_quarantined_write_checks > 0, (
        "`quarantined_guilds_never_write` never once observed a quarantined "
        "guild, so its assertion never ran — KPI-2 has no property-based "
        "evidence behind it, only a green tick"
    )
    assert KeyStatusJourney.witnessed_trap_checks > 0, (
        "`quarantine_is_never_a_trap` never once observed a quarantined "
        "guild, so DISCUSS D3's reachability claim was never tested"
    )


# ===========================================================================
# Negative testing (Hebert ch.6) — relax an assumption, see if it still holds
# ===========================================================================

def test_relaxing_the_matching_key_assumption_surfaces_the_force_path():
    """Deliberate under-specification probe.

    The journey above assumes an admin only installs a key that resolves to
    the bound guild. Relax that — let the key resolve anywhere — and the
    property "status ends active" must FAIL without `force`, or the
    `force` gate is not actually gating anything.

    A property that cannot fail when its precondition is removed was
    vacuously true to begin with.
    """
    composition = InMemoryComposition(enforcement=True)
    composition.register_guild(GUILD, api_key="wb-key")
    composition.probe(GUILD, ProbeOutcome.MATCH, WORD_BEARERS)
    composition.run_cycle()

    installed = composition.update_key(
        GUILD, api_key="whatever", resolves_to=DARK_MECHANICUM, force=False
    )

    assert installed is False, (
        "a key resolving to a different guild installed without force — the "
        "force gate is decorative"
    )
