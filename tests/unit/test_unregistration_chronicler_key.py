"""Property tests for removing the key on /registration unregister (02-02).

WHY-NEW-FILE: tests/unit/test_unregistration_chronicler_key.py
  CLOSEST-EXISTING: tests/unit/test_registration_chronicler_key.py
  EXTENSION-COST: that module drives `register` (Tacticus check, link,
    storage notice); unregister has a different target resolution
    (self / member / raw ID), a capped-state cleanup and a different
    Chronicl3r call sequence. Adding them there would double its universe.
  PARALLEL-RATIONALE: different port. Those properties enter through the
    `/registration register` callback; these enter through
    `/registration unregister`.

THE PORT. `RegistrationCog.unregister`, invoked through
`RegistrationCog.__cog_app_commands__`, with a REAL `PlayerService` over a
REAL `chronicl3rClient` whose `requests.post` / `requests.delete` are replaced
by a fake Chronicl3r that answers only scripted routes. Registration and
capped-state storage (`bot.guilds` helpers bound in the cog module) are faked
at the cog module.

DECLARED UNIVERSE, strict:

    replies              — every message the command sent back
    registrations.saved  — every registrations table the command saved
    capped.saved         — every capped-state table the command saved
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

cog_module = importlib.import_module("bot.cogs.registration_cog")
client_module = importlib.import_module("bot.services.chronicl3r.client")
service_module = importlib.import_module("bot.services.chronicl3r.player_service")

RegistrationCog = cog_module.RegistrationCog

SERVER_ID = 535353
GUILD_ID = "guild-bravo"
INVOKER_ID = 4004
TARGET_ID = 5005
RAW_ID = "600600600600600600"  # a real snowflake shape: `user_id` is validated
OTHER_ID = "7007"

BASE = "https://www.chronicl3r.com/tacticus-guild-raid/api/v1/"
TOKEN = "fake-chronicler-token"
LOOKUP_URL = BASE + "player-profiles/lookup/"

CONTRACT_CODES = (
    "missing_key", "invalid_key", "no_profiles", "no_name_match", "no_match",
    "insufficient_data", "ambiguous", "upstream_rate_limited",
    "upstream_unavailable", "throttled",
)
ERROR_STATUSES = (400, 404, 409, 422, 429, 500, 502, 503)

REMOVED = "removed"
UNCONFIRMED = "unconfirmed"

UNIVERSE = ("replies", "registrations.saved", "capped.saved",
            "chronicler.calls", "log.records")

SETTINGS = settings(max_examples=40, deadline=None)

# ---------------------------------------------------------------- strategies

target_keys = st.from_regex(r"fake-key-tgt-[a-z0-9]{4,10}", fullmatch=True)
admin_keys = st.from_regex(r"fake-key-adm-[a-z0-9]{4,10}", fullmatch=True)
other_keys = st.from_regex(r"fake-key-oth-[a-z0-9]{4,10}", fullmatch=True)
sentinel_details = st.from_regex(r"sentinel-detail-[a-f0-9]{8}", fullmatch=True)
sentinel_user_ids = st.from_regex(r"sentinel-tuid-[a-f0-9]{8}", fullmatch=True)
whos = st.sampled_from(("self", "target_user", "user_id"))
capped = st.booleans()
no_key_entries = st.sampled_from((
    {"guild_id": GUILD_ID},
    {"guild_id": GUILD_ID, "api_key": None},
    {"guild_id": GUILD_ID, "api_key": ""},
))


def _lookup_call(key):
    return ("POST", LOOKUP_URL, {"tacticus_player_api_key": key})


def _delete_url(user_id):
    return BASE + f"player-profiles/{user_id}/api-key/"


def _delete_call(user_id):
    return ("DELETE", _delete_url(user_id), None)


class _Unlink:
    """Scripted Chronicl3r answers for one unlink, the calls they cost,
    the reply line they must produce and the log code (None = no log)."""

    def __init__(self, label, routes, calls, line, log_code):
        self.label = label
        self.routes = routes
        self.calls = calls
        self.line = line
        self.log_code = log_code

    def __repr__(self):
        return f"_Unlink({self.label})"


@st.composite
def unlink_outcomes(draw, key):
    kind = draw(st.sampled_from(("deleted", "not_linked", "lookup_fails",
                                 "delete_fails", "raises")))
    lookup = ("POST", LOOKUP_URL)
    if kind == "not_linked":
        body = {"code": "not_linked", "detail": draw(sentinel_details)}
        return _Unlink(kind, {lookup: _response(404, json_body=body)},
                       [_lookup_call(key)], REMOVED, None)
    if kind == "lookup_fails":
        which = draw(st.sampled_from(("contract", "not_deployed", "transport")))
        if which == "contract":
            body = {"code": draw(st.sampled_from(CONTRACT_CODES)),
                    "detail": draw(sentinel_details),
                    "tacticus_user_id": draw(sentinel_user_ids)}
            answer = _response(draw(st.sampled_from(ERROR_STATUSES)), json_body=body)
        elif which == "not_deployed":
            answer = _response(404, text="<html>Not Found</html>", content_type="text/html")
        else:
            exc = draw(st.sampled_from((requests.Timeout, requests.ConnectionError,
                                        requests.RequestException)))
            answer = exc(draw(sentinel_details))
        return _Unlink(f"{kind}:{which}", {lookup: answer},
                       [_lookup_call(key)], UNCONFIRMED, "unlink_failed")
    if kind == "raises":
        exc = draw(st.sampled_from((RuntimeError, KeyError, ValueError)))
        return _Unlink(kind, {lookup: exc(draw(sentinel_details))},
                       [_lookup_call(key)], UNCONFIRMED, "transport")
    user_id = draw(sentinel_user_ids)
    held = _response(200, json_body={"tacticus_user_id": user_id,
                                     "detail": draw(sentinel_details),
                                     "has_valid_api_key": True})
    status = 204 if kind == "deleted" else draw(st.sampled_from((200, 400, 404, 500)))
    deleted = _response(status, json_body=None, text="")
    return _Unlink(kind, {lookup: held, ("DELETE", _delete_url(user_id)): deleted},
                   [_lookup_call(key), _delete_call(user_id)],
                   REMOVED if kind == "deleted" else UNCONFIRMED,
                   None if kind == "deleted" else "unlink_failed")


def failing_unlink_outcomes(key):
    return unlink_outcomes(key).filter(lambda u: u.line == UNCONFIRMED)


# ------------------------------------------------------------- fake Chronicl3r

class _FakeChronicler:
    """Answers only the scripted (method, url) routes; refuses everything else."""

    def __init__(self, routes):
        self.routes = dict(routes)
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None, **kwargs):
        return self._serve("POST", url, json, headers, kwargs)

    def delete(self, url, json=None, headers=None, timeout=None, **kwargs):
        return self._serve("DELETE", url, json, headers, kwargs)

    def _serve(self, method, url, body, headers, kwargs):
        if (method, url) not in self.routes:
            raise AssertionError(f"unexpected request {method}")
        if kwargs:
            raise AssertionError(f"unexpected kwargs {sorted(kwargs)}")
        if headers != {"Authorization": f"Token {TOKEN}"}:
            raise AssertionError(f"{method} sent without the Chronicl3r token")
        self.calls.append((method, url, body))
        answer = self.routes[(method, url)]
        if isinstance(answer, BaseException):
            raise answer
        return answer


# ------------------------------------------------------------- fake Discord

class _Role:
    def __init__(self, role_id):
        self.id = role_id


class _Permissions:
    administrator = True


class _Member:
    def __init__(self, member_id):
        self.id = member_id
        self.mention = f"<@{member_id}>"
        self.roles = [_Role(1)]
        self.guild_permissions = _Permissions()


class _Response:
    async def defer(self, *, ephemeral=False, **kwargs):
        return None


class _Followup:
    def __init__(self, replies):
        self._replies = replies

    async def send(self, content="", *, embed=None, ephemeral=False, **kwargs):
        self._replies.append(content)


class _Interaction:
    def __init__(self):
        self.guild_id = SERVER_ID
        self.user = _Member(INVOKER_ID)
        self.replies = []
        self.response = _Response()
        self.followup = _Followup(self.replies)


class _Cluster:
    role_tiers = {"admin": []}


class _Repo:
    def load(self, server_id):
        return _Cluster()


class _Storage:
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


# ------------------------------------------------------------ the scenario

class _World:
    def __init__(self, routes, seeded, capped_seed):
        self.fake = _FakeChronicler(routes)
        self.storage = _Storage(seeded)
        self.capped = _Storage(capped_seed)
        self.recorder = _LogRecorder()
        self.interaction = _Interaction()

    def capture(self):
        return {
            "replies": list(self.interaction.replies),
            "registrations.saved": copy.deepcopy(self.storage.saved),
            "capped.saved": copy.deepcopy(self.capped.saved),
            "chronicler.calls": list(self.fake.calls),
            "log.records": list(self.recorder.records),
        }


@contextmanager
def _patched(world):
    with recording_bot_logs(world.recorder), chronicler_transport(world.fake), \
            patch("bot.cogs.registration_cog.load_player_registrations",
                  world.storage.load), \
            patch("bot.cogs.registration_cog.save_player_registrations",
                  world.storage.save), \
            patch("bot.cogs.registration_cog.load_capped_state",
                  world.capped.load), \
            patch("bot.cogs.registration_cog.save_capped_state",
                  world.capped.save), \
            patch("bot.cogs.registration_cog.repo", _Repo()):
        yield


def _unregister_command():
    for command in RegistrationCog.__cog_app_commands__:
        for child in getattr(command, "commands", ()):
            if child.name == "unregister":
                return child
    raise AssertionError("no `unregister` command is registered on RegistrationCog")


def _cog():
    client = client_module.chronicl3rClient()
    client.token = TOKEN
    return RegistrationCog(object(), service_module.PlayerService(client))


def _target_id(who):
    return {"self": str(INVOKER_ID), "target_user": str(TARGET_ID), "user_id": RAW_ID}[who]


def _seed(who, entry, admin_key, other_key, is_capped):
    """The unregistered player's entry, plus the admin's own and a bystander's."""
    seeded = {OTHER_ID: {"api_key": other_key, "guild_id": GUILD_ID}}
    if who != "self":
        seeded[str(INVOKER_ID)] = {"api_key": admin_key, "guild_id": GUILD_ID}
    target = _target_id(who)
    seeded[target] = entry
    capped_seed = {OTHER_ID: {"is_capped": True}}
    if is_capped:
        capped_seed[target] = {"is_capped": True}
    return seeded, capped_seed, target


def _run(who, routes, seeded, capped_seed, cog=None):
    world = _World(routes, seeded, capped_seed)
    before = world.capture()
    kwargs = {}
    if who == "target_user":
        kwargs["target_user"] = _Member(TARGET_ID)
    elif who == "user_id":
        kwargs["user_id"] = f" {RAW_ID} "
    with _patched(world):
        asyncio.run(_unregister_command().callback(cog or _cog(), world.interaction, **kwargs))
    return before, world.capture(), world


def _without(table, key):
    table = copy.deepcopy(table)
    del table[key]
    return table


# ------------------------------------------------------------ state delta

def unchanged():
    return ("unchanged", None)


def appended_with(*items):
    return ("appended_with", list(items))


def satisfies(description, check):
    return ("satisfies", (description, check))


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
        elif kind == "satisfies":
            description, check = operand
            assert check(before[slot], after[slot]), f"{slot} is {after[slot]!r}: not {description}"
        else:
            raise AssertionError(f"unknown predicate {kind}")


def _chronicler_lines():
    return {
        REMOVED: cog_module.CHRONICLER_UNLINKED_LINE,
        UNCONFIRMED: cog_module.CHRONICLER_UNLINK_UNCONFIRMED_LINE,
    }


def _one_success_reply(line_kind):
    """Exactly one reply: the success line, then exactly the expected Chronicl3r line
    (or none at all when `line_kind` is None)."""
    lines = _chronicler_lines()

    def check(before, after):
        if len(after) != len(before) + 1:
            return False
        reply = after[-1].split("\n")
        found = [part for part in reply if part in lines.values()]
        expected = [] if line_kind is None else [lines[line_kind]]
        return (reply[0].startswith("✅")
                and found == expected
                and len(reply) == 1 + len(expected))

    return satisfies(f"one success reply with the {line_kind} Chronicl3r line", check)


def _expected_delta(seeded, capped_seed, target, is_capped, unlink, calls=None, line="auto"):
    # The audit record; written right after the save, before any Chronicl3r call.
    logs = [("registration.unregister", {"server_id": SERVER_ID, "discord_id": target})]
    if unlink is not None and unlink.log_code is not None:
        logs += [("chronicler.key.unlink",
                 {"server_id": SERVER_ID, "discord_id": target, "code": unlink.log_code})]
    delta = {
        "replies": _one_success_reply(unlink.line if line == "auto" else line),
        "registrations.saved": appended_with(_without(seeded, target)),
        "chronicler.calls": appended_with(*(unlink.calls if calls is None else calls)),
        "log.records": appended_with(*logs),
    }
    if is_capped:
        delta["capped.saved"] = appended_with(_without(capped_seed, target))
    return delta


# ------------------------------------------------------------- properties

@SETTINGS
@given(key=target_keys, admin=admin_keys, other=other_keys, who=whos,
       is_capped=capped, data=st.data())
def test_unregistering_removes_the_key_from_chronicler_and_says_so(
        key, admin, other, who, is_capped, data):
    unlink = data.draw(unlink_outcomes(key).filter(lambda u: u.line == REMOVED))
    seeded, capped_seed, target = _seed(
        who, {"api_key": key, "guild_id": GUILD_ID}, admin, other, is_capped)

    before, after, _ = _run(who, unlink.routes, seeded, capped_seed)

    _assert_state_delta(before, after,
                        _expected_delta(seeded, capped_seed, target, is_capped, unlink))


@SETTINGS
@given(key=target_keys, admin=admin_keys, other=other_keys, who=whos,
       is_capped=capped, data=st.data())
def test_unregistering_still_succeeds_when_chronicler_fails(
        key, admin, other, who, is_capped, data):
    unlink = data.draw(failing_unlink_outcomes(key))
    seeded, capped_seed, target = _seed(
        who, {"api_key": key, "guild_id": GUILD_ID}, admin, other, is_capped)

    before, after, _ = _run(who, unlink.routes, seeded, capped_seed)

    _assert_state_delta(before, after,
                        _expected_delta(seeded, capped_seed, target, is_capped, unlink))
    assert cog_module.CHRONICLER_UNLINKED_LINE not in after["replies"][-1]


@SETTINGS
@given(key=target_keys, admin=admin_keys, other=other_keys,
       who=st.sampled_from(("target_user", "user_id")), is_capped=capped, data=st.data())
def test_an_admin_unregistering_another_player_removes_that_players_key(
        key, admin, other, who, is_capped, data):
    unlink = data.draw(unlink_outcomes(key))
    seeded, capped_seed, target = _seed(
        who, {"api_key": key, "guild_id": GUILD_ID}, admin, other, is_capped)

    before, after, _ = _run(who, unlink.routes, seeded, capped_seed)

    _assert_state_delta(before, after,
                        _expected_delta(seeded, capped_seed, target, is_capped, unlink))
    sent_keys = [body["tacticus_player_api_key"] for _, _, body in after["chronicler.calls"]
                 if body is not None]
    assert sent_keys == [key]
    assert str(INVOKER_ID) in after["registrations.saved"][-1]


@SETTINGS
@given(entry=no_key_entries, admin=admin_keys, other=other_keys, who=whos, is_capped=capped)
def test_an_entry_with_no_key_unregisters_without_calling_chronicler(
        entry, admin, other, who, is_capped):
    seeded, capped_seed, target = _seed(who, entry, admin, other, is_capped)

    before, after, _ = _run(who, {}, seeded, capped_seed)

    _assert_state_delta(before, after,
                        _expected_delta(seeded, capped_seed, target, is_capped, None,
                                        calls=[], line=None))


@SETTINGS
@given(key=target_keys, admin=admin_keys, other=other_keys, who=whos, is_capped=capped)
def test_a_cog_without_a_player_service_unregisters_without_calling_chronicler(
        key, admin, other, who, is_capped):
    seeded, capped_seed, target = _seed(
        who, {"api_key": key, "guild_id": GUILD_ID}, admin, other, is_capped)

    before, after, _ = _run(who, {}, seeded, capped_seed, cog=RegistrationCog(object()))

    _assert_state_delta(before, after,
                        _expected_delta(seeded, capped_seed, target, is_capped, None,
                                        calls=[], line=None))


@SETTINGS
@given(key=target_keys, admin=admin_keys, other=other_keys, who=whos,
       is_capped=capped, data=st.data())
def test_no_unregister_reply_or_log_carries_a_key_or_a_tacticus_id(
        key, admin, other, who, is_capped, data):
    unlink = data.draw(unlink_outcomes(key))
    seeded, capped_seed, _ = _seed(
        who, {"api_key": key, "guild_id": GUILD_ID}, admin, other, is_capped)

    _, after, world = _run(who, unlink.routes, seeded, capped_seed)

    secrets = {key, admin, other, TOKEN}
    for answer in unlink.routes.values():
        if isinstance(answer, BaseException):
            secrets.update(str(arg) for arg in answer.args)
        elif answer.headers.get("Content-Type") == "application/json":
            body = answer.json()
            secrets.update(str(body[field]) for field in ("detail", "tacticus_user_id")
                           if field in body)
    surfaces = list(after["replies"]) + list(world.recorder.raw)
    assert after["replies"], "the command sent no reply"
    for secret in secrets:
        for surface in surfaces:
            assert secret not in surface, "a reply or log record carries a secret"
