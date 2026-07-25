import asyncio

import httpx
import discord
from discord.ext import commands, tasks

from config import UPDATE_CHANNEL_ID
from bot.guilds import (
    load_guilds,
    get_player_list,
    load_player_registrations,
    load_capped_state,
    save_capped_state,
    load_live_leaderboards,
    save_live_leaderboards,
    repo,
)
from bot.tracker import process_api_response
from bot.guilds import load_player_list
from bot.embeds import encounter_limit
from bot.services.chronicl3r.player_service import PlayerService

TACTICUS_PLAYER_URL   = "https://api.tacticusgame.com/api/v1/player"
TACTICUS_RAID_URL     = "https://api.tacticusgame.com/api/v1/guildRaid/{season}"
TACTICUS_CURRENT_RAID = "https://api.tacticusgame.com/api/v1/guildRaid"


class TasksCog(commands.Cog):
    def __init__(self, bot: commands.Bot, player_service: PlayerService):
        self.bot            = bot
        self.player_service = player_service
        self.cap_detect.start()
        self.auto_update.start()

    def cog_unload(self):
        self.cap_detect.cancel()
        self.auto_update.cancel()

    # ==========================================
    # TASK: CAP DETECT (runs every hour)
    # ==========================================

    @tasks.loop(hours=1)
    async def cap_detect(self):
        server_ids = repo.list_server_ids()
        print(f"[cap_detect] Loop fired, checking {len(server_ids)} server(s)...")

        for server_id in server_ids:
            registrations = load_player_registrations(server_id)
            if not registrations:
                continue

            guilds        = load_guilds(server_id)
            capped_state  = load_capped_state(server_id)
            state_changed = False

            # Resolve channels upfront
            channel_cache: dict[int, discord.TextChannel | None] = {}
            for guild_data in guilds.values():
                channel_id = guild_data.get("notification_channel_id")
                if not channel_id or channel_id in channel_cache:
                    continue
                channel = self.bot.get_channel(channel_id)
                if channel is None:
                    try:
                        channel = await self.bot.fetch_channel(channel_id)
                    except Exception as e:
                        print(f"[cap_detect] Channel {channel_id} not found — {e}")
                        channel = None
                channel_cache[channel_id] = channel

            # Build list of valid players to check
            players_to_check = []
            for discord_id, reg in registrations.items():
                api_key  = reg.get("api_key")
                guild_id = reg.get("guild_id")
                if not api_key or not guild_id:
                    continue
                guild_data = guilds.get(guild_id)
                if not guild_data:
                    continue
                channel_id = guild_data.get("notification_channel_id")
                if not channel_id or channel_cache.get(channel_id) is None:
                    continue
                players_to_check.append((discord_id, api_key, channel_id))

            # Fetch all player token data in parallel
            async def _fetch(discord_id, api_key):
                headers = {"accept": "application/json", "X-API-KEY": api_key}
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        response = await client.get(TACTICUS_PLAYER_URL, headers=headers)
                        response.raise_for_status()
                        return discord_id, response.json()
                except Exception as e:
                    print(f"[cap_detect] Failed to fetch data for {discord_id}: {e}")
                    return discord_id, None

            results = await asyncio.gather(*[
                _fetch(discord_id, api_key)
                for discord_id, api_key, _ in players_to_check
            ])

            # Map channel_id back to each result
            channel_by_player = {did: cid for did, _, cid in players_to_check}

            # Process results and send notifications
            for (discord_id, player_data), (_, _, channel_id) in zip(results, players_to_check):
                if player_data is None:
                    continue

                player     = player_data.get("player") or {}
                progress   = player.get("progress") or {}
                guild_raid = progress.get("guildRaid") or {}
                tokens     = guild_raid.get("tokens") or {}
                current    = tokens.get("current", 0)
                maximum    = tokens.get("max", 3)
                is_capped  = current >= maximum
                print(f"[cap_detect] {discord_id}: {current}/{maximum} capped={is_capped}")

                was_capped = capped_state.get(discord_id, False)
                channel    = channel_cache[channel_id]

                if is_capped and not was_capped:
                    try:
                        await channel.send(
                            f"⚔️ <@{discord_id}> your raid tokens are full ({current}/{maximum})! "
                            f"Time to raid!"
                        )
                        print(f"[cap_detect] Pinged {discord_id}")
                    except discord.Forbidden:
                        print(f"[cap_detect] Missing permission to send in channel {channel_id}")
                        continue
                    capped_state[discord_id] = True
                    state_changed = True

                elif not is_capped and was_capped:
                    print(f"[cap_detect] {discord_id} spent tokens, resetting state")
                    capped_state[discord_id] = False
                    state_changed = True

            if state_changed:
                save_capped_state(server_id, capped_state)

    @cap_detect.before_loop
    async def before_cap_detect(self):
        await self.bot.wait_until_ready()

    # ==========================================
    # TASK: AUTO UPDATE (runs every hour)
    # ==========================================

    @tasks.loop(hours=1)
    async def auto_update(self):
        print("[auto_update] Loop fired...")

        channel = self.bot.get_channel(UPDATE_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(UPDATE_CHANNEL_ID)
            except Exception as e:
                print(f"[auto_update] Channel {UPDATE_CHANNEL_ID} not found — {e}")
                return

        server_ids = repo.list_server_ids()

        for server_id in server_ids:
            guilds = load_guilds(server_id)
            if not guilds:
                continue

            season    = None
            first_gd  = next(iter(guilds.values()))
            first_key = first_gd.get("api_key")
            if first_key:
                try:
                    async with httpx.AsyncClient(timeout=20.0) as client:
                        resp = await client.get(
                            TACTICUS_CURRENT_RAID,
                            headers={"accept": "application/json", "X-API-KEY": first_key}
                        )
                        resp.raise_for_status()
                        season = resp.json().get("season")
                except Exception as e:
                    print(f"[auto_update] Failed to determine current season for server {server_id}: {e}")

            if season is None:
                print(f"[auto_update] Could not determine season for server {server_id}, skipping.")
                continue

            print(f"[auto_update] Updating server {server_id} guilds for season {season}...")
            results = []

            async with httpx.AsyncClient(timeout=20.0) as client:
                for guild_id, guild_data in guilds.items():
                    guild_name = guild_data["name"]
                    api_key    = guild_data.get("api_key")

                    if api_key:
                        try:
                            await self.player_service.validate_if_stale(server_id, guild_id, api_key)
                        except Exception as e:
                            print(f"[auto_update] Player list validation failed for {guild_name}: {e}")

                    if not api_key:
                        results.append(f"⚠️ **{guild_name}** — skipped, no API key set.")
                        continue

                    headers  = {"accept": "application/json", "X-API-KEY": api_key}
                    url      = TACTICUS_RAID_URL.format(season=season)

                    try:
                        response = await client.get(url, headers=headers)
                        response.raise_for_status()
                        api_data = response.json()

                        process_api_response(api_data, season, server_id, guild_id)

                        await self._register_unknown_players(server_id, guild_id, api_data)
                        results.append(f"✅ **{guild_name}** — updated successfully.")
                        print(f"[auto_update] {guild_name} updated.")

                    except httpx.HTTPStatusError as e:
                        results.append(f"❌ **{guild_name}** — HTTP {e.response.status_code}")
                        print(f"[auto_update] {guild_name} failed: HTTP {e.response.status_code}")
                    except Exception as e:
                        results.append(f"❌ **{guild_name}** — {str(e)[:80]}")
                        print(f"[auto_update] {guild_name} failed: {e}")

            try:
                await channel.send(
                    f"🔄 **Auto-update complete — Season {season}**\n" + "\n".join(results)
                )
            except discord.Forbidden:
                print(f"[auto_update] Missing permission to send in channel {UPDATE_CHANNEL_ID}")

            await self._refresh_live_leaderboards(server_id, season, guilds)

    async def _register_unknown_players(self, server_id: int, guild_id: str, api_data: dict) -> None:
        known   = set(load_player_list(server_id, guild_id).get("players", {}).keys())
        seen    = {e["userId"] for e in api_data.get("entries", []) if "userId" in e}
        unknown = seen - known
        for user_id in unknown:
            try:
                saved = await self.player_service.ensure_player_in_list(server_id, guild_id, user_id)
                if saved:
                    print(f"[auto_update] Saved unknown player {user_id} to player list")
            except Exception as e:
                print(f"[auto_update] Failed to save unknown player {user_id}: {e}")

    async def _refresh_live_leaderboards(self, server_id: int, season: int, guilds: dict):
        """Refresh live leaderboards.

        Same season -> edit the existing messages in place.
        New season  -> leave the old messages untouched (frozen archive of the
                       previous season), send a fresh set, and repoint the
                       live config at the new message IDs.
        """
        from config import TIER_CHOICES
        from bot.embeds import build_battle_messages, build_cluster_messages

        live = load_live_leaderboards(server_id)
        if not live:
            return

        to_remove = []
        dirty     = False  # config changed (rollover, season adoption, removals)

        for key, config in live.items():
            channel_id  = config.get("channel_id")
            message_ids = config.get("messages", {})
            channel     = self.bot.get_channel(channel_id)

            if channel is None:
                print(f"[live_leaderboard] Channel {channel_id} not found, removing {key}")
                to_remove.append(key)
                continue

            # ------------------------------------------------------------
            # Build per-tier content for the CURRENT season
            # ------------------------------------------------------------
            if key.startswith("guild:"):
                guild_id   = config.get("guild_id")
                guild_data = guilds.get(guild_id)
                if not guild_data:
                    to_remove.append(key)
                    continue

                guild_name = guild_data["name"]
                data = repo.load_battle_hits(server_id, guild_id, season)

                contents = {}
                for tier in TIER_CHOICES:
                    if not data or not data.get("boss_hits"):
                        contents[tier.value] = f"📊 **{guild_name} — {tier.name} — No data yet**"
                    else:
                        messages = build_battle_messages(
                            data, season, tier, server_id, guild_id, guild_name
                        )
                        contents[tier.value] = (
                            "\n\n".join(messages)
                            if messages
                            else f"📊 **{guild_name} — {tier.name} — No data yet**"
                        )

            elif key == "cluster":
                merged = {}
                for gid, gdata in guilds.items():
                    data = repo.load_battle_hits(server_id, gid, season)
                    if not data or not data.get("boss_hits"):
                        continue
                    id_to_name = get_player_list(server_id, gid)
                    guild_name = gdata["name"]
                    for boss_id, encounter_dict in data.get("boss_hits", {}).items():
                        for e_index, tiers in encounter_dict.items():
                            for tier_key, entries in tiers.items():
                                bucket = (
                                    merged.setdefault(boss_id, {})
                                    .setdefault(e_index, {})
                                    .setdefault(tier_key, [])
                                )
                                for entry in entries:
                                    user_id      = entry.get("user_id", "Unknown")
                                    user_display = id_to_name.get(user_id, str(user_id)[:8])
                                    bucket.append(
                                        {**entry, "_display": user_display, "_guild": guild_name}
                                    )

                for boss_id, encounter_dict in merged.items():
                    for e_index, tiers in encounter_dict.items():
                        for tier_key in tiers:
                            limit = encounter_limit(e_index)
                            tiers[tier_key] = sorted(
                                tiers[tier_key], key=lambda e: (-e["damage"], e.get("completed_on", ""))
                            )[:limit]

                contents = {}
                for tier in TIER_CHOICES:
                    tier_merged = {
                        boss_id: {
                            e_index: tiers[tier.value]
                            for e_index, tiers in encounter_dict.items()
                            if tier.value in tiers
                        }
                        for boss_id, encounter_dict in merged.items()
                    }
                    messages = build_cluster_messages(tier_merged, season, tier)
                    contents[tier.value] = (
                        "\n\n".join(messages)
                        if messages
                        else f"🌐 **Cluster — {tier.name} — No data yet**"
                    )

            else:
                continue  # unknown key, skip

            # ------------------------------------------------------------
            # Same season -> edit in place. New season -> send fresh set.
            # ------------------------------------------------------------
            stored_season = config.get("season")

            if stored_season is None:
                # Legacy config from before season tracking existed.
                # Adopt the current season without spawning new messages.
                config["season"] = season
                stored_season    = season
                dirty            = True

            if stored_season == season:
                for tier in TIER_CHOICES:
                    msg_id = message_ids.get(tier.value)
                    if not msg_id:
                        continue
                    try:
                        msg = await channel.fetch_message(msg_id)
                        await msg.edit(content=contents[tier.value])
                    except discord.NotFound:
                        to_remove.append(key)
                        break
                    except discord.Forbidden:
                        print(f"[live_leaderboard] No permission to edit message in channel {channel_id} ({key})")
                        break
                    except Exception as e:
                        print(f"[live_leaderboard] Error editing message {msg_id} ({key}): {e}")

            else:
                # Season rollover: old messages stay as a frozen archive.
                print(f"[live_leaderboard] Season rollover for {key}: {stored_season} -> {season}, sending new messages")
                new_message_ids = {}
                failed = False
                for tier in TIER_CHOICES:
                    try:
                        msg = await channel.send(contents[tier.value])
                        new_message_ids[tier.value] = msg.id
                    except discord.Forbidden:
                        print(f"[live_leaderboard] No permission to send rollover messages in channel {channel_id} ({key})")
                        failed = True
                        break
                    except Exception as e:
                        print(f"[live_leaderboard] Error sending rollover message ({key}): {e}")
                        failed = True
                        break

                if failed and not new_message_ids:
                    # Nothing sent — keep the old config and retry next hour.
                    continue

                config["messages"] = new_message_ids
                config["season"]   = season
                dirty              = True

        if to_remove:
            for key in to_remove:
                live.pop(key, None)
            dirty = True
            print(f"[live_leaderboard] Removed broken configs: {to_remove}")

        if dirty:
            save_live_leaderboards(server_id, live)

    @auto_update.before_loop
    async def before_auto_update(self):
        await self.bot.wait_until_ready()


async def setup_tasks(bot: commands.Bot, player_service: PlayerService):
    await bot.add_cog(TasksCog(bot, player_service))