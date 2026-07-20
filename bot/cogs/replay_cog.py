from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import TIER_CHOICES
from bot.permissions import require_guild_member
from bot.repository import ReplayEntry
import bot.guilds as guilds

# ADR-006 D10/D11 + ADR-004 §3 closure (04-03): the cog no longer reads or
# writes the retired JSON replay index file and no longer carries the
# hardcoded forum/thread-ID constants. Thread IDs come from the
# `replay_threads` table (seeded in 03-03) via the repository; replay
# entries live in `replay_entries` with per-tenant URL uniqueness on
# (discord_server_id, boss, map_name, url). The cog routes through the
# `ClusterRepository` ABC (`bot.guilds.repo`) — it does NOT import
# `bot.db.*` directly (hexagonal boundary).

POSITION_CHOICES = [
    app_commands.Choice(name="LHS",     value="LHS"),
    app_commands.Choice(name="RHS",     value="RHS"),
    app_commands.Choice(name="Default", value="Default"),
]

TEAM_CHOICES = [
    app_commands.Choice(name="Neuro",       value="Neuro"),
    app_commands.Choice(name="Laviscus",    value="Laviscus"),
    app_commands.Choice(name="Mech",        value="Mech"),
    app_commands.Choice(name="Battlesuit",  value="Battlesuit"),
    app_commands.Choice(name="RA",          value="RA"),
    app_commands.Choice(name="MH",          value="MH"),
    app_commands.Choice(name="Other",       value="Other"),
]


def build_index_message(entries: list) -> str:
    """Render the replay index body for a single (boss, map_name) thread.

    Pure function kept post-cutover — the renderer is unchanged; only the
    SOURCE of the entries (SQL via the repo vs the retired JSON file)
    changed in 04-03.
    """
    if not entries:
        return "*No replays submitted yet.*"

    by_team = {}
    for entry in entries:
        by_team.setdefault(entry["team"], {}).setdefault(entry["tier"], []).append(entry)

    lines = []
    team_order = [t.value for t in TEAM_CHOICES]
    tier_order = [t.name for t in TIER_CHOICES]

    for team_name in team_order:
        if team_name not in by_team:
            continue
        lines.append(f"**{team_name}**")
        for tier_name in tier_order:
            if tier_name not in by_team[team_name]:
                continue
            lines.append(f"*{tier_name}*")
            for e in by_team[team_name][tier_name]:
                pos     = f" • {e['position']}" if e.get("position") else ""
                dmg     = f" • {e['damage']}" if e.get("damage") else ""
                comment = f" — {e['comment']}" if e.get("comment") else ""
                lines.append(f"[replay]({e['url']}){pos}{dmg}{comment}")
        lines.append("")

    return "\n".join(lines).strip()


async def boss_autocomplete(interaction: discord.Interaction, current: str):
    threads = guilds.repo.list_replay_threads(interaction.guild_id)
    return [
        app_commands.Choice(name=boss, value=boss)
        for boss in threads
        if current.lower() in boss.lower()
    ][:25]


async def map_autocomplete(interaction: discord.Interaction, current: str):
    boss = interaction.namespace.boss
    threads = guilds.repo.list_replay_threads(interaction.guild_id)
    maps = threads.get(boss, {})
    return [
        app_commands.Choice(name=m, value=m)
        for m in maps
        if current.lower() in m.lower()
    ][:25]


class ReplayCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _resolve_forum_channel(self, forum_channel_id: int):
        if not forum_channel_id:
            return None
        forum = self.bot.get_channel(forum_channel_id)
        if forum is None:
            try:
                forum = await self.bot.fetch_channel(forum_channel_id)
            except Exception as e:
                print(f"[replay] Failed to fetch forum channel: {e}")
                return None
        return forum

    async def _get_thread(self, forum_channel_id: int, thread_id: int) -> Optional[discord.Thread]:
        forum = await self._resolve_forum_channel(forum_channel_id)
        if forum is None:
            return None

        thread = forum.get_thread(thread_id)
        if thread:
            return thread

        try:
            async for t in forum.archived_threads():
                if t.id == thread_id:
                    return t
        except Exception as e:
            print(f"[replay] Failed to check archived threads: {e}")
        return None

    async def _edit_index_message(self, discord_server_id: int, boss: str,
                                  map_name: str, entries: list):
        thread_info = guilds.repo.get_replay_thread(discord_server_id, boss, map_name)
        if not thread_info:
            return
        msg_id = thread_info.get("index_message_id")
        thread_id = thread_info.get("thread_id")
        forum_channel_id = thread_info.get("forum_channel_id")
        if not msg_id or not thread_id:
            return

        thread = await self._get_thread(forum_channel_id, thread_id)
        if thread is None:
            return

        try:
            msg     = await thread.fetch_message(msg_id)
            content = build_index_message(entries)
            await msg.edit(content=content)
        except Exception as e:
            print(f"[replay] Failed to edit index message in {boss}/{map_name}: {e}")

    # ==========================================
    # SLASH COMMAND: UPLOAD_REPLAY
    # ==========================================

    @app_commands.command(name="upload_replay", description="Submit a raid replay to the index.")
    @require_guild_member()
    @app_commands.describe(
        boss="The boss this replay is for",
        map_name="The map this replay was played on",
        team="Team type used",
        tier="Boss tier",
        damage="Damage dealt (e.g. 1.33M)",
        url="Link to the replay",
        position="Starting position (optional)",
        comment="Optional notes about the run",
    )
    @app_commands.autocomplete(boss=boss_autocomplete, map_name=map_autocomplete)
    @app_commands.choices(team=TEAM_CHOICES, tier=TIER_CHOICES, position=POSITION_CHOICES)
    async def upload_replay(
        self,
        interaction: discord.Interaction,
        boss: str,
        map_name: str,
        team: app_commands.Choice[str],
        tier: app_commands.Choice[str],
        damage: str,
        url: str,
        position: Optional[app_commands.Choice[str]] = None,
        comment: Optional[str] = None,
    ):
        await interaction.response.defer(ephemeral=True)
        print(f"[replay] upload_replay called — boss={boss} map={map_name}")

        discord_server_id = interaction.guild_id
        thread_info = guilds.repo.get_replay_thread(discord_server_id, boss, map_name)
        if thread_info is None:
            await interaction.followup.send(
                f"❌ **{boss} / {map_name}** not found in the index.", ephemeral=True)
            return

        thread_id = thread_info.get("thread_id")
        forum_channel_id = thread_info.get("forum_channel_id")
        if not thread_id:
            await interaction.followup.send(
                f"❌ Could not find thread for **{boss} / {map_name}**. Check thread ID.",
                ephemeral=True)
            return

        print(f"[replay] Getting thread {thread_id}...")
        thread = await self._get_thread(forum_channel_id, thread_id)
        if thread is None:
            await interaction.followup.send(
                f"❌ Could not find thread for **{boss} / {map_name}**. Check thread ID.",
                ephemeral=True)
            return

        print(f"[replay] Thread found: {thread}, proceeding...")
        entry: ReplayEntry = {
            "team":         team.value,
            "tier":         tier.name,
            "position":     position.value if position else "",
            "damage":       damage,
            "url":          url,
            "comment":      comment or "",
            "submitted_by": str(interaction.user.id),
        }

        from bot.repository import DuplicateReplayUrlError
        try:
            guilds.repo.upsert_replay_entry(discord_server_id, boss, map_name, entry)
        except DuplicateReplayUrlError as dup:
            await interaction.followup.send(
                f"❌ This replay URL has already been submitted under **{dup.boss} / {dup.map_name}**.",
                ephemeral=True)
            return

        if not thread_info.get("index_message_id"):
            msg = await thread.send("*No replays submitted yet.*")
            guilds.repo.set_replay_thread_index_message(
                discord_server_id, boss, map_name, msg.id)

        entries = guilds.repo.load_replay_entries(discord_server_id, boss, map_name)
        await self._edit_index_message(discord_server_id, boss, map_name, entries)
        await interaction.followup.send(f"✅ Replay submitted for **{boss} / {map_name}**!", ephemeral=True)
        print(f"[replay] Upload complete for {boss}/{map_name}")

    # ==========================================
    # SLASH COMMAND: GET_REPLAY
    # ==========================================

    @app_commands.command(name="get_replay", description="View replays for a map, optionally filtered by team.")
    @require_guild_member()
    @app_commands.describe(
        boss="The boss to look up",
        map_name="The map to look up",
        team="Filter by team (optional)",
    )
    @app_commands.autocomplete(boss=boss_autocomplete, map_name=map_autocomplete)
    @app_commands.choices(team=TEAM_CHOICES)
    async def get_replay(
        self,
        interaction: discord.Interaction,
        boss: str,
        map_name: str,
        team: Optional[app_commands.Choice[str]] = None,
    ):
        await interaction.response.defer()

        discord_server_id = interaction.guild_id
        entries = guilds.repo.load_replay_entries(discord_server_id, boss, map_name)

        if not entries:
            await interaction.followup.send(f"No replays found for **{boss} / {map_name}**.")
            return

        if team:
            entries = [e for e in entries if e["team"] == team.value]
            if not entries:
                await interaction.followup.send(
                    f"No **{team.value}** replays found for **{boss} / {map_name}**.")
                return

        title   = f"**{boss} — {map_name}**"
        if team:
            title += f" • {team.value}"
        content = title + "\n" + build_index_message(entries)

        await interaction.followup.send(content)

    # ==========================================
    # SLASH COMMAND: DELETE_REPLAY
    # ==========================================

    @app_commands.command(name="delete_replay", description="Remove a replay from the index by its URL.")
    @require_guild_member()
    @app_commands.describe(
        boss="The boss the replay belongs to",
        map_name="The map the replay belongs to",
        url="The replay URL to delete",
    )
    @app_commands.autocomplete(boss=boss_autocomplete, map_name=map_autocomplete)
    async def delete_replay(
        self,
        interaction: discord.Interaction,
        boss: str,
        map_name: str,
        url: str,
    ):
        await interaction.response.defer(ephemeral=True)

        discord_server_id = interaction.guild_id
        removed = guilds.repo.delete_replay_entry(discord_server_id, boss, map_name, url)
        if not removed:
            await interaction.followup.send("❌ No replay with that URL found.", ephemeral=True)
            return

        entries = guilds.repo.load_replay_entries(discord_server_id, boss, map_name)
        await self._edit_index_message(discord_server_id, boss, map_name, entries)
        await interaction.followup.send(f"✅ Replay removed from **{boss} / {map_name}**.", ephemeral=True)


async def setup_replay(bot: commands.Bot):
    await bot.add_cog(ReplayCog(bot))