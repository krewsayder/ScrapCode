import discord
from discord import app_commands
from discord.ext import commands

from config import REQUIRED_ROLES
from guilds import (
    load_guilds,
    save_guilds,
    get_guild_data_path,
    load_live_leaderboards,
    save_live_leaderboards,
)
from embeds import guild_autocomplete
from services.chronicl3r.player_service import PlayerService


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
    @app_commands.checks.has_any_role("Guild Leader", "Dark Tech", "Tech-Priest")
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

        guild_id = guild_id.strip().lower().replace(" ", "_")
        guilds   = load_guilds()

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
            "name": name,
            "api_key": api_key,
            "role_id": role.id,
        }
        save_guilds(guilds)
        get_guild_data_path(guild_id)  # Creates the data directory

        await interaction.followup.send(
            f"✅ Guild **{name}** registered! Fetching player roster...",
            ephemeral=True,
        )

        try:
            await self.player_service.refresh_guild(guild_id, api_key)
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
    @app_commands.checks.has_any_role(
        "Guild Leader", 
        "Dark Tech",
        "Tech-Priest"
    )
    @app_commands.describe(guild_id="The guild to deregister")
    @app_commands.autocomplete(guild_id=guild_autocomplete)
    async def deregister_guild(self, interaction: discord.Interaction, guild_id: str):
        await interaction.response.defer(ephemeral=True)

        guilds     = load_guilds()
        guild_data = guilds.get(guild_id)

        if not guild_data:
            await interaction.followup.send(
                f"❌ No guild found with ID `{guild_id}`.", ephemeral=True
            )
            return

        guild_name = guild_data["name"]
        del guilds[guild_id]
        save_guilds(guilds)

        await interaction.followup.send(
            f"✅ Guild **{guild_name}** (`{guild_id}`) has been deregistered.\n"
            f"⚠️ Their data folder `data/{guild_id}/` has been left intact in case you need it.",
            ephemeral=True,
        )

    # ==========================================
    # SLASH COMMAND: LIST_GUILDS
    # ==========================================

    @app_commands.command(
        name="list_guilds",
        description="List all registered guilds in the cluster.",
    )
    @app_commands.checks.has_any_role("Captain","Guild Leader","Dark Tech","Tech-Priest")
    async def list_guilds(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        guilds = load_guilds()
        if not guilds:
            await interaction.followup.send("❌ No guilds registered yet.", ephemeral=True)
            return

        embed = discord.Embed(
            title="🏰 Registered Guilds",
            description=f"{len(guilds)} guild(s) in the cluster",
            color=discord.Color.blurple(),
        )

        from guilds import load_player_list
        for guild_id, guild_data in guilds.items():
            guild_name   = guild_data.get("name", "Unknown")
            role_id      = guild_data.get("role_id")
            role_mention = f"<@&{role_id}>" if role_id else "❌ No role set"
            has_api_key  = "✅" if guild_data.get("api_key") else "❌ Missing"

            players      = load_player_list(guild_id).get("players", {})
            active        = sum(1 for p in players.values() if not p.get("is_former"))
            last_vals    = [p["last_validated"] for p in players.values() if p.get("last_validated") and p["last_validated"] != "1970-01-01T00:00:00Z"]
            last_sync    = max(last_vals) if last_vals else None
            roster_line  = f"✅ {active} active players • Last sync: {last_sync[:10] if last_sync else 'never'}" if players else "❌ Never synced"

            embed.add_field(
                name=f"{guild_name} • `{guild_id}`",
                value=(
                    f"**Leader role:** {role_mention}\n"
                    f"**API key:** {has_api_key}\n"
                    f"**Roster:** {roster_line}"
                ),
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ==========================================
    # SLASH COMMAND: SET_LIVE_LEADERBOARD
    # ==========================================

    @app_commands.command(
        name="set_live_leaderboard",
        description="Set up a live Battle leaderboard for a guild that auto-updates every hour.",
    )
    @app_commands.checks.has_any_role("Captain","Guild Leader","Dark Tech","Tech-Priest")
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
        import httpx

        print(f"[set_live_leaderboard] Invoked by {interaction.user} (id={interaction.user.id})")
        print(f"[set_live_leaderboard] Target channel: #{channel.name} (id={channel.id}, type={type(channel).__name__})")
        print(f"[set_live_leaderboard] Channel category: {channel.category} (id={channel.category_id})")

        # Log computed permissions for the bot in this channel
        bot_member = interaction.guild.me
        perms = channel.permissions_for(bot_member)
        print(f"[set_live_leaderboard] Bot permissions in #{channel.name}:")
        print(f"[set_live_leaderboard]   view_channel={perms.view_channel}")
        print(f"[set_live_leaderboard]   send_messages={perms.send_messages}")
        print(f"[set_live_leaderboard]   read_message_history={perms.read_message_history}")
        print(f"[set_live_leaderboard]   embed_links={perms.embed_links}")
        print(f"[set_live_leaderboard]   administrator={perms.administrator}")

        guilds     = load_guilds()
        guild_data = guilds.get(guild_id)
        if not guild_data:
            await interaction.followup.send(f"❌ Guild `{guild_id}` not found.", ephemeral=True)
            return

        guild_name = guild_data["name"]
        api_key    = guild_data.get("api_key")
        if not api_key:
            await interaction.followup.send(f"❌ Guild `{guild_id}` has no API key set.", ephemeral=True)
            return

        # Determine current season
        print(f"[set_live_leaderboard] Fetching current season from Tacticus API...")
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(
                    "https://api.tacticusgame.com/api/v1/guildRaid",
                    headers={"accept": "application/json", "X-API-KEY": api_key}
                )
                resp.raise_for_status()
                season = resp.json().get("season")
        except Exception as e:
            print(f"[set_live_leaderboard] Tacticus API error: {e}")
            await interaction.followup.send(f"❌ Could not determine current season: {e}", ephemeral=True)
            return

        print(f"[set_live_leaderboard] Current season: {season}")

        data_dir    = get_guild_data_path(guild_id)
        data, err   = load_leaderboard_file(data_dir / f"highest_hits_season_{season}.json")
        if err:
            print(f"[set_live_leaderboard] Leaderboard file error: {err}")
            await interaction.followup.send(f"❌ {err} — run `/update_leaderboard` first.", ephemeral=True)
            return

        # Post one message per tier and collect message IDs
        message_ids = {}
        for tier in TIER_CHOICES:
            messages = build_battle_messages(data, season, tier, guild_id, guild_name)
            if not messages:
                content = f"📊 **{guild_name} — {tier.name} — No data yet**"
            else:
                content = "\n\n".join(messages)
            print(f"[set_live_leaderboard] Sending {tier.name} message to #{channel.name} (len={len(content)})...")
            try:
                msg = await channel.send(content)
                message_ids[tier.value] = msg.id
                print(f"[set_live_leaderboard] Sent {tier.name} message (id={msg.id})")
            except discord.Forbidden as e:
                print(f"[set_live_leaderboard] Forbidden sending {tier.name} message: {e}")
                await interaction.followup.send(
                    f"❌ Missing permissions to send messages in {channel.mention}.\n"
                    f"Error: `{e}`",
                    ephemeral=True,
                )
                return
            except Exception as e:
                print(f"[set_live_leaderboard] Error sending {tier.name} message: {e}")
                await interaction.followup.send(f"❌ Unexpected error sending message: {e}", ephemeral=True)
                return

        # Save config
        live = load_live_leaderboards()
        live[f"guild:{guild_id}"] = {
            "channel_id": channel.id,
            "guild_id":   guild_id,
            "messages":   message_ids,
        }
        save_live_leaderboards(live)
        print(f"[set_live_leaderboard] Config saved. message_ids={message_ids}")

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
    @app_commands.checks.has_any_role("Captain","Guild Leader","Dark Tech","Tech-Priest")
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
        import httpx

        guilds = load_guilds()
        if not guilds:
            await interaction.followup.send("❌ No guilds registered yet.", ephemeral=True)
            return

        # Determine current season using first guild
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

        # Build merged cluster data
        merged = {}
        for gid, gdata in guilds.items():
            data_dir   = get_guild_data_path(gid)
            data, err  = load_leaderboard_file(data_dir / f"highest_hits_season_{season}.json")
            if err or not data:
                continue
            id_to_name = get_player_list(gid)
            guild_name = gdata["name"]
            for boss_id, encounter_dict in data.get("boss_hits", {}).items():
                for e_index, tiers in encounter_dict.items():
                    for tier_key, entries in tiers.items():
                        bucket = merged.setdefault(boss_id, {}).setdefault(e_index, {}).setdefault(tier_key, [])
                        for entry in entries:
                            user_id      = entry.get("user_id", "Unknown")
                            user_display = id_to_name.get(user_id, str(user_id)[:8])
                            bucket.append({**entry, "_display": user_display, "_guild": guild_name})

        # Sort and trim each bucket
        from config import TIER_CHOICES as TC
        for boss_id, encounter_dict in merged.items():
            for e_index, tiers in encounter_dict.items():
                for tier_key in tiers:
                    limit = 5 if e_index == "0" else 1
                    tiers[tier_key] = sorted(tiers[tier_key], key=lambda e: e["damage"], reverse=True)[:limit]

        # Post one message per tier
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
            if not messages:
                content = f"🌐 **Cluster — {tier.name} — No data yet**"
            else:
                content = "\n\n".join(messages)
            msg = await channel.send(content)
            message_ids[tier.value] = msg.id

        # Save config
        live = load_live_leaderboards()
        live["cluster"] = {
            "channel_id": channel.id,
            "messages":   message_ids,
        }
        save_live_leaderboards(live)

        await interaction.followup.send(
            f"✅ Live Cluster leaderboard set up in {channel.mention}!\n"
            f"It will automatically update every hour.",
            ephemeral=True,
        )


async def setup_admin(bot: commands.Bot, player_service: PlayerService):
    await bot.add_cog(AdminCog(bot, player_service))