"""Chronicler-side player bookkeeping.

This module used to fetch the Tacticus guild roster for itself, in
`_fetch_roster`, with no check that the key it used still belonged to the
guild it was writing. That function is DELETED (ADR-008 / DDD-2, step 03-02):
`refresh_guild` and `validate_if_stale` are now HANDED a snapshot by a caller
that has already been through the key-policy chokepoint (`bot/guild_keys.py`),
and this package makes no outbound HTTP call at all.

Why the snapshot type is not imported
-------------------------------------
`bot.services.tacticus.guild_client.GuildSnapshot` is what callers actually
pass, and it satisfies `RosterSnapshot` below structurally. Importing it here
would nonetheless re-break the rule this step exists to establish:
`guild_client` imports `httpx` (inside `fetch_guild_snapshot`), the
`pytest-archon` rule `chronicler makes no outbound calls` is TRANSITIVE, and
its module walk sees imports nested in functions and in `TYPE_CHECKING`
blocks alike. So there is no import that would be invisible to it — not a
deferred one, not a typing-only one.

Declaring the two attributes this service reads is the better answer anyway:
the consumer states its own requirement, and the dependency now points inward.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Protocol

import requests

from bot.guilds import load_player_list, save_player_list
from bot.obs import emit_structured
from bot.services.chronicl3r.client import chronicl3rClient

logger = logging.getLogger(__name__)

STALE_AFTER_HOURS = 1

# Emitted when a roster write is refused. Deliberately NOT in the `guild.key.*`
# family: that family's contract (docs/product/kpi-contracts.yaml) requires
# `key_ref` on every record, and this module no longer sees a key to derive one
# from — which is the entire point of the change that introduced this record.
ROSTER_REFUSED_EVENT = "player_list.refresh.refused"


class RosterSnapshot(Protocol):
    """One read of a guild, as this service needs to see it.

    Exactly two attributes, because exactly two are read. `identity` is only
    ever tested for presence here — WHICH guild it names is the key policy's
    decision, not this module's, and duplicating that comparison would create
    a second place for the two answers to disagree.
    """

    identity: object | None
    members: frozenset[str]


def _roster_write_refusal(snapshot: RosterSnapshot) -> str | None:
    """Name the reason this snapshot may not drive a roster write, or None.

    THE GUARD. `refresh_guild` writes every listed member as `is_former:
    False` and flips everyone absent to `is_former: True`, so an empty member
    set flips EVERY player to former. On 2026-07-28 that inversion corrupted
    60 of 67 `players` rows — the larger half of the incident.

    Two refusable shapes:

    `identity_absent` — nobody can say which guild this roster belongs to
        (no `guildId`, an unreachable service, a refused key). A roster whose
        owner cannot be established is one nobody may write.

    `members_empty` — an identity resolved, but no members came back. A guild
        whose key still works contains at least the key-holder, so zero
        members is not a credible successful response; it is a partial or
        malformed one that happens to carry a 200.

    "the `members` key was absent" and "`members` was present and empty" are
    deliberately NOT separated. Two reasons. `parse_guild_snapshot` renders
    both as `frozenset()`, so telling them apart would mean either changing
    the snapshot type or re-reading the raw vendor payload here — and parsing
    vendor bodies in this package is precisely the coupling step 03-02
    deletes. And the correct action is identical in both cases: refuse. A
    distinction that changes no behaviour belongs in the diagnostic record,
    not in the control flow — which is what `reason` is for.

    Deliberately NOT refused: a snapshot the policy layer classified MISMATCH.
    That one carries a full, well-formed roster for the WRONG guild, and
    blocking it is enforcement — which lands in Slice 03, one slice after
    `/update_guild_key` ships the recovery path (ADR-008 D3). Slice 01 reports
    and does not block, and quietly blocking here would make the shipped
    behaviour differ from the documented one.
    """
    if snapshot.identity is None:
        return "identity_absent"
    if not snapshot.members:
        return "members_empty"
    return None


def _utc_timestamp() -> str:
    """ISO-8601 UTC, seconds — the shape `last_validated` has always had.

    `validate_if_stale` parses this back with `fromisoformat`, so the format
    is a contract with the reader immediately below it, not a display choice.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class PlayerService:
    def __init__(self, client: chronicl3rClient):
        self.client = client

    # ==========================================
    # PUBLIC API
    # ==========================================

    async def get_or_register(self, tacticus_user_id: str) -> dict:
        """Upsert a player profile in chronicl3r.
        Tries register first; on 409 (already exists) fetches the existing profile."""
        def _sync() -> dict:
            try:
                return self.client.register_user(tacticus_user_id)
            except requests.HTTPError as e:
                if e.response.status_code == 409:
                    return self.client.get_profile(tacticus_user_id)
                raise
        return await asyncio.to_thread(_sync)

    async def refresh_guild(
        self, discord_server_id: int, guild_id: str, snapshot: RosterSnapshot
    ) -> None:
        """Sync the player list for a guild against a roster it was handed.

        Makes no Tacticus call. The caller obtains `snapshot` from
        `bot.guild_keys.verify_and_resolve`, which is the only sanctioned
        reader of a guild's key.
        """
        refusal = _roster_write_refusal(snapshot)
        if refusal:
            self._refuse_roster_write(discord_server_id, guild_id, refusal)
            return

        current_ids = snapshot.members
        data = load_player_list(discord_server_id, guild_id)
        players = data["players"]

        await self._mark_current(players, current_ids)
        _mark_departed(players, current_ids)

        save_player_list(discord_server_id, guild_id, data)
        former = sum(1 for p in players.values() if p.get("is_former"))
        logger.info(
            "[PlayerService] Refreshed %s: %d current, %d former",
            guild_id, len(current_ids), former,
        )

    async def validate_if_stale(
        self, discord_server_id: int, guild_id: str, snapshot: RosterSnapshot
    ) -> None:
        """Call refresh_guild if any player entry is older than STALE_AFTER_HOURS.

        The guard lives in `refresh_guild`, not here: this method decides only
        WHETHER a write is due. A snapshot that cannot drive a write is
        refused at the write, so no caller and no entry point can route around
        it.
        """
        players = load_player_list(discord_server_id, guild_id).get("players", {})
        threshold = datetime.now(timezone.utc) - timedelta(hours=STALE_AFTER_HOURS)

        if not players:
            await self.refresh_guild(discord_server_id, guild_id, snapshot)
            return

        for entry in players.values():
            if _is_stale(entry.get("last_validated"), threshold):
                await self.refresh_guild(discord_server_id, guild_id, snapshot)
                return

    async def ensure_player_in_list(self, discord_server_id: int, guild_id: str, user_id: str) -> bool:
        """Register a player with chronicl3r and add them to the local player list
        if they aren't already there. Marks them as is_former=True. Returns True if saved."""
        data    = load_player_list(discord_server_id, guild_id)
        players = data["players"]

        if user_id in players:
            return False

        profile = await self.get_or_register(user_id)
        players[user_id] = {
            "display_name":   profile["tacticus_display_nm"],
            "last_validated": _utc_timestamp(),
            "is_former":      True,
        }
        save_player_list(discord_server_id, guild_id, data)
        return True

    def get_display_name(self, discord_server_id: int, tacticus_user_id: str, guild_id: str) -> str:
        """Return the display name for a player, falling back to a truncated ID."""
        players = load_player_list(discord_server_id, guild_id).get("players", {})
        entry   = players.get(tacticus_user_id)
        if not entry:
            return tacticus_user_id[:8]
        name = entry.get("display_name", tacticus_user_id[:8])
        if entry.get("is_former"):
            name += " (former)"
        return name

    # ==========================================
    # INTERNAL
    # ==========================================

    async def _mark_current(self, players: dict, current_ids: frozenset[str]) -> None:
        """Write every member of the roster as present, refreshing their name.

        One member failing to register or fetch must not abandon the rest of
        the roster half-written, so the failure is logged per player and the
        loop continues.
        """
        now = _utc_timestamp()
        for user_id in current_ids:
            try:
                profile = await self._profile_for(user_id, players)
            except Exception as e:
                logger.warning(
                    "[PlayerService] Failed to register/fetch %s: %s", user_id, e
                )
                continue
            players[user_id] = {
                "display_name": profile["tacticus_display_nm"],
                "last_validated": now,
                "is_former": False,
            }

    async def _profile_for(self, user_id: str, known_players: dict) -> dict:
        """The Chronicler profile for a member of the current roster.

        Already registered — fetch directly, which picks up name changes.
        New — register first, falling back to a fetch on 409.
        """
        if user_id in known_players:
            return await asyncio.to_thread(self.client.get_profile, user_id)
        return await self.get_or_register(user_id)

    def _refuse_roster_write(
        self, discord_server_id: int, guild_id: str, reason: str
    ) -> None:
        """Decline the write, loudly.

        One structured ERROR record and no state change. ERROR rather than
        WARNING because every path that reaches here is a caller asking for a
        write it was not entitled to make, and a quiet skip is how this class
        of bug survived for three days.

        `known_players` is the blast radius the refusal prevented — the
        postmortem's "60 of 67". Which probe outcome produced the refusal is
        deliberately not repeated here: `bot/guild_keys.py` emits
        `guild.key.dead` / `guild.key.unreachable` / `guild.key.unverifiable`
        for the same probe on the same tick, correlated by `guild_id`, and
        restating it would couple this module to a vocabulary it no longer
        needs to know.
        """
        players = load_player_list(discord_server_id, guild_id).get("players", {})
        emit_structured(
            logger, logging.ERROR, ROSTER_REFUSED_EVENT,
            ts=_utc_timestamp(),
            server_id=discord_server_id,
            guild_id=guild_id,
            reason=reason,
            known_players=len(players),
        )


def _mark_departed(players: dict, current_ids: frozenset[str]) -> None:
    """Flip everyone absent from the roster to former.

    Rewritten wholesale on every refresh, so a returning player is un-flagged
    by the next cycle — and so a roster that should never have been trusted
    corrupts every row at once. That asymmetry is why `_roster_write_refusal`
    runs before this function is ever reached.
    """
    for user_id in list(players.keys()):
        if user_id not in current_ids:
            players[user_id]["is_former"] = True


def _is_stale(last_validated: str | None, threshold: datetime) -> bool:
    """True when an entry has never been validated or was validated too long ago.

    A missing `last_validated` counts as stale: an entry nobody can date is
    one nobody can vouch for.
    """
    if not last_validated:
        return True
    validated_at = datetime.fromisoformat(last_validated.rstrip("Z")).replace(
        tzinfo=timezone.utc
    )
    return validated_at < threshold
