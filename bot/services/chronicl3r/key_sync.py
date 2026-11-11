"""When to sync each registered player's Tacticus key with Chronicl3r.

PURE decision logic: no I/O, no clock reads (every call is told `now`), no
logging. The caller hands in its current registrations as plain
`(server_id, discord_id, key)` triples and reports back what each call
returned; the schedule answers which registrations need a lookup or a link,
and whether the whole loop is paused.

The schedule never holds a key. A player's state is keyed by
`(server_id, discord_id)` and remembers only the SHA-256 fingerprint of the
key it was recorded against; a key that no longer matches its fingerprint
starts that player afresh. Keys leave `due()` only inside the triples the
caller passed in.

Outcome vocabulary for `record()` — the contract code strings, plus:

    linked          lookup said the key is held, or the link succeeded -> done
    not_linked      lookup said the key is not held               -> link now
    no_match        retried as a link in 15 min; parked after 4 tries
    no_name_match   retried as a link in 24 h, no cap
    insufficient_data / ambiguous / invalid_key / missing_key
                    parked until the key changes
    no_profiles / not_deployed
                    the whole loop pauses for 1 h
    anything else   (upstream_rate_limited, upstream_unavailable, throttled,
                    transport, a code-less error, None)
                    the whole loop pauses for Retry-After (300 s when absent
                    or shorter); consecutive transient pauses double the
                    wait, capped at 3600 s (never below Retry-After itself),
                    and any player outcome resets the doubling

Pausing outcomes leave the player's own state untouched: when the pause
ends the player is due for exactly what it was due for before.

State is in memory by design; a restart starts every player afresh.
"""
from __future__ import annotations

import dataclasses
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Iterable

Registration = tuple[int, int, str]
PlayerId = tuple[int, int]


class KeySyncAction(Enum):
    LOOKUP = "lookup"
    LINK = "link"


NO_MATCH_RETRY = timedelta(minutes=15)
NO_MATCH_MAX_TRIES = 4
NO_NAME_MATCH_RETRY = timedelta(hours=24)
CONFIGURATION_PAUSE = timedelta(hours=1)
TRANSIENT_PAUSE_SECONDS = 300
TRANSIENT_PAUSE_CAP_SECONDS = 3600
# Bounds the exponent only; the cap above is what limits the wait.
_MAX_DOUBLINGS = 16

PARKING_OUTCOMES = frozenset({"insufficient_data", "ambiguous", "invalid_key", "missing_key"})
CONFIGURATION_OUTCOMES = frozenset({"no_profiles", "not_deployed"})
PLAYER_OUTCOMES = frozenset({"linked", "not_linked", "no_match", "no_name_match"}) | PARKING_OUTCOMES


def _fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _PlayerState:
    """`action` None means nothing is due until the key changes (linked or parked)."""

    fingerprint: str
    action: KeySyncAction | None = KeySyncAction.LOOKUP
    not_before: datetime | None = None
    no_match_tries: int = 0

    def is_due(self, now: datetime) -> bool:
        if self.action is None:
            return False
        return self.not_before is None or now >= self.not_before

    def after(self, outcome: str, now: datetime) -> _PlayerState:
        if outcome == "not_linked":
            return dataclasses.replace(self, action=KeySyncAction.LINK, not_before=None)
        if outcome == "no_match":
            return self._after_no_match(now)
        if outcome == "no_name_match":
            return dataclasses.replace(
                self, action=KeySyncAction.LINK, not_before=now + NO_NAME_MATCH_RETRY,
            )
        return dataclasses.replace(self, action=None, not_before=None)

    def _after_no_match(self, now: datetime) -> _PlayerState:
        tries = self.no_match_tries + 1
        if tries >= NO_MATCH_MAX_TRIES:
            return dataclasses.replace(self, action=None, not_before=None, no_match_tries=tries)
        return dataclasses.replace(
            self, action=KeySyncAction.LINK, not_before=now + NO_MATCH_RETRY, no_match_tries=tries,
        )


@dataclass
class _LoopPause:
    until: datetime | None = None
    transient_streak: int = 0

    def is_active(self, now: datetime) -> bool:
        return self.until is not None and now < self.until

    def hold(self, until: datetime, now: datetime) -> None:
        if self.is_active(now) and self.until >= until:
            return
        self.until = until

    def back_off(self, now: datetime, retry_after: int | None) -> None:
        self.transient_streak += 1
        base = max(retry_after or 0, TRANSIENT_PAUSE_SECONDS)
        doubled = base * 2 ** min(self.transient_streak - 1, _MAX_DOUBLINGS)
        seconds = min(doubled, max(base, TRANSIENT_PAUSE_CAP_SECONDS))
        self.hold(now + timedelta(seconds=seconds), now)

    def reset_back_off(self) -> None:
        self.transient_streak = 0


class KeySyncSchedule:
    def __init__(self) -> None:
        self._players: dict[PlayerId, _PlayerState] = {}
        self._pause = _LoopPause()

    def due(
        self, registrations: Iterable[Registration], now: datetime,
    ) -> list[tuple[Registration, KeySyncAction]]:
        """The registrations needing a call now, each with the call it needs.

        State for a player absent from `registrations` is forgotten, so a
        player who re-registers starts afresh. Nothing is due while paused.
        """
        current = list(registrations)
        listed = {(server_id, discord_id) for server_id, discord_id, _ in current}
        self._players = {player: state for player, state in self._players.items() if player in listed}
        if self._pause.is_active(now):
            return []
        due = []
        for registration in current:
            state = self._state_for(registration)
            if state.is_due(now):
                due.append((registration, state.action))
        return due

    def record(
        self,
        server_id: int,
        discord_id: int,
        key: str,
        outcome: str | None,
        now: datetime,
        retry_after: int | None = None,
    ) -> None:
        """Record what a lookup or link call for this registration returned."""
        if outcome in CONFIGURATION_OUTCOMES:
            self._pause.hold(now + CONFIGURATION_PAUSE, now)
            return
        if outcome not in PLAYER_OUTCOMES:
            self._pause.back_off(now, retry_after)
            return
        self._pause.reset_back_off()
        registration = (server_id, discord_id, key)
        self._players[(server_id, discord_id)] = self._state_for(registration).after(outcome, now)

    def paused_until(self, now: datetime) -> datetime | None:
        if self._pause.is_active(now):
            return self._pause.until
        return None

    def _state_for(self, registration: Registration) -> _PlayerState:
        server_id, discord_id, key = registration
        fingerprint = _fingerprint(key)
        state = self._players.get((server_id, discord_id))
        if state is None or state.fingerprint != fingerprint:
            return _PlayerState(fingerprint=fingerprint)
        return state
