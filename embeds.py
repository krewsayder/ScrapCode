import json
from pathlib import Path

import discord
from discord import app_commands

from config import LABELS
from getNameAndEmoji import get_boss_emoji, get_clean_boss_name, get_mow_emoji
from guilds import get_player_list, load_guilds


# ==========================================
# FILE HELPER
# ==========================================

def load_leaderboard_file(file_path: Path) -> tuple[dict | None, str | None]:
    """Load a leaderboard JSON file. Returns (data, error_message)."""
    if not file_path.exists():
        return None, "No data file found."
    try:
        return json.loads(file_path.read_text(encoding='utf-8')), None
    except json.JSONDecodeError:
        return None, "Leaderboard file is corrupted."
    except Exception as e:
        return None, f"Unexpected error reading file: {e}"


# ==========================================
# AUTOCOMPLETE
# ==========================================

async def guild_autocomplete(interaction: discord.Interaction, current: str):
    guilds = load_guilds()
    return [
        app_commands.Choice(name=data["name"], value=gid)
        for gid, data in guilds.items()
        if current.lower() in gid.lower() or current.lower() in data["name"].lower()
    ][:25]


# ==========================================
# SHARED HELPERS
# ==========================================

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
    guild_id: str = "",
    guild_name: str = "",
) -> list[str]:
    """Returns a list of plain text messages for the Battle leaderboard.
    First message is the header, followed by one message per boss."""
    tier_key   = tier.value
    id_to_name = get_player_list(guild_id) if guild_id else {}
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

            limit = 5 if e_index == "0" else 1
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

        if len(boss_lines) > 1:  # only add if there's actual data beyond the header
            messages.append("\n".join(boss_lines))

    return messages if len(messages) > 1 else []


# ==========================================
# BOMB MESSAGES
# ==========================================

def build_bomb_messages(
    data: dict,
    season: int,
    tier: app_commands.Choice[str],
    guild_id: str = "",
    guild_name: str = "",
) -> list[str]:
    """Returns a list of plain text messages for the Bomb leaderboard."""
    tier_key   = tier.value
    id_to_name = get_player_list(guild_id) if guild_id else {}
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

            for rank, entry in enumerate(tiers[tier_key][:5], start=1):
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