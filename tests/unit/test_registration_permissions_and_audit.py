"""/registration register | unregister | move — who may act on whom, and the audit trail.

Pinned here:
  * Acting on ANOTHER player is officer-tier (`check_tier("officer")`, which
    also admits the admin tier and Discord Administrators). A guild member may
    still act only on themselves.
  * Registering or moving someone into a guild requires them to hold that
    guild's member role — refused before Tacticus is called or anything saved.
  * `unregister user_id:` accepts a Discord ID (or a pasted mention) and
    refuses anything else before touching storage.
  * Every registration change leaves exactly one `registration.*` record:
    who acted, whose registration changed, which guild — never the API key.

The cog runs with no Chronicl3r service (`player_service=None`), so these
tests see only the registration behaviour; the Chronicl3r side is covered by
the test_*_chronicler_key modules.
"""
import os

os.environ.setdefault("SCRAPCODE_REPO_BACKEND", "json")
os.environ.setdefault("UPDATE_CHANNEL_ID", "0")
os.environ.setdefault("REPLAY_INDEX_CHANNEL_ID", "0")

import asyncio
import copy
import logging
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.cogs import registration_cog as cog_module
from bot.models import Cluster

SERVER_ID = 4242
ADMIN_ROLE = 11
OFFICER_ROLE = 22
ALPHA_ROLE = 33
BRAVO_ROLE = 44

ALPHA = "alpha"
BRAVO = "bravo"
GUILDS = {
    ALPHA: {"name": "Alpha Guild", "member_role_ids": [ALPHA_ROLE]},
    BRAVO: {"name": "Bravo Guild", "member_role_ids": [BRAVO_ROLE]},
}

ACTOR_ID = 100000000000000001
TARGET_ID = 200000000000000002
BYSTANDER_ID = "300000000000000003"

API_KEY = "sentinel-new-api-key-0001"
TARGET_KEY = "sentinel-target-api-key-0002"
BYSTANDER_KEY = "sentinel-bystander-api-key-0003"
ALL_KEYS = (API_KEY, TARGET_KEY, BYSTANDER_KEY)

CLUSTER = Cluster(
    discord_server_id=SERVER_ID,
    role_tiers={"admin": [ADMIN_ROLE], "officer": [OFFICER_ROLE]},
)


# ------------------------------------------------------------------ fakes

def _member(member_id, role_ids=(), *, administrator=False):
    member = MagicMock()
    member.id = member_id
    member.mention = f"<@{member_id}>"
    member.roles = [MagicMock(id=rid) for rid in role_ids]
    member.guild_permissions.administrator = administrator
    return member


def _interaction(actor):
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = actor
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _reply(interaction):
    interaction.followup.send.assert_awaited_once()
    return interaction.followup.send.await_args.args[0]


class _Storage:
    def __init__(self, table):
        self.table = copy.deepcopy(table)
        self.saves = 0

    def load(self, server_id):
        assert server_id == SERVER_ID
        return copy.deepcopy(self.table)

    def save(self, server_id, table):
        assert server_id == SERVER_ID
        self.table = copy.deepcopy(table)
        self.saves += 1


def _tacticus_accepts():
    response = MagicMock()
    response.raise_for_status = lambda: None
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    context = AsyncMock()
    context.__aenter__.return_value = client
    context.__aexit__.return_value = None
    return context


@contextmanager
def _world(registrations):
    storage = _Storage(registrations)
    capped = _Storage({})
    tacticus = MagicMock(return_value=_tacticus_accepts())
    with patch("bot.permissions.repo") as permissions_repo, \
            patch.object(cog_module, "load_guilds", lambda server_id: copy.deepcopy(GUILDS)), \
            patch.object(cog_module, "load_player_registrations", storage.load), \
            patch.object(cog_module, "save_player_registrations", storage.save), \
            patch.object(cog_module, "load_capped_state", capped.load), \
            patch.object(cog_module, "save_capped_state", capped.save), \
            patch.object(cog_module.httpx, "AsyncClient", tacticus):
        permissions_repo.load.return_value = CLUSTER
        yield storage, tacticus


def _command(name):
    for command in cog_module.RegistrationCog.__cog_app_commands__:
        for child in getattr(command, "commands", ()):
            if child.name == name:
                return child
    raise AssertionError(f"no `{name}` command on RegistrationCog")


def _run(name, interaction, **kwargs):
    cog = cog_module.RegistrationCog(object())
    asyncio.run(_command(name).callback(cog, interaction, **kwargs))


def _audit_records(caplog):
    return [r for r in caplog.records
            if getattr(r, "event", "").startswith("registration.")]


def _seeded():
    return {
        str(TARGET_ID): {"api_key": TARGET_KEY, "guild_id": ALPHA},
        BYSTANDER_ID: {"api_key": BYSTANDER_KEY, "guild_id": ALPHA},
    }


@pytest.fixture(autouse=True)
def _capture_info(caplog):
    caplog.set_level(logging.INFO, logger=cog_module.logger.name)


def _assert_no_key_logged(caplog):
    text = "\n".join(r.getMessage() + repr(r.__dict__) for r in caplog.records)
    for key in ALL_KEYS:
        assert key not in text


# Who may act on someone else: officer, admin tier, Discord Administrator.
ON_BEHALF_ALLOWED = {
    "officer": dict(role_ids=(OFFICER_ROLE,)),
    "admin_tier": dict(role_ids=(ADMIN_ROLE,)),
    "discord_admin": dict(administrator=True),
}
# A guild member of the right guild is still not allowed to act on others.
ON_BEHALF_DENIED = {
    "guild_member": dict(role_ids=(ALPHA_ROLE,)),
    "no_roles": dict(),
}


# --------------------------------------------------------------- register

@pytest.mark.parametrize("who", ON_BEHALF_ALLOWED)
def test_officers_and_admins_can_register_another_player(who, caplog):
    interaction = _interaction(_member(ACTOR_ID, **ON_BEHALF_ALLOWED[who]))
    target = _member(TARGET_ID, (ALPHA_ROLE,))
    with _world({}) as (storage, _):
        _run("register", interaction, api_key=API_KEY, guild_id=ALPHA, target_user=target)

    assert storage.table == {str(TARGET_ID): {"api_key": API_KEY, "guild_id": ALPHA}}
    assert _reply(interaction).startswith("✅")
    [record] = _audit_records(caplog)
    assert record.event == "registration.register"
    assert (record.server_id, record.discord_id, record.actor_id) == (
        SERVER_ID, str(TARGET_ID), str(ACTOR_ID))
    assert record.on_behalf is True
    assert (record.guild_id, record.previous_guild_id, record.updated) == (ALPHA, None, False)
    _assert_no_key_logged(caplog)


@pytest.mark.parametrize("who", ON_BEHALF_DENIED)
def test_members_cannot_register_another_player(who, caplog):
    interaction = _interaction(_member(ACTOR_ID, **ON_BEHALF_DENIED[who]))
    target = _member(TARGET_ID, (ALPHA_ROLE,))
    with _world({}) as (storage, tacticus):
        _run("register", interaction, api_key=API_KEY, guild_id=ALPHA, target_user=target)

    assert storage.saves == 0
    tacticus.assert_not_called()
    assert "Only officers and admins" in _reply(interaction)
    assert _audit_records(caplog) == []


def test_registering_another_player_into_a_guild_they_are_not_in_is_refused(caplog):
    interaction = _interaction(_member(ACTOR_ID, (OFFICER_ROLE,)))
    target = _member(TARGET_ID, (BRAVO_ROLE,))  # Bravo member, registered into Alpha
    with _world({}) as (storage, tacticus):
        _run("register", interaction, api_key=API_KEY, guild_id=ALPHA, target_user=target)

    assert storage.saves == 0
    tacticus.assert_not_called()
    reply = _reply(interaction)
    assert "Alpha Guild" in reply and "member role" in reply
    assert _audit_records(caplog) == []


def test_registering_yourself_is_audited_as_not_on_behalf(caplog):
    interaction = _interaction(_member(ACTOR_ID, (ALPHA_ROLE,)))
    with _world(_seeded()) as (storage, _):
        _run("register", interaction, api_key=API_KEY, guild_id=ALPHA)

    assert storage.table[str(ACTOR_ID)] == {"api_key": API_KEY, "guild_id": ALPHA}
    [record] = _audit_records(caplog)
    assert (record.discord_id, record.actor_id, record.on_behalf) == (
        str(ACTOR_ID), str(ACTOR_ID), False)
    _assert_no_key_logged(caplog)


def test_re_registering_records_the_previous_guild_and_the_key_change(caplog):
    interaction = _interaction(_member(ACTOR_ID, (OFFICER_ROLE,)))
    target = _member(TARGET_ID, (ALPHA_ROLE, BRAVO_ROLE))
    with _world(_seeded()) as (storage, _):
        _run("register", interaction, api_key=API_KEY, guild_id=BRAVO, target_user=target)

    assert storage.table[str(TARGET_ID)] == {"api_key": API_KEY, "guild_id": BRAVO}
    [record] = _audit_records(caplog)
    assert (record.guild_id, record.previous_guild_id) == (BRAVO, ALPHA)
    assert record.updated is True and record.key_changed is True
    _assert_no_key_logged(caplog)


# ------------------------------------------------------------- unregister

@pytest.mark.parametrize("who", ON_BEHALF_ALLOWED)
def test_officers_and_admins_can_unregister_another_player(who, caplog):
    interaction = _interaction(_member(ACTOR_ID, **ON_BEHALF_ALLOWED[who]))
    target = _member(TARGET_ID)
    with _world(_seeded()) as (storage, _):
        _run("unregister", interaction, target_user=target)

    assert str(TARGET_ID) not in storage.table
    assert BYSTANDER_ID in storage.table
    [record] = _audit_records(caplog)
    assert record.event == "registration.unregister"
    assert (record.discord_id, record.actor_id, record.on_behalf, record.guild_id) == (
        str(TARGET_ID), str(ACTOR_ID), True, ALPHA)
    _assert_no_key_logged(caplog)


@pytest.mark.parametrize("who", ON_BEHALF_DENIED)
@pytest.mark.parametrize("by", ("target_user", "user_id"))
def test_members_cannot_unregister_another_player(who, by, caplog):
    interaction = _interaction(_member(ACTOR_ID, **ON_BEHALF_DENIED[who]))
    kwargs = ({"target_user": _member(TARGET_ID)} if by == "target_user"
              else {"user_id": str(TARGET_ID)})
    with _world(_seeded()) as (storage, _):
        _run("unregister", interaction, **kwargs)

    assert storage.saves == 0
    assert "Only officers and admins" in _reply(interaction)
    assert _audit_records(caplog) == []


@pytest.mark.parametrize("raw", (
    str(TARGET_ID),
    f"  {TARGET_ID}  ",
    f"<@{TARGET_ID}>",
    f"<@!{TARGET_ID}>",
))
def test_unregister_by_user_id_accepts_an_id_or_a_mention(raw, caplog):
    interaction = _interaction(_member(ACTOR_ID, (OFFICER_ROLE,)))
    with _world(_seeded()) as (storage, _):
        _run("unregister", interaction, user_id=raw)

    assert str(TARGET_ID) not in storage.table
    [record] = _audit_records(caplog)
    assert record.discord_id == str(TARGET_ID)


@pytest.mark.parametrize("raw", (
    "",
    "6006",                      # too short to be a snowflake
    "Krona",                     # a display name
    "@Krona",
    f"{TARGET_ID}x",
    f"<@{TARGET_ID}",            # half a mention
    f"{TARGET_ID} {TARGET_ID}",
    "1" * 21,                    # too long
))
def test_unregister_by_user_id_refuses_anything_else_before_touching_storage(raw, caplog):
    interaction = _interaction(_member(ACTOR_ID, (OFFICER_ROLE,)))
    with _world(_seeded()) as (storage, _), \
            patch.object(cog_module, "load_player_registrations") as load:
        _run("unregister", interaction, user_id=raw)

    load.assert_not_called()
    assert storage.saves == 0
    assert "must be a Discord user ID" in _reply(interaction)
    assert _audit_records(caplog) == []


def test_unregistering_yourself_is_audited_as_not_on_behalf(caplog):
    interaction = _interaction(_member(TARGET_ID, (ALPHA_ROLE,)))
    with _world(_seeded()) as (storage, _):
        _run("unregister", interaction)

    assert str(TARGET_ID) not in storage.table
    [record] = _audit_records(caplog)
    assert (record.discord_id, record.on_behalf) == (str(TARGET_ID), False)


# ------------------------------------------------------------------- move

def test_moving_a_player_is_audited_with_both_guilds(caplog):
    interaction = _interaction(_member(ACTOR_ID, (OFFICER_ROLE,)))
    target = _member(TARGET_ID, (BRAVO_ROLE,))
    with _world(_seeded()) as (storage, _):
        _run("move", interaction, target_user=target, guild_id=BRAVO)

    assert storage.table[str(TARGET_ID)] == {"api_key": TARGET_KEY, "guild_id": BRAVO}
    [record] = _audit_records(caplog)
    assert record.event == "registration.move"
    assert (record.discord_id, record.actor_id, record.guild_id, record.previous_guild_id) == (
        str(TARGET_ID), str(ACTOR_ID), BRAVO, ALPHA)
    _assert_no_key_logged(caplog)


def test_moving_a_player_into_a_guild_they_are_not_in_is_refused(caplog):
    interaction = _interaction(_member(ACTOR_ID, (OFFICER_ROLE,)))
    target = _member(TARGET_ID, (ALPHA_ROLE,))  # Alpha member, moved into Bravo
    with _world(_seeded()) as (storage, _):
        _run("move", interaction, target_user=target, guild_id=BRAVO)

    assert storage.saves == 0
    reply = _reply(interaction)
    assert "Bravo Guild" in reply and "member role" in reply
    assert _audit_records(caplog) == []


# ------------------------------------------------------------ the helpers

@pytest.mark.parametrize("raw, expected", (
    ("123456789012345678", "123456789012345678"),
    (" 12345678901234567 ", "12345678901234567"),
    ("<@12345678901234567890>", "12345678901234567890"),
    ("<@!123456789012345678>", "123456789012345678"),
    ("1234567890123456", None),
    ("<@!>", None),
    ("abc", None),
))
def test_parse_discord_id(raw, expected):
    assert cog_module._parse_discord_id(raw) == expected
