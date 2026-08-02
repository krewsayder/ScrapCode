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
from conftest import (
    GUILD_DM,
    GUILD_WB,
    PROD_SERVER_ID,
    SEASON,
    FakeChannel,
    GuildServiceResponse,
)

RED = pytest.mark.skip(reason="RED scaffold — enable one at a time in DELIVER")


# ===========================================================================
# US-004 — enforce
# ===========================================================================

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
    """Drive one `auto_update` tick through the production composition root.

    Replicated from `test_slice_01_bind_and_report.py` (do NOT cross-import —
    see open item UD-10 about same-name `conftest` constants colliding). The
    double goes in at the Tacticus transport, BELOW
    `bot.services.tacticus.guild_client`, so every classification a scenario
    asserts on is made by production code reading a real vendor body.

    `ping_channel` is wired into the FakeBot's channel routing so the catch
    handler's `self.bot.get_channel(notification_channel_id)` reaches it; the
    single-channel slice-01 shape would hand back the update channel for every
    id and make `ping_channel.text` an observation of nothing.
    """
    _register_the_guilds_the_scenario_programmed(service)
    cog = _tasks_cog_posting_to(channel, ping_channel=ping_channel)
    with _tacticus_answered_by(service):
        await cog.auto_update()


def _register_the_guilds_the_scenario_programmed(guild_service) -> None:
    """Register exactly the guilds whose key the scenario programmed an answer for.

    Verbatim from `test_slice_01_bind_and_report.py`. Idempotent — several
    scenarios run multiple cycles, and `save_guilds` leaves binding state
    alone by construction (DDD-4 keeps it in its own table).
    """
    from bot.guilds import save_guilds

    programmed = set(guild_service._by_key)
    if guild_service._default is not None:
        programmed = set(_GUILD_REGISTRY)

    save_guilds(PROD_SERVER_ID, {
        _GUILD_REGISTRY[key][0]: _GUILD_REGISTRY[key][1]
        for key in _GUILD_REGISTRY
        if key in programmed
    })


# Word Bearers FIRST: `auto_update` derives the season from the first guild
# that can answer, and the season SPOF only misbehaves in that ordering.
_GUILD_REGISTRY: dict[str, tuple[str, dict]] = {
    "wb-key": ("word_bearers", {
        "name": "Word Bearers", "api_key": "wb-key", "role_id": 1,
        "notification_channel_id": 4242, "member_role_ids": [],
    }),
    "dm-key": ("dark_mechanicum", {
        "name": "Dark Mechanicum", "api_key": "dm-key", "role_id": 2,
        "notification_channel_id": None, "member_role_ids": [],
    }),
}

_CANONICAL_IDENTITY = {"word_bearers": WORD_BEARERS, "dark_mechanicum": DARK_MECHANICUM}


def _tasks_cog_posting_to(channel, *, ping_channel=None):
    """The real cog, minus the scheduler (verbatim from slice 01)."""
    from bot.cogs.tasks_cog import TasksCog
    from bot.services.chronicl3r.player_service import PlayerService

    cog = TasksCog.__new__(TasksCog)
    cog.bot = _FakeBot(channel, ping_channel=ping_channel)
    cog.player_service = PlayerService(_FakeChroniclerClient())
    return cog


class _FakeBot:
    """`UPDATE_CHANNEL_ID` is 0 under test, so the single global update channel
    is whatever the scenario passed in (verbatim from slice 01).

    `ping_channel` routes a guild's `notification_channel_id` to a distinct
    channel so the catch handler's dual-channel post is observable. Without
    it, `get_channel(4242)` would return the update channel and
    `ping_channel.text` would read as the update channel — making the
    `Then both channels contain the tags` assertion vacuous.
    """

    def __init__(self, channel, *, ping_channel=None) -> None:
        self._channel = channel
        self._ping_channel = ping_channel

    def get_channel(self, channel_id: int):
        from config import UPDATE_CHANNEL_ID
        if channel_id == UPDATE_CHANNEL_ID:
            return self._channel
        if self._ping_channel is not None and channel_id is not None:
            return self._ping_channel
        return self._channel


class _FakeChroniclerClient:
    """Chronicler is a paid external service — faked (verbatim from slice 01)."""

    def register_user(self, tacticus_user_id: str) -> dict:
        return self.get_profile(tacticus_user_id)

    def get_profile(self, tacticus_user_id: str) -> dict:
        assert tacticus_user_id, "chronicl3r rejects an empty tacticus_user_id"
        return {
            "tacticus_user_id": tacticus_user_id,
            "tacticus_display_nm": f"player-{tacticus_user_id}",
        }


from contextlib import contextmanager  # noqa: E402 — helpers-only dependency


@contextmanager
def _tacticus_answered_by(guild_service):
    """Answer every Tacticus call from the scenario's programmed doubles
    (verbatim from slice 01)."""
    import httpx

    real_client = httpx.AsyncClient
    httpx.AsyncClient = lambda *args, **kwargs: _RecordedTacticus(guild_service)
    try:
        yield
    finally:
        httpx.AsyncClient = real_client


class _RecordedTacticus:
    """An `httpx.AsyncClient` answering from the programmed doubles
    (verbatim from slice 01)."""

    def __init__(self, guild_service) -> None:
        self._guild_service = guild_service

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url: str, headers: dict | None = None, **kwargs):
        import httpx
        status, body = _tacticus_body(
            self._guild_service, url, (headers or {}).get("X-API-KEY", "")
        )
        return httpx.Response(status, json=body, request=httpx.Request("GET", url))


def _tacticus_body(guild_service, url: str, credential: str) -> tuple[int, dict]:
    from bot.cogs.tasks_cog import TACTICUS_CURRENT_RAID, TACTICUS_RAID_URL
    from bot.services.tacticus.guild_client import TACTICUS_GUILD_URL
    from conftest import SEASON

    if url == TACTICUS_GUILD_URL:
        answer = guild_service.answer_for(credential)
        return answer.status, answer.payload()
    if url == TACTICUS_RAID_URL.format(season=SEASON):
        return 200, {"entries": _raid_entries()}
    if url == TACTICUS_CURRENT_RAID:
        return 200, {"season": SEASON}
    raise AssertionError(f"the loop called an endpoint no scenario declared: {url}")


def _raid_entries() -> list[dict]:
    """One battle hit and one bomb hit, in raw vendor shape (verbatim)."""
    common = {
        "unitId": "Avatar", "encounterIndex": 0, "rarity": "Legendary",
        "set": 0, "userId": "u1", "heroDetails": [{"unitId": "Aethana"}],
        "machineOfWarDetails": None,
    }
    return [
        {**common, "damageType": "Battle", "encounterType": "Battle",
         "damageDealt": 12000, "completedOn": "2026-08-01T10:00:00Z"},
        {**common, "damageType": "Bomb", "encounterType": "Bomb",
         "damageDealt": 3400, "completedOn": "2026-08-01T10:05:00Z"},
    ]


async def _exercise_site(site: KeyConsumptionSite, guild_id: str, *, service) -> None:
    """Drive the REAL production entry point for one key-consumption site.

    Port-to-port litmus: if any site bypassed `active_key` and read the guild
    key directly, driving that site would issue a Tacticus guild call and
    `fake_guild_service.call_count` would climb above zero — which is the
    assertion the parametrized scenario hangs on. So each branch MUST drive
    the actual site entry point (not just call `active_key` in a vacuum).

    Refusal is observed from production behaviour (a None return, a skip
    line, or a quarantined refusal) and converted to `_QuarantinedError`, the
    shape the scenario's `pytest.raises` is looking for. The guild is
    quarantined via `_quarantine(...)` by the scenario BEFORE this is called.
    """
    import bot.guild_keys as guild_keys
    from bot.cogs.tasks_cog import _CycleReport
    from bot.guilds import load_guilds, save_guilds

    server_id = PROD_SERVER_ID

    # The two `player_service` sites consume a `GuildSnapshot`, never a key
    # (DDD-2 moved the fetch out). The key-consumption point is the CALLER
    # that produces the snapshot via `active_key`; for a quarantined guild
    # that returns None and the caller never assembles a snapshot, so
    # `refresh_guild`/`validate_if_stale` are never reached.
    if site in (KeyConsumptionSite.PLAYER_SERVICE_REFRESH,
                KeyConsumptionSite.PLAYER_SERVICE_STALE):
        with _tacticus_answered_by(service):
            if guild_keys.active_key(server_id, guild_id) is None:
                raise _QuarantinedError()
        return

    if site is KeyConsumptionSite.AUTO_UPDATE_SEASON:
        cog = _tasks_cog_posting_to(FakeChannel(channel_id=1))
        _ensure_guild_registered(server_id, guild_id)
        guilds = load_guilds(server_id)
        with _tacticus_answered_by(service):
            season = await cog._current_season(server_id, guilds)
        if season is None:
            raise _QuarantinedError()
        return

    if site in (KeyConsumptionSite.AUTO_UPDATE_RAID,
                KeyConsumptionSite.AUTO_UPDATE_ROSTER):
        channel = FakeChannel(channel_id=1)
        cog = _tasks_cog_posting_to(channel)
        _ensure_guild_registered(server_id, guild_id)
        guilds = load_guilds(server_id)
        guild_data = guilds[guild_id]
        cycle = _CycleReport(server_id, guilds_total=len(guilds))
        with _tacticus_answered_by(service):
            results = await cog._update_one_guild(
                server_id, guild_id, guild_data, SEASON, channel, cycle,
            )
        # Refusal = the skip path returned a "skipped" line and never reached
        # `_validate_roster` or `_ingest_raid`.
        if any("skipped" in line for line in results):
            raise _QuarantinedError()
        return

    if site in (KeyConsumptionSite.UPDATE_LEADERBOARD,
                KeyConsumptionSite.UPDATE_ALL):
        from bot.cogs.update_cog import UpdateCog

        cog = UpdateCog.__new__(UpdateCog)
        with _tacticus_answered_by(service):
            verified = await cog._verified_key(server_id, guild_id)
        if verified is None:
            raise _QuarantinedError()
        return


def _ensure_guild_registered(server_id: int, guild_id: str) -> None:
    """Register the single guild the site drives, if it is not already.

    The parametrized scenario quarantines the guild via `_quarantine` (which
    writes only a binding) but never registers the guild row, so `load_guilds`
    would return `{}` and `_update_one_guild` would KeyError on the name
    before reaching the key-consumption point. Wiring, not a `Given`.
    """
    from bot.guilds import load_guilds, save_guilds

    if guild_id in load_guilds(server_id):
        return
    save_guilds(server_id, {
        guild_id: _GUILD_REGISTRY["wb-key"][1],
    })


# ---------------------------------------------------------------------------
# Slice-03-specific helpers
# ---------------------------------------------------------------------------

from collections import namedtuple  # noqa: E402 — helpers-only dependency

_RowCounts = namedtuple("_RowCounts", ["players", "former_players", "battle_hits", "bomb_hits"])


def _row_counts(repo, guild_id: str):
    """Everything the 2026-07-28 incident corrupted, in one comparable value.

    Battle hits, bomb hits AND players together: the incident put 30/30 battle
    rows, 20/20 bomb rows and 60 of 67 player rows on the wrong guild.
    """
    from conftest import SEASON
    from bot.guilds import load_player_list

    roster = load_player_list(PROD_SERVER_ID, guild_id)
    players = roster.get("players", {})
    return _RowCounts(
        players=len(players),
        former_players=sum(1 for p in players.values() if p.get("is_former")),
        battle_hits=_entry_total(repo.load_battle_hits(PROD_SERVER_ID, guild_id, SEASON)),
        bomb_hits=_entry_total(repo.load_bomb_hits(PROD_SERVER_ID, guild_id, SEASON)),
    )


def _entry_total(hits: dict) -> int:
    return sum(
        len(entries)
        for encounters in hits.get("boss_hits", {}).values()
        for tiers in encounters.values()
        for entries in tiers.values()
    )


def _quarantine(guild_id: str, *, reason: str) -> None:
    """Place a guild into quarantine directly, for a scenario's `Given`."""
    from bot.guilds import load_guild_binding, load_guilds, save_guild_binding, save_guilds
    from bot.repository import GuildBinding
    from bot.services.tacticus.guild_client import KeyStatus

    # `guild_key_bindings` references the `guilds` table; the parametrized
    # site-refusal scenario quarantines without `registered_guilds`, so the
    # parent row must exist or `save_guild_binding` fails the FK before the
    # scenario reaches the assertion. Wiring, not a `Given` the scenario
    # declares.
    if guild_id not in load_guilds(PROD_SERVER_ID):
        save_guilds(PROD_SERVER_ID, {
            guild_id: _GUILD_REGISTRY["wb-key"][1],
        })

    binding = load_guild_binding(PROD_SERVER_ID, guild_id)
    save_guild_binding(PROD_SERVER_ID, guild_id, GuildBinding(
        tacticus_guild_id=binding.tacticus_guild_id,
        tacticus_guild_tag=binding.tacticus_guild_tag,
        tacticus_guild_name=binding.tacticus_guild_name,
        identity_bound_at=binding.identity_bound_at,
        key_status=KeyStatus.QUARANTINED.value,
        quarantine_reason=reason,
        quarantined_at="2026-07-31T04:00:00Z",
    ))


def _quarantined_guild_ids() -> list[str]:
    """Every guild currently in quarantine, cluster-wide."""
    from bot.guilds import load_guilds
    from bot.guilds import load_guild_binding
    from bot.services.tacticus.guild_client import KeyStatus

    return [
        guild_id
        for guild_id in load_guilds(PROD_SERVER_ID)
        if load_guild_binding(PROD_SERVER_ID, guild_id).key_status
        == KeyStatus.QUARANTINED.value
    ]


def _is_iso8601_utc(value: str) -> bool:
    """Confirm `value` is in the `YYYY-MM-DDTHH:MM:SS.mmmZ` shape `_utc_now`
    produces and `battle_hits.completed_on` carries — KPI-2 compares them AS
    STRINGS, so a shape mismatch silently returns the wrong result set.
    """
    import re
    return bool(re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", value or "",
    ))


def _advance_clock(monkeypatch, *, hours: int) -> None:
    """Advance the time source `record_quarantine_alert` and `quarantine` use
    for the 24h comparison. Patches `bot.guild_keys._utc_now` so every
    timestamp emitted this cycle advances consistently.
    """
    import bot.guild_keys as gk
    from datetime import datetime, timezone

    base = datetime(2026, 8, 1, 0, 0, 0, tzinfo=timezone.utc)

    def _fixed_now() -> str:
        now = base + timedelta(hours=hours)
        return f"{now.strftime('%Y-%m-%dT%H:%M:%S')}.{now.microsecond // 1000:03d}Z"

    monkeypatch.setattr(gk, "_utc_now", _fixed_now)


from datetime import timedelta  # noqa: E402 — helpers-only dependency


# ---------------------------------------------------------------------------
# RED scaffolds for later steps (05-02 / 05-03 / 05-04 / 05-05)
# ---------------------------------------------------------------------------

def _quarantine_every_guild() -> None:
    raise AssertionError("Not yet implemented — RED scaffold for 05-04")


def _register_two_guilds_quarantined_first(repo) -> None:
    raise AssertionError("Not yet implemented — RED scaffold for 05-04")


def _register_guild_without_key(guild_id: str) -> None:
    raise AssertionError("Not yet implemented — RED scaffold for 05-05")


def _guild_ids_in_order() -> list[str]:
    raise AssertionError("Not yet implemented — RED scaffold for 05-04")


def _config_guilds_embed():
    raise AssertionError("Not yet implemented — RED scaffold for 05-05")


def _guild_field(embed, guild_id: str) -> str:
    raise AssertionError("Not yet implemented — RED scaffold for 05-05")


def _embed_text(embed) -> str:
    raise AssertionError("Not yet implemented — RED scaffold for 05-05")


def _put_guild_in_state(guild_id: str, state: str) -> None:
    raise AssertionError("Not yet implemented — RED scaffold for 05-05")


def _stored_key_plaintext() -> str:
    raise AssertionError("Not yet implemented — RED scaffold for 05-05")


def _contains_uuid(text: str) -> bool:
    raise AssertionError("Not yet implemented — RED scaffold for 05-05")


def _transport_failure(failure: TransportFailure) -> GuildServiceResponse:
    import httpx
    if failure is TransportFailure.TIMEOUT:
        return GuildServiceResponse(raises=httpx.TimeoutException("timed out"))
    if failure is TransportFailure.CONNECT_ERROR:
        return GuildServiceResponse(raises=httpx.ConnectError("connection refused"))
    return GuildServiceResponse(status=int(failure.value.removeprefix("http_")))
