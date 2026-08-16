"""Mandate 4 — Environmental Realism. Implements
`acceptance/environment-matrix.feature`.

One scenario per target environment in
`docs/feature/dynamic-tier-registry/environments.yaml`, so the suite is
parametrized over the states a real deployment is actually in rather than over
the one state a fixture happens to build.

The traceability tests at the top are NOT scaffolds — they assert about
artifacts that exist today and are green now. They are here because a matrix
that has drifted from the platform artifact is a matrix nobody is maintaining,
and the drift is invisible: both files keep looking reasonable on their own.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tier_types import Environment
from tier_types import environment_names_from_devops_artifact  # noqa: F401

RED = pytest.mark.skipif(
    os.getenv("SCRAPCODE_RED_GATE") != "1",
    reason="RED scaffold — enable one at a time in DELIVER",
)

REPO_ROOT = Path(__file__).resolve().parents[3]


# ===========================================================================
# Traceability — green today
# ===========================================================================

@pytest.mark.traceability
def test_environment_names_match_devops_artifact():
    """The enum and `environments.yaml` cannot drift apart.

    Set equality in both directions. An environment declared by DEVOPS and
    absent from the suite is a coverage gap the platform architect believes is
    covered; an environment in the suite and absent from the artifact is a
    state nobody has agreed is real.
    """
    declared = set(environment_names_from_devops_artifact())
    covered = {e.value for e in Environment}

    assert declared == covered, (
        f"missing from the suite: {sorted(declared - covered)}\n"
        f"invented by the suite:  {sorted(covered - declared)}"
    )


@pytest.mark.traceability
def test_this_feature_adds_no_alembic_revision():
    """ADR-009 D4, asserted rather than asserted-in-prose.

    The claim underpinning the entire DEVOPS deployment strategy: no migration,
    so no migrate-before-restart ordering constraint, so deploy is a checkout
    and a restart and rollback is the reverse.

    Green today and it must STAY green. A revision added during DELIVER would
    silently reintroduce the ordering hazard — the ADR-006 startup probe
    refuses on any `alembic_version` mismatch in BOTH directions — and the
    runbook that says "no migration to rehearse" would become wrong at the
    worst moment.
    """
    versions = REPO_ROOT / "bot" / "db" / "alembic" / "versions"
    revisions = sorted(p.name for p in versions.glob("*.py")
                       if not p.name.startswith("__"))
    assert not any("tier" in name.lower() for name in revisions), (
        f"a tier-related alembic revision appeared: {revisions}. ADR-009 D4 "
        "freezes stored tier keys precisely so that none is needed."
    )


# ===========================================================================
# One scenario per environment
# ===========================================================================

@RED
@pytest.mark.real_io
@pytest.mark.kpi
async def test_known_tiers_only_is_completely_silent(
    sqlite_repo, registered_guilds, update_channel, cycle_events,
    api_response, make_entry,
):
    """`known-tiers-only` — THE REGRESSION ENVIRONMENT.

    A suite that only covers the new tier will not catch an implementation that
    reports a discard on every cycle. Every other scenario in this feature
    passes against an implementation that warns constantly; this is the one
    that fails it.

    Three assertions, and the third is the pair the observability contract
    demands: silent to the human, explicit to the log.
    """
    raise AssertionError("RED scaffold — only pre-existing tiers, assert full silence")


@RED
@pytest.mark.real_io
@pytest.mark.kpi
async def test_mythic_3_live_is_the_incident_replay(
    sqlite_repo, registered_guilds, api_response, make_entry
):
    """`mythic-3-live`. Capture AND display, in one environment.

    Acceptance for this environment uses real cluster data, not a synthetic
    `set=2` fixture. A fixture proves the parser, which was never in doubt —
    the write path was rebuilt during the SQLite cutover and has only ever run
    against seven enumerated keys.
    """
    raise AssertionError("RED scaffold — ingest, then render")


@RED
@pytest.mark.error
async def test_tier_beyond_the_registry_is_captured_and_reported(
    sqlite_repo, registered_guilds, update_channel, cycle_events,
    api_response, make_entry,
):
    """`tier-beyond-the-registry`. The residual the feature reports rather than fixes.

    After Slice 02 this becomes close to unreachable — the derivation rule
    labels any well-formed key. It stays in the matrix because "close to
    unreachable" is not "impossible", and this whole feature exists because an
    unreachable-in-theory branch swallowed production data for weeks.
    """
    raise AssertionError("RED scaffold — set=3, assert stored + reported")


@RED
@pytest.mark.error
async def test_untracked_rarity_is_counted_and_named_never_adopted(
    sqlite_repo, registered_guilds, api_response, make_entry
):
    """`untracked-rarity`. The over-generalisation guard, at cycle grain."""
    raise AssertionError("RED scaffold — Epic/Rare/Uncommon/Common, assert no rows")


@RED
@pytest.mark.error
@pytest.mark.kpi
async def test_malformed_set_gives_each_case_its_own_reason(
    sqlite_repo, registered_guilds, api_response, make_entry
):
    """`malformed-set`. Kept separate from `untracked-rarity` on purpose.

    They share an outcome — nothing written — and differ in the only thing TK-5
    measures. Merged into one "hostile payload" environment they would pass
    against an implementation with one counter and no reasons, which is the
    state the feature is fixing.
    """
    raise AssertionError("RED scaffold — four malformed cases, assert the sum invariant")


@RED
@pytest.mark.driving_port
async def test_live_board_incomplete_adds_exactly_one_message(
    sqlite_repo, registered_guilds, live_channel, live_config_missing_mythic_3,
    seed_hits,
):
    """`live-board-incomplete`. Refresh twice; exactly one message appears."""
    raise AssertionError("RED scaffold — two refreshes, one send")


@RED
@pytest.mark.driving_port
@pytest.mark.error
async def test_live_board_rollover_race_produces_one_set(
    sqlite_repo, registered_guilds, live_channel, live_config, seed_hits
):
    """`live-board-rollover-race`. Per-tier counts, not a total."""
    raise AssertionError("RED scaffold — rollover + gap in one cycle")


@RED
@pytest.mark.driving_port
@pytest.mark.error
async def test_discord_send_refused_remembers_nothing_unsent(
    sqlite_repo, registered_guilds, live_channel, live_config_missing_mythic_3,
    seed_hits,
):
    """`discord-send-refused`. The map is byte-identical after a failure."""
    raise AssertionError("RED scaffold — refuse the send, assert the map unchanged")


@RED
@pytest.mark.real_io
@pytest.mark.error
async def test_historical_replay_labels_survive_the_registry(sqlite_repo):
    """`historical-replay-labels`. ADR-009 D4's entire bet, as an environment.

    A stored-LABEL precondition rather than a payload one, which is why it is
    its own environment and not folded into an ingest scenario.
    """
    raise AssertionError("RED scaffold — old-label replays, assert still returned")


@RED
@pytest.mark.error
async def test_picker_at_the_cap_refuses_to_start(monkeypatch):
    """`picker-at-the-cap`. Loud refusal beats a silently rejected sync."""
    raise AssertionError("RED scaffold — >25 registered, assert startup refusal")


@RED
@pytest.mark.real_io
@pytest.mark.error
async def test_json_backend_rollback_still_works(json_repo, api_response, make_entry):
    """`json-backend-rollback`. The environment an operator lands in under pressure.

    Slices 01-03 make NO repository change, so this backend must behave exactly
    as it does today — the parser, the counters and the reconciliation all sit
    above the repository boundary. Slice 04 is the exception and is asserted in
    that slice's own module.

    "Does not crash" is the whole requirement, and that is not a low bar: it is
    the requirement precisely because this is the path somebody takes when the
    primary one has already failed them.
    """
    raise AssertionError("RED scaffold — full cycle on the JSON backend")
