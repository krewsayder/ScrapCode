"""Property tests for the key-sync schedule (03-01).

WHY-NEW-FILE: tests/unit/test_chronicler_key_sync_schedule.py
  CLOSEST-EXISTING: tests/unit/test_chronicler_key_calls.py
  EXTENSION-COST: that module drives `chronicl3rClient` through a faked
    `requests` transport and declares a universe of result/transport/token
    slots; the schedule has no transport and no result, so every property
    here would have to declare six slots it cannot move.
  PARALLEL-RATIONALE: different port. Those properties observe one HTTP
    call; these observe a pure decision object over a sequence of recorded
    outcomes and clock advances, and the two universes share no slot.

THE PORT. `KeySyncSchedule` is the driving port: `due(registrations, now)`,
`record(server_id, discord_id, key, outcome, now, retry_after=None)` and
`paused_until(now)`. Nothing else is touched.

DECLARED UNIVERSE, strict, observed at a given instant:

    subject       — the action due for the registration the property drives
                    (LOOKUP, LINK, or None when it is not due)
    bystanders    — the action due for every other registration, by player
    paused_until  — when the whole loop resumes, or None when it is running

A slot with no declared predicate must come out identical.
"""
from __future__ import annotations

import os

os.environ.setdefault("SCRAPCODE_REPO_BACKEND", "json")
os.environ.setdefault("UPDATE_CHANNEL_ID", "0")
os.environ.setdefault("REPLAY_INDEX_CHANNEL_ID", "0")

import pickle
from datetime import datetime, timedelta, timezone

import pytest

hypothesis = pytest.importorskip("hypothesis")

import hypothesis.strategies as st
from hypothesis import given, settings

from bot.services.chronicl3r.key_sync import KeySyncAction, KeySyncSchedule

LOOKUP = KeySyncAction.LOOKUP
LINK = KeySyncAction.LINK

FIFTEEN_MINUTES = timedelta(minutes=15)
ONE_DAY = timedelta(hours=24)
ONE_HOUR = timedelta(hours=1)
ONE_SECOND = timedelta(seconds=1)
FOREVER = timedelta(days=3650)

PARKING_CODES = ("insufficient_data", "ambiguous", "invalid_key", "missing_key")
CONFIGURATION_CODES = ("no_profiles", "not_deployed")
TRANSIENT_CODES = ("upstream_rate_limited", "upstream_unavailable", "throttled", "transport")
# Outcomes outside the table — a code-less 401/403/5xx, an unexpected body.
UNKNOWN_OUTCOMES = (None, "unexpected", "http_500")
ALL_OUTCOMES = (
    ("linked", "not_linked", "no_match", "no_name_match")
    + PARKING_CODES + CONFIGURATION_CODES + TRANSIENT_CODES + UNKNOWN_OUTCOMES
)

UNIVERSE = ("subject", "bystanders", "paused_until")

SETTINGS = settings(max_examples=60, deadline=None)

# ---------------------------------------------------------------- strategies

# The prefix keeps every key out of the hex alphabet, so a key can never
# occur inside a SHA-256 fingerprint by coincidence.
keys = st.from_regex(r"fake-key-[a-z0-9]{8,16}", fullmatch=True)
player_ids = st.tuples(st.integers(1, 10**18), st.integers(1, 10**18))
registration_sets = st.lists(
    st.tuples(player_ids, keys), min_size=1, max_size=5, unique_by=lambda r: r[0],
).map(lambda rows: [(server, discord, key) for (server, discord), key in rows])
instants = st.datetimes(
    min_value=datetime(2026, 1, 1), max_value=datetime(2030, 1, 1),
    timezones=st.just(timezone.utc),
)
gaps = st.timedeltas(min_value=timedelta(0), max_value=timedelta(days=3))


# ------------------------------------------------------------------ helpers

def _capture(schedule: KeySyncSchedule, registrations, at: datetime) -> dict:
    """Snapshot every declared universe slot at instant `at`."""
    due = schedule.due(registrations, at)
    passed = {(s, d): (s, d, k) for s, d, k in registrations}
    by_player = {}
    for triple, action in due:
        assert passed[triple[:2]] == triple, "due() handed back a triple it was not given"
        by_player[triple[:2]] = action
    players = [triple[:2] for triple in registrations]
    return {
        "subject": by_player.get(players[0]),
        "bystanders": {player: by_player.get(player) for player in players[1:]},
        "paused_until": schedule.paused_until(at),
    }


class _Unchanged:
    def __repr__(self) -> str:
        return "unchanged()"


def unchanged() -> _Unchanged:
    return _Unchanged()


def set_to(value):
    return ("set_to", value)


def _assert_state_delta(before: dict, after: dict, expected: dict) -> None:
    """Strict: every universe slot without a declared predicate is unchanged."""
    assert set(before) == set(UNIVERSE) == set(after), "capture drifted from the universe"
    assert set(expected) <= set(UNIVERSE), f"undeclared slot in expected: {expected}"
    for slot in UNIVERSE:
        predicate = expected.get(slot, unchanged())
        if isinstance(predicate, _Unchanged):
            assert after[slot] == before[slot], (
                f"{slot} moved but was declared unchanged: {before[slot]!r} -> {after[slot]!r}"
            )
            continue
        kind, operand = predicate
        assert kind == "set_to", f"unknown predicate {kind!r}"
        assert after[slot] == operand, f"{slot} is {after[slot]!r}, expected {operand!r}"


def _record(schedule, triple, outcome, now, retry_after=None) -> None:
    server_id, discord_id, key = triple
    schedule.record(server_id, discord_id, key, outcome, now, retry_after=retry_after)


def _all_none(registrations) -> dict:
    return {triple[:2]: None for triple in registrations[1:]}


# --------------------------------------------------------------- properties

@SETTINGS
@given(registrations=registration_sets, now=instants)
def test_an_unseen_registration_is_due_at_once(registrations, now):
    """Every registration the schedule has never seen needs a lookup now,
    whatever the schedule already knows about everyone else."""
    schedule = KeySyncSchedule()
    before = _capture(schedule, registrations, now)
    assert before["subject"] is LOOKUP
    assert set(before["bystanders"].values()) <= {LOOKUP}

    for bystander in registrations[1:]:
        _record(schedule, bystander, "linked", now)

    _assert_state_delta(before, _capture(schedule, registrations, now), expected={
        "bystanders": set_to(_all_none(registrations)),
    })


@SETTINGS
@given(
    registrations=registration_sets, now=instants, via_link=st.booleans(),
    later=st.lists(gaps, min_size=1, max_size=4), new_key=keys,
)
def test_a_linked_key_is_never_due_again(registrations, now, via_link, later, new_key):
    """Linked (lookup said held, or the link succeeded) is final while the
    key is unchanged; a changed key starts the player afresh."""
    schedule = KeySyncSchedule()
    subject = registrations[0]
    if via_link:
        _record(schedule, subject, "not_linked", now)
        assert _capture(schedule, registrations, now)["subject"] is LINK
    before = _capture(schedule, registrations, now)

    _record(schedule, subject, "linked", now)

    _assert_state_delta(before, _capture(schedule, registrations, now), expected={
        "subject": set_to(None),
    })
    at = now
    for gap in later:
        at += gap
        assert _capture(schedule, registrations, at)["subject"] is None

    if new_key == subject[2]:
        return
    rekeyed = [(subject[0], subject[1], new_key)] + registrations[1:]
    assert _capture(schedule, rekeyed, at)["subject"] is LOOKUP


@SETTINGS
@given(
    registrations=registration_sets, now=instants,
    tries=st.integers(1, 6),
    extra_wait=st.lists(gaps, min_size=6, max_size=6),
)
def test_no_match_retries_every_fifteen_minutes_and_parks_after_four_tries(
    registrations, now, tries, extra_wait,
):
    schedule = KeySyncSchedule()
    subject = registrations[0]
    _record(schedule, subject, "not_linked", now)
    at = now
    for attempt in range(1, tries + 1):
        before = _capture(schedule, registrations, at)
        assert before["subject"] is LINK

        _record(schedule, subject, "no_match", at)

        _assert_state_delta(before, _capture(schedule, registrations, at), expected={
            "subject": set_to(None),
        })
        if attempt >= 4:
            assert _capture(schedule, registrations, at + FOREVER)["subject"] is None
            break
        not_yet = _capture(schedule, registrations, at + FIFTEEN_MINUTES - ONE_SECOND)
        assert not_yet["subject"] is None, "no_match retried before 15 minutes"
        at = at + FIFTEEN_MINUTES + extra_wait[attempt - 1]
        assert _capture(schedule, registrations, at)["subject"] is LINK


@SETTINGS
@given(
    registrations=registration_sets, now=instants,
    tries=st.integers(1, 8),
    extra_wait=st.lists(gaps, min_size=8, max_size=8),
)
def test_no_name_match_retries_a_day_later(registrations, now, tries, extra_wait):
    """No cap: every no_name_match is retried as a link 24 hours later."""
    schedule = KeySyncSchedule()
    subject = registrations[0]
    _record(schedule, subject, "not_linked", now)
    at = now
    for attempt in range(tries):
        before = _capture(schedule, registrations, at)
        assert before["subject"] is LINK

        _record(schedule, subject, "no_name_match", at)

        _assert_state_delta(before, _capture(schedule, registrations, at), expected={
            "subject": set_to(None),
        })
        not_yet = _capture(schedule, registrations, at + ONE_DAY - ONE_SECOND)
        assert not_yet["subject"] is None, "no_name_match retried before 24 hours"
        at = at + ONE_DAY + extra_wait[attempt]


@SETTINGS
@given(
    registrations=registration_sets, now=instants,
    code=st.sampled_from(PARKING_CODES), after_not_linked=st.booleans(), new_key=keys,
)
def test_a_permanent_refusal_parks_until_the_key_changes(
    registrations, now, code, after_not_linked, new_key,
):
    schedule = KeySyncSchedule()
    subject = registrations[0]
    if after_not_linked:
        _record(schedule, subject, "not_linked", now)
    before = _capture(schedule, registrations, now)

    _record(schedule, subject, code, now)

    _assert_state_delta(before, _capture(schedule, registrations, now), expected={
        "subject": set_to(None),
    })
    assert _capture(schedule, registrations, now + FOREVER)["subject"] is None

    if new_key == subject[2]:
        return
    rekeyed = [(subject[0], subject[1], new_key)] + registrations[1:]
    assert _capture(schedule, rekeyed, now + ONE_SECOND)["subject"] is LOOKUP


@SETTINGS
@given(
    registrations=registration_sets, now=instants,
    code=st.sampled_from(CONFIGURATION_CODES), after_not_linked=st.booleans(),
)
def test_configuration_outcomes_pause_the_whole_loop_for_an_hour(
    registrations, now, code, after_not_linked,
):
    """The pause holds back everyone, and costs the player nothing: when it
    ends, the subject is due for exactly what it was due for before."""
    schedule = KeySyncSchedule()
    subject = registrations[0]
    if after_not_linked:
        _record(schedule, subject, "not_linked", now)
    before = _capture(schedule, registrations, now)

    _record(schedule, subject, code, now)

    _assert_state_delta(before, _capture(schedule, registrations, now), expected={
        "subject": set_to(None),
        "bystanders": set_to(_all_none(registrations)),
        "paused_until": set_to(now + ONE_HOUR),
    })
    held = _capture(schedule, registrations, now + ONE_HOUR - ONE_SECOND)
    assert held["subject"] is None and held["paused_until"] == now + ONE_HOUR
    _assert_state_delta(before, _capture(schedule, registrations, now + ONE_HOUR), expected={})


def _expected_wait(retry_after: int | None, streak: int) -> timedelta:
    """The model: double from max(Retry-After, 300 s), capped at 3600 s —
    but never shorter than what upstream asked for."""
    base = max(retry_after or 0, 300)
    return timedelta(seconds=min(base * 2 ** (streak - 1), max(base, 3600)))


pause_steps = st.lists(
    st.one_of(
        st.tuples(
            st.sampled_from(TRANSIENT_CODES + UNKNOWN_OUTCOMES),
            st.one_of(st.none(), st.integers(0, 5000)),
        ),
        st.tuples(st.just("not_linked"), st.none()),
    ),
    min_size=1, max_size=8,
)


@SETTINGS
@given(registrations=registration_sets, now=instants, steps=pause_steps)
def test_upstream_pressure_pauses_for_retry_after_and_backs_off(registrations, now, steps):
    schedule = KeySyncSchedule()
    subject = registrations[0]
    at = now
    streak = 0
    for outcome, retry_after in steps:
        before = _capture(schedule, registrations, at)
        _record(schedule, subject, outcome, at, retry_after=retry_after)
        after = _capture(schedule, registrations, at)

        if outcome == "not_linked":
            streak = 0
            _assert_state_delta(before, after, expected={"subject": set_to(LINK)})
            continue
        streak += 1
        resume = at + _expected_wait(retry_after, streak)
        _assert_state_delta(before, after, expected={
            "subject": set_to(None),
            "bystanders": set_to(_all_none(registrations)),
            "paused_until": set_to(resume),
        })
        _assert_state_delta(before, _capture(schedule, registrations, resume), expected={})
        at = resume


@SETTINGS
@given(
    registrations=registration_sets, now=instants,
    history=st.lists(
        st.tuples(
            st.integers(0, 4), st.sampled_from(ALL_OUTCOMES),
            st.one_of(st.none(), st.integers(0, 5000)), gaps,
        ),
        max_size=25,
    ),
    rekeys=st.lists(st.tuples(st.integers(0, 4), keys), max_size=3),
)
def test_the_schedule_never_holds_a_plaintext_key(registrations, now, history, rekeys):
    schedule = KeySyncSchedule()
    seen_keys = {key for _, _, key in registrations}
    current = list(registrations)
    at = now
    for index, outcome, retry_after, gap in history:
        at += gap
        triple = current[index % len(current)]
        schedule.due(current, at)
        _record(schedule, triple, outcome, at, retry_after=retry_after)
    for index, key in rekeys:
        position = index % len(current)
        server_id, discord_id, _ = current[position]
        current[position] = (server_id, discord_id, key)
        seen_keys.add(key)
        schedule.due(current, at)
        _record(schedule, current[position], "no_match", at)

    surfaces = [repr(schedule), pickle.dumps(schedule).decode("latin-1")]
    surfaces.extend(_walk(schedule, set()))
    for key in seen_keys:
        for surface in surfaces:
            assert key not in surface, f"a plaintext key is held by the schedule: {surface!r}"


def _walk(value, visited: set) -> list[str]:
    """Every string reachable from `value`: attributes, slots, containers."""
    if id(value) in visited:
        return []
    visited.add(id(value))
    if isinstance(value, (str, bytes)):
        return [value if isinstance(value, str) else value.decode("latin-1")]
    found = [repr(value)]
    if isinstance(value, dict):
        for k, v in value.items():
            found += _walk(k, visited) + _walk(v, visited)
        return found
    if isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            found += _walk(item, visited)
        return found
    for name in getattr(type(value), "__slots__", ()):
        if hasattr(value, name):
            found += _walk(getattr(value, name), visited)
    if hasattr(value, "__dict__"):
        found += _walk(vars(value), visited)
    return found
