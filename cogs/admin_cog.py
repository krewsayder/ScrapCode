import httpx
import discord
from discord import app_commands
from discord.ext import commands

from guilds import (
    load_guilds,
    save_guilds,
    get_guild_data_path,
    load_live_leaderboards,
    save_live_leaderboards,
    load_player_list,
    add_cluster_role,
    add_guild_member_role,
)
from embeds import guild_autocomplete
from permissions import require_tier
from services.chronicl3r.player_service import PlayerService

TIER_OPTIONS = [
    app_commands.Choice(name="admin",   value="admin"),
    app_commands.Choice(name="officer", value="officer"),
]


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot, player_service: PlayerService):
        self.bot            = bot
        self.player_service = player_service

    # ==========================================
    # SLASH COMMAND: REGISTER_GUILD
    # ==========================================

    @app_commands.command(
        name="register_guild",
        description="Register a guild into the cluster with its API key and leader role.",
    )
    @require_tier("admin")
    @app_commands.describe(
        name="The guild's display name (e.g. Iron Warriors)",
        guild_id="A short unique ID for the guild, no spaces (e.g. iron_warriors)",
        api_key="The guild's Tacticus API key",
        role="The Discord role assigned to this guild's leader",
    )
    async def register_guild(
        self,
        interaction: discord.Interaction,
        name: str,
        guild_id: str,
        api_key: str,
        role: discord.Role,
    ):
        await interaction.response.defer(ephemeral=True)

        server_id = interaction.guild_id
        guild_id  = guild_id.strip().lower().replace(" ", "_")
        guilds    = load_guilds(server_id)

        if guild_id in guilds:
            await interaction.followup.send(
                f"❌ A guild with ID `{guild_id}` is already registered. "
                f"Choose a different ID or contact an admin to remove the existing entry.",
                ephemeral=True,
            )
            return

        for existing_id, existing_data in guilds.items():
            if existing_data.get("role_id") == role.id:
                await interaction.followup.send(
                    f"❌ That role is already linked to guild `{existing_data['name']}` (`{existing_id}`).",
                    ephemeral=True,
                )
                return

        guilds[guild_id] = {
            "name":                    name,
            "api_key":                 api_key,
            "role_id":                 role.id,
            "notification_channel_id": None,
        }
        save_guilds(server_id, guilds)
        get_guild_data_path(server_id, guild_id)  # creates the data directory

        await interaction.followup.send(
            f"✅ Guild **{name}** registered! Fetching player roster...",
            ephemeral=True,
        )

        try:
            await self.player_service.refresh_guild(server_id, guild_id, api_key)
            await interaction.followup.send(
                f"✅ Player list populated for **{name}**.\n"
                f"• ID: `{guild_id}`\n"
                f"• Leader role: {role.mention}",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(
                f"⚠️ Guild registered but player list could not be fetched: {e}",
                ephemeral=True,
            )

    # ==========================================
    # SLASH COMMAND: DEREGISTER_GUILD
    # ==========================================

    @app_commands.command(
        name="deregister_guild",
        description="Remove a guild from the cluster registry.",
    )
    @require_tier("admin")
    @app_commands.describe(guild_id="The guild to deregister")
    @app_commands.autocomplete(guild_id=guild_autocomplete)
    async def deregister_guild(self, interaction: discord.Interaction, guild_id: str):
        await interaction.response.defer(ephemeral=True)

        server_id  = interaction.guild_id
        guilds     = load_guilds(server_id)
        guild_data = guilds.get(guild_id)

        if not guild_data:
            await interaction.followup.send(
                f"❌ No guild found with ID `{guild_id}`.", ephemeral=True
            )
            return

        guild_name = guild_data["name"]
        del guilds[guild_id]
        save_guilds(server_id, guilds)

        await interaction.followup.send(
            f"✅ Guild **{guild_name}** (`{guild_id}`) has been deregistered.\n"
            f"⚠️ Their data folder has been left intact in case you need it.",
            ephemeral=True,
        )

    # ==========================================
    # SLASH COMMAND: LIST_GUILDS
    # ==========================================

    @app_commands.command(
        name="list_guilds",
        description="List all registered guilds in the cluster.",
    )
    @require_tier("officer")
    async def list_guilds(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        server_id = interaction.guild_id
        guilds    = load_guilds(server_id)
        if not guilds:
            await interaction.followup.send("❌ No guilds registered yet.", ephemeral=True)
            return

        embed = discord.Embed(
            title="🏰 Registered Guilds",
            description=f"{len(guilds)} guild(s) in the cluster",
            color=discord.Color.blurple(),
        )

        for guild_id, guild_data in guilds.items():
            guild_name   = guild_data.get("name", "Unknown")
            role_id      = guild_data.get("role_id")
            role_mention = f"<@&{role_id}>" if role_id else "❌ No role set"
            has_api_key  = "✅" if guild_data.get("api_key") else "❌ Missing"
            ping_channel = guild_data.get("notification_channel_id")
            ping_line    = f"<#{ping_channel}>" if ping_channel else "❌ Not set"

            players    = load_player_list(server_id, guild_id).get("players", {})
            active     = sum(1 for p in players.values() if not p.get("is_former"))
            last_vals  = [p["last_validated"] for p in players.values() if p.get("last_validated") and p["last_validated"] != "1970-01-01T00:00:00Z"]
            last_sync  = max(last_vals) if last_vals else None
            roster_line = f"✅ {active} active • Last sync: {last_sync[:10] if last_sync else 'never'}" if players else "❌ Never synced"

            embed.add_field(
                name=f"{guild_name} • `{guild_id}`",
                value=(
                    f"**Leader role:** {role_mention}\n"
                    f"**API key:** {has_api_key}\n"
                    f"**Ping channel:** {ping_line}\n"
                    f"**Roster:** {roster_line}"
                ),
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ==========================================
    # SLASH COMMAND: SET_PING_CHANNEL
    # ==========================================

    @app_commands.command(
        name="set_ping_channel",
        description="Set the channel where token cap notifications are posted for a guild.",
    )
    @require_tier("officer")
    @app_commands.describe(
        guild_id="The guild to configure",
        channel="The channel to send cap notifications to",
    )
    @app_commands.autocomplete(guild_id=guild_autocomplete)
    async def set_ping_channel(
        self,
        interaction: discord.Interaction,
        guild_id: str,
        channel: discord.TextChannel,
    ):
        await interaction.response.defer(ephemeral=True)

        server_id  = interaction.guild_id
        guilds     = load_guilds(server_id)
        guild_data = guilds.get(guild_id)

        if not guild_data:
            await interaction.followup.send(f"❌ Guild `{guild_id}` not found.", ephemeral=True)
            return

        guild_data["notification_channel_id"] = channel.id
        save_guilds(server_id, guilds)

        await interaction.followup.send(
            f"✅ Token cap notifications for **{guild_data['name']}** will now go to {channel.mention}.",
            ephemeral=True,
        )

    # ==========================================
    # SLASH COMMAND: SET_LIVE_LEADERBOARD
    # ==========================================

    @app_commands.command(
        name="set_live_leaderboard",
        description="Set up a live Battle leaderboard for a guild that auto-updates every hour.",
    )
    @require_tier("officer")
    @app_commands.describe(
        guild_id="The guild to set up a live leaderboard for",
        channel="The channel to post the live leaderboard in",
    )
    @app_commands.autocomplete(guild_id=guild_autocomplete)
    async def set_live_leaderboard(
        self,
        interaction: discord.Interaction,
        guild_id: str,
        channel: discord.TextChannel,
    ):
        await interaction.response.defer(ephemeral=True)

        from config import TIER_CHOICES
        from embeds import build_battle_messages, load_leaderboard_file

        server_id  = interaction.guild_id
        guilds     = load_guilds(server_id)
        guild_data = guilds.get(guild_id)
        if not guild_data:
            await interaction.followup.send(f"❌ Guild `{guild_id}` not found.", ephemeral=True)
            return

        guild_name = guild_data["name"]
        api_key    = guild_data.get("api_key")
        if not api_key:
            await interaction.followup.send(f"❌ Guild `{guild_id}` has no API key set.", ephemeral=True)
            return

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(
                    "https://api.tacticusgame.com/api/v1/guildRaid",
                    headers={"accept": "application/json", "X-API-KEY": api_key}
                )
                resp.raise_for_status()
                season = resp.json().get("season")
        except Exception as e:
            await interaction.followup.send(f"❌ Could not determine current season: {e}", ephemeral=True)
            return

        data_dir  = get_guild_data_path(server_id, guild_id)
        data, err = load_leaderboard_file(data_dir / f"highest_hits_season_{season}.json")
        if err:
            await interaction.followup.send(f"❌ {err} — run `/update_leaderboard` first.", ephemeral=True)
            return

        message_ids = {}
        for tier in TIER_CHOICES:
            messages = build_battle_messages(data, season, tier, server_id, guild_id, guild_name)
            content  = "\n\n".join(messages) if messages else f"📊 **{guild_name} — {tier.name} — No data yet**"
            try:
                msg = await channel.send(content)
                message_ids[tier.value] = msg.id
            except discord.Forbidden as e:
                await interaction.followup.send(
                    f"❌ Missing permissions to send messages in {channel.mention}.\nError: `{e}`",
                    ephemeral=True,
                )
                return

        live = load_live_leaderboards(server_id)
        live[f"guild:{guild_id}"] = {
            "channel_id": channel.id,
            "guild_id":   guild_id,
            "messages":   message_ids,
        }
        save_live_leaderboards(server_id, live)

        await interaction.followup.send(
            f"✅ Live Battle leaderboard set up for **{guild_name}** in {channel.mention}!\n"
            f"It will automatically update every hour.",
            ephemeral=True,
        )

    # ==========================================
    # SLASH COMMAND: SET_LIVE_CLUSTER_LEADERBOARD
    # ==========================================

    @app_commands.command(
        name="set_live_cluster_leaderboard",
        description="Set up a live Cluster leaderboard that auto-updates every hour.",
    )
    @require_tier("officer")
    @app_commands.describe(channel="The channel to post the live cluster leaderboard in")
    async def set_live_cluster_leaderboard(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ):
        await interaction.response.defer(ephemeral=True)

        from config import TIER_CHOICES
        from embeds import build_cluster_messages, load_leaderboard_file
        from guilds import get_player_list

        server_id = interaction.guild_id
        guilds    = load_guilds(server_id)
        if not guilds:
            await interaction.followup.send("❌ No guilds registered yet.", ephemeral=True)
            return

        first_gd  = next(iter(guilds.values()))
        first_key = first_gd.get("api_key")
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(
                    "https://api.tacticusgame.com/api/v1/guildRaid",
                    headers={"accept": "application/json", "X-API-KEY": first_key}
                )
                resp.raise_for_status()
                season = resp.json().get("season")
        except Exception as e:
            await interaction.followup.send(f"❌ Could not determine current season: {e}", ephemeral=True)
            return

        merged = {}
        for gid, gdata in guilds.items():
            data_dir  = get_guild_data_path(server_id, gid)
            data, err = load_leaderboard_file(data_dir / f"highest_hits_season_{season}.json")
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

        message_ids = {}
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
            content  = "\n\n".join(messages) if messages else f"🌐 **Cluster — {tier.name} — No data yet**"
            msg = await channel.send(content)
            message_ids[tier.value] = msg.id

        live = load_live_leaderboards(server_id)
        live["cluster"] = {
            "channel_id": channel.id,
            "messages":   message_ids,
        }
        save_live_leaderboards(server_id, live)

        await interaction.followup.send(
            f"✅ Live Cluster leaderboard set up in {channel.mention}!\n"
            f"It will automatically update every hour.",
            ephemeral=True,
        )


    # ==========================================
    # SLASH COMMAND: SET_CLUSTER_ROLE
    # ==========================================

    @app_commands.command(
        name="set_cluster_role",
        description="Add a Discord role to a cluster permission tier (admin or officer).",
    )
    @require_tier("admin")
    @app_commands.describe(
        tier="The permission tier to assign this role to",
        role="The Discord role to add",
    )
    @app_commands.choices(tier=TIER_OPTIONS)
    async def set_cluster_role(
        self,
        interaction: discord.Interaction,
        tier: app_commands.Choice[str],
        role: discord.Role,
    ):
        await interaction.response.defer(ephemeral=True)
        add_cluster_role(interaction.guild_id, tier.value, role.id)
        await interaction.followup.send(
            f"✅ {role.mention} added to the **{tier.value}** tier.",
            ephemeral=True,
        )

    # ==========================================
    # SLASH COMMAND: SET_GUILD_MEMBER_ROLE
    # ==========================================

    @app_commands.command(
        name="set_guild_member_role",
        description="Add a Discord role as a member role for a specific game guild.",
    )
    @require_tier("admin")
    @app_commands.describe(
        guild_id="The game guild to configure",
        role="The Discord role to add as a member role",
    )
    @app_commands.autocomplete(guild_id=guild_autocomplete)
    async def set_guild_member_role(
        self,
        interaction: discord.Interaction,
        guild_id: str,
        role: discord.Role,
    ):
        await interaction.response.defer(ephemeral=True)

        server_id  = interaction.guild_id
        guilds     = load_guilds(server_id)
        guild_data = guilds.get(guild_id)
        if not guild_data:
            await interaction.followup.send(f"❌ Guild `{guild_id}` not found.", ephemeral=True)
            return

        add_guild_member_role(server_id, guild_id, role.id)
        await interaction.followup.send(
            f"✅ {role.mention} added as a member role for **{guild_data['name']}**.",
            ephemeral=True,
        )


async def setup_admin(bot: commands.Bot, player_service: PlayerService):
    await bot.add_cog(AdminCog(bot, player_service))