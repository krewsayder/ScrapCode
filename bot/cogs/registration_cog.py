import httpx
import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional

from bot.guilds import load_player_registrations, save_player_registrations, load_capped_state, save_capped_state, load_guilds, repo
from bot.embeds import guild_autocomplete
from bot.permissions import require_guild_member, require_tier, check_tier

TACTICUS_PLAYER_URL = "https://api.tacticusgame.com/api/v1/player"


class RegistrationCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    reg = app_commands.Group(name="registration", description="Player registration commands")

    # ==========================================
    # SLASH COMMAND: REGISTRATION REGISTER
    # ==========================================

    @reg.command(
        name="register",
        description="Register your Tacticus API key to enable token cap notifications.",
    )
    @require_guild_member()
    @app_commands.describe(
        api_key="Your personal Tacticus API key",
        guild_id="Your guild",
        target_user="(Admin only) Register on behalf of another Discord user",
    )
    @app_commands.autocomplete(guild_id=guild_autocomplete)
    async def register(
        self,
        interaction: discord.Interaction,
        api_key: str,
        guild_id: str,
        target_user: Optional[discord.Member] = None,
    ):
        await interaction.response.defer(ephemeral=True)

        server_id = interaction.guild_id

        if target_user is not None:
            cluster = repo.load(server_id)
            user_role_ids = {r.id for r in interaction.user.roles}
            admin_roles = set(cluster.role_tiers.get("admin", []))
            if not interaction.user.guild_permissions.administrator and not (user_role_ids & admin_roles):
                await interaction.followup.send(
                    "❌ You don't have permission to register on behalf of another user.",
                    ephemeral=True,
                )
                return

        guilds = load_guilds(server_id)
        if guild_id not in guilds:
            await interaction.followup.send(
                f"❌ Guild `{guild_id}` not found. Please select a valid guild from the list.",
                ephemeral=True,
            )
            return

        guild_name = guilds[guild_id]["name"]

        headers = {"accept": "application/json", "X-API-KEY": api_key}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(TACTICUS_PLAYER_URL, headers=headers)
                response.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                await interaction.followup.send(
                    "❌ Invalid API key — Tacticus rejected it. Please double-check and try again.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    f"❌ Tacticus API returned an error: HTTP {e.response.status_code}",
                    ephemeral=True,
                )
            return
        except Exception as e:
            await interaction.followup.send(
                f"❌ Could not reach the Tacticus API: {e}",
                ephemeral=True,
            )
            return

        discord_id    = str(target_user.id) if target_user else str(interaction.user.id)
        registrations = load_player_registrations(server_id)
        already_exist = discord_id in registrations

        # Check if this api_key is already registered to a different Discord ID
        for existing_id, existing_data in registrations.items():
            if existing_data.get("api_key") == api_key and existing_id != discord_id:
                await interaction.followup.send(
                    "❌ This API key is already registered to another user.",
                    ephemeral=True,
                )
                return

        registrations[discord_id] = {"api_key": api_key, "guild_id": guild_id}
        save_player_registrations(server_id, registrations)

        if target_user:
            action = "updated" if already_exist else "registered"
            await interaction.followup.send(
                f"✅ {target_user.mention} has been {action} successfully in **{guild_name}**!",
                ephemeral=True,
            )
        elif already_exist:
            await interaction.followup.send(
                f"✅ Your registration has been updated! Guild: **{guild_name}**",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"✅ You've been registered in **{guild_name}**! You'll now receive a ping when your raid tokens are capped.",
                ephemeral=True,
            )

    # ==========================================
    # SLASH COMMAND: REGISTRATION UNREGISTER
    # ==========================================

    @reg.command(
        name="unregister",
        description="Remove your Tacticus API key registration.",
    )
    @require_guild_member()
    @app_commands.describe(
        target_user="(Admin only) Unregister on behalf of another Discord user",
    )
    async def unregister(
        self,
        interaction: discord.Interaction,
        target_user: Optional[discord.Member] = None,
    ):
        await interaction.response.defer(ephemeral=True)

        server_id = interaction.guild_id

        if target_user is not None:
            cluster = repo.load(server_id)
            user_role_ids = {r.id for r in interaction.user.roles}
            admin_roles = set(cluster.role_tiers.get("admin", []))
            if not interaction.user.guild_permissions.administrator and not (user_role_ids & admin_roles):
                await interaction.followup.send(
                    "❌ You don't have permission to unregister another user.",
                    ephemeral=True,
                )
                return

        discord_id    = str(target_user.id) if target_user else str(interaction.user.id)
        registrations = load_player_registrations(server_id)

        if discord_id not in registrations:
            target = target_user.mention if target_user else "You are"
            await interaction.followup.send(
                f"❌ {target} not currently registered.",
                ephemeral=True,
            )
            return

        del registrations[discord_id]
        save_player_registrations(server_id, registrations)

        capped_state = load_capped_state(server_id)
        if discord_id in capped_state:
            del capped_state[discord_id]
            save_capped_state(server_id, capped_state)

        if target_user:
            await interaction.followup.send(
                f"✅ {target_user.mention} has been unregistered successfully.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "✅ You've been unregistered and will no longer receive token cap pings.",
                ephemeral=True,
            )

    # ==========================================
    # SLASH COMMAND: REGISTRATION MOVE
    # ==========================================

    @reg.command(
        name="move",
        description="Move a registered player to a different guild.",
    )
    @app_commands.describe(
        target_user="The player to move",
        guild_id="The guild to move them to",
    )
    @app_commands.autocomplete(guild_id=guild_autocomplete)
    async def move(
        self,
        interaction: discord.Interaction,
        target_user: discord.Member,
        guild_id: str,
    ):
        await interaction.response.defer(ephemeral=True)

        if not await check_tier(interaction, "officer"):
            await interaction.followup.send(
                "❌ You don't have permission to use this command.",
                ephemeral=True,
            )
            return

        server_id     = interaction.guild_id
        guilds        = load_guilds(server_id)
        registrations = load_player_registrations(server_id)
        discord_id    = str(target_user.id)

        if guild_id not in guilds:
            await interaction.followup.send(
                f"❌ Guild `{guild_id}` not found. Please select a valid guild from the list.",
                ephemeral=True,
            )
            return

        if discord_id not in registrations:
            await interaction.followup.send(
                f"❌ {target_user.mention} is not currently registered.",
                ephemeral=True,
            )
            return

        guild_name = guilds[guild_id]["name"]
        registrations[discord_id]["guild_id"] = guild_id
        save_player_registrations(server_id, registrations)

        await interaction.followup.send(
            f"✅ {target_user.mention} has been moved to **{guild_name}**.",
            ephemeral=True,
        )

    # ==========================================
    # SLASH COMMAND: REGISTRATION LIST
    # ==========================================

    @reg.command(
        name="list",
        description="List all registered players, optionally filtered by guild.",
    )
    @require_tier("officer")
    @app_commands.describe(guild_id="Filter by guild (optional)")
    @app_commands.autocomplete(guild_id=guild_autocomplete)
    async def list_registrations(
        self,
        interaction: discord.Interaction,
        guild_id: Optional[str] = None,
    ):
        await interaction.response.defer(ephemeral=True)

        server_id     = interaction.guild_id
        guilds        = load_guilds(server_id)
        registrations = load_player_registrations(server_id)

        if not registrations:
            await interaction.followup.send("❌ No players have registered yet.", ephemeral=True)
            return

        if guild_id:
            filtered = {k: v for k, v in registrations.items() if isinstance(v, dict) and v.get("guild_id") == guild_id}
            if not filtered:
                guild_name = guilds.get(guild_id, {}).get("name", guild_id)
                await interaction.followup.send(f"❌ No registered players in **{guild_name}**.", ephemeral=True)
                return
            by_guild = {guild_id: list(filtered.keys())}
        else:
            by_guild: dict[str, list] = {}
            for discord_id, data in registrations.items():
                gid = data.get("guild_id") if isinstance(data, dict) else None
                by_guild.setdefault(gid, []).append(discord_id)

        total = sum(len(v) for v in by_guild.values())
        await interaction.followup.send(
            f"📋 **Registered Players — {total} total**",
            ephemeral=True,
        )

        for gid, members in by_guild.items():
            guild_name = guilds.get(gid, {}).get("name", f"`{gid}`")
            embed = discord.Embed(
                title=f"{guild_name} ({len(members)})",
                description="\n".join(f"• <@{did}>" for did in members),
                color=discord.Color.blurple(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)


async def setup_registration(bot: commands.Bot):
    await bot.add_cog(RegistrationCog(bot))