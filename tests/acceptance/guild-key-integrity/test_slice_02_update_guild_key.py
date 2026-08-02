"""Slice 02 — `/update_guild_key`. Implements
`acceptance/slice-02-update-guild-key.feature`.

Covers US-003. This is the recovery path and it ships BEFORE enforcement:
without it the first quarantine is unrecoverable without an SSH session,
which is the procedure the feature exists to retire.

Driving port: `bot.cogs.admin_cog.AdminCog.update_guild_key`, invoked through
a Discord interaction double rather than by calling a service function — the
ACs are about permission tiers, ephemeral replies and autocomplete, none of
which a service-level call exercises.
"""
from __future__ import annotations

import pytest

from domain_types import (
    DARK_MECHANICUM,
    WORD_BEARERS,
    DeadKeyStatus,
    TransportFailure,
)
from conftest import GUILD_WB, PROD_SERVER_ID, GuildServiceResponse

RED = pytest.mark.skip(reason="RED scaffold — enable one at a time in DELIVER")

NEW_KEY = "new-key-for-word-bearers"


# ===========================================================================

@RED
@pytest.mark.driving_port
@pytest.mark.real_io
async def test_admin_installs_a_new_key_and_is_told_which_guild_it_belongs_to(
    sqlite_repo, fake_guild_service, key_events
):
    """AC-003.1 + KPI-3.

    The reply naming the resolved guild is the pivot of the journey: it is
    the information that did not exist during the incident, shown before the
    key is trusted rather than three days after.
    """
    fake_guild_service.program(NEW_KEY, GuildServiceResponse(identity=WORD_BEARERS))
    interaction = _admin_interaction()

    await _invoke_update_guild_key(interaction, GUILD_WB, NEW_KEY, service=fake_guild_service)

    assert _stored_key(GUILD_WB) == NEW_KEY
    assert _stored_key_hmac(GUILD_WB) == _expected_hmac(NEW_KEY)
    assert WORD_BEARERS.name in interaction.reply_text
    assert interaction.was_ephemeral

    (record,) = key_events.named("guild.key.updated")
    assert record.elapsed_ms >= 0


@pytest.mark.kpi
async def test_replacing_a_key_destroys_nothing(
    sqlite_repo, fake_guild_service, guild_with_recorded_rows
):
    """AC-003.2 — the property that makes this command safe where the
    documented workaround is not.

    `/deregister_guild` + `/register_guild` CASCADE-deletes every player and
    hit row for the guild. That is the procedure this command replaces, so
    "no data loss" is not a nice-to-have, it is the entire justification.
    """
    fake_guild_service.program(NEW_KEY, GuildServiceResponse(identity=WORD_BEARERS))
    before = _row_counts(sqlite_repo, GUILD_WB)
    assert all(n > 0 for n in before), "fixture must have rows for this to mean anything"

    await _invoke_update_guild_key(
        _admin_interaction(), GUILD_WB, NEW_KEY, service=fake_guild_service
    )

    assert _row_counts(sqlite_repo, GUILD_WB) == before


@RED
@pytest.mark.error
@pytest.mark.kpi
async def test_a_key_for_the_wrong_guild_is_refused(sqlite_repo, fake_guild_service):
    """AC-003.3. The probe runs against the SUBMITTED key, before storing —
    an unverified key is never written, so a fat-fingered paste cannot
    recreate the incident."""
    fake_guild_service.program(NEW_KEY, GuildServiceResponse(identity=DARK_MECHANICUM))
    before = _stored_key(GUILD_WB)
    interaction = _admin_interaction()

    await _invoke_update_guild_key(interaction, GUILD_WB, NEW_KEY, service=fake_guild_service)

    assert _stored_key(GUILD_WB) == before
    assert WORD_BEARERS.name in interaction.reply_text
    assert DARK_MECHANICUM.name in interaction.reply_text


@RED
async def test_force_installs_the_key_and_rebinds(sqlite_repo, fake_guild_service, key_events):
    """AC-003.4 / DDD-9.

    `force:true` is a parameter rather than a confirmation button because a
    `View` holds the plaintext key in process memory until the click or the
    timeout — a credential sitting in memory for three minutes waiting on a
    human.
    """
    from bot.guilds import load_guild_binding

    fake_guild_service.program(NEW_KEY, GuildServiceResponse(identity=DARK_MECHANICUM))

    await _invoke_update_guild_key(
        _admin_interaction(), GUILD_WB, NEW_KEY, force=True, service=fake_guild_service
    )

    assert _stored_key(GUILD_WB) == NEW_KEY
    assert load_guild_binding(PROD_SERVER_ID, GUILD_WB).tacticus_guild_id == DARK_MECHANICUM.uuid

    (record,) = key_events.named("guild.key.updated")
    assert record.forced is True
    assert record.rebound_from == WORD_BEARERS.uuid


@RED
@pytest.mark.error
@pytest.mark.parametrize("status", list(DeadKeyStatus), ids=lambda s: str(s.value))
async def test_a_rejected_key_is_never_installed(
    sqlite_repo, fake_guild_service, status: DeadKeyStatus
):
    """AC-003.5."""
    fake_guild_service.program(NEW_KEY, GuildServiceResponse(status=status.value))
    before = _stored_key(GUILD_WB)
    interaction = _admin_interaction()

    await _invoke_update_guild_key(interaction, GUILD_WB, NEW_KEY, service=fake_guild_service)

    assert _stored_key(GUILD_WB) == before
    assert "dead" in interaction.reply_text.lower() or "rejected" in interaction.reply_text.lower()


@RED
@pytest.mark.error
@pytest.mark.parametrize(
    "failure",
    [TransportFailure.TIMEOUT, TransportFailure.CONNECT_ERROR, TransportFailure.SERVER_ERROR_503],
    ids=lambda f: f.value,
)
async def test_an_unverifiable_key_is_never_installed(
    sqlite_repo, fake_guild_service, failure: TransportFailure
):
    """AC-003.6 — never install a key you could not check.

    Note the asymmetry with the hourly loop, and that it is deliberate: an
    outage must NOT quarantine an already-trusted key (D6), but it also must
    not let an UNtrusted one in. Same classification, opposite action,
    because the two paths start from different levels of trust.
    """
    from bot.guilds import load_guild_binding
    import httpx

    fake_guild_service.program(
        NEW_KEY,
        GuildServiceResponse(raises=httpx.TimeoutException("x"))
        if failure is not TransportFailure.SERVER_ERROR_503
        else GuildServiceResponse(status=503),
    )
    before_key = _stored_key(GUILD_WB)
    before_binding = load_guild_binding(PROD_SERVER_ID, GUILD_WB)
    interaction = _admin_interaction()

    await _invoke_update_guild_key(interaction, GUILD_WB, NEW_KEY, service=fake_guild_service)

    assert _stored_key(GUILD_WB) == before_key
    assert load_guild_binding(PROD_SERVER_ID, GUILD_WB) == before_binding
    assert "verify" in interaction.reply_text.lower()


@RED
@pytest.mark.error
@pytest.mark.driving_port
async def test_an_officer_cannot_replace_a_guild_key(sqlite_repo, fake_guild_service):
    """AC-003.7. The key grants read access to a guild's full roster and raid
    history, so officer tier is deliberately not sufficient. Entered through
    the real `require_tier` decorator (ADR-001: one place for permissions)."""
    before = _stored_key(GUILD_WB)
    interaction = _officer_interaction()

    await _invoke_update_guild_key(interaction, GUILD_WB, NEW_KEY, service=fake_guild_service)

    assert _stored_key(GUILD_WB) == before
    assert fake_guild_service.call_count == 0, (
        "the key was probed before the permission check — an officer could "
        "use the command as an oracle for whether a key is valid"
    )


@RED
@pytest.mark.error
@pytest.mark.kpi
@pytest.mark.parametrize(
    "outcome",
    ["accepted", "wrong-guild", "dead", "unreachable", "unknown-guild"],
)
async def test_no_key_value_is_ever_shown_or_written_down(
    sqlite_repo, fake_guild_service, caplog, outcome: str
):
    """AC-003.8 / KPI-6, parametrized across EVERY outcome.

    Parametrizing matters more than usual here: the known exposure in this
    feature's history came from an error path, not a happy path. A leak test
    that only covers success proves nothing about the case where a traceback
    carries the argument list.
    """
    interaction = _admin_interaction()
    _program_outcome(fake_guild_service, outcome, NEW_KEY)

    await _invoke_update_guild_key(interaction, GUILD_WB, NEW_KEY, service=fake_guild_service)

    assert interaction.was_ephemeral
    haystacks = [interaction.reply_text, caplog.text, *(r.getMessage() for r in caplog.records)]
    for haystack in haystacks:
        assert NEW_KEY not in haystack
        assert _stored_key_plaintext_before() not in haystack


async def test_installing_a_matching_key_releases_a_quarantined_guild(
    sqlite_repo, fake_guild_service, bound_guild
):
    """AC-003.9. Quarantine must never be a trap (D3). The clearing logic
    ships in Slice 02, one slice BEFORE anything can enter quarantine, so
    the exit provably exists before the entrance opens."""
    from bot.guilds import load_guild_binding

    _quarantine(GUILD_WB, reason="resolved to Dark Mechanicum")
    fake_guild_service.program(NEW_KEY, GuildServiceResponse(identity=WORD_BEARERS))

    await _invoke_update_guild_key(
        _admin_interaction(), GUILD_WB, NEW_KEY, service=fake_guild_service
    )

    binding = load_guild_binding(PROD_SERVER_ID, GUILD_WB)
    assert binding.key_status == "active"
    assert binding.quarantine_reason is None
    assert binding.quarantined_at is None


@RED
@pytest.mark.error
@pytest.mark.driving_port
async def test_an_unknown_guild_is_refused_with_the_list_of_real_ones(
    sqlite_repo, fake_guild_service
):
    """AC-003.10. The operator reaching for this command is mid-incident;
    a bare "not found" costs another round trip to discover the id."""
    interaction = _admin_interaction()

    await _invoke_update_guild_key(
        interaction, "not_a_guild", NEW_KEY, service=fake_guild_service
    )

    assert GUILD_WB in interaction.reply_text
    assert fake_guild_service.call_count == 0


# ===========================================================================
# Helpers — wiring only
# ===========================================================================
from contextlib import contextmanager

from conftest import PROD_SERVER_ID, SEASON


@contextmanager
def _tacticus_answered_by(guild_service):
    """Answer every Tacticus call from the scenario's programmed doubles.

    Replicated from `test_slice_01_bind_and_report.py` (UD-10: do NOT import
    helpers across test modules — same-name conftest constants collide).
    Swapping `httpx.AsyncClient` keeps ALL classification in production code.
    """
    import httpx

    real_client = httpx.AsyncClient
    httpx.AsyncClient = lambda *args, **kwargs: _RecordedTacticus(guild_service)
    try:
        yield
    finally:
        httpx.AsyncClient = real_client


class _RecordedTacticus:
    """An `httpx.AsyncClient` answering from the programmed guild-service double."""

    def __init__(self, guild_service) -> None:
        self._guild_service = guild_service

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url: str, headers: dict | None = None, **kwargs):
        import httpx
        from bot.services.tacticus.guild_client import TACTICUS_GUILD_URL

        if url != TACTICUS_GUILD_URL:
            raise AssertionError(
                f"the update path called an endpoint no scenario declared: {url}"
            )
        answer = self._guild_service.answer_for((headers or {}).get("X-API-KEY", ""))
        return httpx.Response(
            answer.status, json=answer.payload(), request=httpx.Request("GET", url)
        )


async def _invoke_update_guild_key(interaction, guild_id, api_key, *, force=False, service):
    """Invoke the policy function driving `/update_guild_key` with a Discord double.

    The 04-01 scenarios assert on storage state only, so the driving port is
    the policy function in `bot.guild_keys` (the cog command comes in 04-02).
    The Tacticus call is patched at the httpx transport boundary so all
    classification stays in production code.
    """
    with _tacticus_answered_by(service):
        from bot.guild_keys import install_guild_key

        result = await install_guild_key(
            PROD_SERVER_ID, guild_id, api_key, force=force
        )
    _render_update_reply(interaction, result)
    return result


def _render_update_reply(interaction, result) -> None:
    """Render the policy result to the Discord double's reply surface.

    Minimal for 04-01 (no scenario asserts on reply text here). 04-02's
    scenarios assert on the resolved guild name and refusal reasons, so the
    shape is in place now; the cog (04-02) will render through the same result.
    """
    from bot.services.tacticus.guild_client import ProbeOutcome

    if result.outcome is ProbeOutcome.MATCH:
        name = result.identity.name if result.identity else "the guild"
        interaction.reply(f"✅ Key updated for {name}.")
    elif result.outcome is ProbeOutcome.MISMATCH and not result.forced:
        bound_name = result.bound_name or "the bound guild"
        observed_name = result.identity.name if result.identity else "the submitted key"
        interaction.reply(
            f"❌ {observed_name} does not match {bound_name}."
        )
    elif result.outcome is ProbeOutcome.DEAD:
        interaction.reply("❌ The key was rejected (dead).")
    elif result.outcome is ProbeOutcome.UNVERIFIABLE:
        interaction.reply("❌ The key could not be verified.")
    else:
        interaction.reply("❌ The guild service could not be reached; key not installed.")


class _FakeResponse:
    def __init__(self, interaction) -> None:
        self._interaction = interaction

    async def send_message(self, content="", *, embed=None, ephemeral=False, **kwargs):
        self._interaction.reply_text = content or (getattr(embed, "description", "") or "")
        self._interaction.was_ephemeral = ephemeral

    async def defer(self, *, ephemeral=False, **kwargs):
        self._interaction.was_ephemeral = ephemeral

    def is_done(self):
        return self._interaction._replied


class _FakeFollowup:
    def __init__(self, interaction) -> None:
        self._interaction = interaction

    async def send(self, content="", *, embed=None, ephemeral=False, **kwargs):
        self._interaction.reply_text = content or (getattr(embed, "description", "") or "")
        self._interaction.was_ephemeral = ephemeral


class _FakeInteraction:
    """A Discord interaction double capturing the reply text + ephemerality.

    `reply_text` / `was_ephemeral` are the observable surface the 04-02
    scenarios assert on; 04-01's two scenarios ignore them.
    """

    def __init__(self, *, guild_id: int = PROD_SERVER_ID) -> None:
        self.guild_id = guild_id
        self.reply_text = ""
        self.was_ephemeral = False
        self._replied = False
        self.response = _FakeResponse(self)
        self.followup = _FakeFollowup(self)

    def reply(self, content: str, *, ephemeral: bool = True) -> None:
        self.reply_text = content
        self.was_ephemeral = ephemeral
        self._replied = True


def _admin_interaction():
    """An interaction double authorized at admin tier (04-02 adds the real
    `require_tier` check; 04-01 calls the policy directly)."""
    return _FakeInteraction()


def _officer_interaction():
    raise AssertionError("Not yet implemented — RED scaffold")


def _stored_key(guild_id: str) -> str:
    """Decrypt and return the stored `api_key` for the guild (port read)."""
    from bot.guilds import load_guilds

    return load_guilds(PROD_SERVER_ID).get(guild_id, {}).get("api_key", "")


def _stored_key_plaintext_before() -> str:
    """The plaintext of the key GUILD_WB was registered with, for leak tests."""
    return "wb-key"


def _stored_key_hmac(guild_id: str) -> str | None:
    """Read the stored `api_key_hmac` column directly from the guild row."""
    import bot.guilds as guilds_mod
    from bot.db.models import GuildRow
    from sqlalchemy import select

    with guilds_mod.repo._db.session_scope() as session:  # noqa: SLF001
        row = session.get(GuildRow, (PROD_SERVER_ID, guild_id))
        return row.api_key_hmac if row else None


def _expected_hmac(api_key: str) -> str | None:
    """The hmac `replace_guild_key` should have written for `api_key`."""
    import os

    from bot.db.secrets import api_key_hmac

    return api_key_hmac(api_key, os.environ["SCRAPCODE_DB_KEY"])


def _row_counts(repo, guild_id: str) -> tuple[int, int, int]:
    """(players, battle_hits, bomb_hits) for the guild — the AC-003.2 surface."""
    from bot.guilds import load_player_list

    players = len(load_player_list(PROD_SERVER_ID, guild_id).get("players", {}))
    battle = _count_hits(repo.load_battle_hits(PROD_SERVER_ID, guild_id, SEASON))
    bomb = _count_hits(repo.load_bomb_hits(PROD_SERVER_ID, guild_id, SEASON))
    return (players, battle, bomb)


def _count_hits(hits: dict) -> int:
    """Count the leaf entries in a `{"boss_hits": {boss: {enc: {tier: [...]}}}}`."""
    total = 0
    for boss in (hits.get("boss_hits") or {}).values():
        for encounter in boss.values():
            for tier in encounter.values():
                total += len(tier)
    return total


def _quarantine(guild_id: str, *, reason: str) -> None:
    """Flip a bound guild's binding to quarantined (the Slice 03 entrance, here
    used to prove the exit exists)."""
    from dataclasses import replace

    from bot.guilds import load_guild_binding, save_guild_binding

    current = load_guild_binding(PROD_SERVER_ID, guild_id)
    save_guild_binding(PROD_SERVER_ID, guild_id, replace(
        current,
        key_status="quarantined",
        quarantine_reason=reason,
        quarantined_at="2026-07-31T05:00:00.000Z",
    ))


def _program_outcome(service, outcome: str, api_key: str) -> None:
    raise AssertionError("Not yet implemented — RED scaffold")
