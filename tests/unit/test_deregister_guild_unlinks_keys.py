"""Property tests for removing a guild's keys on /deregister_guild (02-03).

WHY-NEW-FILE: tests/unit/test_deregister_guild_unlinks_keys.py
  CLOSEST-EXISTING: tests/unit/test_deregister_confirmation_gate.py
  EXTENSION-COST: that module's universe is the CASCADE's row counts over a
    real migrated database, and it must stay unmodified (the gate is
    pinned). The claim here is about Chronicl3r: which keys the
    confirmation removes, and that no Chronicl3r outcome reaches the
    deletion. Adding a fake Chronicl3r and a registrations table to every
    example there would double its universe.
  PARALLEL-RATIONALE: different collaborator. The gate properties observe
    SQLite rows; these observe the Chronicl3r requests and the log records
    the same confirmation produces.

THE PORT. `AdminCog.deregister_guild`, invoked through
`AdminCog.__cog_app_commands__`, then the confirmation button pressed through
the view it offered (the gate test's `_confirm_if_awaiting` shape). A REAL
`PlayerService` over a REAL `chronicl3rClient` whose `requests.post` /
`requests.delete` are replaced by a fake Chronicl3r answering only scripted
routes. The `bot.guilds` helpers the cog binds are faked at the cog module.

DECLARED UNIVERSE, strict:

    replies              — every message the command and the button sent back
    guilds.saved         — every guild registry the confirmation saved
    registrations.saved  — every registrations table saved (CASCADE owns it)
    tombstones           — every quarantine tombstone recorded
    chronicler.calls     — every request the fake Chronicl3r received
    log.records          — every `bot.*` log record emitted (structured
                           records reduced to event + fields)

A slot no property declares must come out identical. No test makes a
network call.
"""
from __future__ import annotations

import asyncio
import copy
import importlib
import json
import os
from contextlib import contextmanager
from unittest.mock import patch

os.environ.setdefault("SCRAPCODE_REPO_BACKEND", "json")
os.environ.setdefault("UPDATE_CHANNEL_ID", "0")
os.environ.setdefault("REPLAY_INDEX_CHANNEL_ID", "0")

import pytest
import requests

hypothesis = pytest.importorskip("hypothesis")

import hypothesis.strategies as st
from hypothesis import given, settings

from chronicler_key_fakes import (
    StructuredLogRecorder as _LogRecorder, chronicler_transport, recording_bot_logs,
    response as _response,
)

client_module = importlib.import_module("bot.services.chronicl3r.client")
service_module = importlib.import_module("bot.services.chronicl3r.player_service")
repository_module = importlib.import_module("bot.repository")


def _admin_cog():
    """`bot.cogs.admin_cog` as it is imported NOW, never cached at load.

    `tests/acceptance/cluster-board-control/conftest.py` evicts
    `bot.cogs.admin_cog` from `sys.modules` after its suite. A module captured
    at collection would then be the evicted one, while the
    `patch("bot.cogs.admin_cog....")` targets in `_patched` re-import a fresh
    one — the command would run against unpatched globals. Resolving the module
    at call time keeps the command and the patches on the same module.
    """
    return importlib.import_module("bot.cogs.admin_cog")

SERVER_ID = 737373
GUILD_TARGET = "guild-target"
GUILD_SIBLING = "guild-sibling"
GUILD_NAME = "Target Guild"
COUNTS = {"players": 3, "battle_hits": 5, "bomb_hits": 2}

BASE = "https://www.chronicl3r.com/tacticus-guild-raid/api/v1/"
TOKEN = "fake-chronicler-token"
LOOKUP_URL = BASE + "player-profiles/lookup/"
UNLINK_EVENT = "chronicler.key.unlink"

CONTRACT_CODES = (
    "missing_key", "invalid_key", "no_profiles", "no_name_match", "no_match",
    "insufficient_data", "ambiguous", "upstream_rate_limited",
    "upstream_unavailable", "throttled",
)
ERROR_STATUSES = (400, 404, 409, 422, 429, 500, 502, 503)

_CONFIRMATION_WORDS = ("confirm", "deregister")

UNIVERSE = ("replies", "guilds.saved", "registrations.saved", "tombstones",
            "chronicler.calls", "log.records")

SETTINGS = settings(max_examples=40, deadline=None)

sentinel_details = st.from_regex(r"sentinel-detail-[a-f0-9]{8}", fullmatch=True)


# ---------------------------------------------------------------- responses

def _delete_url(user_id):
    return BASE + f"player-profiles/{user_id}/api-key/"


class _Unlink:
    """Scripted Chronicl3r answers for one key, the calls they cost, and the
    log code the cog must record for it (None = success, no record)."""

    def __init__(self, label, lookup, delete, calls, log_code):
        self.label = label
        self.lookup = lookup
        self.delete = delete
        self.calls = calls
        self.log_code = log_code

    def __repr__(self):
        return f"_Unlink({self.label})"


@st.composite
def unlink_outcomes(draw, key, index):
    """One key's unlink outcome. The tacticus_user_id is a per-player sentinel."""
    kind = draw(st.sampled_from(("deleted", "not_linked", "lookup_fails",
                                 "delete_fails", "raises")))
    lookup_call = ("POST", LOOKUP_URL, {"tacticus_player_api_key": key})
    user_id = f"sentinel-tuid-{index:02d}-{draw(st.from_regex(r'[a-f0-9]{8}', fullmatch=True))}"
    if kind == "not_linked":
        body = {"code": "not_linked", "detail": draw(sentinel_details)}
        return _Unlink(kind, _response(404, json_body=body), None, [lookup_call], None)
    if kind == "lookup_fails":
        which = draw(st.sampled_from(("contract", "not_deployed", "transport")))
        if which == "contract":
            body = {"code": draw(st.sampled_from(CONTRACT_CODES)),
                    "detail": draw(sentinel_details), "tacticus_user_id": user_id}
            answer = _response(draw(st.sampled_from(ERROR_STATUSES)), json_body=body)
        elif which == "not_deployed":
            answer = _response(404, text="<html>Not Found</html>", content_type="text/html")
        else:
            exc = draw(st.sampled_from((requests.Timeout, requests.ConnectionError,
                                        requests.RequestException)))
            answer = exc(draw(sentinel_details))
        return _Unlink(f"{kind}:{which}", answer, None, [lookup_call], "unlink_failed")
    if kind == "raises":
        exc = draw(st.sampled_from((RuntimeError, KeyError, ValueError)))
        return _Unlink(kind, exc(draw(sentinel_details)), None, [lookup_call], "transport")
    held = _response(200, json_body={"tacticus_user_id": user_id,
                                     "detail": draw(sentinel_details),
                                     "has_valid_api_key": True})
    status = 204 if kind == "deleted" else draw(st.sampled_from((200, 400, 404, 500)))
    delete = (_delete_url(user_id), _response(status, text=""))
    return _Unlink(kind, held, delete,
                   [lookup_call, ("DELETE", _delete_url(user_id), None)],
                   None if kind == "deleted" else "unlink_failed")


# ---------------------------------------------------------------- scenarios

class _Player:
    def __init__(self, discord_id, key, unlink):
        self.discord_id = discord_id
        self.key = key
        self.unlink = unlink


@st.composite
def scenarios(draw, *, outcome=None):
    """Target-guild players (each with an unlink outcome), sibling-guild
    players, an optional keyless target entry, and the binding state.

    `outcome` filters each target player's outcome: "succeeds", "fails", or
    None for any. With "fails", at least one target player is drawn.
    """
    min_target = 1 if outcome == "fails" else 0
    target_count = draw(st.integers(min_value=min_target, max_value=4))
    sibling_count = draw(st.integers(min_value=0, max_value=3))
    target = []
    for i in range(target_count):
        key = f"fake-key-tgt-{i}-{draw(st.from_regex(r'[a-z0-9]{4,8}', fullmatch=True))}"
        strategy = unlink_outcomes(key, i)
        if outcome == "succeeds":
            strategy = strategy.filter(lambda u: u.log_code is None)
        elif outcome == "fails":
            strategy = strategy.filter(lambda u: u.log_code is not None)
        target.append(_Player(str(1000 + i), key, draw(strategy)))
    siblings = [
        _Player(str(2000 + i),
                f"fake-key-sib-{i}-{draw(st.from_regex(r'[a-z0-9]{4,8}', fullmatch=True))}",
                None)
        for i in range(sibling_count)
    ]
    keyless = draw(st.sampled_from((None, {"guild_id": GUILD_TARGET},
                                    {"guild_id": GUILD_TARGET, "api_key": None},
                                    {"guild_id": GUILD_TARGET, "api_key": ""})))
    order = draw(st.permutations(target + siblings))
    registrations = {p.discord_id: {"api_key": p.key,
                                    "guild_id": GUILD_SIBLING if p.unlink is None else GUILD_TARGET}
                     for p in order}
    if keyless is not None:
        registrations["3000"] = dict(keyless)
    quarantined = draw(st.booleans())
    targets_in_order = [p for p in order if p.unlink is not None]
    return _Scenario(targets_in_order, siblings, registrations, quarantined)


class _Scenario:
    def __init__(self, target, siblings, registrations, quarantined):
        self.target = target
        self.siblings = siblings
        self.registrations = registrations
        self.quarantined = quarantined

    def __repr__(self):
        return (f"_Scenario(target={[p.unlink for p in self.target]}, "
                f"siblings={len(self.siblings)}, keys={len(self.registrations)}, "
                f"quarantined={self.quarantined})")


# ------------------------------------------------------------- fake Chronicl3r

class _FakeChronicler:
    """Answers the scripted lookup per KEY and delete per URL; refuses the rest."""

    def __init__(self, scenario):
        self.lookups = {p.key: p.unlink.lookup for p in scenario.target}
        self.deletes = dict(p.unlink.delete for p in scenario.target if p.unlink.delete)
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None, **kwargs):
        self._check("POST", headers, kwargs)
        key = (json or {}).get("tacticus_player_api_key")
        if url != LOOKUP_URL or key not in self.lookups:
            raise AssertionError("unexpected POST")
        self.calls.append(("POST", url, json))
        return self._answer(self.lookups[key])

    def delete(self, url, json=None, headers=None, timeout=None, **kwargs):
        self._check("DELETE", headers, kwargs)
        if url not in self.deletes:
            raise AssertionError("unexpected DELETE")
        self.calls.append(("DELETE", url, json))
        return self._answer(self.deletes[url])

    @staticmethod
    def _check(method, headers, kwargs):
        if kwargs:
            raise AssertionError(f"unexpected kwargs {sorted(kwargs)}")
        if headers != {"Authorization": f"Token {TOKEN}"}:
            raise AssertionError(f"{method} sent without the Chronicl3r token")

    @staticmethod
    def _answer(answer):
        if isinstance(answer, BaseException):
            raise answer
        return answer


# ------------------------------------------------------------- fake Discord

class _Response:
    async def defer(self, *, ephemeral=False, **kwargs):
        return None


class _Followup:
    def __init__(self, interaction):
        self._interaction = interaction

    async def send(self, content="", *, embed=None, ephemeral=False, **kwargs):
        self._interaction.replies.append(content)
        if kwargs.get("view") is not None:
            self._interaction.views.append(kwargs["view"])


class _Interaction:
    def __init__(self):
        self.guild_id = SERVER_ID
        self.replies = []
        self.views = []
        self.extras = {}
        self.response = _Response()
        self.followup = _Followup(self)


async def _confirm_if_awaiting(interaction):
    """Press the confirmation button if the command offered one."""
    for view in interaction.views:
        for child in getattr(view, "children", ()):
            label = " ".join(str(getattr(child, attr, "") or "")
                             for attr in ("label", "custom_id")).lower()
            if any(word in label for word in _CONFIRMATION_WORDS):
                await child.callback(interaction)
                return


# ------------------------------------------------------------- fake storage

class _Store:
    def __init__(self, seeded):
        self.table = copy.deepcopy(seeded)
        self.saved = []

    def load(self, server_id):
        assert server_id == SERVER_ID
        return copy.deepcopy(self.table)

    def save(self, server_id, table):
        assert server_id == SERVER_ID
        self.table = copy.deepcopy(table)
        self.saved.append(copy.deepcopy(table))


class _Repo:
    def __init__(self):
        self.tombstones = []

    def count_guild_destruction_rows(self, server_id, guild_id):
        assert (server_id, guild_id) == (SERVER_ID, GUILD_TARGET)
        return dict(COUNTS)

    def record_quarantine_tombstone(self, server_id, tombstone):
        assert server_id == SERVER_ID
        self.tombstones.append(tombstone.guild_id)


def _binding(quarantined):
    return repository_module.GuildBinding(
        tacticus_guild_id="bound-uuid",
        tacticus_guild_tag="TGT",
        tacticus_guild_name=GUILD_NAME,
        identity_bound_at="2026-07-30T04:00:00.000Z",
        key_status="quarantined" if quarantined else "active",
        quarantine_reason="key drift — observed=other-uuid" if quarantined else "",
        quarantined_at="2026-07-31T04:00:00.000Z" if quarantined else None,
    )


def _guilds():
    return {
        GUILD_TARGET: {"name": GUILD_NAME, "api_key": "guild-key-tgt", "role_id": 1},
        GUILD_SIBLING: {"name": "Sibling", "api_key": "guild-key-sib", "role_id": 2},
    }


class _World:
    def __init__(self, scenario):
        self.scenario = scenario
        self.fake = _FakeChronicler(scenario)
        self.guilds = _Store(_guilds())
        self.registrations = _Store(scenario.registrations)
        self.repo = _Repo()
        self.recorder = _LogRecorder()
        self.interaction = _Interaction()

    def capture(self):
        return {
            "replies": list(self.interaction.replies),
            "guilds.saved": copy.deepcopy(self.guilds.saved),
            "registrations.saved": copy.deepcopy(self.registrations.saved),
            "tombstones": list(self.repo.tombstones),
            "chronicler.calls": list(self.fake.calls),
            "log.records": list(self.recorder.records),
        }


@contextmanager
def _patched(world):
    quarantined = world.scenario.quarantined
    with recording_bot_logs(world.recorder), chronicler_transport(world.fake), \
            patch("bot.cogs.admin_cog.load_guilds", world.guilds.load), \
            patch("bot.cogs.admin_cog.save_guilds", world.guilds.save), \
            patch("bot.cogs.admin_cog.load_player_registrations",
                  world.registrations.load, create=True), \
            patch("bot.cogs.admin_cog.save_player_registrations",
                  world.registrations.save, create=True), \
            patch("bot.cogs.admin_cog.load_guild_binding",
                  lambda server_id, guild_id: _binding(quarantined)), \
            patch("bot.guilds.repo", world.repo):
        yield


def _deregister_command():
    for command in _admin_cog().AdminCog.__cog_app_commands__:
        if command.name == "deregister_guild":
            return command
    raise AssertionError("no `deregister_guild` command is registered on AdminCog")


def _cog():
    client = client_module.chronicl3rClient()
    client.token = TOKEN
    return _admin_cog().AdminCog(object(), service_module.PlayerService(client))


def _run(scenario, *, press="confirm"):
    """Invoke the command, then press the button / let the view time out / walk away."""
    world = _World(scenario)
    before = world.capture()

    async def drive():
        await _deregister_command().callback(_cog(), world.interaction, guild_id=GUILD_TARGET)
        if press == "confirm":
            await _confirm_if_awaiting(world.interaction)
        elif press == "timeout":
            for view in world.interaction.views:
                await view.on_timeout()

    with _patched(world):
        asyncio.run(drive())
    return before, world.capture(), world


# ------------------------------------------------------------ state delta

def unchanged():
    return ("unchanged", None)


def appended_with(*items):
    return ("appended_with", list(items))


def _assert_state_delta(before, after, expected):
    """Strict: a universe slot with no declared predicate must not move."""
    assert set(before) == set(UNIVERSE) == set(after), "capture drifted from UNIVERSE"
    assert set(expected) <= set(UNIVERSE), "a predicate names an undeclared slot"
    for slot in UNIVERSE:
        kind, operand = expected.get(slot, unchanged())
        if kind == "unchanged":
            assert after[slot] == before[slot], f"{slot} moved: {before[slot]!r} -> {after[slot]!r}"
        elif kind == "appended_with":
            assert after[slot] == before[slot] + operand, f"{slot} is {after[slot]!r}, expected +{operand!r}"
        else:
            raise AssertionError(f"unknown predicate {kind}")


def _warning():
    return _admin_cog()._deregistration_warning(GUILD_TARGET, GUILD_NAME, COUNTS)


def _success():
    return (f"✅ Guild **{GUILD_NAME}** (`{GUILD_TARGET}`) has been deregistered.\n"
            f"Destroyed: {COUNTS['players']} players, {COUNTS['battle_hits']} battle hits, "
            f"{COUNTS['bomb_hits']} bomb hits.")


def _confirmed_delta(scenario):
    """The gate's own delta plus exactly the Chronicl3r calls and failure logs."""
    remaining = _guilds()
    del remaining[GUILD_TARGET]
    calls = [call for p in scenario.target for call in p.unlink.calls]
    logs = [(UNLINK_EVENT, {"server_id": SERVER_ID, "discord_id": p.discord_id,
                            "code": p.unlink.log_code})
            for p in scenario.target if p.unlink.log_code is not None]
    delta = {
        "replies": appended_with(_warning(), _success()),
        "guilds.saved": appended_with(remaining),
        "chronicler.calls": appended_with(*calls),
        "log.records": appended_with(*logs),
    }
    if scenario.quarantined:
        delta["tombstones"] = appended_with(GUILD_TARGET)
    return delta


def _sent_keys(calls):
    return [body["tacticus_player_api_key"] for _, _, body in calls if body is not None]


# ------------------------------------------------------------- properties

@SETTINGS
@given(scenario=scenarios(outcome="succeeds"))
def test_confirming_unlinks_every_key_registered_to_that_guild(scenario):
    before, after, _ = _run(scenario)

    _assert_state_delta(before, after, _confirmed_delta(scenario))
    assert _sent_keys(after["chronicler.calls"]) == [p.key for p in scenario.target]


@SETTINGS
@given(scenario=scenarios())
def test_keys_registered_to_other_guilds_are_left_linked(scenario):
    before, after, _ = _run(scenario)

    _assert_state_delta(before, after, _confirmed_delta(scenario))
    sent = set(_sent_keys(after["chronicler.calls"]))
    assert sent.isdisjoint(p.key for p in scenario.siblings)
    assert all(method != "DELETE" or "sentinel-tuid-" in url
               for method, url, _ in after["chronicler.calls"])


@SETTINGS
@given(scenario=scenarios(), press=st.sampled_from(("walk_away", "timeout")))
def test_an_unconfirmed_deregistration_unlinks_nothing(scenario, press):
    before, after, _ = _run(scenario, press=press)

    _assert_state_delta(before, after, {"replies": appended_with(_warning())})


@SETTINGS
@given(scenario=scenarios(outcome="fails"))
def test_a_chronicler_failure_never_blocks_the_deregistration(scenario):
    before, after, _ = _run(scenario)

    _assert_state_delta(before, after, _confirmed_delta(scenario))
    assert after["guilds.saved"][-1] == {GUILD_SIBLING: _guilds()[GUILD_SIBLING]}


@SETTINGS
@given(scenario=scenarios())
def test_no_deregistration_reply_or_log_carries_a_key_or_a_tacticus_id(scenario):
    _, after, world = _run(scenario)

    secrets = {TOKEN, "guild-key-tgt", "guild-key-sib"}
    secrets.update(p.key for p in scenario.target + scenario.siblings)
    for p in scenario.target:
        answers = [p.unlink.lookup] + ([p.unlink.delete[1]] if p.unlink.delete else [])
        for answer in answers:
            if isinstance(answer, BaseException):
                secrets.update(str(arg) for arg in answer.args)
            elif answer.headers.get("Content-Type") == "application/json":
                body = answer.json()
                secrets.update(str(body[field]) for field in ("detail", "tacticus_user_id")
                               if field in body)
    surfaces = list(after["replies"]) + list(world.recorder.raw)
    assert len(after["replies"]) == 2, "the command and the button did not both reply"
    for secret in secrets:
        for surface in surfaces:
            assert secret not in surface, "a reply or log record carries a secret"
