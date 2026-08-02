"""Tacticus guild endpoint — identity + roster from a single read.

The only module in the codebase that issues `GET /api/v1/guild`.

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

TACTICUS_GUILD_URL = "https://api.tacticusgame.com/api/v1/guild"

# Reused verbatim from `PlayerService._fetch_roster`, the call this module
# replaces. The endpoint, the header shape and this timeout are the same read
# that produced every roster written so far; keeping them identical is what
# makes the member set in a snapshot the set the old reader produced.
_REQUEST_TIMEOUT_SECONDS = 20.0

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

    A resolved identity is reported as MATCH: this function was given no
    binding to compare against, so it reports "a guild resolved". Deciding
    MATCH vs MISMATCH is the policy layer's call, and it is the only caller
    that holds the expected identity.
    """
    guild = payload.get("guild") or {}
    uuid = guild.get("guildId")

    if not uuid:
        # The members are deliberately dropped as well: a roster whose owner
        # cannot be established is one nobody may write, and handing it back
        # anyway is the invitation to write it.
        return GuildSnapshot(
            outcome=ProbeOutcome.UNVERIFIABLE,
            status=200,
            error="the response carries no guildId",
            raw=payload,
        )

    return GuildSnapshot(
        outcome=ProbeOutcome.MATCH,
        identity=GuildIdentity(
            uuid=uuid,
            tag=guild.get("guildTag"),
            name=guild.get("name"),
        ),
        members=frozenset(m["userId"] for m in guild.get("members") or []),
        status=200,
        raw=payload,
    )


async def fetch_guild_snapshot(api_key: str) -> GuildSnapshot:
    """Read `/api/v1/guild` with `api_key` and classify the result.

    Never raises for an expected failure — transport errors, 5xx and dead-key
    statuses all come back as a classified snapshot, because the caller's job
    is to distinguish them and an exception erases the distinction.
    """
    # Imported here, not at module scope, so importing this module costs
    # `dataclasses` and `enum` only. `domain_types.py` and the policy layer
    # import it for the vocabulary alone and must not pay for httpx.
    import httpx

    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.get(
                TACTICUS_GUILD_URL,
                headers={"accept": "application/json", "X-API-KEY": api_key},
            )
    except httpx.HTTPError as transport_error:
        # Timeout, DNS failure, connection refused, protocol error. UNREACHABLE
        # means "no state change, retry next cycle" — collapsing it into
        # MISMATCH would quarantine every guild during a Tacticus outage
        # (DDD-6).
        return GuildSnapshot(
            outcome=ProbeOutcome.UNREACHABLE,
            error=f"{type(transport_error).__name__}: {transport_error}",
        )

    if response.status_code in DEAD_KEY_STATUSES:
        # A revoked key returns no data, so there is nothing to contaminate:
        # report it, never quarantine on it.
        return GuildSnapshot(
            outcome=ProbeOutcome.DEAD,
            status=response.status_code,
            error=f"the key was refused with HTTP {response.status_code}",
        )

    if response.status_code != 200:
        # 5xx and everything else unexpected. Deliberately UNREACHABLE rather
        # than a narrower 5xx test: an unrecognised status is a reason to
        # retry, never a reason to act on a guild's binding.
        return GuildSnapshot(
            outcome=ProbeOutcome.UNREACHABLE,
            status=response.status_code,
            error=f"the guild service answered HTTP {response.status_code}",
        )

    return parse_guild_snapshot(response.json())
