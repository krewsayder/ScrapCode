import discord
from discord import app_commands
from discord.ext import commands

from config import REQUIRED_ROLES, TIER_CHOICES
from guilds import load_guilds, get_guild_data_path, get_player_list
from embeds import (
    build_battle_messages,
    build_bomb_messages,
    build_cluster_messages,
    load_leaderboard_file,
    guild_autocomplete,
)


class ViewCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ==========================================
    # SLASH COMMAND: VIEW_LEADERBOARD (Battle)
    # ==========================================

    @app_commands.command(
        name="view_leaderboard",
        description="View top Battle damage leaderboard for a specific guild and tier.",
    )
    @app_commands.checks.has_any_role("Captain","Guild Leader","Dark Tech","Tech-Priest")
    @app_commands.describe(
        guild_id="Select the guild",
        season="Season number (e.g., 94)",
        tier="Select the boss tier",
    )
    @app_commands.autocomplete(guild_id=guild_autocomplete)
    @app_commands.choices(tier=TIER_CHOICES)
    async def view_leaderboard(
        self,
        interaction: discord.Interaction,
        guild_id: str,
        season: int,
        tier: app_commands.Choice[str],
    ):
        await interaction.response.defer()

        guilds     = load_guilds()
        guild_data = guilds.get(guild_id)
        if not guild_data:
            await interaction.followup.send(f"❌ Guild `{guild_id}` not found.")
            return

        guild_name = guild_data["name"]
        data_dir   = get_guild_data_path(guild_id)
        data, err  = load_leaderboard_file(data_dir / f"highest_hits_season_{season}.json")
        if err:
            await interaction.followup.send(f"❌ {err}")
            return

        messages = build_battle_messages(data, season, tier, guild_id, guild_name)
        if not messages:
            await interaction.followup.send(
                f"❌ No Battle entries found for **{tier.name}** in season {season}."
            )
            return

        # Send header as the followup, then each boss as a separate message
        await interaction.followup.send(messages[0])
        for msg in messages[1:]:
            await interaction.channel.send(msg)

    # ==========================================
    # SLASH COMMAND: VIEW_BOMB_LEADERBOARD
    # ==========================================

    @app_commands.command(
        name="view_bomb_leaderboard",
        description="View top Bomb damage leaderboard for a specific guild and tier.",
    )
    @app_commands.checks.has_any_role("Tech-Priest") 
    @app_commands.describe(
        guild_id="Select the guild",
        season="Season number (e.g., 94)",
        tier="Select the boss tier",
    )
    @app_commands.autocomplete(guild_id=guild_autocomplete)
    @app_commands.choices(tier=TIER_CHOICES)
    async def view_bomb_leaderboard(
        self,
        interaction: discord.Interaction,
        guild_id: str,
        season: int,
        tier: app_commands.Choice[str],
    ):
        await interaction.response.defer()

        guilds     = load_guilds()
        guild_data = guilds.get(guild_id)
        if not guild_data:
            await interaction.followup.send(f"❌ Guild `{guild_id}` not found.")
            return

        guild_name = guild_data["name"]
        data_dir   = get_guild_data_path(guild_id)
        data, err  = load_leaderboard_file(data_dir / f"highest_bombs_season_{season}.json")
        if err:
            await interaction.followup.send(f"❌ {err}")
            return

        messages = build_bomb_messages(data, season, tier, guild_id, guild_name)
        if not messages:
            await interaction.followup.send(
                f"❌ No Bomb entries found for **{tier.name}** in season {season}."
            )
            return

        await interaction.followup.send(messages[0])
        for msg in messages[1:]:
            await interaction.channel.send(msg)

    # ==========================================
    # SLASH COMMAND: VIEW_CLUSTER_LEADERBOARD
    # ==========================================

    @app_commands.command(
        name="view_cluster_leaderboard",
        description="View top Battle damage leaderboard across all guilds in the cluster.",
    )
    @app_commands.checks.has_any_role("Captain","Guild Leader","Dark Tech","Tech-Priest")
    @app_commands.describe(
        season="Season number (e.g., 94)",
        tier="Select the boss tier",
    )
    @app_commands.choices(tier=TIER_CHOICES)
    async def view_cluster_leaderboard(
        self,
        interaction: discord.Interaction,
        season: int,
        tier: app_commands.Choice[str],
    ):
        await interaction.response.defer()

        guilds = load_guilds()
        if not guilds:
            await interaction.followup.send("❌ No guilds registered yet.")
            return

        tier_key = tier.value
        merged   = {}

        for guild_id, guild_data in guilds.items():
            data_dir   = get_guild_data_path(guild_id)
            data, err  = load_leaderboard_file(data_dir / f"highest_hits_season_{season}.json")
            if err or not data:
                continue

            id_to_name = get_player_list(guild_id)
            guild_name = guild_data["name"]

            for boss_id, encounter_dict in data.get("boss_hits", {}).items():
                for e_index, tiers in encounter_dict.items():
                    if tier_key not in tiers:
                        continue
                    bucket = merged.setdefault(boss_id, {}).setdefault(e_index, [])
                    for entry in tiers[tier_key]:
                        user_id      = entry.get("user_id", "Unknown")
                        user_display = id_to_name.get(user_id, str(user_id)[:8])
                        bucket.append({**entry, "_display": user_display, "_guild": guild_name})

        if not merged:
            await interaction.followup.send(
                f"❌ No cluster data found for **{tier.name}** in season {season}."
            )
            return

        # Sort each bucket and keep top 5 for Main ("0"), top 1 for sides
        for boss_id, encounter_dict in merged.items():
            for e_index in encounter_dict:
                limit = 5 if e_index == "0" else 1
                encounter_dict[e_index] = sorted(
                    encounter_dict[e_index], key=lambda e: e["damage"], reverse=True
                )[:limit]

        messages = build_cluster_messages(merged, season, tier)
        if not messages:
            await interaction.followup.send(f"❌ No cluster entries found for **{tier.name}**.")
            return

        await interaction.followup.send(messages[0])
        for msg in messages[1:]:
            await interaction.channel.send(msg)


async def setup_view(bot: commands.Bot):
    await bot.add_cog(ViewCog(bot))