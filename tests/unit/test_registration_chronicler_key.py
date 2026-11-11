"""Property tests for linking the key on /registration register (02-01).

WHY-NEW-FILE: tests/unit/test_registration_chronicler_key.py
  CLOSEST-EXISTING: tests/unit/test_registration_validate_keys.py
  EXTENSION-COST: that module tests two pure module-level helpers with
    example tests and mocks httpx only; these properties drive the real
    `register` callback end to end and need a fake Chronicl3r transport,
    fake registration storage and a log recorder that module has none of.
  PARALLEL-RATIONALE: different port. Those tests enter through
    `_probe_api_keys` / `_format_key_validation`; these enter through the
    `/registration register` slash-command callback and observe the reply,
    the saved registrations, the Chronicl3r requests and the log.

THE PORT. `RegistrationCog.register`, invoked through
`RegistrationCog.__cog_app_commands__`, with a REAL `PlayerService` over a
REAL `chronicl3rClient` whose `requests.post` / `requests.delete` are replaced
by a fake Chronicl3r that answers only scripted routes. The Tacticus check
(httpx) and the registration storage (`bot.guilds` helpers bound in the cog
module) are faked at the cog module.

DECLARED UNIVERSE, strict:

    replies              — every message the command sent back
    registrations.saved  — every registrations table the command saved
    chronicler.calls     — every request the fake Chronicl3r received
    log.records          — every `bot.*` log record emitted (structured
                           records reduced to event + fields)

A slot no property declares must come out identical. No test makes a
network call.
"""
from __future__ import annotations

import ast
import asyncio
import copy
import importlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

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

SERVER_ID = 424242
GUILD_ID = "guild-alpha"
GUILD_NAME = "Alpha Company"
INVOKER_ID = 1001
TARGET_ID = 2002
OTHER_ID = "3003"

BASE = "https://www.chronicl3r.com/tacticus-guild-raid/api/v1/"
TOKEN = "fake-chronicler-token"
LINK_URL = BASE + "player-profiles/api-keys/"
LOOKUP_URL = BASE + "player-profiles/lookup/"

CONTRACT_CODES = (
    "missing_key", "invalid_key", "no_profiles", "no_name_match", "no_match",
    "insufficient_data", "ambiguous", "upstream_rate_limited",
    "upstream_unavailable", "throttled", "not_linked",
)
ERROR_STATUSES = (400, 404, 409, 422, 429, 500, 502, 503)

LINKED = "linked"
RETRY = "retry"
INSUFFICIENT = "insufficient"

NOTICE = ("Your key is also stored in Chronicl3r, our backend data store, "
          "which may allow roster analytics. Unregistering deletes it.")

UNIVERSE = ("replies", "registrations.saved", "chronicler.calls", "log.records")

SETTINGS = settings(max_examples=40, deadline=None)

# ---------------------------------------------------------------- strategies

new_keys = st.from_regex(r"fake-key-new-[a-z0-9]{4,10}", fullmatch=True)
old_keys = st.from_regex(r"fake-key-old-[a-z0-9]{4,10}", fullmatch=True)
other_keys = st.from_regex(r"fake-key-oth-[a-z0-9]{4,10}", fullmatch=True)
sentinel_details = st.from_regex(r"sentinel-detail-[a-f0-9]{8}", fullmatch=True)
sentinel_user_ids = st.from_regex(r"sentinel-tuid-[a-f0-9]{8}", fullmatch=True)
variants = st.sampled_from(("new", "updated", "target_user"))


class _Answer:
    """One scripted Chronicl3r answer and the reply line it must produce."""

    def __init__(self, label, answer, line, log_code):
        self.label = label
        self.answer = answer
        self.line = line
        self.log_code = log_code

    def __repr__(self):
        return f"_Answer({self.label})"


@st.composite
def linked_answers(draw):
    user_id = draw(sentinel_user_ids)
    body = {"tacticus_user_id": user_id,
            "tacticus_display_nm": "Sentinel Name",
            "detail": draw(sentinel_details),
            "has_valid_api_key": True}
    return _Answer("200", _response(200, json_body=body), LINKED, "linked")


@st.composite
def contract_answers(draw, codes=CONTRACT_CODES):
    status = draw(st.sampled_from(ERROR_STATUSES))
    code = draw(st.sampled_from(codes))
    body = {"code": code, "detail": draw(sentinel_details),
            "tacticus_user_id": draw(sentinel_user_ids)}
    line = INSUFFICIENT if code == "insufficient_data" else RETRY
    return _Answer(f"{status} {code}", _response(status, json_body=body), line, code)


def not_deployed_answers():
    return st.just(None).map(lambda _: _Answer(
        "not_deployed", _response(404, text="<html>Not Found</html>",
                                  content_type="text/html"),
        RETRY, "not_deployed"))


@st.composite
def transport_answers(draw):
    exc = draw(st.sampled_from((requests.Timeout, requests.ConnectionError,
                                requests.RequestException)))
    return _Answer("transport", exc(draw(sentinel_details)), RETRY, "transport")


@st.composite
def raised_answers(draw):
    exc = draw(st.sampled_from((RuntimeError, KeyError, ValueError)))
    return _Answer("raised", exc(draw(sentinel_details)), RETRY, "transport")


def failing_link_answers():
    return st.one_of(contract_answers(), not_deployed_answers(),
                     transport_answers(), raised_answers())


def any_link_answer():
    return st.one_of(linked_answers(), failing_link_answers())


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
        answer = self.routes[(method, url)].answer
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


def _tacticus_accepts():
    async def _get(url, headers=None, **kwargs):
        response = AsyncMock()
        response.raise_for_status = lambda: None
        return response

    client = AsyncMock()
    client.get = _get
    context = AsyncMock()
    context.__aenter__.return_value = client
    context.__aexit__.return_value = None
    return context


class _Storage:
    def __init__(self, seeded):
        self.table = copy.deepcopy(seeded)
        self.saved = []

    def load(self, server_id):
        assert server_id == SERVER_ID
        return copy.deepcopy(self.table)

    def save(self, server_id, registrations):
        assert server_id == SERVER_ID
        self.table = copy.deepcopy(registrations)
        self.saved.append(copy.deepcopy(registrations))


# ------------------------------------------------------------ the scenario

class _World:
    def __init__(self, routes, seeded):
        self.fake = _FakeChronicler(routes)
        self.storage = _Storage(seeded)
        self.recorder = _LogRecorder()
        self.interaction = _Interaction()

    def capture(self):
        return {
            "replies": list(self.interaction.replies),
            "registrations.saved": copy.deepcopy(self.storage.saved),
            "chronicler.calls": list(self.fake.calls),
            "log.records": list(self.recorder.records),
        }


@contextmanager
def _patched(world):
    with recording_bot_logs(world.recorder), chronicler_transport(world.fake), \
            patch("bot.cogs.registration_cog.httpx.AsyncClient",
                  return_value=_tacticus_accepts()), \
            patch("bot.cogs.registration_cog.load_guilds",
                  lambda server_id: {GUILD_ID: {"name": GUILD_NAME, "member_role_ids": [1]}}), \
            patch("bot.cogs.registration_cog.load_player_registrations",
                  world.storage.load), \
            patch("bot.cogs.registration_cog.save_player_registrations",
                  world.storage.save), \
            patch("bot.cogs.registration_cog.repo", _Repo()):
        yield


def _register_command():
    for command in RegistrationCog.__cog_app_commands__:
        for child in getattr(command, "commands", ()):
            if child.name == "register":
                return child
    raise AssertionError("no `register` command is registered on RegistrationCog")


def _cog():
    client = client_module.chronicl3rClient()
    client.token = TOKEN
    return RegistrationCog(object(), service_module.PlayerService(client))


def _seed(variant, previous_key, other_key):
    seeded = {OTHER_ID: {"api_key": other_key, "guild_id": GUILD_ID}}
    registrant = str(TARGET_ID if variant == "target_user" else INVOKER_ID)
    if variant != "new":
        seeded[registrant] = {"api_key": previous_key, "guild_id": GUILD_ID}
    return seeded, registrant


def _run(variant, key, routes, seeded):
    world = _World(routes, seeded)
    before = world.capture()
    kwargs = {"api_key": key, "guild_id": GUILD_ID}
    if variant == "target_user":
        kwargs["target_user"] = _Member(TARGET_ID)
    with _patched(world):
        asyncio.run(_register_command().callback(_cog(), world.interaction, **kwargs))
    return before, world.capture(), world


def _link_call(key):
    return ("POST", LINK_URL, {"tacticus_player_api_key": key})


def _lookup_call(key):
    return ("POST", LOOKUP_URL, {"tacticus_player_api_key": key})


def _delete_call(user_id):
    return ("DELETE", BASE + f"player-profiles/{user_id}/api-key/", None)


def _saved_table(seeded, registrant, key):
    table = copy.deepcopy(seeded)
    table[registrant] = {"api_key": key, "guild_id": GUILD_ID}
    return table


def _register_log(registrant):
    # The audit record; written right after the save, before any Chronicl3r call.
    return ("registration.register", {"server_id": SERVER_ID, "discord_id": registrant})


def _link_log(registrant, answer):
    return ("chronicler.key.link",
            {"server_id": SERVER_ID, "discord_id": registrant, "code": answer.log_code})


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


def _reply_lines():
    return {
        LINKED: cog_module.CHRONICLER_LINKED_LINE,
        RETRY: cog_module.CHRONICLER_RETRY_LINE,
        INSUFFICIENT: cog_module.CHRONICLER_INSUFFICIENT_DATA_LINE,
    }


def _one_success_reply(line_kind):
    """Exactly one reply: a success line, exactly one link line, then the notice."""
    lines = _reply_lines()

    def check(before, after):
        if len(after) != len(before) + 1:
            return False
        reply = after[-1].split("\n")
        link_lines = [part for part in reply if part in lines.values()]
        return (reply[0].startswith("✅")
                and link_lines == [lines[line_kind]]
                and reply[-1] == NOTICE
                and reply.count(NOTICE) == 1
                and len(reply) == 3)

    return satisfies(f"one success reply with the {line_kind} line and the notice", check)


# ------------------------------------------------------------- properties

@SETTINGS
@given(key=new_keys, other=other_keys, variant=variants, data=st.data())
def test_registering_links_the_key_and_says_so(key, other, variant, data):
    answer = data.draw(linked_answers())
    seeded, registrant = _seed(variant, key, other)

    before, after, _ = _run(variant, key, {("POST", LINK_URL): answer}, seeded)

    _assert_state_delta(before, after, {
        "replies": _one_success_reply(LINKED),
        "registrations.saved": appended_with(_saved_table(seeded, registrant, key)),
        "chronicler.calls": appended_with(_link_call(key)),
        "log.records": appended_with(_register_log(registrant), _link_log(registrant, answer)),
    })


@SETTINGS
@given(key=new_keys, other=other_keys, variant=variants, data=st.data())
def test_a_link_failure_never_fails_the_registration(key, other, variant, data):
    answer = data.draw(failing_link_answers())
    seeded, registrant = _seed(variant, key, other)

    before, after, _ = _run(variant, key, {("POST", LINK_URL): answer}, seeded)

    _assert_state_delta(before, after, {
        "replies": _one_success_reply(answer.line),
        "registrations.saved": appended_with(_saved_table(seeded, registrant, key)),
        "chronicler.calls": appended_with(_link_call(key)),
        "log.records": appended_with(_register_log(registrant), _link_log(registrant, answer)),
    })


@SETTINGS
@given(key=new_keys, other=other_keys, variant=variants, data=st.data())
def test_each_link_outcome_gets_one_reply_line_in_our_own_words(key, other, variant, data):
    answer = data.draw(any_link_answer())
    seeded, registrant = _seed(variant, key, other)

    before, after, _ = _run(variant, key, {("POST", LINK_URL): answer}, seeded)

    _assert_state_delta(before, after, {
        "replies": _one_success_reply(answer.line),
        "registrations.saved": appended_with(_saved_table(seeded, registrant, key)),
        "chronicler.calls": appended_with(_link_call(key)),
        "log.records": appended_with(_register_log(registrant), _link_log(registrant, answer)),
    })
    if answer.line == INSUFFICIENT:
        line = cog_module.CHRONICLER_INSUFFICIENT_DATA_LINE
        assert "3" in line and "showcase" in line and "not linked" in line


@SETTINGS
@given(key=new_keys, other=other_keys, variant=variants, data=st.data())
def test_every_successful_registration_reply_carries_the_storage_notice(key, other, variant, data):
    answer = data.draw(any_link_answer())
    seeded, registrant = _seed(variant, key, other)

    before, after, _ = _run(variant, key, {("POST", LINK_URL): answer}, seeded)

    _assert_state_delta(before, after, {
        "replies": satisfies("one reply ending in the notice", lambda b, a: (
            len(a) == len(b) + 1 and a[-1].endswith("\n" + NOTICE) and a[-1].count(NOTICE) == 1)),
        "registrations.saved": appended_with(_saved_table(seeded, registrant, key)),
        "chronicler.calls": appended_with(_link_call(key)),
        "log.records": appended_with(_register_log(registrant), _link_log(registrant, answer)),
    })


@st.composite
def unlink_routes(draw, old):
    """Chronicl3r's answers to unlinking `old`, the calls they cost, and whether it worked."""
    kind = draw(st.sampled_from(("deleted", "never_linked", "lookup_fails",
                                 "delete_fails", "raises")))
    lookup = ("POST", LOOKUP_URL)
    if kind == "never_linked":
        body = {"code": "not_linked", "detail": draw(sentinel_details)}
        return {lookup: _Answer(kind, _response(404, json_body=body), None, None)}, \
            [_lookup_call(old)], True
    if kind == "lookup_fails":
        answer = draw(st.one_of(contract_answers(tuple(c for c in CONTRACT_CODES
                                                       if c != "not_linked")),
                                not_deployed_answers(), transport_answers()))
        return {lookup: answer}, [_lookup_call(old)], False
    if kind == "raises":
        return {lookup: draw(raised_answers())}, [_lookup_call(old)], False
    user_id = draw(sentinel_user_ids)
    held = _response(200, json_body={"tacticus_user_id": user_id,
                                     "detail": draw(sentinel_details),
                                     "has_valid_api_key": True})
    status = 204 if kind == "deleted" else draw(st.sampled_from((200, 400, 404, 500)))
    deleted = _response(status, json_body=None, text="")
    return {lookup: _Answer("held", held, None, None),
            ("DELETE", BASE + f"player-profiles/{user_id}/api-key/"):
                _Answer(kind, deleted, None, None)}, \
        [_lookup_call(old), _delete_call(user_id)], kind == "deleted"


@SETTINGS
@given(key=new_keys, old=old_keys, other=other_keys,
       variant=st.sampled_from(("updated", "target_user")),
       changed=st.booleans(), data=st.data())
def test_only_a_changed_key_unlinks_the_old_one_and_before_the_new_link(
        key, old, other, variant, changed, data):
    link = data.draw(any_link_answer())
    previous = old if changed else key
    seeded, registrant = _seed(variant, previous, other)
    routes = {("POST", LINK_URL): link}
    unlink_calls, unlink_logs = [], []
    if changed:
        extra_routes, unlink_calls, unlinked = data.draw(unlink_routes(old))
        routes.update(extra_routes)
        if not unlinked:
            code = "transport" if any(isinstance(a.answer, Exception) and
                                      not isinstance(a.answer, requests.RequestException)
                                      for a in extra_routes.values()) else "unlink_failed"
            unlink_logs = [("chronicler.key.unlink",
                            {"server_id": SERVER_ID, "discord_id": registrant, "code": code})]

    before, after, _ = _run(variant, key, routes, seeded)

    _assert_state_delta(before, after, {
        "replies": _one_success_reply(link.line),
        "registrations.saved": appended_with(_saved_table(seeded, registrant, key)),
        "chronicler.calls": appended_with(*unlink_calls, _link_call(key)),
        "log.records": appended_with(_register_log(registrant), *unlink_logs,
                                     _link_log(registrant, link)),
    })


@SETTINGS
@given(key=new_keys, old=old_keys, other=other_keys, variant=variants,
       changed=st.booleans(), data=st.data())
def test_no_register_reply_or_log_carries_a_key_a_detail_or_a_tacticus_id(
        key, old, other, variant, changed, data):
    link = data.draw(any_link_answer())
    changed = changed and variant != "new"
    seeded, registrant = _seed(variant, old if changed else key, other)
    routes = {("POST", LINK_URL): link}
    if changed:
        routes.update(data.draw(unlink_routes(old))[0])

    _, after, world = _run(variant, key, routes, seeded)

    secrets = {key, old, other, TOKEN}
    for answer in routes.values():
        raw = answer.answer
        if isinstance(raw, BaseException):
            secrets.update(str(arg) for arg in raw.args)
        elif raw.headers.get("Content-Type") == "application/json":
            body = raw.json()
            secrets.update(str(body[field]) for field in ("detail", "tacticus_user_id",
                                                          "tacticus_display_nm")
                           if field in body)
    surfaces = list(after["replies"]) + list(world.recorder.raw)
    assert after["replies"], "the command sent no reply"
    for secret in secrets:
        for surface in surfaces:
            assert secret not in surface, "a reply or log record carries a secret"


def test_the_registration_cog_is_built_with_the_shared_player_service():
    # bypass: composition wiring — one representative call proves the wire.
    class _Bot:
        def __init__(self):
            self.cogs = []

        async def add_cog(self, cog):
            self.cogs.append(cog)

    fake_bot = _Bot()
    service = service_module.PlayerService(client_module.chronicl3rClient())
    asyncio.run(cog_module.setup_registration(fake_bot, service))
    assert len(fake_bot.cogs) == 1
    assert isinstance(fake_bot.cogs[0], RegistrationCog)
    assert fake_bot.cogs[0].player_service is service

    # main.py's load_cogs hands setup_registration the SAME name it builds
    # from PlayerService(...) and hands every other service-taking setup.
    source = (Path(__file__).resolve().parents[2] / "main.py").read_text(encoding="utf-8")
    load_cogs = next(node for node in ast.walk(ast.parse(source))
                     if isinstance(node, ast.AsyncFunctionDef) and node.name == "load_cogs")
    built = {target.id for node in ast.walk(load_cogs) if isinstance(node, ast.Assign)
             and isinstance(node.value, ast.Call)
             and getattr(node.value.func, "id", None) == "PlayerService"
             for target in node.targets if isinstance(target, ast.Name)}
    assert len(built) == 1
    calls = {node.func.id: [getattr(arg, "id", None) for arg in node.args]
             for node in ast.walk(load_cogs)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
             and node.func.id.startswith("setup_")}
    assert calls["setup_registration"] == ["bot", *built]
    assert calls["setup_admin"] == ["bot", *built]
    assert calls["setup_tasks"] == ["bot", *built]
