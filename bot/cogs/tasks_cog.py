import httpx
import discord
from discord.ext import commands, tasks

from config import UPDATE_CHANNEL_ID
from bot.guilds import (
    load_guilds,
    get_guild_data_path,
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
from bot.services.chronicl3r.player_service import PlayerService

TACTICUS_PLAYER_URL   = "https://api.tacticusgame.com/api/v1/player"
TACTICUS_RAID_URL     = "https://api.tacticusgame.com/api/v1/guildRaid/{season}"
TACTICUS_CURRENT_RAID = "https://api.tacticusgame.com/api/v1/guildRaid"


class TasksCog(commands.Cog):
    def __init__(self, bot: commands.Bot, file_lock, player_service: PlayerService):
        self.bot            = bot
        self.file_lock      = file_lock
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

        async with httpx.AsyncClient(timeout=10.0) as client:
            for server_id in server_ids:
                registrations = load_player_registrations(server_id)
                if not registrations:
                    continue

                guilds        = load_guilds(server_id)
                capped_state  = load_capped_state(server_id)
                state_changed = False

                # Cache fetched channels to avoid redundant lookups
                channel_cache: dict[int, discord.TextChannel | None] = {}

                for discord_id, reg in registrations.items():
                    api_key  = reg.get("api_key")
                    guild_id = reg.get("guild_id")
                    if not api_key or not guild_id:
                        continue

                    guild_data = guilds.get(guild_id)
                    if not guild_data:
                        continue

                    channel_id = guild_data.get("notification_channel_id")
                    if not channel_id:
                        continue

                    if channel_id not in channel_cache:
                        channel = self.bot.get_channel(channel_id)
                        if channel is None:
                            try:
                                channel = await self.bot.fetch_channel(channel_id)
                            except Exception as e:
                                print(f"[cap_detect] Channel {channel_id} not found for {guild_id} — {e}")
                                channel = None
                        channel_cache[channel_id] = channel

                    channel = channel_cache[channel_id]
                    if channel is None:
                        continue

                    headers = {"accept": "application/json", "X-API-KEY": api_key}
                    try:
                        response = await client.get(TACTICUS_PLAYER_URL, headers=headers)
                        response.raise_for_status()
                        player_data = response.json()
                    except Exception as e:
                        print(f"[cap_detect] Failed to fetch data for {discord_id}: {e}")
                        continue

                    tokens    = player_data.get("player", {}).get("progress", {}).get("guildRaid", {}).get("tokens", {})
                    current   = tokens.get("current", 0)
                    maximum   = tokens.get("max", 3)
                    is_capped = current >= maximum
                    print(f"[cap_detect] {discord_id}: {current}/{maximum} capped={is_capped}")

                    was_capped = capped_state.get(discord_id, False)

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
                    data_dir = get_guild_data_path(server_id, guild_id)
                    url      = TACTICUS_RAID_URL.format(season=season)

                    try:
                        response = await client.get(url, headers=headers)
                        response.raise_for_status()
                        api_data = response.json()

                        async with self.file_lock:
                            process_api_response(api_data, season, data_dir)

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
        """Edit all live leaderboard messages with fresh data."""
        from config import TIER_CHOICES
        from embeds import build_battle_messages, build_cluster_messages, load_leaderboard_file

        live = load_live_leaderboards(server_id)
        if not live:
            return

        to_remove = []

        for key, config in live.items():
            channel_id  = config.get("channel_id")
            message_ids = config.get("messages", {})
            channel     = self.bot.get_channel(channel_id)

            if channel is None:
                print(f"[live_leaderboard] Channel {channel_id} not found, removing {key}")
                to_remove.append(key)
                continue

            if key.startswith("guild:"):
                guild_id   = config.get("guild_id")
                guild_data = guilds.get(guild_id)
                if not guild_data:
                    to_remove.append(key)
                    continue

                guild_name = guild_data["name"]
                data_dir   = get_guild_data_path(server_id, guild_id)
                data, err  = load_leaderboard_file(data_dir / f"highest_hits_season_{season}.json")

                for tier in TIER_CHOICES:
                    msg_id = message_ids.get(tier.value)
                    if not msg_id:
                        continue
                    try:
                        msg = await channel.fetch_message(msg_id)
                        if err or not data:
                            new_content = f"📊 **{guild_name} — {tier.name} — No data yet**"
                        else:
                            messages = build_battle_messages(data, season, tier, server_id, guild_id, guild_name)
                            new_content = "\n\n".join(messages) if messages else f"📊 **{guild_name} — {tier.name} — No data yet**"
                        await msg.edit(content=new_content)
                    except discord.NotFound:
                        to_remove.append(key)
                        break
                    except discord.Forbidden:
                        print(f"[live_leaderboard] No permission to edit message in channel {channel_id}")
                        break
                    except Exception as e:
                        print(f"[live_leaderboard] Error editing message {msg_id}: {e}")

            elif key == "cluster":
                merged = {}
                for gid, gdata in guilds.items():
                    data_dir   = get_guild_data_path(server_id, gid)
                    data, err  = load_leaderboard_file(data_dir / f"highest_hits_season_{season}.json")
                    if err or not data:
                        continue
                    id_to_name = get_player_list(server_id, gid)
                    guild_name = gdata["name"]
                    for boss_id, encounter_dict in data.get("boss_hits", {}).items():
                        for e_index, tiers in encounter_dict.items():
                            for tier_key, entries in tiers.items():
                                bucket = merged.setdefault(boss_id, {}).setdefault(e_index, {}).setdefault(tier_key, [])
                                for entry in entries:
                                    user_id      = entry.get("user_id", "Unknown")
                                    user_display = id_to_name.get(user_id, str(user_id)[:8])
                                    bucket.append({**entry, "_display": user_display, "_guild": guild_name})

                for boss_id, encounter_dict in merged.items():
                    for e_index, tiers in encounter_dict.items():
                        for tier_key in tiers:
                            limit = 5 if e_index == "0" else 1
                            tiers[tier_key] = sorted(tiers[tier_key], key=lambda e: e["damage"], reverse=True)[:limit]

                for tier in TIER_CHOICES:
                    msg_id = message_ids.get(tier.value)
                    if not msg_id:
                        continue
                    try:
                        msg = await channel.fetch_message(msg_id)
                        tier_merged = {
                            boss_id: {
                                e_index: tiers[tier.value]
                                for e_index, tiers in encounter_dict.items()
                                if tier.value in tiers
                            }
                            for boss_id, encounter_dict in merged.items()
                        }
                        messages = build_cluster_messages(tier_merged, season, tier)
                        new_content = "\n\n".join(messages) if messages else f"🌐 **Cluster — {tier.name} — No data yet**"
                        await msg.edit(content=new_content)
                    except discord.NotFound:
                        to_remove.append(key)
                        break
                    except discord.Forbidden:
                        print(f"[live_leaderboard] No permission to edit cluster message in channel {channel_id}")
                        break
                    except Exception as e:
                        print(f"[live_leaderboard] Error editing cluster message {msg_id}: {e}")

        if to_remove:
            for key in to_remove:
                live.pop(key, None)
            save_live_leaderboards(server_id, live)
            print(f"[live_leaderboard] Removed broken configs: {to_remove}")

    @auto_update.before_loop
    async def before_auto_update(self):
        await self.bot.wait_until_ready()


async def setup_tasks(bot: commands.Bot, file_lock, player_service: PlayerService):
    await bot.add_cog(TasksCog(bot, file_lock, player_service))