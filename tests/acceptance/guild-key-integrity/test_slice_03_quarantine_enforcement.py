"""Slice 03 — quarantine enforcement. Implements
`acceptance/slice-03-quarantine-enforcement.feature`.

Covers US-004 (enforce) and US-005 (show it). Deploys only after Slice 02
exists and after the 7-day soak of Slice 01 (DEVOPS D11).

The two tests that carry the most weight here are
`test_every_key_consumption_site_refuses_a_quarantined_guild` (a guard on six
of seven sites is a silent contamination path) and
`test_a_quarantined_guild_listed_first_does_not_stop_the_server` (fixing the
bug in a way that halts every other guild is strictly worse than the bug).
"""
from __future__ import annotations

import pytest

from domain_types import (
    DARK_MECHANICUM,
    WORD_BEARERS,
    KeyConsumptionSite,
    KeyStatus,
    TransportFailure,
)
from conftest import GUILD_DM, GUILD_WB, PROD_SERVER_ID, GuildServiceResponse

RED = pytest.mark.skip(reason="RED scaffold — enable one at a time in DELIVER")


# ===========================================================================
# US-004 — enforce
# ===========================================================================

@RED
@pytest.mark.kpi
@pytest.mark.driving_port
async def test_a_drifted_guild_is_quarantined_with_both_identities_recorded(
    sqlite_repo, drifted_guild, update_channel, key_events
):
    """AC-004.1. `quarantine_reason` carries both identities because the
    operator reading it a week later has no other way to reconstruct what
    the key was pointing at."""
    from bot.guilds import load_guild_binding

    await _run_hourly_cycle(drifted_guild, update_channel)

    binding = load_guild_binding(PROD_SERVER_ID, GUILD_WB)
    assert binding.key_status == KeyStatus.QUARANTINED.value
    assert WORD_BEARERS.tag in binding.quarantine_reason
    assert DARK_MECHANICUM.tag in binding.quarantine_reason
    assert binding.quarantined_at is not None

    # KPI-2 compares quarantined_at against battle_hits.completed_on as
    # STRINGS. A shape mismatch returns a wrong result set silently instead
    # of erroring, so the shape is asserted here rather than discovered
    # during an incident review.
    assert _is_iso8601_utc(binding.quarantined_at), (
        f"quarantined_at={binding.quarantined_at!r} is not in the same shape "
        "as battle_hits.completed_on — KPI-2's query will silently mislead"
    )


@RED
@pytest.mark.kpi
async def test_a_quarantined_guild_writes_not_one_raid_record(
    sqlite_repo, drifted_guild, update_channel, key_events
):
    """AC-004.2 / KPI-2. The incident wrote 30 of 30 battle rows and 20 of
    20 bomb rows from the wrong guild into season 106."""
    before = _row_counts(sqlite_repo, GUILD_WB)

    await _run_hourly_cycle(drifted_guild, update_channel)

    after = _row_counts(sqlite_repo, GUILD_WB)
    assert after.battle_hits == before.battle_hits
    assert after.bomb_hits == before.bomb_hits
    assert key_events.named("guild.key.ingest.blocked")


@RED
@pytest.mark.kpi
async def test_a_quarantined_guild_writes_not_one_roster_record(
    sqlite_repo, drifted_guild, update_channel
):
    """AC-004.3 / DDD-5 — the half that is easy to forget.

    Roster inversion was the LARGER share of the damage: 60 of 67 `players`
    rows corrupted, against 50 hit rows. Blocking hits alone would have left
    the worse half running, and `is_former` flips are destructive in a way
    hit rows are not — the bot marks real members as departed.
    """
    before = _row_counts(sqlite_repo, GUILD_WB)

    await _run_hourly_cycle(drifted_guild, update_channel)

    after = _row_counts(sqlite_repo, GUILD_WB)
    assert after.players == before.players
    assert after.former_players == before.former_players


@RED
@pytest.mark.driving_port
async def test_entering_quarantine_alerts_both_channels(
    sqlite_repo, drifted_guild, update_channel, ping_channel, key_events
):
    """AC-004.4. The guild's own officers see their leaderboard stop moving;
    they need to know why without asking the cluster admin."""
    await _run_hourly_cycle(drifted_guild, update_channel, ping_channel=ping_channel)

    for channel in (update_channel, ping_channel):
        assert WORD_BEARERS.tag in channel.text
        assert DARK_MECHANICUM.tag in channel.text

    assert key_events.named("guild.key.alert.sent")


@RED
@pytest.mark.error
async def test_a_persisting_quarantine_alerts_at_most_once_a_day(
    sqlite_repo, drifted_guild, update_channel, key_events, monkeypatch
):
    """AC-004.5. An hourly loop that alerts hourly gets the channel muted,
    and a muted channel defeats KPI-1 entirely — detection that nobody reads
    is not detection.

    Suppressed alerts are RECORDED as suppressed, not dropped: otherwise the
    log cannot distinguish "we suppressed 23 alerts" from "the loop stopped".
    """
    for hour in range(24):
        _advance_clock(monkeypatch, hours=hour)
        await _run_hourly_cycle(drifted_guild, update_channel)

    assert len(key_events.named("guild.key.alert.sent")) == 1
    assert len(key_events.named("guild.key.alert.suppressed")) == 23


@RED
@pytest.mark.kpi
@pytest.mark.parametrize("site", list(KeyConsumptionSite), ids=lambda s: s.name.lower())
async def test_every_key_consumption_site_refuses_a_quarantined_guild(
    sqlite_repo, fake_guild_service, site: KeyConsumptionSite
):
    """AC-004.6 / DDD-3 — the reason the chokepoint exists.

    Seven call sites span three cogs plus a service. Six-of-seven is not
    "mostly fixed", it is a silent contamination path that looks fixed. The
    enum in `domain_types.py` is this suite's definition of "all of them";
    an eighth site added without a row there is invisible to this test,
    which is why the architecture test also runs.
    """
    _quarantine(GUILD_WB, reason="resolved to Dark Mechanicum")

    with pytest.raises(_QuarantinedError):
        await _exercise_site(site, GUILD_WB, service=fake_guild_service)

    assert fake_guild_service.call_count == 0, (
        f"{site.value} fetched the guild's data before refusing — the other "
        "guild's roster reached memory and possibly the logs"
    )


@RED
@pytest.mark.kpi
async def test_a_quarantined_guild_listed_first_does_not_stop_the_server(
    sqlite_repo, fake_guild_service, update_channel, key_events
):
    """AC-004.7 / DDD-7 — the season SPOF.

    `auto_update` derives the season from `next(iter(guilds.values()))` and
    skips the ENTIRE server when that fails (tasks_cog.py:173). The defect is
    only reachable when the quarantined guild is FIRST in dict order: a
    scenario that happens to place it second passes while the bug is fully
    present. The fixture pins the ordering for exactly that reason.
    """
    _register_two_guilds_quarantined_first(sqlite_repo)
    fake_guild_service.program("dm-key", GuildServiceResponse(identity=DARK_MECHANICUM))

    await _run_hourly_cycle(fake_guild_service, update_channel)

    assert _guild_ids_in_order()[0] == GUILD_WB, "fixture ordering precondition lost"

    (cycle,) = key_events.named("auto_update.cycle")
    assert cycle.season is not None, "the server was skipped — the SPOF is still present"
    assert cycle.guilds_processed == 1
    assert cycle.guilds_skipped == 1


@RED
@pytest.mark.error
async def test_a_server_with_no_usable_key_is_skipped_for_a_stated_reason(
    sqlite_repo, fake_guild_service, update_channel, key_events
):
    """AC-004.8. A silent `continue` is how the original whole-server skip
    would have looked — no signal of any kind."""
    _quarantine_every_guild()

    await _run_hourly_cycle(fake_guild_service, update_channel)

    (cycle,) = key_events.named("auto_update.cycle")
    assert cycle.guilds_processed == 0
    assert cycle.skip_reasons, "guilds were skipped with no reason recorded"
    assert "quarantined" in " ".join(cycle.skip_reasons)


@RED
@pytest.mark.kpi
async def test_a_healthy_guild_beside_a_quarantined_one_updates_normally(
    sqlite_repo, fake_guild_service, update_channel, key_events
):
    """AC-004.9 / KPI-5 — blast-radius containment.

    KPI-5's baseline is 0%: today one bad key halts the whole server. The
    target is 100%, and this is the scenario that measures it.
    """
    _register_two_guilds_quarantined_first(sqlite_repo)
    fake_guild_service.program("dm-key", GuildServiceResponse(identity=DARK_MECHANICUM))
    before = _row_counts(sqlite_repo, GUILD_DM)

    await _run_hourly_cycle(fake_guild_service, update_channel)

    assert _row_counts(sqlite_repo, GUILD_DM).battle_hits > before.battle_hits
    assert "✅" in update_channel.text
    assert "⛔" in update_channel.text

    (cycle,) = key_events.named("auto_update.cycle")
    assert cycle.guilds_total == 2
    assert cycle.guilds_processed == 1
    assert cycle.guilds_skipped == 1


@RED
@pytest.mark.error
@pytest.mark.kpi
@pytest.mark.parametrize("failure", list(TransportFailure), ids=lambda f: f.value)
async def test_an_outage_never_quarantines_anything(
    sqlite_repo, fake_guild_service, update_channel, failure: TransportFailure
):
    """AC-004.10 / D6. Asserted cluster-wide, not per-guild: a Tacticus
    outage hits every guild at once, so the failure mode being guarded is
    "the whole cluster quarantined itself overnight"."""
    fake_guild_service.program_default(_transport_failure(failure))

    await _run_hourly_cycle(fake_guild_service, update_channel)

    assert _quarantined_guild_ids() == []


@RED
@pytest.mark.error
@pytest.mark.kpi
async def test_a_missing_identifier_never_quarantines_anything(
    sqlite_repo, fake_guild_service, update_channel, key_events
):
    """DDD-10 at enforcement scale — the vendor-change blast radius.

    `guildId` is undocumented. If Tacticus drops it, EVERY guild becomes
    unverifiable in the same cycle. Quarantining on that would take the whole
    cluster down over someone else's release note, so `unverifiable` alerts
    loudly and blocks nothing even once enforcement is live.
    """
    fake_guild_service.program_default(
        GuildServiceResponse(identity=WORD_BEARERS, drop_fields=("guildId",))
    )
    before = _row_counts(sqlite_repo, GUILD_WB)

    await _run_hourly_cycle(fake_guild_service, update_channel)

    assert _quarantined_guild_ids() == []
    assert _row_counts(sqlite_repo, GUILD_WB).battle_hits > before.battle_hits
    assert key_events.named("guild.key.unverifiable")
    assert "verification is offline" in update_channel.text.lower()


# ===========================================================================
# US-005 — show it
# ===========================================================================

@RED
@pytest.mark.driving_port
def test_the_guild_list_shows_quarantine_and_why(sqlite_repo, env_vars):
    """AC-005.1."""
    _quarantine(GUILD_WB, reason=f"resolves to {DARK_MECHANICUM.tag}, expected {WORD_BEARERS.tag}")

    field = _guild_field(_config_guilds_embed(), GUILD_WB)

    assert "⛔" in field
    assert DARK_MECHANICUM.tag in field
    assert WORD_BEARERS.tag in field
    assert "2026" in field


@RED
@pytest.mark.driving_port
def test_the_guild_list_shows_a_healthy_guild_as_verified(sqlite_repo, env_vars):
    """AC-005.2."""
    field = _guild_field(_config_guilds_embed(), GUILD_WB)

    assert "✅" in field
    assert WORD_BEARERS.tag in field
    assert "verified" in field.lower()


@RED
@pytest.mark.error
def test_a_guild_with_no_key_reads_exactly_as_before(sqlite_repo, env_vars):
    """AC-005.3 — an explicit no-regression assertion on the existing
    rendering (`admin_cog.py:183`), so the new states are additive."""
    _register_guild_without_key("keyless")

    field = _guild_field(_config_guilds_embed(), "keyless")

    assert "❌ Missing" in field


@RED
@pytest.mark.error
@pytest.mark.kpi
@pytest.mark.parametrize("state", ["healthy", "quarantined", "unbound", "keyless"])
def test_no_key_value_and_no_full_identifier_is_ever_shown(
    sqlite_repo, env_vars, state: str
):
    """AC-005.4 / KPI-6. `/view_config` is officer-tier and NOT ephemeral,
    so anything it renders is visible to everyone in the channel."""
    _put_guild_in_state(GUILD_WB, state)

    rendered = _embed_text(_config_guilds_embed())

    assert _stored_key_plaintext() not in rendered
    assert WORD_BEARERS.uuid not in rendered
    assert DARK_MECHANICUM.uuid not in rendered
    assert not _contains_uuid(rendered), "a full identifier leaked into the embed"


# ===========================================================================
# Helpers — wiring only
# ===========================================================================

class _QuarantinedError(Exception):
    """Placeholder for the refusal the chokepoint raises."""


async def _run_hourly_cycle(service, channel, *, ping_channel=None) -> None:
    raise AssertionError("Not yet implemented — RED scaffold")


async def _exercise_site(site: KeyConsumptionSite, guild_id: str, *, service) -> None:
    raise AssertionError("Not yet implemented — RED scaffold")


def _row_counts(repo, guild_id: str):
    """Namedtuple-ish: .players, .former_players, .battle_hits, .bomb_hits."""
    raise AssertionError("Not yet implemented — RED scaffold")


def _quarantine(guild_id: str, *, reason: str) -> None:
    raise AssertionError("Not yet implemented — RED scaffold")


def _quarantine_every_guild() -> None:
    raise AssertionError("Not yet implemented — RED scaffold")


def _quarantined_guild_ids() -> list[str]:
    raise AssertionError("Not yet implemented — RED scaffold")


def _register_two_guilds_quarantined_first(repo) -> None:
    raise AssertionError("Not yet implemented — RED scaffold")


def _register_guild_without_key(guild_id: str) -> None:
    raise AssertionError("Not yet implemented — RED scaffold")


def _guild_ids_in_order() -> list[str]:
    raise AssertionError("Not yet implemented — RED scaffold")


def _config_guilds_embed():
    raise AssertionError("Not yet implemented — RED scaffold")


def _guild_field(embed, guild_id: str) -> str:
    raise AssertionError("Not yet implemented — RED scaffold")


def _embed_text(embed) -> str:
    raise AssertionError("Not yet implemented — RED scaffold")


def _put_guild_in_state(guild_id: str, state: str) -> None:
    raise AssertionError("Not yet implemented — RED scaffold")


def _stored_key_plaintext() -> str:
    raise AssertionError("Not yet implemented — RED scaffold")


def _contains_uuid(text: str) -> bool:
    raise AssertionError("Not yet implemented — RED scaffold")


def _is_iso8601_utc(value: str) -> bool:
    raise AssertionError("Not yet implemented — RED scaffold")


def _advance_clock(monkeypatch, *, hours: int) -> None:
    raise AssertionError("Not yet implemented — RED scaffold")


def _transport_failure(failure: TransportFailure) -> GuildServiceResponse:
    import httpx
    if failure is TransportFailure.TIMEOUT:
        return GuildServiceResponse(raises=httpx.TimeoutException("timed out"))
    if failure is TransportFailure.CONNECT_ERROR:
        return GuildServiceResponse(raises=httpx.ConnectError("connection refused"))
    return GuildServiceResponse(status=int(failure.value.removeprefix("http_")))
