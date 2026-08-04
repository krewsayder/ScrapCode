import httpx
import discord
from discord import app_commands
from discord.ext import commands

from bot import guild_keys
# The MODULE, not `repo` by value. `bot/guilds.py` binds `repo = build_repo()`
# at import time; a test (or a rollback that rebuilds it) swaps that attribute,
# and a `from bot.guilds import repo` in this module would keep pointing at the
# object that existed when the cog was first imported. Every wrapper in
# `bot/guilds.py` resolves `repo` as a module global at CALL time, so reaching
# it through the module is the same late binding they get.
from bot import guilds as guild_registry
from bot.guilds import (
    load_guilds,
    save_guilds,
    load_guild_binding,
    load_live_leaderboards,
    save_live_leaderboards,
    load_player_list,
    add_cluster_role,
    add_guild_member_role,
    repo,
)
from bot.repository import QuarantineTombstone
from bot.embeds import guild_autocomplete, encounter_limit
from bot.permissions import require_tier, check_tier
from bot.services.chronicl3r.player_service import PlayerService
from bot.services.tacticus.guild_client import GuildSnapshot, KeyStatus

# Shown in place of a display field the guild service did not send. Display
# fields are never load-bearing (ADR-008 D1): a guild that has not set a tag
# must still render, still bind, and never look like an error (AC-001.6).
EM_DASH = "—"

# The first EIGHT characters of an identifier. No more than this ever reaches
# an embed, and no key value ever does — AC-005.4 / KPI-6 is 0 leaks.
IDENTIFIER_PREFIX_LENGTH = 8

# `identity_bound_at` is ISO-8601 UTC; the officer is asking "was this checked
# recently", so the date answers it and the time only crowds a field that
# already carries a name, a tag and an identifier.
ISO_DATE_LENGTH = 10

# What `/register_guild` says when the id is taken for any reason OTHER than
# quarantine. Unchanged wording, moved to a constant so the quarantine refusal
# sits beside it rather than inside the command body.
_ALREADY_REGISTERED = (
    "❌ A guild with ID `{guild_id}` is already registered. "
    "Choose a different ID or contact an admin to remove the existing entry."
)

# What a guild with no registered key is told. Unchanged wording, and
# deliberately NOT what a quarantined guild is told: a quarantined guild has a
# key, and sending its officer here sends them to `/register_guild`.
_NO_API_KEY = "❌ Guild `{guild_id}` has no API key set."

# The way out of quarantine, written once. Every surface that refuses a
# quarantined guild ends on this text, so the destructive route can never be
# named on one surface and the recovery on another. `/deregister_guild` erases
# the guild's whole raid history (AC-009.4) and launders the quarantine on
# re-registration (AC-009.5); `/update_guild_key` probes the SUBMITTED key
# before storing anything (AC-003.6) and is the only exit.
_QUARANTINE_EXIT = (
    "Run `/update_guild_key guild_id:{guild_id}` with the guild's real key — "
    "that is the only exit from quarantine. Do NOT deregister and re-register: "
    "that erases the guild's raid history and clears the quarantine without "
    "ever fixing the key."
)

# `guild_keys.unusable_key_reason`'s vocabulary in the words an officer acts
# on. A rendering table, not a second definition — the discrimination itself
# is made once, in the chokepoint. `.get(reason, reason)` rather than `[]`: a
# reason this table has not learned yet must degrade to the log vocabulary
# inside a Discord reply, never to a KeyError inside a refusal.
_UNUSABLE_KEY_WORDS = {
    guild_keys.QUARANTINED: "quarantined — its stored key resolves to another guild",
    guild_keys.NO_KEY_REGISTERED: "no API key registered",
}

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
            # AC-008.1: an already-registered id is refused here, before any
            # probe, so no roster was ever at risk on this branch. What WAS at
            # risk is the officer: "remove the existing entry" is
            # `/deregister_guild`, which destroys the guild's entire raid
            # history (AC-009.4) and launders the quarantine on
            # re-registration (AC-009.5). An officer one command away from
            # `/update_guild_key` must not be routed through the two most
            # destructive commands in this cog, so when the id is taken
            # BECAUSE the guild is quarantined, the reply says so and names
            # the exit instead.
            quarantined = _quarantine_refusal(
                load_guild_binding(server_id, guild_id), guild_id
            )
            await interaction.followup.send(
                quarantined or _ALREADY_REGISTERED.format(guild_id=guild_id),
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

        # AC-009.5 — SURFACED, NEVER USED TO REFUSE. The registration above
        # has already happened: an admin re-registering a slug is doing
        # something legitimate, and the operator's decision of 2026-08-02 is
        # that deregistering destroys data by design. What must not happen
        # silently is the ADOPTION — trust-on-first-use (DDD-8) is about to
        # bind whatever the submitted key resolves to, and if that slug was
        # quarantined before it was deregistered, the identity being adopted
        # may be the drift that caused the quarantine. Sent as its own
        # message, before the probe, so the history reaches the admin even
        # when the probe fails or the guild is quarantined again.
        history = _quarantine_history_warning(
            guild_registry.repo.list_quarantine_tombstones(server_id, guild_id),
            guild_id,
        )
        if history:
            await interaction.followup.send(history, ephemeral=True)

        try:
            # One call does both jobs, which is why it is here and not in two
            # places. The key was installed a line ago and has never been
            # probed, so this is trust-on-first-use (DDD-8) at the cheapest
            # possible moment — the operator learns what the key resolves to
            # NOW instead of waiting up to an hour for the next cycle. The
            # same probe returns the roster snapshot `refresh_guild` needs:
            # `PlayerService` no longer fetches for itself (DDD-2) and takes a
            # snapshot, never a key.
            snapshot = await guild_keys.verify_and_resolve(
                server_id, guild_id, enforce=False
            )
            await self.player_service.refresh_guild(server_id, guild_id, snapshot)
            await interaction.followup.send(
                f"✅ Player list populated for **{name}**.\n"
                f"• ID: `{guild_id}`\n"
                f"• Leader role: {role.mention}\n"
                f"• Bound to: {_registration_binding_line(snapshot)}",
                ephemeral=True,
            )
        except guild_keys.GuildQuarantined:
            # AC-008.1c — NARROW THE SWALLOW. Step 07-01 moved the quarantine
            # gate inside `verify_and_resolve`, so a slug still carrying a
            # quarantined binding (rollback residue: the binding outlived the
            # guild row) refuses here before any request. The broad handler
            # below caught that refusal and rendered it as "player list could
            # not be fetched" — which reads as a transient outage, is false,
            # and leaves the operator nothing to act on. A refusal must reach
            # them AS a refusal, naming the only exit.
            #
            # This branch is keyed on quarantine and nothing else: an UNBOUND
            # guild never raises, so trust-on-first-use (DDD-8) still adopts
            # on the line above. That distinction is the point of the command.
            await interaction.followup.send(
                _quarantine_refusal_text(
                    load_guild_binding(server_id, guild_id), guild_id
                ),
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(
                f"⚠️ Guild registered but player list could not be fetched: {e}",
                ephemeral=True,
            )

    # ==========================================
    # SLASH COMMAND: UPDATE_GUILD_KEY
    # ==========================================

    @app_commands.command(
        name="update_guild_key",
        description=(
            "Replace a guild's Tacticus API key. The new key is verified "
            "against the guild before it is stored."
        ),
    )
    @require_tier("admin")
    @app_commands.describe(
        guild_id="The registered guild whose key is being replaced",
        api_key="The new Tacticus API key for the guild",
        force="Rebind the guild if the new key resolves to a different Tacticus guild",
    )
    @app_commands.autocomplete(guild_id=guild_autocomplete)
    async def update_guild_key(
        self,
        interaction: discord.Interaction,
        guild_id: str,
        api_key: str,
        force: bool = False,
    ):
        # AC-003.6: probe the SUBMITTED key before storing anything. An
        # unverified key is never written, so a fat-fingered paste cannot
        # recreate the incident. All probe/store/release logic stays in
        # `install_guild_key` (step 04-01); this command is a thin renderer
        # plus the unknown-guild guard and the admin permission gate.
        await interaction.response.defer(ephemeral=True)

        server_id = interaction.guild_id
        registered = load_guilds(server_id)
        if guild_id not in registered:
            # Unknown-guild guard BEFORE the probe (AC-003.10): the command
            # must never become an oracle for whether an arbitrary key is
            # valid, so the probe does not fire when the guild is not
            # registered. The reply names the real guild ids so an operator
            # mid-incident does not need another round trip to discover them.
            registered_ids = ", ".join(registered) or "(none)"
            await interaction.followup.send(
                f"❌ No guild `{guild_id}` is registered. "
                f"Registered guilds: {registered_ids}",
                ephemeral=True,
            )
            return

        try:
            result = await guild_keys.install_guild_key(
                server_id, guild_id, api_key, force=force
            )
        except guild_keys.GuildKeyAlreadyRegisteredError as collision:
            # AC-009.1 / AC-009.2 / KPI-6 — THE REFUSAL IS RENDERED HERE, and
            # that is the whole point of catching it. Uncaught, it reaches
            # `main.py:91-101`, which does BOTH `print(f"Command error:
            # {error}")` and `followup.send(f"❌ An error occurred: {error}")`.
            # The exception it interpolated before step 08-01 was a raw
            # `IntegrityError` with the bound parameters inlined — the Fernet
            # ciphertext of the key AND the full 64-hex `api_key_hmac` — into
            # a Discord message, `discord.log` and the systemd journal, three
            # copies of material KPI-6 records as appearing in zero records.
            # A refusal that depends on that handler rendering something clean
            # is one `str()` change away from disclosing them again, so the
            # exception must never get there.
            #
            # Caught in the cog rather than translated in the policy layer
            # because naming the holder needs `registered`, a read this
            # command has already done, and because a typed error the renderer
            # cannot silently forget is what keeps the refusal from falling
            # back to the generic handler.
            await interaction.followup.send(
                _collision_refusal(collision.guild_id, registered), ephemeral=True
            )
            return
        # KPI-6: no key value ever reaches the reply. The renderer names the
        # resolved guild and the outcome, never the submitted or stored key.
        await interaction.followup.send(
            _render_update_result(result), ephemeral=True
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
        # AC-009.5 — WRITTEN BEFORE THE DELETION, AND ON THE PATH THAT
        # PERFORMS IT. `save_guilds` drops the `guilds` row; `PRAGMA
        # foreign_keys=ON` plus `ondelete="CASCADE"` then destroys the
        # binding, which is the only record that this guild was ever
        # quarantined. Reading it afterwards is not possible and recording it
        # at quarantine time would miss every binding written before this
        # shipped, so the tombstone is taken from the binding one line before
        # it ceases to exist. It sits here rather than at command invocation
        # so a later confirmation gate (AC-009.4) cannot leave a tombstone
        # behind for a deletion the admin declined.
        _record_quarantine_history(server_id, guild_id)
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
            binding      = load_guild_binding(server_id, guild_id)
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
                    f"**API key:** {has_api_key}{_binding_suffix(binding)}\n"
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

        # THE chokepoint (ADR-008 D3). Season discovery needs the key string
        # and nothing else, so it takes `active_key` — sync, storage-only, no
        # probe (DDD-7). Asking Tacticus who this key belongs to just to learn
        # a season number would double the call for an answer this command
        # never reads.
        credential = guild_keys.active_key(server_id, guild_id)
        if credential is None:
            # AC-008.5b — the refusal has to say WHICH of the two it is. A
            # quarantined guild HAS a key; "has no API key set" sends the
            # officer to `/register_guild`, the one command that overwrites
            # the roster (AC-008.1). The guild NAMED in the command is the one
            # resolved here and there is no fall-through to apply (AC-008.5):
            # a sibling's key would build this guild's board over data the bot
            # has stopped updating, which is a product decision nobody has
            # made (UI-9).
            await interaction.followup.send(
                _unusable_key_refusal(server_id, guild_id), ephemeral=True
            )
            return

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(
                    "https://api.tacticusgame.com/api/v1/guildRaid",
                    headers={"accept": "application/json", "X-API-KEY": credential}
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

        # Same chokepoint, same reason as `set_live_leaderboard`: this reads a
        # season number, never a roster, so it pays for no probe (DDD-7).
        # Refusing here rather than sending an empty credential is deliberate —
        # an unregistered key produces a 401 the officer then has to interpret,
        # and the answer to "why did this fail" would be a Tacticus error
        # message about a request this command should never have made.
        #
        # AC-008.4 / KPI-5 — this used to read `next(iter(guilds))`: an
        # arbitrary guild, unrelated to anything the officer asked for, whose
        # single unusable key disabled the cluster-wide board for every healthy
        # sibling. The season is a CLUSTER fact and any healthy key can answer
        # it, so the fall-through `_current_season` already carries (DDD-7 /
        # AC-004.7) applies here unchanged.
        # The loop is INLINE and not a helper on purpose. `KeyConsumptionSite`
        # declares WHICH production function consumes a key, AC-004.6 is
        # parametrized over that inventory, and the enclosing function is the
        # coordinate — so extracting these three lines moves this command out
        # of the set of sites proven to refuse a quarantined guild and moves a
        # private helper into it. `test_the_key_consumption_inventory_matches_
        # production` fails on exactly that, which is the AST chokepoint doing
        # its job rather than an inconvenience to route around.
        credential = None
        for candidate_id in guilds:
            credential = guild_keys.active_key(server_id, candidate_id)
            if credential is not None:
                break

        if credential is None:
            await interaction.followup.send(
                _cluster_season_refusal(server_id, guilds), ephemeral=True
            )
            return

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(
                    "https://api.tacticusgame.com/api/v1/guildRaid",
                    headers={"accept": "application/json", "X-API-KEY": credential}
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



def _collision_refusal(holder_guild_id: str, registered: dict) -> str:
    """Name the guild that already holds the key — and carry nothing else.

    A Tacticus key identifies exactly one guild, and `guilds.api_key_hmac` is
    UNIQUE table-global, so installing a key a sibling already holds cannot
    succeed. The admin's next move depends entirely on WHICH guild that is:
    told only "no", they retry the same paste forever; told the guild, they
    either fix the paste or free the key. That one fact is the whole payload —
    no plaintext, no ciphertext, no hmac, no SQL, no bound parameters.

    Deliberately not a suggestion to deregister anything. `/deregister_guild`
    destroys the named guild's entire raid history (AC-009.4), which is a
    catastrophic answer to "you pasted the wrong key".
    """
    return (
        f"❌ That API key is already registered to "
        f"{_holder_label(holder_guild_id, registered)}. A Tacticus key belongs "
        f"to one guild, so no key was replaced and nothing was changed.\n"
        f"Check the key you pasted. If two guilds' keys were swapped, install "
        f"the other guild's key on it first to free this one."
    )


def _holder_label(holder_guild_id: str, registered: dict) -> str:
    """The holding guild, by display name and slug (AC-009.1).

    The slug is always shown and the display name only when this cluster knows
    it: `uq_guilds_api_key_hmac` spans the whole table, so the holder may be a
    guild registered on a DIFFERENT Discord server, which this command's
    registry read cannot name. Showing the slug alone there is honest;
    inventing a name would not be.

    An EMPTY holder id is `repository_sqlalchemy._HOLDER_VANISHED` — the
    holder row disappeared between the lookup and the flush, so there is
    genuinely no guild to name. The refusal still has to read as a refusal
    about a key that is already registered: an admin told "that key is taken"
    without a name can retry, an admin sent the ciphertext cannot un-disclose
    it.
    """
    if not holder_guild_id:
        return "another guild"
    name = (registered.get(holder_guild_id) or {}).get("name")
    if not name:
        return f"`{holder_guild_id}`"
    return f"**{name}** (`{holder_guild_id}`)"


def _render_update_result(result) -> str:
    """Render an `InstallResult` to the `/update_guild_key` reply text.

    The cog is a thin renderer over `install_guild_key` (step 04-01): every
    outcome the policy can RETURN has a reply here, and no reply carries the
    submitted or stored key (KPI-6). The reply names the resolved guild for a
    successful install (AC-003.1) and names BOTH guilds on a mismatch refused
    without force (AC-003.3).

    The one refusal that does NOT arrive as an `InstallResult` is the key
    collision — it is a typed exception caught by name in the command and
    rendered by `_collision_refusal` (AC-009.1). That asymmetry is deliberate:
    an outcome a renderer can forget falls through to `main.py`'s generic
    handler, and on that path the generic handler is the disclosure.
    """
    from bot.services.tacticus.guild_client import ProbeOutcome

    if result.outcome is ProbeOutcome.MATCH:
        name = result.identity.name if result.identity else "the guild"
        return f"✅ Key updated for {name}."
    if result.outcome is ProbeOutcome.MISMATCH and result.forced:
        name = result.identity.name if result.identity else "the new guild"
        return f"✅ Key installed and rebound to {name}."
    if result.outcome is ProbeOutcome.MISMATCH:
        bound = result.bound_name or "the bound guild"
        observed = result.identity.name if result.identity else "the submitted key"
        return (
            f"❌ The new key resolves to {observed}, which does not match "
            f"{bound}. Use `force=True` to rebind."
        )
    if result.outcome is ProbeOutcome.DEAD:
        return "❌ The key was rejected (dead)."
    # UNREACHABLE or UNVERIFIABLE — an untrusted key must not enter on an
    # outage (AC-003.6); both report a verification failure, never a key value.
    return "❌ Could not verify the key."


# ==========================================
# Binding rendering (AC-001.2 / AC-005.2 / AC-005.4)
# ==========================================

def _binding_suffix(binding) -> str:
    """What this guild's key resolves to, and when that was last verified.

    Renders ALONGSIDE the API-key presence check, never instead of it. An
    unbound guild returns the empty string, which is what keeps AC-005.3 true
    by construction rather than by a second test on the key: a guild with no
    key can never be bound — adoption requires a probe that SUCCEEDED — so the
    `❌ Missing` rendering this feature must leave alone is reached by exactly
    the path it always was, and a guild whose key has simply never been probed
    yet reads the same as it did yesterday.

    A quarantined guild (AC-005.1) renders ⛔ and BOTH tags so an officer can
    tell at a glance what the key was pointing at and what it should point at.
    The quarantine date's year answers "when did this happen" without crowding
    the field. Never the full uuid: `quarantine_reason` carries one for drift
    re-reporting, and `/view_config` is officer-tier and non-ephemeral (KPI-6).
    """
    if binding.is_unbound:
        return ""
    if binding.key_status == KeyStatus.QUARANTINED.value:
        return f" ⛔ {_quarantine_line(binding)}"
    return (
        f" {_identity_label(binding)}"
        f" • verified {_verified_on(binding.identity_bound_at)}"
    )


def _identity_label(binding) -> str:
    """Name, tag and the first eight characters of the identifier.

    All three, always. The comparison is on the identifier alone (DDD-1), but
    an identifier tells an officer nothing about what to do next — and both
    guilds in the 2026-07-28 incident carried the 【UNDV】 alliance prefix, so
    the name alone does not tell them apart either. Never more than eight
    characters of the identifier and never a key value: KPI-6 is 0 leaks.
    """
    identifier = binding.tacticus_guild_id or ""
    return (
        f"{binding.tacticus_guild_name or EM_DASH} "
        f"【{binding.tacticus_guild_tag or EM_DASH}】 "
        f"({identifier[:IDENTIFIER_PREFIX_LENGTH] or EM_DASH})"
    )


def _verified_on(identity_bound_at: str | None) -> str:
    """The date half of an ISO-8601 UTC instant, or an em dash."""
    return (identity_bound_at or EM_DASH)[:ISO_DATE_LENGTH]


# ==========================================
# Quarantine rendering (AC-005.1 / KPI-6)
# ==========================================

_UUID_PATTERN = (
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)


def _quarantine_line(binding) -> str:
    """Both tags and the quarantine date, never the full uuid.

    `quarantine_reason` (set by `bot.guild_keys._quarantine_reason`) embeds the
    FULL observed uuid for drift re-reporting, so the reason is NEVER rendered
    raw. The bound tag comes from the binding; the observed tag is extracted
    from the reason's `resolves to 【TAG】` marker. A short-form reason without
    the marker is sanitized of any uuid and rendered as-is so both tags remain
    visible (KPI-6: 0 full-identifier leaks across every state).
    """
    bound_tag = binding.tacticus_guild_tag or EM_DASH
    observed_tag = _observed_tag_from_reason(binding.quarantine_reason or "")
    date = (binding.quarantined_at or EM_DASH)[:ISO_DATE_LENGTH]
    return f"Quarantined: bound 【{bound_tag}】 resolves to 【{observed_tag}】 ({date})"


def _observed_tag_from_reason(reason: str) -> str:
    """The observed guild's tag, without the uuid the reason carries.

    The production reason shape is `key drift: bound 【T】 N but resolves to
    【T】 N — observed=UUID`; the FIRST `【...】` after `resolves to` is the
    observed tag (the name's `【alliance】` prefix follows it, not precedes it).
    A reason without the marker (test short-form) is sanitized of uuids and
    returned verbatim so both tags remain visible without leaking an id.
    """
    import re

    match = re.search(r"resolves to 【([^】]*)】", reason)
    if match:
        return match.group(1) or EM_DASH
    return _strip_uuids(reason) or EM_DASH


def _strip_uuids(text: str) -> str:
    """Remove any full uuid from `text` (KPI-6)."""
    import re

    return re.sub(_UUID_PATTERN, "", text).strip()


# ==========================================
# Quarantine history across the CASCADE (AC-009.5 / UI-11)
# ==========================================

def _record_quarantine_history(discord_server_id: int, guild_id: str) -> None:
    """Keep the quarantine after the binding that recorded it is destroyed.

    Called from `/deregister_guild` immediately BEFORE `save_guilds` drops the
    `guilds` row. `PRAGMA foreign_keys=ON` plus `ondelete="CASCADE"` then takes
    the binding with it, and the binding is the only place a quarantine is
    written. The tombstone table has no foreign key, so it is the one thing
    that outlives the deletion.

    Reads the binding rather than being called from `guild_keys.quarantine`:
    every binding quarantined before this shipped would otherwise have no
    history at all, and the state that matters at deregistration time is what
    the binding SAYS NOW, not what some earlier command did.

    A guild that is not quarantined leaves no tombstone. Writing one for every
    deregistration would put the word "quarantined" in front of an admin
    re-registering a guild that never was, which trains them to ignore the
    warning that matters.

    `guild_keys._observed_uuid_from_reason` and `guild_keys._utc_now` are
    reached through their own module rather than re-implemented here. Both the
    `— observed=` marker and the millisecond ISO-8601 shape KPI-2 compares as
    strings are DEFINED in `bot/guild_keys.py`; a second parser or a second
    `strftime` in this file is the drift UI-5 is a record of.
    """
    binding = load_guild_binding(discord_server_id, guild_id)
    if binding.key_status != KeyStatus.QUARANTINED.value:
        return
    guild_registry.repo.record_quarantine_tombstone(
        discord_server_id,
        QuarantineTombstone(
            guild_id=guild_id,
            tacticus_guild_id=binding.tacticus_guild_id,
            tacticus_guild_tag=binding.tacticus_guild_tag,
            tacticus_guild_name=binding.tacticus_guild_name,
            observed_tacticus_guild_id=guild_keys._observed_uuid_from_reason(
                binding.quarantine_reason or ""
            ),
            quarantine_reason=binding.quarantine_reason,
            quarantined_at=binding.quarantined_at,
            recorded_at=guild_keys._utc_now(),
        ),
    )


def _quarantine_history_warning(
    tombstones: list[QuarantineTombstone], guild_id: str,
) -> str:
    """What a re-registered slug's history says, or the empty string.

    SURFACED, NEVER ENFORCING. The registration has already succeeded by the
    time this is rendered, and that is deliberate: refusing would break a
    legitimate re-registration, and the operator's decision of 2026-08-02 is
    that `/deregister_guild` destroys data by design. What this closes is the
    silence — trust-on-first-use (DDD-8) is about to adopt whatever the
    submitted key resolves to, and on a slug that was quarantined before it
    was deregistered, that identity may be the drift which caused the
    quarantine. Two commands, and the incident becomes the new truth.

    Both identifiers are truncated to eight characters and the reason is never
    rendered raw — it embeds the full observed uuid for drift re-reporting
    (KPI-6: 0 full-identifier leaks).
    """
    if not tombstones:
        return ""
    latest = tombstones[-1]
    observed = latest.observed_tacticus_guild_id or ""
    return (
        f"⚠️ `{guild_id}` was QUARANTINED before it was deregistered, and that "
        f"history outlived the guild ({len(tombstones)} on record, most recent "
        f"{(latest.quarantined_at or EM_DASH)[:ISO_DATE_LENGTH]}).\n"
        f"• It was bound to {_identity_label(latest)} and its key had drifted "
        f"to ({observed[:IDENTIFIER_PREFIX_LENGTH] or EM_DASH}).\n"
        f"• Nothing was refused — the registration went ahead. Check that the "
        f"key you just submitted is this guild's real key before trusting the "
        f"binding reported below; `/update_guild_key` replaces it."
    )


# ==========================================
# Quarantine refusal (AC-008.1 / AC-008.1c)
# ==========================================

def _quarantine_refusal(binding, guild_id: str) -> str | None:
    """The refusal a quarantined guild's officer needs, or None (AC-008.1).

    KEYED ON `key_status` AND NOTHING ELSE, which is the whole discrimination
    this step exists to make. `/register_guild` carries its probe so the
    operator learns at registration time what a brand-new key resolves to
    instead of waiting up to an hour for the next cycle (DDD-8,
    trust-on-first-use). A gate that refused every guild without a verified
    binding would close the write hole and take that with it — an UNBOUND
    guild has no stored identity to be wrong about, and "never checked" is not
    "known bad". So this returns None for every state except quarantine,
    including states no migration ever wrote.

    `/register_guild` calls it on BOTH refusal paths: the already-registered
    branch reads the binding directly, and the post-probe branch arrives via
    `GuildQuarantined` from the chokepoint.
    """
    if binding.key_status != KeyStatus.QUARANTINED.value:
        return None
    return _quarantine_refusal_text(binding, guild_id)


def _quarantine_refusal_text(binding, guild_id: str) -> str:
    """Name the problem, then name the one command that ends it.

    THE ROUTING IS THE POINT. The two replies this text replaces sent an
    officer somewhere harmful: "contact an admin to remove the existing entry"
    is `/deregister_guild`, which destroys the guild's whole raid history
    (AC-009.4) and launders the quarantine when the guild is registered again
    (AC-009.5); "player list could not be fetched" reads as an outage and
    invites a retry that will refuse identically forever. `/update_guild_key`
    is the only exit — it probes the SUBMITTED key before storing anything
    (AC-003.6) and releases the quarantine when the key agrees — so it is
    named explicitly, and the destructive route is named as one to avoid
    rather than left for the officer to rediscover.

    Renders through `_quarantine_line`, the same renderer `/view_config` uses,
    so both surfaces describe a quarantine identically and neither can leak a
    full identifier (KPI-6: `quarantine_reason` carries the observed uuid by
    design, and that renderer is where it is stripped).
    """
    return (
        f"⛔ Guild `{guild_id}` is quarantined, so nothing was fetched and "
        f"nothing was written.\n"
        f"• {_quarantine_line(binding)}\n"
        + _QUARANTINE_EXIT.format(guild_id=guild_id)
    )


def _unusable_key_refusal(server_id: int, guild_id: str) -> str:
    """The ONE rendering of "this guild's key cannot be used" (AC-008.5b).

    Both leaderboard commands refuse through this, and which of the two
    refusals it is comes from `guild_keys.unusable_key_reason` — the single
    definition, shared with the hourly cycle. The cog does not compare
    `key_status` itself: a second comparison here is the drift that produced
    the defect being fixed, where one surface called a quarantine a missing
    key and routed the officer into `/register_guild`.
    """
    if guild_keys.unusable_key_reason(server_id, guild_id) == guild_keys.QUARANTINED:
        return _quarantine_refusal_text(
            load_guild_binding(server_id, guild_id), guild_id
        )
    return _NO_API_KEY.format(guild_id=guild_id)


def _cluster_season_refusal(server_id: int, guild_ids) -> str:
    """No key in the cluster can answer the season — say so, per guild.

    The fall-through has to end in an explained refusal rather than a silent
    skip or an empty board (AC-008.6). It names each guild's reason from the
    same `guild_keys.unusable_key_reason` the fall-through itself skipped on,
    so the reply cannot describe a cluster the command did not walk, and it
    names `/update_guild_key` when any guild is quarantined — reporting a
    quarantine as a missing key is what routes an officer into
    `/register_guild` and the roster overwrite (AC-008.1).
    """
    reasons = [
        (guild_id, guild_keys.unusable_key_reason(server_id, guild_id))
        for guild_id in guild_ids
    ]
    lines = "\n".join(
        f"• `{guild_id}` — {_UNUSABLE_KEY_WORDS.get(reason, reason)}"
        for guild_id, reason in reasons
    )
    refusal = (
        "❌ No registered guild has a usable key, so the current season could "
        f"not be determined.\n{lines}"
    )
    if any(reason == guild_keys.QUARANTINED for _, reason in reasons):
        return f"{refusal}\n{_QUARANTINE_EXIT.format(guild_id='<guild_id>')}"
    return refusal


def _registration_binding_line(snapshot: GuildSnapshot) -> str:
    """What `/register_guild` just bound the new key to.

    A probe that produced no identity says so rather than reporting a bind
    that did not happen: `verify_and_resolve` leaves the binding untouched on
    UNVERIFIABLE, UNREACHABLE and DEAD, and a line claiming otherwise is the
    reassuring-but-false signal this whole feature exists to remove.
    """
    if snapshot.identity is None:
        return (
            f"nothing yet — the key could not be verified "
            f"({snapshot.outcome.value}). The hourly cycle retries."
        )
    return (
        f"{snapshot.identity.name or EM_DASH} "
        f"【{snapshot.identity.tag or EM_DASH}】 ({snapshot.identity.short})"
    )


async def setup_admin(bot: commands.Bot, player_service: PlayerService):
    await bot.add_cog(AdminCog(bot, player_service))