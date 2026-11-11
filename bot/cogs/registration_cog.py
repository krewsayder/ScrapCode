import asyncio
import logging
import os
import re
from datetime import datetime, timezone

import httpx
import discord
from discord import app_commands
from discord.ext import commands, tasks
from typing import Optional

from bot.guilds import load_player_registrations, save_player_registrations, load_capped_state, save_capped_state, load_guilds, repo
from bot.embeds import guild_autocomplete, resolve_members
from bot.obs import emit_structured
from bot.permissions import require_guild_member, require_tier, check_tier
from bot.services.chronicl3r.key_sync import KeySyncAction, KeySyncSchedule
from bot.cogs.chronicler_key_removal import RAISED_CODE, remove_key_from_chronicler

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
CHRONICLER_UNLINKED_LINE = "🗑️ The key was removed from Chronicl3r."
CHRONICLER_UNLINK_UNCONFIRMED_LINE = (
    "⚠️ Removal of the key from Chronicl3r could not be confirmed."
)
CHRONICLER_LINK_EVENT = "chronicler.key.link"

# Background key sync: backfills keys registered before the Chronicl3r link
# existed. One link per tick = 30/h, half of Chronicl3r's per-account link
# throttle, leaving the rest for live registrations. Lookups have their own
# far larger bucket.
KEY_SYNC_SWITCH_ENV = "CHRONICL3R_KEY_SYNC"
KEY_SYNC_LOOKUPS_PER_TICK = 20
KEY_SYNC_LOOKUP_EVENT = "chronicler.key_sync.lookup"
KEY_SYNC_LINK_EVENT = "chronicler.key_sync.link"
KEY_SYNC_PAUSED_EVENT = "chronicler.key_sync.paused"
KEY_SYNC_TICK_FAILED_EVENT = "chronicler.key_sync.tick_failed"

# Outcomes that settle a player normally; anything else is logged as a warning.
_SETTLED_OUTCOMES = frozenset({"linked", "not_linked"})


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


def _lookup_sync_outcome(result) -> str | None:
    """The schedule outcome for one lookup. A held but invalid key is re-linked."""
    if result.status == 200:
        valid = bool(result.profile) and result.profile.get("has_valid_api_key") is True
        return "linked" if valid else "not_linked"
    return result.code


def _link_sync_outcome(result) -> str | None:
    return "linked" if result.status == 200 else result.code


def _key_sync_switched_off() -> bool:
    return os.getenv(KEY_SYNC_SWITCH_ENV, "").strip().lower() == "off"


# Acting on ANOTHER player's registration (register, unregister, move) is an
# officer-tier action. `check_tier("officer")` also admits the admin tier and
# Discord Administrators (ADR-005). Acting on yourself stays a guild-member action.
ON_BEHALF_TIER = "officer"

REGISTRATION_REGISTER_EVENT = "registration.register"
REGISTRATION_UNREGISTER_EVENT = "registration.unregister"
REGISTRATION_MOVE_EVENT = "registration.move"

# A Discord user ID as an officer would paste it: the bare snowflake, or a
# mention (`<@id>` / `<@!id>`). Snowflakes are 17–20 digits.
_DISCORD_ID_PATTERN = re.compile(r"<@!?(\d{17,20})>|(\d{17,20})")


def _parse_discord_id(raw: str) -> str | None:
    """The bare ID when `raw` is a Discord user ID or mention, else None."""
    match = _DISCORD_ID_PATTERN.fullmatch(raw.strip())
    return (match.group(1) or match.group(2)) if match else None


def _holds_guild_member_role(member, guild_data: dict) -> bool:
    """Whether `member` holds one of the guild's configured member roles."""
    member_role_ids = set(guild_data.get("member_role_ids") or [])
    return any(role.id in member_role_ids for role in member.roles)


def _audit(event: str, interaction: discord.Interaction, discord_id: str, **fields) -> None:
    """One record per registration change: who acted, on whose registration, where.

    `discord_id` is the player whose registration changed — the same field the
    `chronicler.key.*` records use, so one grep for an ID shows its whole
    history. Identifiers and guild ids only: an API key never reaches here.
    """
    actor_id = str(interaction.user.id)
    emit_structured(
        logger, logging.INFO, event,
        server_id=interaction.guild_id,
        discord_id=discord_id,
        actor_id=actor_id,
        on_behalf=actor_id != discord_id,
        **fields,
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RegistrationCog(commands.Cog):
    def __init__(self, bot: commands.Bot, player_service=None):
        # `player_service` defaults to None so callers that predate the
        # Chronicl3r link keep constructing the cog; `register` then skips
        # Chronicl3r entirely (no link, no link line, no storage notice).
        self.bot = bot
        self.player_service = player_service
        # One schedule shared by the loop and `register`, so a key the
        # command just linked (or saw refused) is not posted again.
        self.key_sync = KeySyncSchedule()
        self.clock = _utc_now

    async def cog_load(self):
        # Started on load rather than in __init__: discord.py's Loop.start
        # needs a running event loop, which `add_cog` always provides.
        if self.player_service is not None:
            self.chronicler_key_sync.start()

    def cog_unload(self):
        self.chronicler_key_sync.cancel()

    # ==========================================
    # TASK: CHRONICL3R KEY SYNC (every 2 minutes)
    # ==========================================

    @tasks.loop(minutes=2)
    async def chronicler_key_sync(self):
        """Look up at most 20 due keys, then link at most one.

        Every player key read stays inside this function (the architecture
        chokepoint exempts it by name). A failing tick is logged by exception
        type only and never ends the loop.
        """
        if self.player_service is None or _key_sync_switched_off():
            return
        try:
            now = self.clock()
            if self.key_sync.paused_until(now) is not None:
                return
            # The FULL list every tick: the schedule forgets anyone missing.
            registrations = [
                (server_id, discord_id, entry.get("api_key"))
                for server_id in repo.list_server_ids()
                for discord_id, entry in load_player_registrations(server_id).items()
                if isinstance(entry, dict) and entry.get("api_key")
            ]

            due = self.key_sync.due(registrations, now)
            lookups = [r for r, action in due if action is KeySyncAction.LOOKUP]
            for registration in lookups[:KEY_SYNC_LOOKUPS_PER_TICK]:
                if not await self._settle(registration, KeySyncAction.LOOKUP, now):
                    return
            # Re-asked, so a key this tick's lookups found unlinked is linked now.
            links = [r for r, action in self.key_sync.due(registrations, now)
                     if action is KeySyncAction.LINK]
            if links:
                await self._settle(links[0], KeySyncAction.LINK, now)
        except Exception as exc:
            emit_structured(logger, logging.ERROR, KEY_SYNC_TICK_FAILED_EVENT,
                            error_type=type(exc).__name__)

    async def _settle(self, registration, action: KeySyncAction, now: datetime) -> bool:
        """Make one sync call and record it; False when the loop is now paused."""
        server_id, discord_id, key = registration
        if action is KeySyncAction.LOOKUP:
            call, outcome_of, event = (self.player_service.lookup_key,
                                       _lookup_sync_outcome, KEY_SYNC_LOOKUP_EVENT)
        else:
            call, outcome_of, event = (self.player_service.link_key,
                                       _link_sync_outcome, KEY_SYNC_LINK_EVENT)
        try:
            result = await call(key)
            outcome, retry_after = outcome_of(result), result.retry_after
        except Exception:
            outcome, retry_after = RAISED_CODE, None
        self.key_sync.record(server_id, discord_id, key, outcome, now, retry_after)
        code = outcome or "unexpected"
        level = logging.INFO if code in _SETTLED_OUTCOMES else logging.WARNING
        _log_chronicler(event, level, server_id, discord_id, code)
        paused_until = self.key_sync.paused_until(now)
        if paused_until is None:
            return True
        emit_structured(
            logger, logging.WARNING, KEY_SYNC_PAUSED_EVENT,
            code=code, pause_seconds=int((paused_until - now).total_seconds()),
        )
        return False

    async def _link_registered_key(self, server_id, discord_id: str, key: str) -> str:
        """Link a just-registered key and record it; the reply line. Never raises."""
        try:
            result = await self.player_service.link_key(key)
            line, link_code = _link_outcome(result.status, result.code)
            retry_after = result.retry_after
        except Exception:
            line, link_code = _link_outcome(None, RAISED_CODE)
            retry_after = None
        # Same mapping as the loop's link: "unexpected" pauses exactly as a
        # code-less answer would.
        self.key_sync.record(server_id, discord_id, key, link_code, self.clock(), retry_after)
        level = logging.INFO if line == CHRONICLER_LINKED_LINE else logging.WARNING
        _log_chronicler(CHRONICLER_LINK_EVENT, level, server_id, discord_id, link_code)
        return line

    @chronicler_key_sync.before_loop
    async def _before_chronicler_key_sync(self):
        await self.bot.wait_until_ready()

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
        target_user="(Officer only) Register on behalf of another Discord user",
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

        if target_user is not None and not await check_tier(interaction, ON_BEHALF_TIER):
            await interaction.followup.send(
                "❌ Only officers and admins can register on behalf of another user.",
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

        if target_user is not None and not _holds_guild_member_role(target_user, guilds[guild_id]):
            await interaction.followup.send(
                f"❌ {target_user.mention} doesn't have the **{guild_name}** member role. "
                "Pick the guild they belong to, or give them the role first.",
                ephemeral=True,
            )
            return

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

        previous_guild_id = previous_entry.get("guild_id") if isinstance(previous_entry, dict) else None
        registrations[discord_id] = {"api_key": api_key, "guild_id": guild_id}
        save_player_registrations(server_id, registrations)
        _audit(
            REGISTRATION_REGISTER_EVENT, interaction, discord_id,
            guild_id=guild_id, previous_guild_id=previous_guild_id,
            updated=already_exist, key_changed=bool(previous_key) and previous_key != api_key,
        )

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
                await remove_key_from_chronicler(
                    self.player_service, previous_key,
                    logger=logger, server_id=server_id, discord_id=discord_id,
                )
            line = await self._link_registered_key(server_id, discord_id, api_key)
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
        target_user="(Officer only) Unregister on behalf of another Discord user",
        user_id="(Officer only) Raw Discord ID — use when the player has left the server",
    )
    async def unregister(
        self,
        interaction: discord.Interaction,
        target_user: Optional[discord.Member] = None,
        user_id: Optional[str] = None,
    ):
        await interaction.response.defer(ephemeral=True)

        server_id = interaction.guild_id

        if (target_user is not None or user_id is not None) \
                and not await check_tier(interaction, ON_BEHALF_TIER):
            await interaction.followup.send(
                "❌ Only officers and admins can unregister another user.",
                ephemeral=True,
            )
            return

        if target_user is not None:
            discord_id = str(target_user.id)
        elif user_id is not None:
            discord_id = _parse_discord_id(user_id)
            if discord_id is None:
                await interaction.followup.send(
                    "❌ `user_id` must be a Discord user ID (17–20 digits) or a mention. "
                    "With Developer Mode on: right-click the user → Copy User ID.",
                    ephemeral=True,
                )
                return
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

        entry = registrations[discord_id]
        key = entry.get("api_key") if isinstance(entry, dict) else None

        del registrations[discord_id]
        save_player_registrations(server_id, registrations)

        capped_state = load_capped_state(server_id)
        if discord_id in capped_state:
            del capped_state[discord_id]
            save_capped_state(server_id, capped_state)

        _audit(
            REGISTRATION_UNREGISTER_EVENT, interaction, discord_id,
            guild_id=entry.get("guild_id") if isinstance(entry, dict) else None,
        )

        if target_user:
            message = f"✅ {target_user.mention} has been unregistered successfully."
        elif user_id:
            message = f"✅ User `{discord_id}` has been unregistered successfully."
        else:
            message = "✅ You've been unregistered and will no longer receive token cap pings."

        if key and self.player_service is not None:
            # Best-effort: the unregistration above is already saved, and
            # nothing Chronicl3r does (or raises) may fail the command. Removal
            # is only claimed when Chronicl3r confirmed it.
            removed = await remove_key_from_chronicler(
                self.player_service, key,
                logger=logger, server_id=server_id, discord_id=discord_id,
            )
            line = CHRONICLER_UNLINKED_LINE if removed else CHRONICLER_UNLINK_UNCONFIRMED_LINE
            message = "\n".join((message, line))

        await interaction.followup.send(message, ephemeral=True)

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

        if not await check_tier(interaction, ON_BEHALF_TIER):
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
        if not _holds_guild_member_role(target_user, guilds[guild_id]):
            await interaction.followup.send(
                f"❌ {target_user.mention} doesn't have the **{guild_name}** member role. "
                "Give them the role first.",
                ephemeral=True,
            )
            return

        previous_guild_id = registrations[discord_id].get("guild_id")
        registrations[discord_id]["guild_id"] = guild_id
        save_player_registrations(server_id, registrations)
        _audit(
            REGISTRATION_MOVE_EVENT, interaction, discord_id,
            guild_id=guild_id, previous_guild_id=previous_guild_id,
        )

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