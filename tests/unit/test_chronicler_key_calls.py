"""Property tests for the three key calls on the Chronicl3r client (01-01).

WHY-NEW-FILE: tests/unit/test_chronicler_key_calls.py
  CLOSEST-EXISTING: tests/unit/test_registration_validate_keys.py
  EXTENSION-COST: that module drives the registration cog's Tacticus probe
    through a mocked `httpx`; these properties drive `chronicl3rClient`
    through a faked `requests`, so the two share neither transport nor port.
  PARALLEL-RATIONALE: incompatible dependency set — `bot.services.chronicl3r`
    must never import httpx (pytest-archon rule), and its tests patch a
    different library at a different module path.

THE PORT. `chronicl3rClient.link_api_key`, `lookup_api_key` and
`delete_api_key` are the driving port for this step. Every property enters
there and observes only what the port exposes: the returned `KeyCallResult`,
the request the fake Chronicl3r received, and the client's token.

DECLARED UNIVERSE, strict:

    result.status       — HTTP status, or None when no response arrived
    result.code         — contract code (or not_deployed / transport)
    result.retry_after  — Retry-After seconds, when given as digits
    result.profile      — the profile body, on a 200 only
    transport.calls     — every request the fake Chronicl3r received
    client.token        — the Chronicl3r token the client holds

Before a call there is no result, so the four result slots start at None.
A slot no property declares must come out byte-identical.

No test makes a network call: the fake raises AssertionError for any URL,
method or timeout the contract does not name.
"""
from __future__ import annotations

import dataclasses
import importlib
import json
import os
from contextlib import contextmanager
from unittest.mock import patch

# Same precedent as tests/unit/test_registration_validate_keys.py: pin
# placeholders before any `bot.*` module is loaded.
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

BASE = "https://www.chronicl3r.com/tacticus-guild-raid/api/v1/"
AUTH_URL = "https://www.chronicl3r.com/api/auth/token/"
TOKEN = "fake-chronicler-token"
LINK_TIMEOUT = 60
OTHER_TIMEOUT = 10

CONTRACT_CODES = (
    "missing_key", "invalid_key", "no_profiles", "no_name_match", "no_match",
    "insufficient_data", "ambiguous", "upstream_rate_limited",
    "upstream_unavailable", "throttled", "not_linked",
)
ERROR_STATUSES = (400, 404, 409, 422, 429, 500, 502, 503)

UNIVERSE = (
    "result.status", "result.code", "result.retry_after", "result.profile",
    "transport.calls", "client.token",
)

SETTINGS = settings(max_examples=60, deadline=None)

# ---------------------------------------------------------------- strategies

fake_keys = st.from_regex(r"fake-key-[a-z0-9]{4,12}", fullmatch=True)
fake_user_ids = st.from_regex(r"sentinel-tuid-[a-f0-9]{8}", fullmatch=True)
operations = st.sampled_from(("link", "lookup", "delete"))
posting_operations = st.sampled_from(("link", "lookup"))
digit_seconds = st.integers(min_value=0, max_value=86_400)
non_digit_retry_after = st.one_of(
    st.just("Wed, 21 Oct 2026 07:28:00 GMT"),
    st.just("-5"), st.just("1.5"), st.just("soon"), st.just(""),
    st.just("١٢"),  # Arabic-Indic digits: isdigit() but not ASCII
)
html_bodies = st.sampled_from((
    "<!DOCTYPE html><html><body><h1>Not Found</h1></body></html>",
    "<html><title>404</title></html>",
    "Not Found",
    "",
))


@st.composite
def profiles(draw, user_id):
    return {
        "id": draw(st.integers(min_value=1, max_value=10**6)),
        "tacticus_user_id": user_id,
        "tacticus_display_nm": draw(st.text(min_size=1, max_size=20)),
        "has_valid_api_key": True,
    }


# ------------------------------------------------------------- fake transport

def _response(status, *, json_body=None, text=None, content_type=None,
              retry_after=None):
    response = requests.Response()
    response.status_code = status
    headers = CaseInsensitiveDict()
    if json_body is not None:
        response._content = json.dumps(json_body).encode()
        headers["Content-Type"] = content_type or "application/json"
    else:
        response._content = (text or "").encode()
        if content_type is not None:
            headers["Content-Type"] = content_type
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    response.headers = headers
    response.encoding = "utf-8"
    return response


def _route(operation, user_id):
    if operation == "link":
        return "POST", BASE + "player-profiles/api-keys/", LINK_TIMEOUT
    if operation == "lookup":
        return "POST", BASE + "player-profiles/lookup/", OTHER_TIMEOUT
    return "DELETE", BASE + f"player-profiles/{user_id}/api-key/", OTHER_TIMEOUT


class _FakeChronicler:
    """Answers exactly one contract route; refuses everything else."""

    def __init__(self, operation, user_id, answer, auth_failure=None):
        self.method, self.url, self.timeout = _route(operation, user_id)
        self.answer = answer
        self.auth_failure = auth_failure
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None, **kwargs):
        if url == AUTH_URL and self.auth_failure is not None:
            self.calls.append(("POST", url, timeout, None, False))
            raise self.auth_failure
        return self._serve("POST", url, json, headers, timeout, kwargs)

    def delete(self, url, json=None, headers=None, timeout=None, **kwargs):
        return self._serve("DELETE", url, json, headers, timeout, kwargs)

    def _serve(self, method, url, body, headers, timeout, kwargs):
        if (method, url) != (self.method, self.url):
            raise AssertionError(f"unexpected request {method} {url}")
        if timeout != self.timeout:
            raise AssertionError(f"{method} {url} sent timeout={timeout}")
        if kwargs:
            raise AssertionError(f"unexpected kwargs {sorted(kwargs)}")
        authorised = headers == {"Authorization": f"Token {TOKEN}"}
        self.calls.append((method, url, timeout, body, authorised))
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


def _refuse(*args, **kwargs):
    raise AssertionError("a key call used a verb the contract does not name")


@contextmanager
def _chronicler(fake):
    target = "bot.services.chronicl3r.client.requests"
    with patch(f"{target}.post", fake.post), \
            patch(f"{target}.delete", fake.delete), \
            patch(f"{target}.get", _refuse), \
            patch(f"{target}.patch", _refuse), \
            patch(f"{target}.put", _refuse), \
            patch(f"{target}.request", _refuse):
        yield


def _client(token=TOKEN):
    client = client_module.chronicl3rClient()
    client.token = token
    return client


def _invoke(client, operation, key, user_id):
    if operation == "link":
        return client.link_api_key(key)
    if operation == "lookup":
        return client.lookup_api_key(key)
    return client.delete_api_key(user_id)


def _expected_call(operation, key, user_id):
    method, url, timeout = _route(operation, user_id)
    body = None if operation == "delete" else {"tacticus_player_api_key": key}
    return (method, url, timeout, body, True)


# ------------------------------------------------------------ state delta

def _capture(client, fake, result=None):
    return {
        "result.status": None if result is None else result.status,
        "result.code": None if result is None else result.code,
        "result.retry_after": None if result is None else result.retry_after,
        "result.profile": None if result is None else result.profile,
        "transport.calls": list(fake.calls),
        "client.token": client.token,
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
        elif kind == "appended_with":
            assert after[slot] == before[slot] + operand, f"{slot} is {after[slot]!r}"
        else:
            raise AssertionError(f"unknown predicate {kind}")


def _run(operation, key, user_id, answer, client=None, auth_failure=None):
    client = client or _client()
    fake = _FakeChronicler(operation, user_id, answer, auth_failure)
    before = _capture(client, fake)
    with _chronicler(fake):
        result = _invoke(client, operation, key, user_id)
    assert isinstance(result, client_module.KeyCallResult)
    return before, _capture(client, fake, result), result


# ------------------------------------------------------------- properties

@SETTINGS
@given(operation=operations, key=fake_keys, user_id=fake_user_ids, data=st.data())
def test_a_linked_key_comes_back_as_a_profile_with_no_code(operation, key, user_id, data):
    if operation == "delete":
        answer, profile, status = _response(204), None, 204
    else:
        profile = data.draw(profiles(user_id))
        answer, status = _response(200, json_body=profile), 200

    before, after, _ = _run(operation, key, user_id, answer)

    _assert_state_delta(before, after, {
        "result.status": set_to(status),
        "result.code": unchanged(),
        "result.retry_after": unchanged(),
        "result.profile": set_to(profile) if profile is not None else unchanged(),
        "transport.calls": appended_with(_expected_call(operation, key, user_id)),
    })


@SETTINGS
@given(operation=operations, key=fake_keys, user_id=fake_user_ids,
       code=st.sampled_from(CONTRACT_CODES), status=st.sampled_from(ERROR_STATUSES))
def test_every_contract_error_code_is_read_from_the_body(operation, key, user_id, code, status):
    answer = _response(status, json_body={"code": code, "detail": f"echo {key}"})

    before, after, _ = _run(operation, key, user_id, answer)

    _assert_state_delta(before, after, {
        "result.status": set_to(status),
        "result.code": set_to(code),
        "transport.calls": appended_with(_expected_call(operation, key, user_id)),
    })


@SETTINGS
@given(operation=operations, key=fake_keys, user_id=fake_user_ids, data=st.data())
def test_a_route_that_is_not_deployed_reads_as_not_deployed(operation, key, user_id, data):
    shape = data.draw(st.sampled_from(("html", "no-content-type", "broken-json")))
    if shape == "broken-json":
        answer = _response(404, text="<html>{not json", content_type="application/json")
    else:
        content_type = "text/html; charset=utf-8" if shape == "html" else None
        answer = _response(404, text=data.draw(html_bodies), content_type=content_type)

    before, after, _ = _run(operation, key, user_id, answer)

    _assert_state_delta(before, after, {
        "result.status": set_to(404),
        "result.code": set_to("not_deployed"),
        "transport.calls": appended_with(_expected_call(operation, key, user_id)),
    })


@SETTINGS
@given(operation=operations, key=fake_keys, user_id=fake_user_ids, data=st.data(),
       throttle=st.sampled_from(((429, "throttled"), (503, "upstream_rate_limited"),
                                 (503, "upstream_unavailable"))))
def test_retry_after_is_honoured_on_throttled_and_unavailable_responses(
        operation, key, user_id, data, throttle):
    status, code = throttle
    if data.draw(st.booleans()):
        seconds = data.draw(digit_seconds)
        header, expected_retry = str(seconds), set_to(seconds)
    else:
        header, expected_retry = data.draw(non_digit_retry_after), unchanged()
    answer = _response(status, json_body={"code": code, "detail": "wait"}, retry_after=header)

    before, after, _ = _run(operation, key, user_id, answer)

    _assert_state_delta(before, after, {
        "result.status": set_to(status),
        "result.code": set_to(code),
        "result.retry_after": expected_retry,
        "transport.calls": appended_with(_expected_call(operation, key, user_id)),
    })


@SETTINGS
@given(operation=operations, key=fake_keys, user_id=fake_user_ids,
       failure=st.sampled_from((requests.Timeout, requests.ConnectTimeout,
                                requests.ReadTimeout, requests.ConnectionError,
                                requests.HTTPError, requests.RequestException)),
       during_token_fetch=st.booleans())
def test_a_timeout_or_dropped_connection_reads_as_transport(
        operation, key, user_id, failure, during_token_fetch):
    if during_token_fetch:
        client = _client(token=None)
        before, after, _ = _run(operation, key, user_id, answer=None, client=client,
                                auth_failure=failure("token endpoint failed"))
        expected_call = ("POST", AUTH_URL, None, None, False)
    else:
        before, after, _ = _run(operation, key, user_id, answer=failure("dropped"))
        expected_call = _expected_call(operation, key, user_id)

    _assert_state_delta(before, after, {
        "result.code": set_to("transport"),
        "transport.calls": appended_with(expected_call),
    })


@SETTINGS
@given(operation=operations, key=fake_keys, user_id=fake_user_ids,
       status=st.sampled_from((400, 401, 403, 404, 405, 415, 500)),
       body=st.one_of(
           st.builds(lambda d: {"detail": d}, st.text(max_size=30)),
           st.builds(lambda c: {"code": c, "detail": "x"},
                     st.one_of(st.none(), st.integers(), st.lists(st.text(max_size=3)),
                               st.dictionaries(st.text(max_size=3), st.integers()))),
           st.lists(st.text(max_size=5), max_size=3),
       ))
def test_a_drf_error_without_a_code_keeps_only_its_status(operation, key, user_id, status, body):
    answer = _response(status, json_body=body)

    before, after, _ = _run(operation, key, user_id, answer)

    _assert_state_delta(before, after, {
        "result.status": set_to(status),
        "transport.calls": appended_with(_expected_call(operation, key, user_id)),
    })


@SETTINGS
@given(operation=operations, key=fake_keys, user_id=fake_user_ids,
       status=st.sampled_from((200, 204, 404, 429, 503)))
def test_linking_waits_sixty_seconds_and_every_other_key_call_ten(operation, key, user_id, status):
    answer = _response(status, json_body={"code": "throttled"}) if status in (429, 503) \
        else _response(status, text="")
    fake = _FakeChronicler(operation, user_id, answer)
    client = _client()

    with _chronicler(fake):
        _invoke(client, operation, key, user_id)

    (call,) = fake.calls
    assert call[2] == (LINK_TIMEOUT if operation == "link" else OTHER_TIMEOUT)


@SETTINGS
@given(operation=operations, key=fake_keys, user_id=fake_user_ids, data=st.data(),
       status=st.sampled_from((200, 204, 400, 404, 409, 429, 503)))
def test_the_key_never_appears_in_a_result_or_its_repr(operation, key, user_id, data, status):
    if status == 200:
        answer = _response(200, json_body=data.draw(profiles(user_id)))
    elif status == 204:
        answer = _response(204)
    else:
        answer = _response(status, json_body={
            "code": data.draw(st.sampled_from(CONTRACT_CODES)),
            "detail": f"key {key} for {user_id} rejected",
            "tacticus_player_api_key": key,
            "tacticus_user_id": user_id,
        }, retry_after="30")

    _, _, result = _run(operation, key, user_id, answer)

    rendered = repr(result)
    assert key not in rendered
    assert user_id not in rendered
    assert [f.name for f in dataclasses.fields(result)] == [
        "status", "code", "retry_after", "profile"]
    for name in ("status", "code", "retry_after"):
        assert key not in str(getattr(result, name))
        assert user_id not in str(getattr(result, name))
    if status != 200:
        assert result.profile is None


def test_the_removed_patch_and_duplicate_getter_are_gone():
    # bypass: absence of an attribute is a single fact with no input space to quantify.
    client = client_module.chronicl3rClient()
    assert not hasattr(client, "set_player_api_key")
    assert not hasattr(client, "get_player_profile")
    assert hasattr(client, "get_profile")
