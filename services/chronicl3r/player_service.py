import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import requests

from guilds import load_player_list, save_player_list
from services.chronicl3r.client import chronicl3rClient

TACTICUS_GUILD_URL = "https://api.tacticusgame.com/api/v1/guild"
STALE_AFTER_HOURS  = 1


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

    async def refresh_guild(self, guild_id: str, api_key: str) -> None:
        """Sync the player list for a guild against the current Tacticus roster.
        - Registers any new members with chronicl3r.
        - Updates display_name and last_validated for current members.
        - Marks anyone absent from the current roster as is_former=True."""
        current_ids = await self._fetch_roster(api_key)
        now         = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        data    = load_player_list(guild_id)
        players = data["players"]

        for user_id in current_ids:
            try:
                profile = await self.get_or_register(user_id)
                players[user_id] = {
                    "display_name":  profile["tacticus_display_nm"],
                    "last_validated": now,
                    "is_former":     False,
                }
            except Exception as e:
                print(f"[PlayerService] Failed to register/fetch {user_id}: {e}")

        for user_id in list(players.keys()):
            if user_id not in current_ids:
                players[user_id]["is_former"] = True

        save_player_list(guild_id, data)
        print(f"[PlayerService] Refreshed {guild_id}: {len(current_ids)} current, {sum(1 for p in players.values() if p.get('is_former')) } former")

    async def validate_if_stale(self, guild_id: str, api_key: str) -> None:
        """Call refresh_guild if any player entry is older than STALE_AFTER_HOURS."""
        players   = load_player_list(guild_id).get("players", {})
        threshold = datetime.now(timezone.utc) - timedelta(hours=STALE_AFTER_HOURS)

        if not players:
            await self.refresh_guild(guild_id, api_key)
            return

        for entry in players.values():
            lv = entry.get("last_validated")
            if not lv:
                await self.refresh_guild(guild_id, api_key)
                return
            if datetime.fromisoformat(lv.rstrip("Z")).replace(tzinfo=timezone.utc) < threshold:
                await self.refresh_guild(guild_id, api_key)
                return

    async def ensure_player_in_list(self, guild_id: str, user_id: str) -> bool:
        """Register a player with chronicl3r and save them to the local player list
        if they aren't already there. Marks them as is_former=True since they're
        not on the current roster. Returns True if a new entry was saved."""
        data    = load_player_list(guild_id)
        players = data["players"]

        if user_id in players:
            return False

        profile = await self.get_or_register(user_id)
        players[user_id] = {
            "display_name":  profile["tacticus_display_nm"],
            "last_validated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "is_former":     True,
        }
        save_player_list(guild_id, data)
        return True

    def get_display_name(self, tacticus_user_id: str, guild_id: str) -> str:
        """Return the display name for a player, with ' (former)' appended if applicable.
        Falls back to a truncated ID if the player isn't in the list."""
        players = load_player_list(guild_id).get("players", {})
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

    async def _fetch_roster(self, api_key: str) -> set[str]:
        """Fetch current guild member user IDs from the Tacticus API."""
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                TACTICUS_GUILD_URL,
                headers={"accept": "application/json", "X-API-KEY": api_key},
            )
            resp.raise_for_status()
            members = resp.json()["guild"]["members"]
        return {m["userId"] for m in members}
