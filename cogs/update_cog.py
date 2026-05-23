import asyncio

import httpx
import discord
from discord import app_commands
from discord.ext import commands

from config import REQUIRED_ROLES
from guilds import load_guilds, get_guild_data_path, load_player_list
from tracker import process_api_response
from embeds import guild_autocomplete
from services.chronicl3r.player_service import PlayerService

TACTICUS_RAID_URL = "https://api.tacticusgame.com/api/v1/guildRaid/{season}"


class UpdateCog(commands.Cog):
    def __init__(self, bot: commands.Bot, file_lock: asyncio.Lock, player_service: PlayerService):
        self.bot            = bot
        self.file_lock      = file_lock
        self.player_service = player_service

    # ==========================================
    # SLASH COMMAND: UPDATE_LEADERBOARD
    # ==========================================

    @app_commands.command(
        name="update_leaderboard",
        description="Fetches raid data from the Tacticus API and updates local records.",
    )
    @app_commands.checks.has_any_role("Captain","Guild Leader","Dark Tech","Tech-Priest")
    @app_commands.describe(
        guild_id="The guild to update",
        season="The season number to update (e.g. 94)",
    )
    @app_commands.autocomplete(guild_id=guild_autocomplete)
    async def update_leaderboard(self, interaction: discord.Interaction, guild_id: str, season: int):
        await interaction.response.defer(thinking=True)

        guilds     = load_guilds()
        guild_data = guilds.get(guild_id.strip().lower())

        if not guild_data:
            await interaction.followup.send(
                f"❌ No guild found with ID `{guild_id}`. "
                f"Registered guilds: {', '.join(f'`{g}`' for g in guilds) or 'none'}"
            )
            return

        api_key    = guild_data.get("api_key")
        guild_name = guild_data["name"]

        if not api_key:
            await interaction.followup.send(f"❌ Guild `{guild_id}` has no API key set.")
            return

        url      = TACTICUS_RAID_URL.format(season=season)
        headers  = {"accept": "application/json", "X-API-KEY": api_key}
        data_dir = get_guild_data_path(guild_id)

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                api_data = response.json()

            async with self.file_lock:
                process_api_response(api_data, season, data_dir)

            await self._register_unknown_players(guild_id, api_data)

            await interaction.followup.send(
                f"✅ Leaderboard for **{guild_name}** — Season **{season}** updated successfully."
            )

        except httpx.HTTPStatusError as e:
            await interaction.followup.send(
                f"❌ API Error for **{guild_name}**: HTTP {e.response.status_code}\n```{e.response.text}```"
            )
        except Exception as e:
            await interaction.followup.send(f"⚠️ An unexpected error occurred: {str(e)}")

    # ==========================================
    # SLASH COMMAND: UPDATE_ALL
    # ==========================================

    @app_commands.command(
        name="update_all",
        description="Fetches raid data for ALL registered guilds and updates local records.",
    )
    @app_commands.checks.has_any_role("Captain","Guild Leader","Dark Tech","Tech-Priest")
    @app_commands.describe(season="The season number to update (e.g. 94)")
    async def update_all(self, interaction: discord.Interaction, season: int):
        await interaction.response.defer(thinking=True)

        guilds = load_guilds()
        if not guilds:
            await interaction.followup.send("❌ No guilds registered yet.")
            return

        url     = TACTICUS_RAID_URL.format(season=season)
        results = []

        # Reuse a single client for all guild requests
        async with httpx.AsyncClient(timeout=20.0) as client:
            for guild_id, guild_data in guilds.items():
                guild_name = guild_data["name"]
                api_key    = guild_data.get("api_key")

                if not api_key:
                    results.append(f"⚠️ **{guild_name}** — skipped, no API key set.")
                    continue

                headers  = {"accept": "application/json", "X-API-KEY": api_key}
                data_dir = get_guild_data_path(guild_id)

                try:
                    response = await client.get(url, headers=headers)
                    response.raise_for_status()
                    api_data = response.json()

                    async with self.file_lock:
                        process_api_response(api_data, season, data_dir)

                    await self._register_unknown_players(guild_id, api_data)

                    results.append(f"✅ **{guild_name}** — updated successfully.")

                except httpx.HTTPStatusError as e:
                    results.append(f"❌ **{guild_name}** — HTTP {e.response.status_code}: {e.response.text[:80]}")
                except Exception as e:
                    results.append(f"❌ **{guild_name}** — {str(e)[:80]}")

        await interaction.followup.send(
            f"**Season {season} update complete:**\n" + "\n".join(results)
        )


    async def _register_unknown_players(self, guild_id: str, api_data: dict) -> None:
        """Register any user IDs from raid data that aren't in the local player list."""
        known   = set(load_player_list(guild_id).get("players", {}).keys())
        seen    = {e["userId"] for e in api_data.get("entries", []) if "userId" in e}
        unknown = seen - known
        for user_id in unknown:
            try:
                await self.player_service.get_or_register(user_id)
                print(f"[UpdateCog] Registered previously unknown player {user_id}")
            except Exception as e:
                print(f"[UpdateCog] Failed to register {user_id}: {e}")


async def setup_update(bot: commands.Bot, file_lock: asyncio.Lock, player_service: PlayerService):
    await bot.add_cog(UpdateCog(bot, file_lock, player_service))
