import asyncio
import logging

import httpx
import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional

from bot.guilds import load_player_registrations, save_player_registrations, load_capped_state, save_capped_state, load_guilds, repo
from bot.embeds import guild_autocomplete, resolve_members
from bot.obs import emit_structured
from bot.permissions import require_guild_member, require_tier, check_tier

# This module's OWN logger: `emit_structured` keeps records under the emitting
# module's name, so registration records stay greppable by origin.
logger = logging.getLogger(__name__)

TACTICUS_PLAYER_URL = "https://api.tacticusgame.com/api/v1/player"

# Bounded concurrency for bulk key validation. Avoids the resource-exhaustion
# pattern of firing one client per registration simultaneously (the shape that
# makes cap_detect's 108-at-once bursts fragile). One shared AsyncClient + a
# semaphore caps in-flight requests.
_VALIDATE_CONCURRENCY = 10

# HTTP statuses Tacticus returns for an invalid key. The live `register`
# command checks only 401, but revoked/expired keys have been observed returning
# 403 (verified on 2026-07-25) — treat both as "dead".
_DEAD_KEY_STATUSES = (401, 403)

# Chronicl3r reply lines. Always ScrapCode's own words: Chronicl3r's `detail`
# and any identity it returns never reach a player.
CHRONICLER_LINKED_LINE = "🔗 Your key is linked in Chronicl3r."
CHRONICLER_RETRY_LINE = (
    "⏳ Your key is not linked in Chronicl3r yet — the bot will retry automatically."
)
CHRONICLER_INSUFFICIENT_DATA_LINE = (
    "⚠️ Your key was not linked in Chronicl3r: it needs at least 3 levelled "
    "showcase units to match your key to a player."
)
CHRONICLER_STORAGE_NOTICE = (
    "Your key is also stored in Chronicl3r, our backend data store, "
    "which may allow roster analytics. Unregistering deletes it."
)
CHRONICLER_LINK_EVENT = "chronicler.key.link"
CHRONICLER_UNLINK_EVENT = "chronicler.key.unlink"

# What a raised link/unlink call is recorded as: the same as an unreachable
# Chronicl3r, since the outcome for the player is identical.
_RAISED_CODE = "transport"


async def _probe_api_keys(
    api_keys: dict[str, str],
) -> dict[str, tuple[int | None, str | None]]:
    """Probe each ``{discord_id: api_key}`` against the Tacticus player endpoint.

    Returns ``{discord_id: (status_code, error_type)}`` where ``status_code`` is
    the HTTP status (int) or ``None`` on a transport error, and ``error_type``
    is ``type(e).__name__`` on an exception or ``None``. A single shared
    ``httpx.AsyncClient`` is used with a semaphore so at most
    ``_VALIDATE_CONCURRENCY`` requests are in flight at once, and the exception
    type is preserved (not discarded as ``{e}``). Entries with a falsy key are
    skipped.
    """
    if not api_keys:
        return {}

    sem = asyncio.Semaphore(_VALIDATE_CONCURRENCY)

    async with httpx.AsyncClient(timeout=15.0) as client:
        async def _check(discord_id: str, key: str) -> tuple[str, int | None, str | None]:
            async with sem:
                try:
                    resp = await client.get(
                        TACTICUS_PLAYER_URL,
                        headers={"accept": "application/json", "X-API-KEY": key},
                    )
                    return discord_id, resp.status_code, None
                except Exception as e:
                    return discord_id, None, type(e).__name__

        outcomes = await asyncio.gather(
            *[_check(d, k) for d, k in api_keys.items() if k]
        )

    return {d: (code, err) for d, code, err in outcomes}


def _format_key_validation(
    results: dict[str, tuple[int | None, str | None]],
    name_map: dict[str, str],
    guild_name: str,
) -> str:
    """Render probe results as an officer-facing summary string.

    ``results`` comes from :func:`_probe_api_keys`; ``name_map`` maps a
    ``discord_id`` to a display name, falling back to the raw ID when absent.
    Classifies each result as valid (200), dead key (401/403), unreachable
    (transport error), or other API error, and lists the non-valid categories by
    name so an officer knows exactly who to chase.
    """
    valid       = [d for d, (c, _) in results.items() if c == 200]
    dead        = [d for d, (c, _) in results.items() if c in _DEAD_KEY_STATUSES]
    unreachable = [d for d, (c, _) in results.items() if c is None]
    other       = [
        d for d, (c, _) in results.items()
        if c is not None and c != 200 and c not in _DEAD_KEY_STATUSES
    ]

    def _label(discord_id: str) -> str:
        return name_map.get(discord_id, f"`{discord_id}`")

    lines = [
        f"🔍 **Key validation — {guild_name}** ({len(results)} checked)",
        f"✅ Valid: {len(valid)}",
    ]
    if dead:
        lines.append(
            f"❌ Dead key — ask to re-register: {len(dead)} — "
            + ", ".join(_label(d) for d in dead)
        )
    if unreachable:
        lines.append(
            "⚠️ Could not check (network/timeout): "
            + ", ".join(f"{_label(d)} ({results[d][1]})" for d in unreachable)
        )
    if other:
        lines.append(
            "⚠️ API error: "
            + ", ".join(f"{_label(d)} (HTTP {results[d][0]})" for d in other)
        )
    if not dead and not unreachable and not other:
        lines.append("All keys valid ✅")
    return "\n".join(lines)


def _link_outcome(status: int | None, code: str | None) -> tuple[str, str]:
    """The reply line and the log code for one link outcome. Reads no key."""
    if status == 200:
        return CHRONICLER_LINKED_LINE, "linked"
    if code == "insufficient_data":
        return CHRONICLER_INSUFFICIENT_DATA_LINE, code
    return CHRONICLER_RETRY_LINE, code or "unexpected"


def _log_chronicler(event: str, level: int, server_id, discord_id: str, code: str) -> None:
    emit_structured(
        logger, level, event,
        server_id=server_id, discord_id=discord_id, code=code,
    )


class RegistrationCog(commands.Cog):
    def __init__(self, bot: commands.Bot, player_service=None):
        # `player_service` defaults to None so callers that predate the
        # Chronicl3r link keep constructing the cog; `register` then skips
        # Chronicl3r entirely (no link, no link line, no storage notice).
        self.bot = bot
        self.player_service = player_service

    reg = app_commands.Group(name="registration", description="Player registration commands")

    # ==========================================
    # SLASH COMMAND: REGISTRATION REGISTER
    # ==========================================

    @reg.command(
        name="register",
        description="Register your Tacticus API key for token cap pings; it is also stored in Chronicl3r.",
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
        previous_entry = registrations.get(discord_id)
        previous_key = previous_entry.get("api_key") if isinstance(previous_entry, dict) else None

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
            message = f"✅ {target_user.mention} has been {action} successfully in **{guild_name}**!"
        elif already_exist:
            message = f"✅ Your registration has been updated! Guild: **{guild_name}**"
        else:
            message = f"✅ You've been registered in **{guild_name}**! You'll now receive a ping when your raid tokens are capped."

        if self.player_service is not None:
            # Best-effort: the registration above is already saved, and nothing
            # Chronicl3r does (or raises) may fail the command. The old key is
            # unlinked BEFORE the new one is linked.
            if previous_key and previous_key != api_key:
                try:
                    unlinked = await self.player_service.unlink_key(previous_key)
                    unlink_code = None if unlinked else "unlink_failed"
                except Exception:
                    unlink_code = _RAISED_CODE
                if unlink_code is not None:
                    _log_chronicler(CHRONICLER_UNLINK_EVENT, logging.WARNING,
                                    server_id, discord_id, unlink_code)
            try:
                result = await self.player_service.link_key(api_key)
                line, link_code = _link_outcome(result.status, result.code)
            except Exception:
                line, link_code = _link_outcome(None, _RAISED_CODE)
            level = logging.INFO if line == CHRONICLER_LINKED_LINE else logging.WARNING
            _log_chronicler(CHRONICLER_LINK_EVENT, level, server_id, discord_id, link_code)
            message = "\n".join((message, line, CHRONICLER_STORAGE_NOTICE))

        await interaction.followup.send(message, ephemeral=True)

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
        user_id="(Admin only) Raw Discord ID — use when the player has left the server",
    )
    async def unregister(
        self,
        interaction: discord.Interaction,
        target_user: Optional[discord.Member] = None,
        user_id: Optional[str] = None,
    ):
        await interaction.response.defer(ephemeral=True)

        server_id = interaction.guild_id

        # Require admin to use target_user or user_id
        if target_user is not None or user_id is not None:
            cluster = repo.load(server_id)
            user_role_ids = {r.id for r in interaction.user.roles}
            admin_roles = set(cluster.role_tiers.get("admin", []))
            if not interaction.user.guild_permissions.administrator and not (user_role_ids & admin_roles):
                await interaction.followup.send(
                    "❌ You don't have permission to unregister another user.",
                    ephemeral=True,
                )
                return

        if target_user is not None:
            discord_id = str(target_user.id)
        elif user_id is not None:
            discord_id = user_id.strip()
        else:
            discord_id = str(interaction.user.id)

        registrations = load_player_registrations(server_id)

        if discord_id not in registrations:
            target = target_user.mention if target_user else f"`{discord_id}`" if user_id else "You are"
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
        elif user_id:
            await interaction.followup.send(
                f"✅ User `{discord_id}` has been unregistered successfully.",
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

        all_ids = [did for members in by_guild.values() for did in members]
        present, gone = await resolve_members(interaction.guild, all_ids)
        member_map = {did: member for did, member in present}
        gone_set   = set(gone)

        total        = sum(len(v) for v in by_guild.values())
        multi_guild  = len(by_guild) > 1

        if multi_guild:
            await interaction.followup.send(
                f"📋 **Registered Players — {total} total**",
                ephemeral=True,
            )

        for gid, members in by_guild.items():
            guild_name    = guilds.get(gid, {}).get("name", f"`{gid}`")
            on_server     = [did for did in members if did in member_map]
            off_server    = [did for did in members if did in gone_set]

            embed = discord.Embed(
                title=f"📋 Registered Players — {guild_name} ({len(members)})" if not multi_guild else f"{guild_name} ({len(members)})",
                color=discord.Color.blurple(),
            )
            if on_server:
                embed.description = "\n".join(f"• @{member_map[did].display_name}" for did in on_server)
            if off_server:
                embed.add_field(
                    name=f"🚪 No longer on server ({len(off_server)})",
                    value="\n".join(f"• `{did}`" for did in off_server),
                    inline=False,
                )
            await interaction.followup.send(embed=embed, ephemeral=True)

    # ==========================================
    # SLASH COMMAND: REGISTRATION VALIDATE_KEYS
    # ==========================================

    @reg.command(
        name="validate_keys",
        description="Check registered Tacticus API keys for a guild; report dead ones.",
    )
    @require_tier("officer")
    @app_commands.describe(guild_id="Guild to validate (required)")
    @app_commands.autocomplete(guild_id=guild_autocomplete)
    async def validate_keys(
        self,
        interaction: discord.Interaction,
        guild_id: str,
    ):
        """Officer-only bulk key check. Probes every registration in a guild
        against the Tacticus player endpoint and reports which keys are dead
        (401/403), which couldn't be reached (with the exception type), and which
        hit an API error — naming the members so an officer knows who to chase.
        Bounded concurrency; no key is ever printed."""
        await interaction.response.defer(ephemeral=True)

        server_id = interaction.guild_id
        guilds = load_guilds(server_id)
        if guild_id not in guilds:
            await interaction.followup.send(
                f"❌ Guild `{guild_id}` not found. Please select a valid guild from the list.",
                ephemeral=True,
            )
            return
        guild_name = guilds[guild_id]["name"]

        registrations = load_player_registrations(server_id)
        filtered = {
            d: v for d, v in registrations.items()
            if isinstance(v, dict) and v.get("guild_id") == guild_id
        }
        if not filtered:
            await interaction.followup.send(
                f"❌ No registered players in **{guild_name}**.",
                ephemeral=True,
            )
            return

        # Resolve display names live (cache=False) so dead keys name a person,
        # and departed members are flagged rather than silently dropped.
        present, gone = await resolve_members(interaction.guild, list(filtered))
        name_map = {d: m.display_name for d, m in present}
        for d in gone:
            name_map[d] = f"`{d}` (left server)"

        api_keys = {d: v.get("api_key") for d, v in filtered.items()}
        no_key = [d for d, v in filtered.items() if not v.get("api_key")]
        results = await _probe_api_keys({d: k for d, k in api_keys.items() if k})

        msg = _format_key_validation(results, name_map, guild_name)
        if no_key:
            msg += "\n⚠️ No key on file: " + ", ".join(name_map[d] for d in no_key)
        await interaction.followup.send(msg, ephemeral=True)


async def setup_registration(bot: commands.Bot, player_service=None):
    await bot.add_cog(RegistrationCog(bot, player_service))