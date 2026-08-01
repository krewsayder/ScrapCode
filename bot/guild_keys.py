"""Guild key policy — the single sanctioned reader of a guild's `api_key`.

RED scaffold created by DISTILL (Mandate 7). DELIVER implements it.

ADR-008 D3 / DDD-3. Seven call sites across three cogs plus a service read a
guild key today. Six-of-seven is not "mostly fixed" — it is a silent
contamination path that looks fixed. Every one of those sites routes through
this module, and
`tests/acceptance/guild-key-integrity/test_architecture_chokepoint.py` fails
the build when a new one does not.

Two entry points, deliberately different in kind:

  `verify_and_resolve` — async. Probes Tacticus, compares the resolved
      identity against the stored binding, quarantines on drift, and returns a
      snapshot the caller can ingest from. This is what an ingestion path
      calls.

  `active_key` — sync, storage-only, no network. Returns the key ONLY if the
      guild is not quarantined. This is what season discovery calls, and the
      reason it exists separately is DDD-7: season discovery must be able to
      fall through to the next guild cheaply, without a probe per candidate.

This module MUST NOT be imported by `bot/repository*.py`: policy depends on
storage, never the reverse.
"""
from __future__ import annotations

from bot.services.tacticus.guild_client import (
    GuildIdentity,
    GuildSnapshot,
    KeyStatus,
    ProbeOutcome,
)

__SCAFFOLD__ = True

# One alert per guild per 24 hours while a quarantine persists (ADR-008 D5).
# An hourly loop that alerts hourly gets its channel muted, and a muted
# channel defeats KPI-1 entirely.
ALERT_SUPPRESSION_HOURS = 24


class GuildQuarantined(Exception):
    """Raised when a caller asks for the key of a quarantined guild.

    Carries both identities so the caller can render an actionable message
    without re-reading the binding.
    """

    def __init__(self, guild_id: str, bound: GuildIdentity | None,
                 observed: GuildIdentity | None) -> None:
        super().__init__(f"{guild_id} is quarantined")
        self.guild_id = guild_id
        self.bound = bound
        self.observed = observed


async def verify_and_resolve(
    discord_server_id: int,
    guild_id: str,
    *,
    enforce: bool = True,
) -> GuildSnapshot:
    """Probe the guild's key, compare identity, and enforce the result.

    `enforce=False` is the Slice 01 behaviour: classify and report, write
    nothing, block nothing. Slice 03 flips the default. Keeping it a
    parameter rather than two functions is what lets the same acceptance
    scenarios run against both slices.

    Raises `GuildQuarantined` when the guild is already quarantined, BEFORE
    any request is made — fetching the data and then discarding it would put
    another guild's roster in memory and possibly in a traceback.
    """
    raise AssertionError("Not yet implemented — RED scaffold")


def active_key(discord_server_id: int, guild_id: str) -> str | None:
    """Return the guild's key, or None when it is quarantined or absent.

    Sync and storage-only by design (DDD-7): season discovery iterates
    candidate guilds and must be able to skip a quarantined one without
    paying for a probe.
    """
    raise AssertionError("Not yet implemented — RED scaffold")


def quarantine(
    discord_server_id: int,
    guild_id: str,
    *,
    bound: GuildIdentity,
    observed: GuildIdentity,
) -> None:
    """Mark a guild quarantined, recording both identities and the moment.

    `quarantined_at` MUST be written ISO-8601 UTC in the SAME shape as
    `battle_hits.completed_on` (String(32), stored verbatim from the Tacticus
    payload). KPI-2 compares them as strings; a shape mismatch returns a
    wrong result set silently rather than erroring.
    """
    raise AssertionError("Not yet implemented — RED scaffold")


def release(discord_server_id: int, guild_id: str) -> None:
    """Clear quarantine after a matching key is installed.

    Quarantine must never be a trap (DISCUSS D3): this ships in Slice 02, one
    slice before anything can enter quarantine, so the exit provably exists
    before the entrance opens.
    """
    raise AssertionError("Not yet implemented — RED scaffold")


def key_ref(api_key_hmac: str) -> str:
    """Correlation ID for log records: first 8 hex of `api_key_hmac`.

    Lets an operator follow one key across bind → mismatch → quarantine →
    update. Not key material: `api_key_hmac` is an HKDF-SHA256 derivation
    keyed by SCRAPCODE_DB_KEY (ADR-006 D7) and is not reversible without that
    key. KPI-6 stays at zero by construction.
    """
    raise AssertionError("Not yet implemented — RED scaffold")
