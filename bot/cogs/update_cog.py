from dataclasses import dataclass

import httpx
import discord
from discord import app_commands
from discord.ext import commands

from bot import guild_keys
from bot.permissions import require_tier
from bot.guilds import load_guilds, load_guild_binding, load_player_list
from bot.tracker import process_api_response
from bot.embeds import guild_autocomplete
from bot.services.chronicl3r.player_service import PlayerService
from bot.services.tacticus.guild_client import GuildSnapshot, ProbeOutcome

TACTICUS_RAID_URL = "https://api.tacticusgame.com/api/v1/guildRaid/{season}"

_TACTICUS_TIMEOUT_SECONDS = 20.0

# Shown in place of a display field the guild service did not send. Display
# fields are never load-bearing (ADR-008 D1), so an absent tag must read as
# "not set", never as an error.
EM_DASH = "—"

# The first EIGHT characters of an identifier — the whole of it never reaches
# a Discord message (AC-005.4 / KPI-6).
IDENTIFIER_PREFIX_LENGTH = 8


class UpdateCog(commands.Cog):
    def __init__(self, bot: commands.Bot, player_service: PlayerService):
        self.bot            = bot
        self.player_service = player_service

    # ==========================================
    # SLASH COMMAND: UPDATE_LEADERBOARD
    # ==========================================

    @app_commands.command(
        name="update_leaderboard",
        description="Fetches raid data from the Tacticus API and updates local records.",
    )
    @require_tier("officer")
    @app_commands.describe(
        guild_id="The guild to update",
        season="The season number to update (e.g. 94)",
    )
    @app_commands.autocomplete(guild_id=guild_autocomplete)
    async def update_leaderboard(self, interaction: discord.Interaction, guild_id: str, season: int):
        await interaction.response.defer(thinking=True)

        server_id  = interaction.guild_id
        # Normalised ONCE, and every later use reads the normalised value.
        # `guilds.get(guild_id.strip().lower())` used to look the guild up
        # under one id and then hand the RAW one to `process_api_response`,
        # which writes the rows — so `/update_leaderboard Word_Bearers` found
        # the guild and filed its season under a guild nobody can read back.
        # The chokepoint takes the same id the rows are written under, or the
        # key it checks is not the key the data came from.
        guild_id   = guild_id.strip().lower()
        guilds     = load_guilds(server_id)
        guild_data = guilds.get(guild_id)

        if not guild_data:
            await interaction.followup.send(
                f"❌ No guild found with ID `{guild_id}`. "
                f"Registered guilds: {', '.join(f'`{g}`' for g in guilds) or 'none'}"
            )
            return

        guild_name = guild_data["name"]

        verified = await self._verified_key(server_id, guild_id)
        if verified is None:
            await interaction.followup.send(f"❌ Guild `{guild_id}` has no API key set.")
            return

        url = TACTICUS_RAID_URL.format(season=season)

        try:
            async with httpx.AsyncClient(timeout=_TACTICUS_TIMEOUT_SECONDS) as client:
                response = await client.get(url, headers=verified.headers)
                response.raise_for_status()
                api_data = response.json()

            process_api_response(api_data, season, server_id, guild_id)

            await self._register_unknown_players(server_id, guild_id, api_data)

            await interaction.followup.send(
                f"✅ Leaderboard for **{guild_name}** — Season **{season}** updated successfully."
                + verified.note
            )

        except httpx.HTTPStatusError as e:
            await interaction.followup.send(
                f"❌ API Error for **{guild_name}**: HTTP {e.response.status_code}\n```{e.response.text}```"
            )
        except Exception as e:
            await interaction.followup.send(f"⚠️ An unexpected error occurred: {str(e)}")

    # ==========================================
    # SLASH COMMAND: UPDATE_ALL
    # ==========================================

    @app_commands.command(
        name="update_all",
        description="Fetches raid data for ALL registered guilds and updates local records.",
    )
    @require_tier("officer")
    @app_commands.describe(season="The season number to update (e.g. 94)")
    async def update_all(self, interaction: discord.Interaction, season: int):
        await interaction.response.defer(thinking=True)

        server_id = interaction.guild_id
        guilds    = load_guilds(server_id)
        if not guilds:
            await interaction.followup.send("❌ No guilds registered yet.")
            return

        url     = TACTICUS_RAID_URL.format(season=season)
        results = []

        async with httpx.AsyncClient(timeout=_TACTICUS_TIMEOUT_SECONDS) as client:
            for guild_id, guild_data in guilds.items():
                guild_name = guild_data["name"]

                verified = await self._verified_key(server_id, guild_id)
                if verified is None:
                    results.append(f"⚠️ **{guild_name}** — skipped, no API key set.")
                    continue

                try:
                    response = await client.get(url, headers=verified.headers)
                    response.raise_for_status()
                    api_data = response.json()

                    process_api_response(api_data, season, server_id, guild_id)

                    await self._register_unknown_players(server_id, guild_id, api_data)
                    results.append(
                        f"✅ **{guild_name}** — updated successfully." + verified.note
                    )

                except httpx.HTTPStatusError as e:
                    results.append(f"❌ **{guild_name}** — HTTP {e.response.status_code}: {e.response.text[:80]}")
                except Exception as e:
                    results.append(f"❌ **{guild_name}** — {str(e)[:80]}")

        await interaction.followup.send(
            f"**Season {season} update complete:**\n" + "\n".join(results)
        )

    async def _verified_key(self, server_id: int, guild_id: str) -> "_VerifiedKey | None":
        """THE chokepoint (ADR-008 D3) — the only way this cog reaches a key.

        Both commands enter here, so a third one cannot quietly take a
        different route: a guard on six of seven call sites is not "mostly
        fixed", it is a silent contamination path that looks fixed.

        None means refuse. `active_key` is sync and storage-only (DDD-7), so a
        guild with no usable key costs no probe and no Tacticus round trip.
        """
        credential = guild_keys.active_key(server_id, guild_id)
        if credential is None:
            return None

        # Read the binding BEFORE the probe: a mismatch has to name the guild
        # the key WAS bound to, and `verify_and_resolve` refreshes the display
        # fields on its way through, so afterwards is too late.
        bound_before = load_guild_binding(server_id, guild_id)
        snapshot = await guild_keys.verify_and_resolve(
            server_id, guild_id, enforce=False
        )
        return _VerifiedKey(credential, bound_before, snapshot)

    async def _register_unknown_players(self, server_id: int, guild_id: str, api_data: dict) -> None:
        known   = set(load_player_list(server_id, guild_id).get("players", {}).keys())
        seen    = {e["userId"] for e in api_data.get("entries", []) if "userId" in e}
        unknown = seen - known
        for user_id in unknown:
            try:
                saved = await self.player_service.ensure_player_in_list(server_id, guild_id, user_id)
                if saved:
                    print(f"[UpdateCog] Saved unknown player {user_id} to player list")
            except Exception as e:
                print(f"[UpdateCog] Failed to save unknown player {user_id}: {e}")


# ==========================================
# The chokepoint's result, and how it reads
# ==========================================

@dataclass(frozen=True)
class _VerifiedKey:
    """A usable credential, and what that same key just resolved to.

    The three travel together because separating them is how they drift: a
    credential used without its probe result is exactly the read this feature
    exists to eliminate, and a probe result carried without the binding that
    preceded it cannot name the guild the key WAS bound to.
    """

    credential: str
    bound_before: object
    snapshot: GuildSnapshot

    @property
    def headers(self) -> dict:
        return {"accept": "application/json", "X-API-KEY": self.credential}

    @property
    def note(self) -> str:
        return _identity_note(self.bound_before, self.snapshot)


def _identity_note(bound_before, snapshot: GuildSnapshot) -> str:
    """What the probe found, appended to the command's reply — or nothing.

    Silence on a clean match is deliberate. A manual command that
    congratulates itself on every run trains the officer to skim, and the one
    line that matters is then the one they skim past.

    Slice 01 REPORTS and does not block: the rows are already written by the
    time this renders, and saying so plainly is the point. Enforcement ships
    in Slice 03, after `/update_guild_key` (Slice 02) provides the only exit
    from quarantine (ADR-008 D3).
    """
    if snapshot.outcome is ProbeOutcome.MISMATCH:
        bound = _short_identity(
            bound_before.tacticus_guild_tag, bound_before.tacticus_guild_id
        )
        resolved = _short_identity(snapshot.identity.tag, snapshot.identity.uuid)
        return (
            f"\n⚠️ Identity mismatch — this key is bound to {bound} but now "
            f"resolves to {resolved}. The data above was still ingested this "
            f"slice — run `/update_guild_key` to correct it."
        )
    if snapshot.outcome is ProbeOutcome.UNVERIFIABLE:
        return (
            "\n⚠️ Identity verification is offline: the guild service answered "
            "without an identifier, so this key could not be checked. The tag "
            "is never compared as a substitute."
        )
    if snapshot.outcome is ProbeOutcome.DEAD:
        return (
            f"\n❌ The guild service refused this key (HTTP {snapshot.status}) "
            f"when checking its identity. Install a working one with "
            f"`/update_guild_key`."
        )
    if snapshot.outcome is ProbeOutcome.UNREACHABLE:
        return (
            f"\n❌ The guild service is unreachable ({snapshot.error}), so the "
            f"identity behind this key was not checked."
        )
    return ""


def _short_identity(tag: str | None, tacticus_guild_id: str | None) -> str:
    """A guild's tag and the first eight characters of its identifier.

    Never the whole identifier and never a key value — KPI-6 is 0 leaks. Both
    guilds in the 2026-07-28 incident carried the 【UNDV】 alliance prefix, so
    the tag alone does not tell them apart and the prefix is what actually
    does. A missing tag renders as an em dash: display fields are never
    load-bearing and must never look like an error.
    """
    identifier = tacticus_guild_id or ""
    return (
        f"【{tag or EM_DASH}】 "
        f"({identifier[:IDENTIFIER_PREFIX_LENGTH] or EM_DASH})"
    )


async def setup_update(bot: commands.Bot, player_service: PlayerService):
    await bot.add_cog(UpdateCog(bot, player_service))