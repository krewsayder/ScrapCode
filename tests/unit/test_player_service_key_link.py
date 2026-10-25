"""Property tests for PlayerService's three key calls (01-02).

WHY-NEW-FILE: tests/unit/test_player_service_key_link.py
  CLOSEST-EXISTING: tests/unit/test_chronicler_key_calls.py
  EXTENSION-COST: that module's fake answers exactly ONE route per call and
    its universe has four `result.*` slots for a raw `KeyCallResult`;
    unlinking makes TWO calls (lookup, then a DELETE on a URL the lookup
    decides) and returns a bool, so every property there would have to
    widen its fake and re-declare its universe. The task also forbids
    editing that file.
  PARALLEL-RATIONALE: different port. Those properties enter through
    `chronicl3rClient`; these enter through `PlayerService`, which is
    async, runs the client on a worker thread, and strips identity from
    what it returns — none of which the client-level universe can observe.

THE PORT. `PlayerService.link_key`, `lookup_key` and `unlink_key`, over a
REAL `chronicl3rClient` whose `requests.post` / `requests.delete` are
replaced by a fake Chronicl3r that answers only the routes a property
scripts, records every request, and raises AssertionError for anything else.

DECLARED UNIVERSE, strict:

    result            — what the PlayerService call returned (None before)
    transport.calls   — every request the fake Chronicl3r received
    client.token      — the Chronicl3r token the client holds
    log.records       — every `bot.*` log message emitted while the call ran

A slot no property declares must come out identical. The worker-thread
idents the fake records are instrumentation for the off-loop property only,
and sit outside the universe.

No test makes a network call.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import threading
from contextlib import contextmanager
from unittest.mock import patch

os.environ.setdefault("SCRAPCODE_REPO_BACKEND", "json")
os.environ.setdefault("UPDATE_CHANNEL_ID", "0")
os.environ.setdefault("REPLAY_INDEX_CHANNEL_ID", "0")

import pytest
import requests
from requests.structures import CaseInsensitiveDict

hypothesis = pytest.importorskip("hypothesis")

import hypothesis.strategies as st
from hypothesis import given, settings

client_module = importlib.import_module("bot.services.chronicl3r.client")
service_module = importlib.import_module("bot.services.chronicl3r.player_service")

KeyCallResult = client_module.KeyCallResult

BASE = "https://www.chronicl3r.com/tacticus-guild-raid/api/v1/"
TOKEN = "fake-chronicler-token"
LINK_URL = BASE + "player-profiles/api-keys/"
LOOKUP_URL = BASE + "player-profiles/lookup/"
LINK_TIMEOUT = 60
OTHER_TIMEOUT = 10

CONTRACT_CODES = (
    "missing_key", "invalid_key", "no_profiles", "no_name_match", "no_match",
    "insufficient_data", "ambiguous", "upstream_rate_limited",
    "upstream_unavailable", "throttled", "not_linked",
)
ERROR_STATUSES = (400, 404, 409, 422, 429, 500, 502, 503)

UNIVERSE = ("result", "transport.calls", "client.token", "log.records")

SETTINGS = settings(max_examples=60, deadline=None)

# ---------------------------------------------------------------- strategies

fake_keys = st.from_regex(r"fake-key-[a-z0-9]{4,12}", fullmatch=True)
sentinel_user_ids = st.from_regex(r"sentinel-tuid-[a-f0-9]{8}", fullmatch=True)


def _delete_url(user_id):
    return BASE + f"player-profiles/{user_id}/api-key/"


def _response(status, *, json_body=None, text=None, content_type=None):
    response = requests.Response()
    response.status_code = status
    headers = CaseInsensitiveDict()
    if json_body is not None:
        response._content = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    else:
        response._content = (text or "").encode()
        if content_type is not None:
            headers["Content-Type"] = content_type
    response.headers = headers
    response.encoding = "utf-8"
    return response


class _Outcome:
    """One scripted Chronicl3r answer and the KeyCallResult the client reads from it."""

    def __init__(self, label, answer, status, code, profile):
        self.label = label
        self.answer = answer
        self.status = status
        self.code = code
        self.profile = profile

    def __repr__(self):
        return f"_Outcome({self.label})"

    def reduced(self):
        """The result PlayerService must hand back: validity only, no identity."""
        profile = None
        if self.profile is not None:
            profile = {"has_valid_api_key": self.profile.get("has_valid_api_key") is True}
        return KeyCallResult(status=self.status, code=self.code, retry_after=None,
                             profile=profile)


@st.composite
def held_profiles(draw, user_id):
    return {
        "id": draw(st.integers(min_value=1, max_value=10**6)),
        "tacticus_user_id": user_id,
        "tacticus_display_nm": draw(st.from_regex(r"Sentinel Name [A-Z]{3}", fullmatch=True)),
        "has_valid_api_key": draw(st.booleans()),
    }


def held(user_id):
    return held_profiles(user_id).map(
        lambda p: _Outcome("held", _response(200, json_body=p), 200, None, p))


def contract_errors(codes=CONTRACT_CODES):
    return st.builds(
        lambda status, code, user_id: _Outcome(
            f"{status} {code}",
            _response(status, json_body={"code": code,
                                         "detail": f"echo {user_id}",
                                         "tacticus_user_id": user_id}),
            status, code, None),
        st.sampled_from(ERROR_STATUSES), st.sampled_from(codes), sentinel_user_ids)


def not_deployed():
    return st.just(None).map(lambda _: _Outcome(
        "not_deployed", _response(404, text="<html>Not Found</html>",
                                  content_type="text/html"),
        404, "not_deployed", None))


def transport():
    return st.sampled_from((requests.Timeout, requests.ConnectionError,
                            requests.RequestException)).map(
        lambda exc: _Outcome("transport", exc("dropped"), None, "transport", None))


def not_linked():
    return st.just(None).map(lambda _: _Outcome(
        "not_linked", _response(404, json_body={"code": "not_linked",
                                                "detail": "no profile holds this key"}),
        404, "not_linked", None))


def held_without_usable_id():
    bad_ids = st.one_of(st.none(), st.just(""), st.integers(), st.lists(st.text(max_size=3)))
    return st.builds(
        lambda bad, drop: _Outcome(
            "held-without-id",
            _response(200, json_body=({"has_valid_api_key": True} if drop
                                      else {"tacticus_user_id": bad,
                                            "has_valid_api_key": True})),
            200, None, {"has_valid_api_key": True}),
        bad_ids, st.booleans())


def any_single_call_outcome(user_id):
    return st.one_of(held(user_id), contract_errors(), not_deployed(), transport())


def delete_success():
    return st.just(None).map(lambda _: _Outcome("deleted", _response(204), 204, None, None))


def delete_failures():
    return st.one_of(
        contract_errors(), not_deployed(), transport(),
        st.sampled_from((200, 202, 400, 500)).map(
            lambda s: _Outcome(f"delete {s}", _response(s, text=""), s, None, None)),
    )


# ------------------------------------------------------------- fake transport

class _FakeChronicler:
    """Answers only the scripted (method, url) routes; refuses everything else."""

    def __init__(self, routes):
        self.routes = dict(routes)
        self.calls = []
        self.threads = []

    def post(self, url, json=None, headers=None, timeout=None, **kwargs):
        return self._serve("POST", url, json, headers, timeout, kwargs)

    def delete(self, url, json=None, headers=None, timeout=None, **kwargs):
        return self._serve("DELETE", url, json, headers, timeout, kwargs)

    def _serve(self, method, url, body, headers, timeout, kwargs):
        self.threads.append(threading.get_ident())
        if (method, url) not in self.routes:
            raise AssertionError(f"unexpected request {method}")
        expected_timeout = LINK_TIMEOUT if url == LINK_URL else OTHER_TIMEOUT
        if timeout != expected_timeout:
            raise AssertionError(f"{method} sent timeout={timeout}")
        if kwargs:
            raise AssertionError(f"unexpected kwargs {sorted(kwargs)}")
        authorised = headers == {"Authorization": f"Token {TOKEN}"}
        self.calls.append((method, url, timeout, body, authorised))
        answer = self.routes[(method, url)].answer
        if isinstance(answer, BaseException):
            raise answer
        return answer


def _refuse(*args, **kwargs):
    raise AssertionError("a key call used a verb the contract does not name")


class _LogRecorder(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage() + " " + repr(record.__dict__))


@contextmanager
def _chronicler(fake, recorder):
    target = "bot.services.chronicl3r.client.requests"
    root = logging.getLogger("bot")
    previous_level = root.level
    root.addHandler(recorder)
    root.setLevel(logging.DEBUG)
    try:
        with patch(f"{target}.post", fake.post), \
                patch(f"{target}.delete", fake.delete), \
                patch(f"{target}.get", _refuse), \
                patch(f"{target}.patch", _refuse), \
                patch(f"{target}.put", _refuse), \
                patch(f"{target}.request", _refuse):
            yield
    finally:
        root.removeHandler(recorder)
        root.setLevel(previous_level)


def _service():
    client = client_module.chronicl3rClient()
    client.token = TOKEN
    return service_module.PlayerService(client), client


# ------------------------------------------------------------ state delta

def _capture(client, fake, recorder, result=None):
    return {
        "result": result,
        "transport.calls": list(fake.calls),
        "client.token": client.token,
        "log.records": list(recorder.messages),
    }


def unchanged():
    return ("unchanged", None)


def set_to(value):
    return ("set_to", value)


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
        elif kind == "set_to":
            assert after[slot] == operand, f"{slot} is {after[slot]!r}, expected {operand!r}"
            assert type(after[slot]) is type(operand), f"{slot} has type {type(after[slot])}"
            if isinstance(operand, KeyCallResult):
                assert after[slot].profile == operand.profile, f"{slot}.profile is {after[slot].profile!r}"
        elif kind == "appended_with":
            assert after[slot] == before[slot] + operand, f"{slot} is {after[slot]!r}"
        else:
            raise AssertionError(f"unknown predicate {kind}")


def _drive(operation, key, routes):
    service, client = _service()
    fake = _FakeChronicler(routes)
    recorder = _LogRecorder()
    before = _capture(client, fake, recorder)
    with _chronicler(fake, recorder):
        result = asyncio.run(getattr(service, operation)(key))
    return before, _capture(client, fake, recorder, result), fake


def _link_call(key):
    return ("POST", LINK_URL, LINK_TIMEOUT, {"tacticus_player_api_key": key}, True)


def _lookup_call(key):
    return ("POST", LOOKUP_URL, OTHER_TIMEOUT, {"tacticus_player_api_key": key}, True)


def _delete_call(user_id):
    return ("DELETE", _delete_url(user_id), OTHER_TIMEOUT, None, True)


# ------------------------------------------------------------- properties

@SETTINGS
@given(key=fake_keys, user_id=sentinel_user_ids, data=st.data())
def test_linking_hands_the_key_to_chronicler_and_returns_the_outcome(key, user_id, data):
    outcome = data.draw(any_single_call_outcome(user_id))

    before, after, _ = _drive("link_key", key, {("POST", LINK_URL): outcome})

    _assert_state_delta(before, after, {
        "result": set_to(outcome.reduced()),
        "transport.calls": appended_with(_link_call(key)),
    })


@SETTINGS
@given(key=fake_keys, user_id=sentinel_user_ids, data=st.data())
def test_unlinking_deletes_the_key_of_the_profile_the_lookup_names(key, user_id, data):
    lookup = data.draw(held(user_id))

    before, after, _ = _drive("unlink_key", key, {
        ("POST", LOOKUP_URL): lookup,
        ("DELETE", _delete_url(user_id)): data.draw(delete_success()),
    })

    _assert_state_delta(before, after, {
        "result": set_to(True),
        "transport.calls": appended_with(_lookup_call(key), _delete_call(user_id)),
    })


@SETTINGS
@given(key=fake_keys, data=st.data())
def test_a_key_chronicler_never_linked_counts_as_unlinked(key, data):
    before, after, _ = _drive("unlink_key", key, {
        ("POST", LOOKUP_URL): data.draw(not_linked()),
    })

    _assert_state_delta(before, after, {
        "result": set_to(True),
        "transport.calls": appended_with(_lookup_call(key)),
    })


@SETTINGS
@given(key=fake_keys, user_id=sentinel_user_ids, data=st.data())
def test_unlinking_reports_failure_when_the_lookup_or_the_delete_fails(key, user_id, data):
    failing_lookups = st.one_of(
        contract_errors(tuple(c for c in CONTRACT_CODES if c != "not_linked")),
        not_deployed(), transport(), held_without_usable_id(),
    )
    lookup_fails = data.draw(st.booleans())
    if lookup_fails:
        routes = {("POST", LOOKUP_URL): data.draw(failing_lookups)}
        calls = (_lookup_call(key),)
    else:
        routes = {("POST", LOOKUP_URL): data.draw(held(user_id)),
                  ("DELETE", _delete_url(user_id)): data.draw(delete_failures())}
        calls = (_lookup_call(key), _delete_call(user_id))

    before, after, _ = _drive("unlink_key", key, routes)

    _assert_state_delta(before, after, {
        "result": set_to(False),
        "transport.calls": appended_with(*calls),
    })


@SETTINGS
@given(key=fake_keys, user_id=sentinel_user_ids, data=st.data())
def test_a_held_key_lookup_reports_validity_only(key, user_id, data):
    outcome = data.draw(st.one_of(held(user_id), not_linked(), contract_errors(),
                                  not_deployed(), transport()))

    before, after, _ = _drive("lookup_key", key, {("POST", LOOKUP_URL): outcome})

    _assert_state_delta(before, after, {
        "result": set_to(outcome.reduced()),
        "transport.calls": appended_with(_lookup_call(key)),
    })
    if outcome.profile is not None:
        assert after["result"].profile == {
            "has_valid_api_key": outcome.profile["has_valid_api_key"]}


@SETTINGS
@given(key=fake_keys, user_id=sentinel_user_ids, data=st.data(),
       operation=st.sampled_from(("link_key", "lookup_key", "unlink_key")))
def test_no_tacticus_user_id_leaves_the_player_service(key, user_id, data, operation):
    first = data.draw(st.one_of(held(user_id), held(user_id), contract_errors(),
                                not_deployed(), transport()))
    url = LINK_URL if operation == "link_key" else LOOKUP_URL
    routes = {("POST", url): first}
    if operation == "unlink_key":
        routes[("DELETE", _delete_url(user_id))] = data.draw(
            st.one_of(delete_success(), delete_failures()))

    _, after, fake = _drive(operation, key, routes)

    result = after["result"]
    rendered = [repr(result), str(result)]
    if isinstance(result, KeyCallResult):
        rendered.append(repr(result.profile))
    for text in rendered + after["log.records"]:
        assert user_id not in text
        assert key not in text
        assert "Sentinel Name" not in text
    assert after["log.records"] == []
    leaked = [call for call in fake.calls if user_id in repr(call)]
    assert leaked == [call for call in fake.calls if call[0] == "DELETE"]
    assert all(call[1] == _delete_url(user_id) for call in leaked)


@SETTINGS
@given(key=fake_keys, user_id=sentinel_user_ids, data=st.data(),
       operation=st.sampled_from(("link_key", "lookup_key", "unlink_key")))
def test_key_calls_run_off_the_event_loop(key, user_id, data, operation):
    url = LINK_URL if operation == "link_key" else LOOKUP_URL
    routes = {("POST", url): data.draw(held(user_id)),
              ("DELETE", _delete_url(user_id)): data.draw(delete_success())}
    service, client = _service()
    fake = _FakeChronicler(routes)
    loop_thread = []

    async def call():
        loop_thread.append(threading.get_ident())
        return await getattr(service, operation)(key)

    with _chronicler(fake, _LogRecorder()):
        asyncio.run(call())

    assert fake.threads, "the key call never reached Chronicl3r"
    assert all(ident != loop_thread[0] for ident in fake.threads)
