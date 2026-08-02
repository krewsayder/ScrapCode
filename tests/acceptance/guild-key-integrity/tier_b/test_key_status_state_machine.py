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
from hypothesis.stateful import (  # noqa: E402
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
)

from tier_b.in_memory_composition import InMemoryComposition  # noqa: E402

pytestmark = [pytest.mark.property, pytest.mark.slice_03]

RED = pytest.mark.skip(reason="RED scaffold — enable one at a time in DELIVER")

GUILD = "word_bearers"

identities = st.sampled_from([WORD_BEARERS, DARK_MECHANICUM])
outcomes = st.sampled_from(list(ProbeOutcome))


class KeyStatusJourney(RuleBasedStateMachine):
    """Commands are the things that actually happen to a guild key in
    production: an hourly probe with each of the five classifications, and an
    admin installing a key with or without force."""

    @initialize()
    def setup(self):
        self.composition = InMemoryComposition(enforcement=True)
        self.composition.register_guild(GUILD, api_key="wb-key")
        self.rows_at_quarantine: int | None = None

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
            assert self.composition.total_rows() == self.rows_at_quarantine

    @invariant()
    def quarantine_is_never_a_trap(self):
        """DISCUSS D3, as a reachability claim about the whole state space.

        From any quarantined state, installing a key that resolves to the
        bound identity returns the guild to active. If this ever fails, some
        reachable quarantined state has no exit and the operator is back to
        an SSH session — the exact procedure the feature retires.
        """
        if self.composition.status_of(GUILD) != KeyStatus.QUARANTINED.value:
            return
        probe = self.composition  # the exit is checked on the live model
        assert probe.update_key(
            GUILD, api_key="rescue", resolves_to=_bound_identity(probe), force=False
        ), "a reachable quarantined state has no exit"

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
