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


class TokenCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="token_availability",
        description="Show raid token status for all registered players in a guild.",
    )
    @require_guild_member()
    @app_commands.describe(guild_id="Select the guild")
    @app_commands.autocomplete(guild_id=guild_autocomplete)
    async def token_availability(
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
                f"❌ No registered players found in **{guild_name}**."
            )
            return

        rows   = []
        failed = []

        async with httpx.AsyncClient(timeout=10.0) as client:
            for discord_id, data in guild_players.items():
                api_key = data.get("api_key") if isinstance(data, dict) else data
                if not api_key:
                    failed.append(discord_id)
                    continue

                headers = {"accept": "application/json", "X-API-KEY": api_key}
                try:
                    response = await client.get(TACTICUS_PLAYER_URL, headers=headers)
                    response.raise_for_status()
                    player_data = response.json()
                except Exception:
                    failed.append(discord_id)
                    continue

                tokens  = (
                    player_data.get("player", {})
                    .get("progress", {})
                    .get("guildRaid", {})
                    .get("tokens", {})
                )
                current = tokens.get("current", 0)
                maximum = tokens.get("max", 3)
                next_in = tokens.get("nextTokenInSeconds", 0)

                rows.append((discord_id, current, maximum, next_in))

        rows.sort(key=lambda x: (-x[1], x[3]))

        embed = discord.Embed(
            title=f"⚔️ Token Count — {guild_name}",
            color=discord.Color.blurple(),
        )

        if rows:
            lines = []
            for discord_id, current, maximum, next_in in rows:
                member = interaction.guild.get_member(int(discord_id))
                if member is None:
                    try:
                        member = await interaction.guild.fetch_member(int(discord_id))
                    except Exception:
                        member = None
                name = f"@{member.display_name}" if member else f"<@{discord_id}>"
                if current >= maximum:
                    lines.append(f"{name} — `{current}/{maximum}` tokens")
                else:
                    lines.append(f"{name} — `{current}/{maximum}` tokens • in {_format_countdown(next_in)}")
            embed.description = "\n".join(lines)

        if failed:
            embed.add_field(
                name=f"⚠️ Failed to fetch ({len(failed)})",
                value="\n".join(f"<@{did}>" for did in failed),
                inline=False,
            )

        embed.set_footer(text=f"{len(rows)} player(s)")
        await interaction.followup.send(embed=embed)


async def setup_token(bot: commands.Bot):
    await bot.add_cog(TokenCog(bot))