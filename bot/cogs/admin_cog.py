import httpx
import discord
from discord import app_commands
from discord.ext import commands

from bot.guilds import (
    load_guilds,
    save_guilds,
    load_live_leaderboards,
    save_live_leaderboards,
    load_player_list,
    add_cluster_role,
    add_guild_member_role,
    repo,
)
from bot.embeds import guild_autocomplete, encounter_limit
from bot.permissions import require_tier, check_tier
from bot.services.chronicl3r.player_service import PlayerService

CONFIG_OPTIONS = [
    app_commands.Choice(name="guilds",        value="guilds"),
    app_commands.Choice(name="roles",         value="roles"),
    app_commands.Choice(name="leaderboards",  value="leaderboards"),
]

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
    # SLASH COMMAND: VIEW_CONFIG
    # ==========================================

    @app_commands.command(
        name="view_config",
        description="View bot configuration for the cluster.",
    )
    @app_commands.describe(config="The configuration to view")
    @app_commands.choices(config=CONFIG_OPTIONS)
    async def view_config(self, interaction: discord.Interaction, config: app_commands.Choice[str]):
        if not await check_tier(interaction, "officer"):
            await interaction.response.send_message(
                "❌ You don't have permission to view configuration.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        server_id = interaction.guild_id

        if config.value == "guilds":
            embed = self._config_guilds(server_id)
        elif config.value == "roles":
            embed = self._config_roles(server_id)
        elif config.value == "leaderboards":
            embed = self._config_leaderboards(server_id)

        await interaction.followup.send(embed=embed, ephemeral=True)

    def _config_guilds(self, server_id: int) -> discord.Embed:
        guilds = load_guilds(server_id)
        embed  = discord.Embed(
            title="🏰 Registered Guilds",
            description=f"{len(guilds)} guild(s) in the cluster" if guilds else "No guilds registered yet.",
            color=discord.Color.blurple(),
        )
        for guild_id, guild_data in guilds.items():
            guild_name   = guild_data.get("name", "Unknown")
            role_id      = guild_data.get("role_id")
            role_mention = f"<@&{role_id}>" if role_id else "❌ No role set"
            has_api_key  = "✅" if guild_data.get("api_key") else "❌ Missing"
            ping_channel = guild_data.get("notification_channel_id")
            ping_line    = f"<#{ping_channel}>" if ping_channel else "❌ Not set"

            players     = load_player_list(server_id, guild_id).get("players", {})
            active      = sum(1 for p in players.values() if not p.get("is_former"))
            last_vals   = [p["last_validated"] for p in players.values() if p.get("last_validated") and p["last_validated"] != "1970-01-01T00:00:00Z"]
            last_sync   = max(last_vals) if last_vals else None
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
        return embed

    def _config_roles(self, server_id: int) -> discord.Embed:
        cluster = repo.load(server_id)

        def fmt_roles(role_ids: list[int]) -> str:
            if not role_ids:
                return "❌ None configured"
            return " ".join(f"<@&{rid}>" for rid in role_ids)

        embed = discord.Embed(title="🔐 Role Configuration", color=discord.Color.blurple())
        embed.add_field(name="🛡️ Admin",   value=fmt_roles(cluster.role_tiers.get("admin", [])),   inline=False)
        embed.add_field(name="🔱 Officer", value=fmt_roles(cluster.role_tiers.get("officer", [])), inline=False)
        for guild_id, guild in cluster.guilds.items():
            embed.add_field(name=f"⚙️ {guild.name} members", value=fmt_roles(guild.member_role_ids), inline=False)
        return embed

    def _config_leaderboards(self, server_id: int) -> discord.Embed:
        live  = load_live_leaderboards(server_id)
        embed = discord.Embed(title="📊 Live Leaderboards", color=discord.Color.blurple())

        if not live:
            embed.description = "No live leaderboards configured."
            return embed

        for key, cfg in live.items():
            channel_id  = cfg.get("channel_id")
            channel_str = f"<#{channel_id}>" if channel_id else "❌ No channel"
            tier_count  = len(cfg.get("messages", {}))
            label       = "Cluster" if key == "cluster" else key.replace("guild:", "")
            embed.add_field(
                name=label,
                value=f"**Channel:** {channel_str}\n**Tiers tracked:** {tier_count}",
                inline=False,
            )
        return embed

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
        from bot.embeds import build_battle_messages

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

        data = repo.load_battle_hits(server_id, guild_id, season)

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
            "season":     season,
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
        from bot.embeds import build_cluster_messages
        from bot.guilds import get_player_list

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
            data = repo.load_battle_hits(server_id, gid, season)
            if not data or not data.get("boss_hits"):
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
                    limit = encounter_limit(e_index)
                    tiers[tier_key] = sorted(tiers[tier_key], key=lambda e: (-e["damage"], e.get("completed_on", "")))[:limit]

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
            "season":     season,
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