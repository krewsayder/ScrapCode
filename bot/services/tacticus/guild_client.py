"""Tacticus guild endpoint — identity + roster from a single read.

RED scaffold created by DISTILL (Mandate 7). DELIVER implements it.

This module owns the vocabulary the rest of the feature classifies against:
`ProbeOutcome`, `KeyStatus` and `GuildIdentity` are defined HERE and imported
everywhere else, including by the acceptance suite's `domain_types.py`
(Mandate-12: one definition, reused, never re-declared per layer).

DDD-2: the identity probe is folded into the roster fetch. One call, one
response — a probe and a roster from two calls could disagree, and the
per-hour Tacticus call volume does not rise.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__SCAFFOLD__ = True

TACTICUS_GUILD_URL = "https://api.tacticusgame.com/api/v1/guild"

# HTTP statuses Tacticus returns for a revoked or invalid key. Verified
# against Tacticus 2026-07-25. Lifted here from
# bot/cogs/registration_cog.py::_DEAD_KEY_STATUSES so the two paths share one
# taxonomy rather than drifting (Reuse Analysis: EXTEND, reuse verbatim).
DEAD_KEY_STATUSES = (401, 403)


class ProbeOutcome(Enum):
    """The five classifications one verification can produce (ADR-008 D6).

    Collapsing any two of these is a documented failure mode:
      MISMATCH vs UNREACHABLE  → a Tacticus outage quarantines the cluster
      MISMATCH vs UNVERIFIABLE → a vendor change quarantines the cluster
      MISMATCH vs DEAD         → a revoked key needs recovery it does not need
    """

    MATCH = "match"
    MISMATCH = "mismatch"
    UNVERIFIABLE = "unverifiable"
    UNREACHABLE = "unreachable"
    DEAD = "dead"


class KeyStatus(Enum):
    """Persisted lifecycle state of a guild's key (ADR-008 D5)."""

    ACTIVE = "active"
    QUARANTINED = "quarantined"


@dataclass(frozen=True)
class GuildIdentity:
    """The guild a key resolves to.

    `uuid` is the ONLY field compared (ADR-008 D1). `tag` and `name` are
    display-only and may be absent — a guild that retags or renames must not
    trip the lock, so they are deliberately not part of any equality check
    used for the binding decision.
    """

    uuid: str
    tag: str | None = None
    name: str | None = None

    def matches(self, other: "GuildIdentity") -> bool:
        return self.uuid == other.uuid

    @property
    def short(self) -> str:
        """First 8 characters — the form shown to operators (AC-005.4)."""
        return self.uuid[:8]


@dataclass(frozen=True)
class GuildSnapshot:
    """One read of `/api/v1/guild`: who the key belongs to, and who is in it.

    `identity` is None exactly when `outcome` is UNVERIFIABLE, UNREACHABLE or
    DEAD. `raw` is retained so the contract test can diff the live response
    against the recorded fixture.
    """

    outcome: ProbeOutcome
    identity: GuildIdentity | None = None
    members: frozenset[str] = frozenset()
    status: int | None = None
    error: str | None = None
    raw: dict | None = None


def parse_guild_snapshot(payload: dict) -> GuildSnapshot:
    """Classify a 200-OK `/api/v1/guild` body into a snapshot.

    Returns UNVERIFIABLE when `guildId` is absent. There is NO fallback to
    comparing `guildTag` — a quiet downgrade to a weaker check is the same
    failure shape as the incident this feature exists to prevent (DDD-10).
    """
    raise AssertionError("Not yet implemented — RED scaffold")


async def fetch_guild_snapshot(api_key: str) -> GuildSnapshot:
    """Read `/api/v1/guild` with `api_key` and classify the result.

    Never raises for an expected failure — transport errors, 5xx and dead-key
    statuses all come back as a classified snapshot, because the caller's job
    is to distinguish them and an exception erases the distinction.
    """
    raise AssertionError("Not yet implemented — RED scaffold")
