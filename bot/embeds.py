import asyncio

import discord
from discord import app_commands

from config import LABELS
from bot.getNameAndEmoji import get_boss_emoji, get_clean_boss_name, get_mow_emoji
from bot.guilds import get_player_list, load_guilds
from bot.tracker import TOP_N

# Encounter-index rendering limits (data-dictionary §2.7).
# Encounter "0" is the primary encounter and renders TOP_N entries; every
# other encounter renders a single top entry. The same constant pair is
# reused by the cogs that pre-truncate merged cluster data before passing
# it to build_cluster_messages.
ENCOUNTER_PRIMARY_INDEX = "0"
ENCOUNTER_PRIMARY_LIMIT = TOP_N
ENCOUNTER_NONPRIMARY_LIMIT = 1


def encounter_limit(e_index: str) -> int:
    """Return the per-encounter render limit (TOP_N for the primary encounter, 1 otherwise)."""
    return ENCOUNTER_PRIMARY_LIMIT if e_index == ENCOUNTER_PRIMARY_INDEX else ENCOUNTER_NONPRIMARY_LIMIT


# ==========================================
# AUTOCOMPLETE
# ==========================================

async def guild_autocomplete(interaction: discord.Interaction, current: str):
    guilds = load_guilds(interaction.guild_id)
    return [
        app_commands.Choice(name=data["name"], value=gid)
        for gid, data in guilds.items()
        if current.lower() in gid.lower() or current.lower() in data["name"].lower()
    ][:25]


# ==========================================
# SHARED HELPERS
# ==========================================

async def resolve_members(guild: discord.Guild, discord_ids: list[str]) -> tuple[list, list]:
    """Resolve Discord IDs to members. Returns (present: [(id, member)], gone: [id]).

    Skips local cache — always queries Discord live so departed members
    are not falsely returned from a stale cache.
    """
    if not discord_ids:
        return [], []

    try:
        fetched     = await guild.query_members(user_ids=[int(did) for did in discord_ids], cache=False)
        fetched_map = {str(m.id): m for m in fetched}
    except Exception:
        fetched_map = {}

    present = [(did, fetched_map[did]) for did in discord_ids if did in fetched_map]
    gone    = [did for did in discord_ids if did not in fetched_map]
    return present, gone


def _build_hero_display(hero_units: list[dict]) -> str:
    sorted_units = sorted(hero_units, key=lambda h: h.get("unitId", ""))
    return " ".join(get_boss_emoji(h.get("unitId", "")) for h in sorted_units) or "❌"


def _build_mow_display(entry: dict) -> str:
    return get_mow_emoji(entry["machine_of_war"]["unitId"]) if entry.get("machine_of_war") else "❌"


# ==========================================
# BATTLE MESSAGES
# ==========================================

def build_battle_messages(
    data: dict,
    season: int,
    tier: app_commands.Choice[str],
    discord_server_id: int = 0,
    guild_id: str = "",
    guild_name: str = "",
) -> list[str]:
    """Returns a list of plain text messages for the Battle leaderboard."""
    tier_key   = tier.value
    id_to_name = get_player_list(discord_server_id, guild_id) if guild_id and discord_server_id else {}
    boss_hits  = data.get("boss_hits", {})

    title_guild = f" • {guild_name}" if guild_name else ""
    messages = [
        f"** **\n"
        f"🏆 **Season {season} — {tier.name} Leaderboard{title_guild}**\n"
    ]

    for boss_id, encounter_dict in reversed(list(boss_hits.items())):
        display_name = get_clean_boss_name(str(boss_id))
        boss_emoji   = get_boss_emoji(str(boss_id))

        boss_lines = [f"{boss_emoji} **{display_name}**"]

        for e_index, tiers in encounter_dict.items():
            if tier_key not in tiers:
                continue

            limit = encounter_limit(e_index)
            encounter_label = LABELS.get(e_index, f"Encounter {e_index}")
            boss_lines.append(f"**{encounter_label}**")

            for rank, entry in enumerate(tiers[tier_key][:limit], start=1):
                user_id      = entry.get("user_id", "Unknown")
                user_display = id_to_name.get(user_id, str(user_id)[:8])
                hero_display = _build_hero_display(entry.get("hero_details", []))
                mow_display  = _build_mow_display(entry)
                boss_lines.append(
                    f"**#{rank}** • {entry.get('damage', 0):,} • **{user_display}**\n"
                    f"{hero_display} | {mow_display}"
                )

        if len(boss_lines) > 1:
            messages.append("\n".join(boss_lines))

    return messages if len(messages) > 1 else []


# ==========================================
# BOMB MESSAGES
# ==========================================

def build_bomb_messages(
    data: dict,
    season: int,
    tier: app_commands.Choice[str],
    discord_server_id: int = 0,
    guild_id: str = "",
    guild_name: str = "",
) -> list[str]:
    """Returns a list of plain text messages for the Bomb leaderboard."""
    tier_key   = tier.value
    id_to_name = get_player_list(discord_server_id, guild_id) if guild_id and discord_server_id else {}
    boss_hits  = data.get("boss_hits", {})

    title_guild = f" • {guild_name}" if guild_name else ""
    messages = [
        f"💣 **Season {season} — {tier.name} Bomb Leaderboard{title_guild}**\n"
        f"Damage type: Bomb"
    ]

    for boss_id, encounter_dict in reversed(list(boss_hits.items())):
        display_name = get_clean_boss_name(str(boss_id))
        boss_emoji   = get_boss_emoji(str(boss_id))

        boss_lines = [f"{boss_emoji} **{display_name}**"]

        for e_index, tiers in encounter_dict.items():
            if tier_key not in tiers:
                continue

            encounter_label = LABELS.get(e_index, f"Encounter {e_index}")
            boss_lines.append(f"**{encounter_label}**")

            for rank, entry in enumerate(tiers[tier_key][:TOP_N], start=1):
                user_id      = entry.get("user_id", "Unknown")
                user_display = id_to_name.get(user_id, str(user_id)[:8])
                boss_lines.append(
                    f"**#{rank}** • {entry.get('damage', 0):,} • **{user_display}**"
                )

        if len(boss_lines) > 1:
            messages.append("\n".join(boss_lines))

    return messages if len(messages) > 1 else []


# ==========================================
# CLUSTER MESSAGES
# ==========================================

def build_cluster_messages(
    merged: dict,
    season: int,
    tier: app_commands.Choice[str],
) -> list[str]:
    """Returns a list of plain text messages for the cluster-wide Battle leaderboard."""
    messages = [
        f"** **\n"
        f"🌐 **Cluster — Season {season} — {tier.name} Leaderboard**\n"
        f"Top Hits Across All Guilds"
    ]

    for boss_id, encounter_dict in reversed(list(merged.items())):
        display_name = get_clean_boss_name(str(boss_id))
        boss_emoji   = get_boss_emoji(str(boss_id))

        boss_lines = [f"{boss_emoji} **{display_name}**"]

        for e_index, entries in encounter_dict.items():
            encounter_label = LABELS.get(e_index, f"Encounter {e_index}")
            boss_lines.append(f"**{encounter_label}**")

            for rank, entry in enumerate(entries, start=1):
                hero_display = _build_hero_display(entry.get("hero_details", []))
                mow_display  = _build_mow_display(entry)
                boss_lines.append(
                    f"**#{rank}** • {entry['damage']:,} • **{entry['_display']}** ({entry['_guild']})\n"
                    f"{hero_display} | {mow_display}"
                )

        if len(boss_lines) > 1:
            messages.append("\n".join(boss_lines))

    return messages if len(messages) > 1 else []