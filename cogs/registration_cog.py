import httpx
import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional

from config import REQUIRED_ROLES
from guilds import load_player_apis, save_player_apis, load_capped_state, save_capped_state, load_guilds
from embeds import guild_autocomplete

TACTICUS_PLAYER_URL = "https://api.tacticusgame.com/api/v1/player"


class RegistrationCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ==========================================
    # SLASH COMMAND: REGISTER
    # ==========================================

    @app_commands.command(
        name="register",
        description="Register your Tacticus API key to enable token cap notifications.",
    )
    @app_commands.checks.has_any_role("Veteran of the Long War")
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

        # If a target_user is provided, check the caller has an admin role
        if target_user is not None:
            caller_roles = {role.name for role in interaction.user.roles}
            if not caller_roles.intersection(REQUIRED_ROLES):
                await interaction.followup.send(
                    "❌ You don't have permission to register on behalf of another user.",
                    ephemeral=True,
                )
                return

        # Validate guild_id exists in registry
        guilds = load_guilds()
        if guild_id not in guilds:
            await interaction.followup.send(
                f"❌ Guild `{guild_id}` not found. Please select a valid guild from the list.",
                ephemeral=True,
            )
            return

        guild_name = guilds[guild_id]["name"]

        # Validate the API key by hitting the Tacticus player endpoint
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

        # Use target_user's ID if provided, otherwise the caller's
        discord_id    = str(target_user.id) if target_user else str(interaction.user.id)
        player_apis   = load_player_apis()
        already_exist = discord_id in player_apis

        # Check if this api_key is already registered to a different Discord ID
        for existing_id, existing_data in player_apis.items():
            existing_key = existing_data.get("api_key") if isinstance(existing_data, dict) else existing_data
            if existing_key == api_key and existing_id != discord_id:
                await interaction.followup.send(
                    "❌ This API key is already registered to another user.",
                    ephemeral=True,
                )
                return

        player_apis[discord_id] = {"api_key": api_key, "guild_id": guild_id}
        save_player_apis(player_apis)

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
    # SLASH COMMAND: UNREGISTER
    # ==========================================

    @app_commands.command(
        name="unregister",
        description="Remove your Tacticus API key registration.",
    )
    @app_commands.checks.has_any_role("Veteran of the Long War")
    @app_commands.describe(
        target_user="(Admin only) Unregister on behalf of another Discord user",
    )
    async def unregister(
        self,
        interaction: discord.Interaction,
        target_user: Optional[discord.Member] = None,
    ):
        await interaction.response.defer(ephemeral=True)

        if target_user is not None:
            caller_roles = {role.name for role in interaction.user.roles}
            if not caller_roles.intersection(REQUIRED_ROLES):
                await interaction.followup.send(
                    "❌ You don't have permission to unregister another user.",
                    ephemeral=True,
                )
                return

        discord_id  = str(target_user.id) if target_user else str(interaction.user.id)
        player_apis = load_player_apis()

        if discord_id not in player_apis:
            target = target_user.mention if target_user else "You are"
            await interaction.followup.send(
                f"❌ {target} not currently registered.",
                ephemeral=True,
            )
            return

        del player_apis[discord_id]
        save_player_apis(player_apis)

        # Clear capped state so they don't get a ghost ping if re-registered later
        capped_state = load_capped_state()
        if discord_id in capped_state:
            del capped_state[discord_id]
            save_capped_state(capped_state)

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
    # SLASH COMMAND: CHECK_REGISTERED_MEMBERS
    # ==========================================

    @app_commands.command(
        name="check_registered_members",
        description="List all players who have registered their Tacticus API key.",
    )
    @app_commands.checks.has_any_role("Dark Tech", "Tech-Priest", "Guild Leader", "Captain")
    async def check_registered_members(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        player_apis = load_player_apis()

        if not player_apis:
            await interaction.followup.send(
                "❌ No players have registered yet.",
                ephemeral=True,
            )
            return

        guilds   = load_guilds()
        by_guild = {}
        no_guild = []

        for discord_id, data in player_apis.items():
            gid = data.get("guild_id") if isinstance(data, dict) else None
            if gid and gid in guilds:
                by_guild.setdefault(gid, []).append(discord_id)
            else:
                no_guild.append(discord_id)

        # Send header then one embed per guild to avoid Discord's 6000 char embed limit
        await interaction.followup.send(
            f"📋 **Registered Players — {len(player_apis)} total**",
            ephemeral=True,
        )

        for gid, members in by_guild.items():
            guild_name = guilds[gid]["name"]
            embed = discord.Embed(
                title=f"{guild_name} ({len(members)})",
                description="\n".join(f"• <@{did}>" for did in members),
                color=discord.Color.blurple(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

        if no_guild:
            embed = discord.Embed(
                title=f"No Guild Assigned ({len(no_guild)})",
                description="\n".join(f"• <@{did}>" for did in no_guild),
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)


async def setup_registration(bot: commands.Bot):
    await bot.add_cog(RegistrationCog(bot))