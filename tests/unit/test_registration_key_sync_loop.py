"""Property tests for the background Chronicl3r key-sync loop (03-02).

WHY-NEW-FILE: tests/unit/test_registration_key_sync_loop.py
  CLOSEST-EXISTING: tests/unit/test_registration_chronicler_key.py
  EXTENSION-COST: that module drives ONE `/registration register` call per
    example against one server; these properties drive repeated loop ticks
    over many servers with a controlled clock, a per-key fake Chronicl3r and
    the cog lifecycle, which would double that module's fixtures.
  PARALLEL-RATIONALE: different port and lifecycle. Those tests enter through
    a slash-command callback inside one `asyncio.run`; these enter through the
    `chronicler_key_sync` tasks.Loop body (and `bot.add_cog`/`remove_cog`),
    across ticks that share one in-memory schedule.

THE PORT. `RegistrationCog.chronicler_key_sync` (the tasks.Loop body, awaited
directly), plus `setup_registration` / `bot.remove_cog` for the lifecycle and
the real `/registration register` callback for the shared schedule. A REAL
`PlayerService` over a REAL `chronicl3rClient` whose `requests.post` /
`requests.delete` are replaced by a fake Chronicl3r answering per key.
Registration storage (`repo.list_server_ids`, `load_player_registrations`,
`save_player_registrations`) is faked at the cog module. The cog's `clock`
attribute is set to the test's `now`.

DECLARED UNIVERSE, strict:

    chronicler.lookups   — every key looked up, in order
    chronicler.links     — every key linked, in order
    registrations.saved  — every registrations table saved
    log.records          — every `bot.*` log record (structured records
                           reduced to event + fields)
    replies              — every message sent back (register only)

A slot no property declares must come out identical. No test makes a
network call.
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import importlib
import json
import math
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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
    StructuredLogRecorder, chronicler_transport, recording_bot_logs,
    response as _response,
)

import discord
from discord.ext import commands

cog_module = importlib.import_module("bot.cogs.registration_cog")
client_module = importlib.import_module("bot.services.chronicl3r.client")
service_module = importlib.import_module("bot.services.chronicl3r.player_service")

RegistrationCog = cog_module.RegistrationCog

BASE = "https://www.chronicl3r.com/tacticus-guild-raid/api/v1/"
TOKEN = "fake-chronicler-token"
LINK_URL = BASE + "player-profiles/api-keys/"
LOOKUP_URL = BASE + "player-profiles/lookup/"

GUILD_ID = "guild-alpha"
INVOKER_ID = 1001
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
TICK = timedelta(minutes=2)

LOOKUPS_PER_TICK = 20
LOOKUP_EVENT = "chronicler.key_sync.lookup"
LINK_EVENT = "chronicler.key_sync.link"
PAUSED_EVENT = "chronicler.key_sync.paused"
TICK_FAILED_EVENT = "chronicler.key_sync.tick_failed"

PARKING_CODES = ("insufficient_data", "ambiguous", "invalid_key", "missing_key")
TRANSIENT_CODES = ("upstream_rate_limited", "upstream_unavailable", "throttled")
CONFIGURATION_CODES = ("no_profiles",)

UNIVERSE = ("chronicler.lookups", "chronicler.links", "registrations.saved", "log.records")
REGISTER_UNIVERSE = UNIVERSE + ("replies",)

SETTINGS = settings(max_examples=30, deadline=None)

# ---------------------------------------------------------------- responses

sentinel_details = st.from_regex(r"sentinel-detail-[a-f0-9]{8}", fullmatch=True)
sentinel_user_ids = st.from_regex(r"sentinel-tuid-[a-f0-9]{8}", fullmatch=True)


class _Answer:
    """One scripted Chronicl3r answer, what the loop must call it, and its effect.

    `effect` is the model's view: 'done', 'link' (link now), 'pause'.
    """

    def __init__(self, label, answer, code, effect, pause_seconds=None):
        self.label = label
        self.answer = answer
        self.code = code
        self.effect = effect
        self.pause_seconds = pause_seconds

    def __repr__(self):
        return f"_Answer({self.label})"


def _profile(draw, valid):
    return {"tacticus_user_id": draw(sentinel_user_ids),
            "tacticus_display_nm": "Sentinel Name",
            "detail": draw(sentinel_details),
            "has_valid_api_key": valid}


@st.composite
def held_answers(draw):
    return _Answer("held", _response(200, json_body=_profile(draw, True)), "linked", "done")


@st.composite
def stale_answers(draw):
    return _Answer("stale", _response(200, json_body=_profile(draw, False)), "not_linked", "link")


@st.composite
def absent_answers(draw):
    body = {"code": "not_linked", "detail": draw(sentinel_details)}
    return _Answer("absent", _response(404, json_body=body), "not_linked", "link")


@st.composite
def parked_answers(draw):
    code = draw(st.sampled_from(PARKING_CODES))
    body = {"code": code, "detail": draw(sentinel_details),
            "tacticus_user_id": draw(sentinel_user_ids)}
    status = draw(st.sampled_from((400, 404, 409, 422)))
    return _Answer(f"{status} {code}", _response(status, json_body=body), code, "done")


def settling_lookup_answers():
    """Lookup answers that settle the player without pausing the loop."""
    return st.one_of(held_answers(), stale_answers(), absent_answers(), parked_answers())


@st.composite
def pausing_answers(draw):
    kind = draw(st.sampled_from(("transient", "codeless", "configuration",
                                 "not_deployed", "transport", "raised")))
    retry_after = draw(st.one_of(st.none(), st.integers(0, 2000)))
    transient = max(retry_after or 0, 300)
    detail = draw(sentinel_details)
    if kind == "transient":
        code = draw(st.sampled_from(TRANSIENT_CODES))
        answer = _response(draw(st.sampled_from((429, 502, 503))),
                           json_body={"code": code, "detail": detail}, retry_after=retry_after)
        return _Answer(f"transient {code}", answer, code, "pause", transient)
    if kind == "codeless":
        answer = _response(500, json_body={"detail": detail}, retry_after=retry_after)
        return _Answer("codeless 500", answer, "unexpected", "pause", transient)
    if kind == "configuration":
        answer = _response(503, json_body={"code": "no_profiles", "detail": detail})
        return _Answer("no_profiles", answer, "no_profiles", "pause", 3600)
    if kind == "not_deployed":
        answer = _response(404, text="<html>Not Found</html>", content_type="text/html")
        return _Answer("not_deployed", answer, "not_deployed", "pause", 3600)
    if kind == "transport":
        exc = draw(st.sampled_from((requests.Timeout, requests.ConnectionError)))
        return _Answer("transport", exc(detail), "transport", "pause", 300)
    exc = draw(st.sampled_from((RuntimeError, KeyError, ValueError)))
    return _Answer("raised", exc(detail), "transport", "pause", 300)


def any_lookup_answers():
    return st.one_of(settling_lookup_answers(), pausing_answers())


@st.composite
def link_answers(draw):
    """Every link answer shape the register command may see."""
    kind = draw(st.sampled_from(("linked", "contract", "pausing")))
    if kind == "linked":
        return _Answer("linked", _response(200, json_body=_profile(draw, True)), "linked", "done")
    if kind == "pausing":
        return draw(pausing_answers())
    code = draw(st.sampled_from(PARKING_CODES + ("no_match", "no_name_match", "not_linked")))
    body = {"code": code, "detail": draw(sentinel_details),
            "tacticus_user_id": draw(sentinel_user_ids)}
    effect = "link" if code == "not_linked" else "done"
    return _Answer(f"link {code}", _response(422, json_body=body), code, effect)


# ------------------------------------------------------------- populations

@st.composite
def populations(draw, min_size=0, max_size=45):
    """Registrations over 2-3 servers: [(server_id, discord_id, key)] in loop order.

    Returns (tables, registrations): `tables` is what storage holds, including
    keyless entries the loop must skip.
    """
    server_ids = draw(st.lists(st.integers(10**17, 10**18), min_size=2, max_size=3, unique=True))
    size = draw(st.integers(min_size, max_size))
    owners = draw(st.lists(st.sampled_from(server_ids), min_size=size, max_size=size))
    salt = draw(st.from_regex(r"[a-z0-9]{4}", fullmatch=True))
    tables = {server_id: {} for server_id in server_ids}
    registrations = []
    for index, server_id in enumerate(owners):
        discord_id = str(5000 + index)
        key = f"fake-key-{salt}-{index}"
        tables[server_id][discord_id] = {"api_key": key, "guild_id": GUILD_ID}
    for server_id in server_ids:
        if draw(st.booleans()):
            tables[server_id]["9999"] = {"api_key": "", "guild_id": GUILD_ID}
        for discord_id, entry in tables[server_id].items():
            if entry["api_key"]:
                registrations.append((server_id, discord_id, entry["api_key"]))
    return tables, registrations


# ------------------------------------------------------------- fake Chronicl3r

class _FakeChronicler:
    """Answers lookups and links per key; refuses every other request."""

    def __init__(self, lookups=None, links=None, default_link=None):
        self.lookup_answers = dict(lookups or {})
        self.link_answers = dict(links or {})
        self.default_link = default_link
        self.lookups = []
        self.links = []

    def post(self, url, json=None, headers=None, timeout=None, **kwargs):
        if kwargs:
            raise AssertionError(f"unexpected kwargs {sorted(kwargs)}")
        if headers != {"Authorization": f"Token {TOKEN}"}:
            raise AssertionError("POST sent without the Chronicl3r token")
        key = (json or {}).get("tacticus_player_api_key")
        if url == LOOKUP_URL:
            self.lookups.append(key)
            answer = self.lookup_answers[key].answer
        elif url == LINK_URL:
            self.links.append(key)
            chosen = self.link_answers.get(key, self.default_link)
            if chosen is None:
                raise AssertionError("a link nobody scripted")
            answer = chosen.answer
        else:
            raise AssertionError("unexpected POST route")
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def delete(self, *args, **kwargs):
        raise AssertionError("the key sync never deletes")


class _LogRecorder(StructuredLogRecorder):
    FIELDS = ("server_id", "discord_id", "code", "pause_seconds", "error_type")


class _Storage:
    def __init__(self, tables, fail_on=None):
        self.tables = copy.deepcopy(tables)
        self.saved = []
        self.fail_on = fail_on or {}

    def list_server_ids(self):
        if "list" in self.fail_on:
            raise self.fail_on["list"]
        return list(self.tables)

    def load(self, server_id):
        if server_id in self.fail_on:
            raise self.fail_on[server_id]
        return copy.deepcopy(self.tables.get(server_id, {}))

    def save(self, server_id, registrations):
        self.tables[server_id] = copy.deepcopy(registrations)
        self.saved.append((server_id, copy.deepcopy(registrations)))


class _World:
    def __init__(self, tables, fake, fail_on=None):
        self.fake = fake
        self.storage = _Storage(tables, fail_on)
        self.recorder = _LogRecorder()
        self.replies = []
        self.cog = _cog()
        self.now = T0
        self.cog.clock = lambda: self.now

    def capture(self, universe=UNIVERSE):
        state = {
            "chronicler.lookups": list(self.fake.lookups),
            "chronicler.links": list(self.fake.links),
            "registrations.saved": copy.deepcopy(self.storage.saved),
            "log.records": list(self.recorder.records),
            "replies": list(self.replies),
        }
        return {slot: state[slot] for slot in universe}

    async def tick(self, at=None):
        if at is not None:
            self.now = at
        await self.cog.chronicler_key_sync.coro(self.cog)


@contextmanager
def _patched(world):
    with recording_bot_logs(world.recorder), chronicler_transport(world.fake), \
            patch.object(cog_module.repo, "list_server_ids", world.storage.list_server_ids), \
            patch("bot.cogs.registration_cog.load_player_registrations", world.storage.load), \
            patch("bot.cogs.registration_cog.save_player_registrations", world.storage.save), \
            patch.dict(os.environ, {"CHRONICL3R_KEY_SYNC": "on"}):
        yield


def _cog(bot=None):
    client = client_module.chronicl3rClient()
    client.token = TOKEN
    return RegistrationCog(bot if bot is not None else object(),
                           service_module.PlayerService(client))


def _run(world, scenario):
    with _patched(world):
        asyncio.run(scenario())


# ------------------------------------------------------------ state delta

def unchanged():
    return ("unchanged", None)


def appended_with(*items):
    return ("appended_with", list(items))


def satisfies(description, check):
    return ("satisfies", (description, check))


def _assert_state_delta(before, after, expected, universe=UNIVERSE):
    """Strict: a universe slot with no declared predicate must not move."""
    assert set(before) == set(universe) == set(after), "capture drifted from UNIVERSE"
    assert set(expected) <= set(universe), "a predicate names an undeclared slot"
    for slot in universe:
        kind, operand = expected.get(slot, unchanged())
        if kind == "unchanged":
            assert after[slot] == before[slot], f"{slot} moved: {before[slot]!r} -> {after[slot]!r}"
        elif kind == "appended_with":
            assert after[slot] == before[slot] + operand, (
                f"{slot} is {after[slot]!r}, expected +{operand!r}")
        elif kind == "satisfies":
            description, check = operand
            assert check(before[slot], after[slot]), f"{slot} is {after[slot]!r}: not {description}"
        else:
            raise AssertionError(f"unknown predicate {kind}")


def _call_log(event, registration, code):
    server_id, discord_id, _ = registration
    return (event, {"server_id": server_id, "discord_id": discord_id, "code": code})


# ----------------------------------------------------- the model of one tick

class _Model:
    """The loop as the plan describes it, for settling answers only."""

    def __init__(self, registrations, answers):
        self.registrations = registrations
        self.answers = answers
        self.state = {r: "lookup" for r in registrations}

    def tick(self):
        """(lookups, links, log records) one tick should produce."""
        lookups, logs = [], []
        for registration in [r for r in self.registrations if self.state[r] == "lookup"][:LOOKUPS_PER_TICK]:
            answer = self.answers[registration[2]]
            lookups.append(registration[2])
            logs.append(_call_log(LOOKUP_EVENT, registration, answer.code))
            self.state[registration] = answer.effect
        links = []
        linkable = [r for r in self.registrations if self.state[r] == "link"]
        if linkable:
            links.append(linkable[0][2])
            logs.append(_call_log(LINK_EVENT, linkable[0], "linked"))
            self.state[linkable[0]] = "done"
        return lookups, links, logs

    def settled(self):
        return all(state == "done" for state in self.state.values())


def _linked_link():
    return _Answer("linked", _response(200, json_body={"has_valid_api_key": True,
                                                        "tacticus_user_id": "sentinel-tuid-0"}),
                   "linked", "done")


# ------------------------------------------------------------- properties

@SETTINGS
@given(population=populations(), data=st.data())
def test_a_tick_spends_at_most_twenty_lookups_and_one_link(population, data):
    tables, registrations = population
    answers = {r[2]: data.draw(settling_lookup_answers()) for r in registrations}
    world = _World(tables, _FakeChronicler(answers, default_link=_linked_link()))
    model = _Model(registrations, answers)
    ticks = math.ceil(len(registrations) / LOOKUPS_PER_TICK) + len(registrations) + 1

    async def scenario():
        for index in range(ticks):
            before = world.capture()
            await world.tick(at=T0 + index * TICK)
            after = world.capture()
            lookups, links, logs = model.tick()
            assert len(lookups) <= LOOKUPS_PER_TICK and len(links) <= 1
            _assert_state_delta(before, after, {
                "chronicler.lookups": appended_with(*lookups),
                "chronicler.links": appended_with(*links),
                "log.records": appended_with(*logs),
            })
        assert model.settled()
        assert sorted(world.fake.lookups) == sorted(r[2] for r in registrations)

    _run(world, scenario)


@SETTINGS
@given(population=populations(min_size=1))
def test_already_linked_keys_are_marked_done_without_spending_a_link(population):
    tables, registrations = population
    held = _Answer("held", _response(200, json_body={"has_valid_api_key": True}), "linked", "done")
    world = _World(tables, _FakeChronicler({r[2]: held for r in registrations}))
    batches = [registrations[i:i + LOOKUPS_PER_TICK]
               for i in range(0, len(registrations), LOOKUPS_PER_TICK)]

    async def scenario():
        for index, batch in enumerate(batches + [[]]):
            before = world.capture()
            await world.tick(at=T0 + index * TICK)
            _assert_state_delta(before, world.capture(), {
                "chronicler.lookups": appended_with(*(r[2] for r in batch)),
                "log.records": appended_with(*(_call_log(LOOKUP_EVENT, r, "linked") for r in batch)),
            })

    _run(world, scenario)


@SETTINGS
@given(population=populations(min_size=1, max_size=25), data=st.data())
def test_a_paused_loop_makes_no_call_until_the_pause_ends(population, data):
    tables, registrations = population
    position = data.draw(st.integers(0, min(len(registrations), LOOKUPS_PER_TICK) - 1))
    pausing = data.draw(pausing_answers())
    held = _Answer("held", _response(200, json_body={"has_valid_api_key": True}), "linked", "done")
    answers = {r[2]: held for r in registrations}
    answers[registrations[position][2]] = pausing
    world = _World(tables, _FakeChronicler(answers))
    pause = timedelta(seconds=pausing.pause_seconds)
    offsets = data.draw(st.lists(st.integers(1, pausing.pause_seconds - 1), max_size=4))

    async def scenario():
        before = world.capture()
        await world.tick(at=T0)
        stopped_at = registrations[:position + 1]
        _assert_state_delta(before, world.capture(), {
            "chronicler.lookups": appended_with(*(r[2] for r in stopped_at)),
            "log.records": appended_with(
                *(_call_log(LOOKUP_EVENT, r, "linked") for r in stopped_at[:-1]),
                _call_log(LOOKUP_EVENT, stopped_at[-1], pausing.code),
                (PAUSED_EVENT, {"code": pausing.code, "pause_seconds": pausing.pause_seconds}),
            ),
        })
        for offset in sorted(offsets):
            before = world.capture()
            await world.tick(at=T0 + timedelta(seconds=offset))
            _assert_state_delta(before, world.capture(), {})

        world.fake.lookup_answers[registrations[position][2]] = held
        before = world.capture()
        await world.tick(at=T0 + pause)
        resumed = registrations[position:position + LOOKUPS_PER_TICK]
        _assert_state_delta(before, world.capture(), {
            "chronicler.lookups": appended_with(*(r[2] for r in resumed)),
            "log.records": appended_with(*(_call_log(LOOKUP_EVENT, r, "linked") for r in resumed)),
        })

    _run(world, scenario)


@SETTINGS
@given(population=populations(min_size=1),
       switch=st.sampled_from(("off", "OFF", "Off", " off ", "oFf")))
def test_the_sync_can_be_switched_off_by_environment(population, switch):
    tables, registrations = population
    held = _Answer("held", _response(200, json_body={"has_valid_api_key": True}), "linked", "done")
    world = _World(tables, _FakeChronicler({r[2]: held for r in registrations}))

    async def scenario():
        with patch.dict(os.environ, {"CHRONICL3R_KEY_SYNC": switch}):
            for index in range(3):
                before = world.capture()
                await world.tick(at=T0 + index * TICK)
                _assert_state_delta(before, world.capture(), {})
        before = world.capture()
        await world.tick(at=T0 + 3 * TICK)
        first = registrations[:LOOKUPS_PER_TICK]
        _assert_state_delta(before, world.capture(), {
            "chronicler.lookups": appended_with(*(r[2] for r in first)),
            "log.records": appended_with(*(_call_log(LOOKUP_EVENT, r, "linked") for r in first)),
        })

    _run(world, scenario)


@SETTINGS
@given(population=populations(min_size=1, max_size=25),
       failure=st.sampled_from((RuntimeError, KeyError, ValueError, OSError, TypeError)),
       where=st.sampled_from(("list", "first_server")),
       detail=sentinel_details)
def test_a_failing_tick_never_stops_the_loop(population, failure, where, detail):
    tables, registrations = population
    held = _Answer("held", _response(200, json_body={"has_valid_api_key": True}), "linked", "done")
    first_server = next(iter(tables))
    fail_on = {"list" if where == "list" else first_server: failure(detail)}
    world = _World(tables, _FakeChronicler({r[2]: held for r in registrations}), fail_on)

    async def scenario():
        before = world.capture()
        await world.tick(at=T0)
        _assert_state_delta(before, world.capture(), {
            "log.records": appended_with((TICK_FAILED_EVENT, {"error_type": failure.__name__})),
        })
        assert not any(detail in line for line in world.recorder.raw)

        world.storage.fail_on.clear()
        before = world.capture()
        await world.tick(at=T0 + TICK)
        first = registrations[:LOOKUPS_PER_TICK]
        _assert_state_delta(before, world.capture(), {
            "chronicler.lookups": appended_with(*(r[2] for r in first)),
            "log.records": appended_with(*(_call_log(LOOKUP_EVENT, r, "linked") for r in first)),
        })

    _run(world, scenario)


def test_the_loop_starts_with_the_cog_and_stops_on_unload():
    # bypass: lifecycle is one wiring path (add_cog -> cog_load, remove_cog ->
    # cog_unload) with no input space to quantify over.
    async def scenario():
        never = asyncio.Event()
        bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
        bot.wait_until_ready = never.wait
        client = client_module.chronicl3rClient()
        client.token = TOKEN
        await cog_module.setup_registration(bot, service_module.PlayerService(client))
        cog = bot.get_cog("RegistrationCog")
        await asyncio.sleep(0)
        assert cog.chronicler_key_sync.is_running()
        task = cog.chronicler_key_sync.get_task()

        await bot.remove_cog("RegistrationCog")
        with contextlib.suppress(asyncio.CancelledError):
            await task
        assert task.cancelled()
        assert not cog.chronicler_key_sync.is_running()

        without_service = RegistrationCog(bot)
        await bot.add_cog(without_service)
        await asyncio.sleep(0)
        assert not without_service.chronicler_key_sync.is_running()
        await bot.remove_cog("RegistrationCog")
        await bot.close()

    asyncio.run(scenario())


# ------------------------------------------------- register shares the schedule

class _Role:
    id = 1


class _Permissions:
    administrator = True


class _Member:
    def __init__(self, member_id):
        self.id = member_id
        self.mention = f"<@{member_id}>"
        self.roles = [_Role()]
        self.guild_permissions = _Permissions()


class _Followup:
    def __init__(self, replies):
        self._replies = replies

    async def send(self, content="", *, embed=None, ephemeral=False, **kwargs):
        self._replies.append(content)


class _Interaction:
    def __init__(self, server_id, replies):
        self.guild_id = server_id
        self.user = _Member(INVOKER_ID)
        self.response = AsyncMock()
        self.followup = _Followup(replies)


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


def _register_command():
    for command in RegistrationCog.__cog_app_commands__:
        for child in getattr(command, "commands", ()):
            if child.name == "register":
                return child
    raise AssertionError("no `register` command is registered on RegistrationCog")


@SETTINGS
@given(population=populations(max_size=10), answer=link_answers(),
       salt=st.from_regex(r"[a-z0-9]{6}", fullmatch=True))
def test_registering_records_its_link_outcome_so_the_loop_does_not_repeat_it(population, answer, salt):
    tables, registrations = population
    server_id = next(iter(tables))
    key = f"fake-key-reg-{salt}"
    registrant = (server_id, str(INVOKER_ID), key)
    held = _Answer("held", _response(200, json_body={"has_valid_api_key": True}), "linked", "done")
    world = _World(tables, _FakeChronicler({r[2]: held for r in registrations},
                                           links={key: answer}))
    interaction = _Interaction(server_id, world.replies)

    async def scenario():
        with patch("bot.cogs.registration_cog.httpx.AsyncClient", return_value=_tacticus_accepts()), \
                patch("bot.cogs.registration_cog.load_guilds",
                      lambda sid: {GUILD_ID: {"name": "Alpha"}}):
            await _register_command().callback(world.cog, interaction, api_key=key, guild_id=GUILD_ID)
        assert world.fake.links == [key] and world.fake.lookups == []

        before = world.capture(REGISTER_UNIVERSE)
        await world.tick(at=T0)
        after = world.capture(REGISTER_UNIVERSE)

        def never_repeats(was, now):
            new = now[len(was):]
            return new.count(key) == (1 if answer.effect == "link" else 0)

        def nothing_while_paused(was, now):
            return answer.effect != "pause" or now == was

        def only_sync_records(was, now):
            return (all(event.startswith("chronicler.key_sync") for event, _ in now[len(was):])
                    and nothing_while_paused(was, now))

        _assert_state_delta(before, after, {
            "chronicler.lookups": satisfies(
                "no lookup of the registered key, none at all while paused",
                lambda was, now: key not in now[len(was):] and nothing_while_paused(was, now)),
            "chronicler.links": satisfies(
                "no repeated link of the registered key, none at all while paused",
                lambda was, now: never_repeats(was, now) and nothing_while_paused(was, now)),
            "log.records": satisfies("only key-sync records, none while paused", only_sync_records),
        }, REGISTER_UNIVERSE)

    _run(world, scenario)


@SETTINGS
@given(population=populations(min_size=1, max_size=30), data=st.data(),
       detail=sentinel_details)
def test_no_sync_log_carries_a_key_or_a_tacticus_id(population, data, detail):
    tables, registrations = population
    answers = {r[2]: data.draw(any_lookup_answers()) for r in registrations}
    links = {r[2]: data.draw(link_answers()) for r in registrations}
    world = _World(tables, _FakeChronicler(answers, links=links))
    failing_key = registrations[-1][2]

    async def scenario():
        for index in range(4):
            await world.tick(at=T0 + index * timedelta(hours=1))
        world.storage.fail_on[next(iter(tables))] = KeyError(failing_key + " " + detail)
        await world.tick(at=T0 + timedelta(hours=5))

    _run(world, scenario)
    assert world.fake.lookups, "the sync made no call at all"
    secrets = [r[2] for r in registrations] + [detail, "sentinel-tuid", "sentinel-detail"]
    leaked = [s for s in secrets for line in world.recorder.raw if s in line]
    assert not leaked, f"a sync log line carried a secret: {sorted(set(leaked))}"
    assert all(event.startswith("chronicler.key_sync") for event, _ in world.recorder.records)
