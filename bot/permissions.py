import discord
from discord import app_commands

from bot.guilds import repo


def _is_discord_admin(interaction: discord.Interaction) -> bool:
    return interaction.user.guild_permissions.administrator


def _user_role_ids(interaction: discord.Interaction) -> set[int]:
    return {r.id for r in interaction.user.roles}


async def check_tier(interaction: discord.Interaction, tier: str) -> bool:
    """Testable core logic for cluster tier checks."""
    if _is_discord_admin(interaction):
        return True
    cluster = repo.load(interaction.guild_id)
    allowed = set(cluster.role_tiers.get(tier, []))
    if tier == "officer":
        allowed |= set(cluster.role_tiers.get("admin", []))
    return bool(_user_role_ids(interaction) & allowed)


async def check_guild_member(interaction: discord.Interaction) -> bool:
    """Testable core logic for guild member checks."""
    if _is_discord_admin(interaction):
        return True
    cluster = repo.load(interaction.guild_id)
    user_roles = _user_role_ids(interaction)

    for tier in ("officer", "admin"):
        if user_roles & set(cluster.role_tiers.get(tier, [])):
            return True

    target_guild_id = getattr(interaction.namespace, "guild_id", None)
    if target_guild_id:
        guild = cluster.guilds.get(target_guild_id)
        return bool(guild and user_roles & set(guild.member_role_ids))

    return any(
        bool(user_roles & set(g.member_role_ids))
        for g in cluster.guilds.values()
    )


def require_tier(tier: str):
    async def predicate(interaction: discord.Interaction) -> bool:
        return await check_tier(interaction, tier)
    return app_commands.check(predicate)


def require_guild_member():
    async def predicate(interaction: discord.Interaction) -> bool:
        return await check_guild_member(interaction)
    return app_commands.check(predicate)