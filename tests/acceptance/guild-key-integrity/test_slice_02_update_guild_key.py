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


@RED
@pytest.mark.kpi
async def test_replacing_a_key_destroys_nothing(sqlite_repo, fake_guild_service):
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


@RED
async def test_installing_a_matching_key_releases_a_quarantined_guild(
    sqlite_repo, fake_guild_service
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

async def _invoke_update_guild_key(interaction, guild_id, api_key, *, force=False, service):
    """Invoke the real `/update_guild_key` callback with a Discord double."""
    raise AssertionError("Not yet implemented — RED scaffold")


def _admin_interaction():
    raise AssertionError("Not yet implemented — RED scaffold")


def _officer_interaction():
    raise AssertionError("Not yet implemented — RED scaffold")


def _stored_key(guild_id: str) -> str:
    raise AssertionError("Not yet implemented — RED scaffold")


def _stored_key_plaintext_before() -> str:
    raise AssertionError("Not yet implemented — RED scaffold")


def _stored_key_hmac(guild_id: str) -> str:
    raise AssertionError("Not yet implemented — RED scaffold")


def _expected_hmac(api_key: str) -> str:
    raise AssertionError("Not yet implemented — RED scaffold")


def _row_counts(repo, guild_id: str) -> tuple[int, int, int]:
    """(players, battle_hits, bomb_hits) for the guild."""
    raise AssertionError("Not yet implemented — RED scaffold")


def _quarantine(guild_id: str, *, reason: str) -> None:
    raise AssertionError("Not yet implemented — RED scaffold")


def _program_outcome(service, outcome: str, api_key: str) -> None:
    raise AssertionError("Not yet implemented — RED scaffold")
