import asyncio
import httpx
import discord
from discord import app_commands
from discord.ext import commands

from bot.embeds import guild_autocomplete
from bot.guilds import load_guilds, load_player_registrations
from bot.permissions import require_guild_member

TACTICUS_PLAYER_URL = "https://api.tacticusgame.com/api/v1/player"


def _format_countdown(seconds: int) -> str:
    if seconds <= 0:
        return "ready"
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


async def _fetch_bomb(client: httpx.AsyncClient, discord_id: str, api_key: str):
    headers = {"accept": "application/json", "X-API-KEY": api_key}
    try:
        response = await client.get(TACTICUS_PLAYER_URL, headers=headers)
        response.raise_for_status()
        return discord_id, response.json()
    except Exception:
        return discord_id, None


class BombCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="bomb_availability",
        description="Show bomb token status for all registered players in a guild.",
    )
    @require_guild_member()
    @app_commands.describe(guild_id="Select the guild")
    @app_commands.autocomplete(guild_id=guild_autocomplete)
    async def bomb_availability(
        self,
        interaction: discord.Interaction,
        guild_id: str,
    ):
        await interaction.response.defer()

        server_id  = interaction.guild_id
        guilds     = load_guilds(server_id)
        guild_data = guilds.get(guild_id)
        if not guild_data:
            await interaction.followup.send(f"❌ Guild `{guild_id}` not found.")
            return

        guild_name    = guild_data["name"]
        registrations = load_player_registrations(server_id)

        guild_players = {
            discord_id: data
            for discord_id, data in registrations.items()
            if data.get("guild_id") == guild_id
        }

        if not guild_players:
            await interaction.followup.send(
                f"❌ No registered players found in **{guild_name}**. "
                f"Players need to `/register` and select this guild."
            )
            return

        ready     = []  # discord_ids
        not_ready = []  # (discord_id, next_in_seconds)
        failed    = []  # discord_ids

        async with httpx.AsyncClient(timeout=10.0) as client:
            tasks = [
                _fetch_bomb(client, discord_id, data.get("api_key", ""))
                for discord_id, data in guild_players.items()
            ]
            results = await asyncio.gather(*tasks)

        for discord_id, player_data in results:
            if player_data is None:
                failed.append(discord_id)
                continue

            player    = player_data.get("player") or {}
            progress  = player.get("progress") or {}
            guild_raid = progress.get("guildRaid") or {}
            bomb_tokens = guild_raid.get("bombTokens") or {}
            current = bomb_tokens.get("current", 0)
            maximum = bomb_tokens.get("max", 1)
            next_in = bomb_tokens.get("nextTokenInSeconds", 0)

            if current >= maximum:
                ready.append(discord_id)
            else:
                not_ready.append((discord_id, next_in))

        # Sort not_ready by soonest ready first
        not_ready.sort(key=lambda x: x[1])

        total = len(ready) + len(not_ready)

        # Build embed
        embed = discord.Embed(
            title=f"💣 Bomb Availability — {guild_name}",
            color=discord.Color.green() if ready else discord.Color.red(),
        )

        # Ready players
        if ready:
            embed.add_field(
                name=f"✅ Ready ({len(ready)})",
                value="\n".join(f"<@{did}>" for did in ready),
                inline=False,
            )

        # Not ready players
        if not_ready:
            embed.add_field(
                name=f"❌ Not Ready ({len(not_ready)})",
                value="\n".join(f"<@{did}> — {_format_countdown(s)}" for did, s in not_ready),
                inline=False,
            )

        # Failed
        if failed:
            embed.add_field(
                name=f"⚠️ Failed to fetch ({len(failed)})",
                value="\n".join(f"<@{did}>" for did in failed),
                inline=False,
            )

        # Copy field inside the embed — use display names so mobile can copy cleanly
        if ready:
            copy_lines = []
            for did in ready:
                member = interaction.guild.get_member(int(did))
                name   = f"@{member.display_name}" if member else f"Unknown"
                copy_lines.append(f"{name} : <@{did}>")
            copy_text = "\n".join(copy_lines)
            embed.add_field(
                name="Copy players with available bombs",
                value=f"```\n{copy_text}\n```",
                inline=False,
            )

        embed.set_footer(text=f"Total bombs: {len(ready)}/{total}")

        await interaction.followup.send(embed=embed)


async def setup_bomb(bot: commands.Bot):
    await bot.add_cog(BombCog(bot))