"""Environmental realism (Mandate 4). Implements
`acceptance/environment-matrix.feature`.

One test per target environment in
`docs/feature/guild-key-integrity/environments.yaml`, each asserting that
environment's headline invariant rather than re-running the slice suites.
The traceability test at the bottom is what stops the DEVOPS artifact and
this suite from quietly drifting apart.
"""
from __future__ import annotations

import pytest

from domain_types import DARK_MECHANICUM, WORD_BEARERS, Environment
from conftest import (
    GUILD_DM,
    GUILD_WB,
    PROD_SERVER_ID,
    GuildServiceResponse,
    environment_names_from_devops_artifact,
)

RED = pytest.mark.skip(reason="RED scaffold — enable one at a time in DELIVER")


# ===========================================================================
# Traceability — runs GREEN today. Not a scaffold.
# ===========================================================================

@pytest.mark.traceability
def test_environment_names_match_the_devops_artifact():
    """The suite's environment vocabulary and `environments.yaml` are the
    same list, or the suite is testing environments nobody deploys into.

    Deliberately NOT skipped: it asserts a property of two files that both
    exist right now, so it is meaningful before a line of the feature is
    written. If it fails, one of the two documents is wrong.
    """
    from_yaml = environment_names_from_devops_artifact()
    from_suite = [e.value for e in Environment]

    assert from_yaml, "environments.yaml parsed to an empty list"
    assert sorted(from_yaml) == sorted(from_suite), (
        f"environments.yaml declares {sorted(from_yaml)} but the suite covers "
        f"{sorted(from_suite)} — Mandate 4 coverage has a hole"
    )


# ===========================================================================
# One per environment
# ===========================================================================

@RED
@pytest.mark.real_io
async def test_clean_adopts_every_identity_once(
    sqlite_repo, fake_guild_service, update_channel, key_events
):
    """`clean` — trust-on-first-use. Also the state EVERY production guild
    is in on the day Slice 01 deploys, which is why it is asserted across
    three guilds rather than one: the announcement volume on deploy day is
    what the operator actually experiences."""
    _register_guilds(sqlite_repo, count=3)
    fake_guild_service.program_default(GuildServiceResponse(identity=WORD_BEARERS))

    await _run_hourly_cycle(fake_guild_service, update_channel)
    assert len(key_events.named("guild.key.bound")) == 3
    assert len(_adoption_messages(update_channel)) == 3

    update_channel.messages.clear()
    await _run_hourly_cycle(fake_guild_service, update_channel)
    assert _adoption_messages(update_channel) == []


@RED
@pytest.mark.kpi
async def test_bound_matching_is_completely_silent(
    sqlite_repo, matching_guild, update_channel, ping_channel, key_events
):
    """`bound-matching` — the steady state, and the environment a
    drift-only suite never visits. Every alerting scenario in this feature
    passes against an implementation that alerts unconditionally; this is
    the one that does not."""
    before = _row_counts(sqlite_repo, GUILD_WB)

    await _run_hourly_cycle(matching_guild, update_channel, ping_channel=ping_channel)

    assert not key_events.any_named(
        "guild.key.mismatch", "guild.key.quarantined",
        "guild.key.unverifiable", "guild.key.alert.sent",
    )
    assert ping_channel.messages == []
    assert _row_counts(sqlite_repo, GUILD_WB).battle_hits > before.battle_hits
    assert key_events.named("guild.key.probe.ok")


@RED
@pytest.mark.kpi
async def test_bound_drifted_writes_nothing_once_enforcement_is_on(
    sqlite_repo, drifted_guild, update_channel
):
    """`bound-drifted` — the incident replay, end to end.

    Baseline for KPI-2 is 50 contaminated hit rows plus 60 corrupted player
    rows. The target is zero, and this is the measurement.
    """
    before = _row_counts(sqlite_repo, GUILD_WB)

    await _run_hourly_cycle(drifted_guild, update_channel, enforcement=True)

    assert _row_counts(sqlite_repo, GUILD_WB) == before


@RED
@pytest.mark.error
@pytest.mark.kpi
async def test_unverifiable_alerts_loudly_and_blocks_nothing(
    sqlite_repo, fake_guild_service, update_channel, key_events
):
    """`unverifiable` — the vendor-change environment, cluster-wide.

    Asserted across every guild because that is how it would actually
    arrive: Tacticus drops the field and all guilds go unverifiable in the
    same cycle. Blocking would be an outage caused by someone else's
    release.
    """
    _register_guilds(sqlite_repo, count=3)
    fake_guild_service.program_default(
        GuildServiceResponse(identity=WORD_BEARERS, drop_fields=("guildId",))
    )

    await _run_hourly_cycle(fake_guild_service, update_channel, enforcement=True)

    assert _quarantined_guild_ids() == []
    assert _all_guilds_ingested(sqlite_repo)
    assert len(key_events.named("guild.key.unverifiable")) == 3
    assert "verification is offline" in update_channel.text.lower()
    assert not key_events.any_named("guild.key.mismatch")


@RED
@pytest.mark.error
@pytest.mark.kpi
async def test_tacticus_unreachable_leaves_every_binding_byte_identical(
    sqlite_repo, fake_guild_service, update_channel
):
    """`tacticus-unreachable` — D6 at cluster scale."""
    import httpx

    _register_guilds(sqlite_repo, count=3)
    before = _all_bindings()
    fake_guild_service.program_default(
        GuildServiceResponse(raises=httpx.ConnectError("no route to host"))
    )

    await _run_hourly_cycle(fake_guild_service, update_channel, enforcement=True)

    assert _quarantined_guild_ids() == []
    assert _all_bindings() == before


@RED
@pytest.mark.error
async def test_dead_key_is_reported_not_quarantined(
    sqlite_repo, fake_guild_service, update_channel, key_events
):
    """`dead-key` — 401/403. Nothing to contaminate, so nothing to
    quarantine; the operator just needs to be told."""
    fake_guild_service.program("wb-key", GuildServiceResponse(status=401))

    await _run_hourly_cycle(fake_guild_service, update_channel, enforcement=True)

    assert key_events.named("guild.key.dead")
    assert _quarantined_guild_ids() == []
    assert "update_guild_key" in update_channel.text


@RED
@pytest.mark.kpi
async def test_mixed_cluster_survives_a_quarantined_first_guild(
    sqlite_repo, fake_guild_service, update_channel, key_events
):
    """`mixed-cluster` — the season SPOF, with the ordering pinned.

    The precondition assertion is not decoration: if the fixture stops
    putting the quarantined guild first, this test keeps passing while
    testing nothing, which is the exact failure the environment note in
    `environments.yaml` warns about.
    """
    _register_two_guilds_quarantined_first(sqlite_repo)
    assert _guild_ids_in_order()[0] == GUILD_WB, "ordering precondition lost"

    fake_guild_service.program("dm-key", GuildServiceResponse(identity=DARK_MECHANICUM))

    await _run_hourly_cycle(fake_guild_service, update_channel, enforcement=True)

    (cycle,) = key_events.named("auto_update.cycle")
    assert cycle.season is not None
    assert cycle.guilds_total == 2
    assert cycle.guilds_processed == 1
    assert cycle.guilds_skipped == 1
    assert cycle.skip_reasons


@pytest.mark.error
@pytest.mark.real_io
@pytest.mark.adapter_integration
async def test_json_backend_rollback_goes_inert_without_raising(
    json_repo, fake_guild_service, update_channel, key_events
):
    """`json-backend-rollback` — ADR-006 D9.

    The environment an operator lands in when Slice 01 or 03 has to be
    rolled back under time pressure. "Does not crash" is the whole
    requirement: the feature must go inert, not half-work.
    """
    from bot.guilds import load_guild_binding, save_guild_binding

    assert load_guild_binding(PROD_SERVER_ID, GUILD_WB).is_unbound
    save_guild_binding(PROD_SERVER_ID, GUILD_WB, _a_binding())
    assert load_guild_binding(PROD_SERVER_ID, GUILD_WB).is_unbound, (
        "the JSON adapter persisted a binding — it must no-op the write"
    )

    await _run_hourly_cycle(fake_guild_service, update_channel, enforcement=True)

    assert _quarantined_guild_ids() == []
    assert not key_events.any_named("guild.key.quarantined")


# ===========================================================================
# Helpers — wiring only
# ===========================================================================

async def _run_hourly_cycle(service, channel, *, ping_channel=None, enforcement=False):
    raise AssertionError("Not yet implemented — RED scaffold")


def _register_guilds(repo, *, count: int) -> None:
    raise AssertionError("Not yet implemented — RED scaffold")


def _register_two_guilds_quarantined_first(repo) -> None:
    raise AssertionError("Not yet implemented — RED scaffold")


def _guild_ids_in_order() -> list[str]:
    raise AssertionError("Not yet implemented — RED scaffold")


def _row_counts(repo, guild_id: str):
    raise AssertionError("Not yet implemented — RED scaffold")


def _quarantined_guild_ids() -> list[str]:
    raise AssertionError("Not yet implemented — RED scaffold")


def _all_bindings() -> dict:
    raise AssertionError("Not yet implemented — RED scaffold")


def _all_guilds_ingested(repo) -> bool:
    raise AssertionError("Not yet implemented — RED scaffold")


def _adoption_messages(channel) -> list[str]:
    raise AssertionError("Not yet implemented — RED scaffold")


def _a_binding():
    raise AssertionError("Not yet implemented — RED scaffold")
