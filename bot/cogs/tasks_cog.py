import asyncio
import logging

import httpx
import discord
from discord.ext import commands, tasks

from config import UPDATE_CHANNEL_ID
from bot import guild_keys
from bot.guilds import (
    load_guilds,
    load_guild_binding,
    get_player_list,
    load_player_registrations,
    load_capped_state,
    save_capped_state,
    load_live_leaderboards,
    save_live_leaderboards,
    repo,
)
from bot.obs import emit_structured
from bot.tracker import process_api_response
from bot.guilds import load_player_list
from bot.embeds import encounter_limit
from bot.services.chronicl3r.player_service import PlayerService
from bot.services.tacticus.guild_client import GuildSnapshot, KeyStatus, ProbeOutcome

logger = logging.getLogger(__name__)

TACTICUS_PLAYER_URL   = "https://api.tacticusgame.com/api/v1/player"
TACTICUS_RAID_URL     = "https://api.tacticusgame.com/api/v1/guildRaid/{season}"
TACTICUS_CURRENT_RAID = "https://api.tacticusgame.com/api/v1/guildRaid"

_TACTICUS_TIMEOUT_SECONDS = 20.0

# One record per server per hourly cycle. `auto_update` emitted nothing
# structured before this feature, which is exactly why a whole-server skip was
# invisible for three days during the 2026-07-28 incident — KPI-5 ("100% of
# guilds survive a sibling's quarantine") is unmeasurable without it.
CYCLE_EVENT = "auto_update.cycle"

# Emitted when the cycle posts a message an operator is meant to act on.
# KPI-1's detection latency is `alerted_at − last_probe_ok_at`, so this record
# is the second operand of the formula and its `ts` must be real.
ALERT_SENT_EVENT = "guild.key.alert.sent"


class TasksCog(commands.Cog):
    def __init__(self, bot: commands.Bot, player_service: PlayerService):
        self.bot            = bot
        self.player_service = player_service
        self.cap_detect.start()
        self.auto_update.start()

    def cog_unload(self):
        self.cap_detect.cancel()
        self.auto_update.cancel()

    # ==========================================
    # TASK: CAP DETECT (runs every hour)
    # ==========================================

    @tasks.loop(hours=1)
    async def cap_detect(self):
        server_ids = repo.list_server_ids()
        print(f"[cap_detect] Loop fired, checking {len(server_ids)} server(s)...")

        for server_id in server_ids:
            registrations = load_player_registrations(server_id)
            if not registrations:
                continue

            guilds        = load_guilds(server_id)
            capped_state  = load_capped_state(server_id)
            state_changed = False

            # Resolve channels upfront
            channel_cache: dict[int, discord.TextChannel | None] = {}
            for guild_data in guilds.values():
                channel_id = guild_data.get("notification_channel_id")
                if not channel_id or channel_id in channel_cache:
                    continue
                channel = self.bot.get_channel(channel_id)
                if channel is None:
                    try:
                        channel = await self.bot.fetch_channel(channel_id)
                    except Exception as e:
                        print(f"[cap_detect] Channel {channel_id} not found — {e}")
                        channel = None
                channel_cache[channel_id] = channel

            # Build list of valid players to check
            players_to_check = []
            for discord_id, reg in registrations.items():
                api_key  = reg.get("api_key")
                guild_id = reg.get("guild_id")
                if not api_key or not guild_id:
                    continue
                guild_data = guilds.get(guild_id)
                if not guild_data:
                    continue
                channel_id = guild_data.get("notification_channel_id")
                if not channel_id or channel_cache.get(channel_id) is None:
                    continue
                players_to_check.append((discord_id, api_key, channel_id))

            # Fetch all player token data in parallel
            async def _fetch(discord_id, api_key):
                headers = {"accept": "application/json", "X-API-KEY": api_key}
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        response = await client.get(TACTICUS_PLAYER_URL, headers=headers)
                        response.raise_for_status()
                        return discord_id, response.json()
                except Exception as e:
                    print(f"[cap_detect] Failed to fetch data for {discord_id}: {e}")
                    return discord_id, None

            results = await asyncio.gather(*[
                _fetch(discord_id, api_key)
                for discord_id, api_key, _ in players_to_check
            ])

            # Map channel_id back to each result
            channel_by_player = {did: cid for did, _, cid in players_to_check}

            # Process results and send notifications
            for (discord_id, player_data), (_, _, channel_id) in zip(results, players_to_check):
                if player_data is None:
                    continue

                player     = player_data.get("player") or {}
                progress   = player.get("progress") or {}
                guild_raid = progress.get("guildRaid") or {}
                tokens     = guild_raid.get("tokens") or {}
                current    = tokens.get("current", 0)
                maximum    = tokens.get("max", 3)
                is_capped  = current >= maximum
                print(f"[cap_detect] {discord_id}: {current}/{maximum} capped={is_capped}")

                was_capped = capped_state.get(discord_id, False)
                channel    = channel_cache[channel_id]

                if is_capped and not was_capped:
                    try:
                        await channel.send(
                            f"⚔️ <@{discord_id}> your raid tokens are full ({current}/{maximum})! "
                            f"Time to raid!"
                        )
                        print(f"[cap_detect] Pinged {discord_id}")
                    except discord.Forbidden:
                        print(f"[cap_detect] Missing permission to send in channel {channel_id}")
                        continue
                    capped_state[discord_id] = True
                    state_changed = True

                elif not is_capped and was_capped:
                    print(f"[cap_detect] {discord_id} spent tokens, resetting state")
                    capped_state[discord_id] = False
                    state_changed = True

            if state_changed:
                save_capped_state(server_id, capped_state)

    @cap_detect.before_loop
    async def before_cap_detect(self):
        await self.bot.wait_until_ready()

    # ==========================================
    # TASK: AUTO UPDATE (runs every hour)
    #
    # Every guild passes through `bot.guild_keys` before a single byte of its
    # data is read (ADR-008 D3). A Tacticus key belongs to a PLAYER, not to a
    # guild: when a guild-scoped key-holder changes guild the key keeps working
    # and silently returns the NEW guild's data. This loop is where that goes
    # from invisible to a message in a channel within the hour.
    #
    # Slice 01 REPORTS and does not block — `enforce=False`, deliberately. An
    # implementation that refused the write here would pass every drift
    # scenario and still be wrong: enforcement ships in Slice 03, AFTER
    # `/update_guild_key` (Slice 02) provides the only exit from quarantine, so
    # the first quarantine is never a trap (ADR-008 D3).
    # ==========================================

    @tasks.loop(hours=1)
    async def auto_update(self):
        print("[auto_update] Loop fired...")

        channel = await self._update_channel()
        if channel is None:
            return

        for server_id in repo.list_server_ids():
            await self._update_one_server(server_id, channel)

    async def _update_channel(self):
        channel = self.bot.get_channel(UPDATE_CHANNEL_ID)
        if channel is not None:
            return channel
        try:
            return await self.bot.fetch_channel(UPDATE_CHANNEL_ID)
        except Exception as e:
            print(f"[auto_update] Channel {UPDATE_CHANNEL_ID} not found — {e}")
            return None

    async def _update_one_server(self, server_id: int, channel) -> None:
        """One server's cycle: find the season, walk every guild, report once."""
        guilds = load_guilds(server_id)
        if not guilds:
            return

        cycle = _CycleReport(server_id, guilds_total=len(guilds))
        season = await self._current_season(server_id, guilds)

        if season is None:
            for guild_id in guilds:
                reason = _unusable_key_reason(server_id, guild_id)
                if reason == "quarantined":
                    # A persisting quarantine re-reports the drift and
                    # rate-limits the alert even when the season cannot be
                    # determined (e.g. the only guild is quarantined). The
                    # mismatch RECORD is never suppressed (AC-002.6).
                    guild_keys.re_report_persisting_drift(server_id, guild_id)
                    guild_keys.record_quarantine_alert(server_id, guild_id, channel)
                cycle.skipped(guild_id, reason)
            cycle.emit(season=None)
            print(f"[auto_update] Could not determine season for server {server_id}, skipping.")
            return

        print(f"[auto_update] Updating server {server_id} guilds for season {season}...")

        results: list[str] = []
        for guild_id, guild_data in guilds.items():
            results += await self._update_one_guild(
                server_id, guild_id, guild_data, season, channel, cycle
            )

        await self._post(
            channel,
            f"🔄 **Auto-update complete — Season {season}**\n" + "\n".join(results),
        )
        cycle.emit(season=season)

        await self._refresh_live_leaderboards(server_id, season, guilds)

    async def _current_season(self, server_id: int, guilds: dict) -> int | None:
        """The current raid season, from the first guild whose key can answer.

        DDD-7 exists for this loop: `active_key` is sync and storage-only, so a
        quarantined or unregistered guild is skipped without paying for a
        probe. The original read `next(iter(guilds.values()))["api_key"]` and
        skipped the WHOLE SERVER when that one guild failed — one bad key
        halting every sibling is KPI-5's 0% baseline. Nothing is quarantined in
        Slice 01, so the fall-through is dormant until Slice 03 opens it.
        """
        for guild_id in guilds:
            credential = guild_keys.active_key(server_id, guild_id)
            if credential is None:
                continue
            season = await self._ask_for_current_season(credential, server_id, guild_id)
            if season is not None:
                return season
        return None

    async def _ask_for_current_season(
        self, credential: str, server_id: int, guild_id: str
    ) -> int | None:
        try:
            async with httpx.AsyncClient(timeout=_TACTICUS_TIMEOUT_SECONDS) as client:
                response = await client.get(
                    TACTICUS_CURRENT_RAID, headers=_tacticus_headers(credential)
                )
                response.raise_for_status()
                return response.json().get("season")
        except Exception as e:
            print(
                f"[auto_update] {guild_id} could not answer the season for "
                f"server {server_id}: {e}"
            )
            return None

    async def _update_one_guild(
        self, server_id: int, guild_id: str, guild_data: dict,
        season: int, channel, cycle: "_CycleReport",
    ) -> list[str]:
        """Verify the guild's identity, then ingest. Returns its summary lines."""
        guild_name = guild_data["name"]

        credential = guild_keys.active_key(server_id, guild_id)
        if credential is None:
            reason = _unusable_key_reason(server_id, guild_id)
            if reason == "quarantined":
                # A persisting quarantine: the alert decision is rate-limited
                # here (not in verify_and_resolve, which is not called on the
                # skip path). The mismatch RECORD is re-emitted so a repeat
                # still reads as "the operator has not acted yet" (AC-002.6).
                guild_keys.re_report_persisting_drift(server_id, guild_id)
                guild_keys.record_quarantine_alert(server_id, guild_id, channel)
            cycle.skipped(guild_id, reason)
            return [f"⛔ **{guild_name}** — skipped, no usable key ({reason})."]

        # Read the binding BEFORE the probe: adoption is announced exactly once
        # (DDD-8) and a mismatch names the guild it WAS bound to, neither of
        # which is recoverable from the snapshot afterwards.
        bound_before = load_guild_binding(server_id, guild_id)
        try:
            snapshot = await guild_keys.verify_and_resolve(
                server_id, guild_id, enforce=True,
            )
        except guild_keys.GuildQuarantined as exc:
            # The raise happens BEFORE any further request (DDD-2/5): no
            # roster fetch, no raid fetch, zero writes. The summary line names
            # both identities so the operator can act without re-reading the
            # binding. The FIRST alert of a quarantine always fires —
            # `record_quarantine_alert` sets `last_alerted_at` and the 24h
            # clock starts here.
            line = (
                f"⚠️ **{guild_name}** — key mismatch: bound to "
                f"{_identity_label(exc.bound.name, exc.bound.tag, exc.bound.uuid)} "
                f"but the key resolves to "
                f"{_identity_label(exc.observed.name, exc.observed.tag, exc.observed.uuid)}. "
                f"⛔ Quarantined — run /update_guild_key to install the correct key."
            )
            guild_keys.record_quarantine_alert(server_id, guild_id, channel)
            cycle.skipped(guild_id, "quarantined")
            return [line]

        await self._announce_adoption(guild_name, bound_before, snapshot, channel)
        results = self._probe_report(server_id, guild_id, guild_name,
                                     bound_before, snapshot, channel)

        if snapshot.outcome is ProbeOutcome.DEAD:
            # A refused key returns no data, so there is nothing to fetch and
            # nothing to contaminate — report it and move to the next guild.
            cycle.skipped(guild_id, "dead_key")
            return results

        await self._validate_roster(server_id, guild_id, guild_name, snapshot)
        cycle.processed(guild_id)
        results.append(await self._ingest_raid(server_id, guild_id, guild_name,
                                               season, credential))
        return results

    def _probe_report(
        self, server_id: int, guild_id: str, guild_name: str,
        bound_before, snapshot: GuildSnapshot, channel,
    ) -> list[str]:
        """The operator-facing consequence of the probe, if there is one.

        A guild whose key resolves to the guild it is bound to says NOTHING —
        an hourly all-clear is alert fatigue by construction and would bury the
        one message that matters. That silence is KPI-4's whole basis.
        """
        line = _probe_line(guild_name, bound_before, snapshot)
        if line is None:
            return []
        if snapshot.outcome in _ALERTING_OUTCOMES:
            self._record_alert(server_id, guild_id, channel)
        return [line]

    async def _announce_adoption(
        self, guild_name: str, bound_before, snapshot: GuildSnapshot, channel
    ) -> None:
        """Trust-on-first-use, said out loud exactly once (DDD-8).

        There is no historical record to reconstruct a binding from, so the
        announcement IS the verification step: an operator reading it is the
        only thing standing between "we adopted the right guild" and "we
        adopted whatever the key happened to resolve to on deploy day".
        """
        if not bound_before.is_unbound or snapshot.identity is None:
            return
        await self._post(
            channel,
            f"🔗 **{guild_name}** is now bound to {_resolved_label(snapshot.identity)}. "
            f"Verify this is the right guild — every later cycle is checked against it.",
        )

    def _record_alert(self, server_id: int, guild_id: str, channel) -> None:
        """KPI-1's `alerted_at`, the second operand of
        `alerted_at − last_probe_ok_at`."""
        emit_structured(
            logger, logging.INFO, ALERT_SENT_EVENT,
            ts=_now(),
            server_id=server_id,
            guild_id=guild_id,
            channel_id=getattr(channel, "id", None),
            # No suppression in Slice 01 (AC-002.6): a repeat means the
            # operator has not acted yet, and that is worth saying. Suppression
            # arrives with quarantine in Slice 03, where the state persists.
            suppressed_until=None,
        )

    async def _validate_roster(
        self, server_id: int, guild_id: str, guild_name: str, snapshot: GuildSnapshot
    ) -> None:
        """Hand the roster to the Chronicler as a SNAPSHOT, never as a key.

        `PlayerService` no longer fetches for itself (DDD-2): it refuses to
        write a roster whose owner cannot be established, which is the guard
        that stops an hourly inversion flipping 60 of 67 players to `former`.
        """
        try:
            await self.player_service.validate_if_stale(server_id, guild_id, snapshot)
        except Exception as e:
            print(f"[auto_update] Player list validation failed for {guild_name}: {e}")

    async def _ingest_raid(
        self, server_id: int, guild_id: str, guild_name: str,
        season: int, credential: str,
    ) -> str:
        url = TACTICUS_RAID_URL.format(season=season)
        try:
            async with httpx.AsyncClient(timeout=_TACTICUS_TIMEOUT_SECONDS) as client:
                response = await client.get(url, headers=_tacticus_headers(credential))
                response.raise_for_status()
                api_data = response.json()

            process_api_response(api_data, season, server_id, guild_id)
            await self._register_unknown_players(server_id, guild_id, api_data)
            print(f"[auto_update] {guild_name} updated.")
            return f"✅ **{guild_name}** — updated successfully."

        except httpx.HTTPStatusError as e:
            print(f"[auto_update] {guild_name} failed: HTTP {e.response.status_code}")
            return f"❌ **{guild_name}** — HTTP {e.response.status_code}"
        except Exception as e:
            print(f"[auto_update] {guild_name} failed: {e}")
            return f"❌ **{guild_name}** — {str(e)[:80]}"

    async def _post(self, channel, content: str) -> None:
        try:
            await channel.send(content)
        except discord.Forbidden:
            print(f"[auto_update] Missing permission to send in channel {UPDATE_CHANNEL_ID}")

    async def _register_unknown_players(self, server_id: int, guild_id: str, api_data: dict) -> None:
        known   = set(load_player_list(server_id, guild_id).get("players", {}).keys())
        seen    = {e["userId"] for e in api_data.get("entries", []) if "userId" in e}
        unknown = seen - known
        for user_id in unknown:
            try:
                saved = await self.player_service.ensure_player_in_list(server_id, guild_id, user_id)
                if saved:
                    print(f"[auto_update] Saved unknown player {user_id} to player list")
            except Exception as e:
                print(f"[auto_update] Failed to save unknown player {user_id}: {e}")

    async def _refresh_live_leaderboards(self, server_id: int, season: int, guilds: dict):
        """Refresh live leaderboards.

        Same season -> edit the existing messages in place.
        New season  -> leave the old messages untouched (frozen archive of the
                       previous season), send a fresh set, and repoint the
                       live config at the new message IDs.
        """
        from config import TIER_CHOICES
        from bot.embeds import build_battle_messages, build_cluster_messages

        live = load_live_leaderboards(server_id)
        if not live:
            return

        to_remove = []
        dirty     = False  # config changed (rollover, season adoption, removals)

        for key, config in live.items():
            channel_id  = config.get("channel_id")
            message_ids = config.get("messages", {})
            channel     = self.bot.get_channel(channel_id)

            if channel is None:
                print(f"[live_leaderboard] Channel {channel_id} not found, removing {key}")
                to_remove.append(key)
                continue

            # ------------------------------------------------------------
            # Build per-tier content for the CURRENT season
            # ------------------------------------------------------------
            if key.startswith("guild:"):
                guild_id   = config.get("guild_id")
                guild_data = guilds.get(guild_id)
                if not guild_data:
                    to_remove.append(key)
                    continue

                guild_name = guild_data["name"]
                data = repo.load_battle_hits(server_id, guild_id, season)

                contents = {}
                for tier in TIER_CHOICES:
                    if not data or not data.get("boss_hits"):
                        contents[tier.value] = f"📊 **{guild_name} — {tier.name} — No data yet**"
                    else:
                        messages = build_battle_messages(
                            data, season, tier, server_id, guild_id, guild_name
                        )
                        contents[tier.value] = (
                            "\n\n".join(messages)
                            if messages
                            else f"📊 **{guild_name} — {tier.name} — No data yet**"
                        )

            elif key == "cluster":
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
                                bucket = (
                                    merged.setdefault(boss_id, {})
                                    .setdefault(e_index, {})
                                    .setdefault(tier_key, [])
                                )
                                for entry in entries:
                                    user_id      = entry.get("user_id", "Unknown")
                                    user_display = id_to_name.get(user_id, str(user_id)[:8])
                                    bucket.append(
                                        {**entry, "_display": user_display, "_guild": guild_name}
                                    )

                for boss_id, encounter_dict in merged.items():
                    for e_index, tiers in encounter_dict.items():
                        for tier_key in tiers:
                            limit = encounter_limit(e_index)
                            tiers[tier_key] = sorted(
                                tiers[tier_key], key=lambda e: (-e["damage"], e.get("completed_on", ""))
                            )[:limit]

                contents = {}
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
                    contents[tier.value] = (
                        "\n\n".join(messages)
                        if messages
                        else f"🌐 **Cluster — {tier.name} — No data yet**"
                    )

            else:
                continue  # unknown key, skip

            # ------------------------------------------------------------
            # Same season -> edit in place. New season -> send fresh set.
            # ------------------------------------------------------------
            stored_season = config.get("season")

            if stored_season is None:
                # Legacy config from before season tracking existed.
                # Adopt the current season without spawning new messages.
                config["season"] = season
                stored_season    = season
                dirty            = True

            if stored_season == season:
                for tier in TIER_CHOICES:
                    msg_id = message_ids.get(tier.value)
                    if not msg_id:
                        continue
                    try:
                        msg = await channel.fetch_message(msg_id)
                        await msg.edit(content=contents[tier.value])
                    except discord.NotFound:
                        to_remove.append(key)
                        break
                    except discord.Forbidden:
                        print(f"[live_leaderboard] No permission to edit message in channel {channel_id} ({key})")
                        break
                    except Exception as e:
                        print(f"[live_leaderboard] Error editing message {msg_id} ({key}): {e}")

            else:
                # Season rollover: old messages stay as a frozen archive.
                print(f"[live_leaderboard] Season rollover for {key}: {stored_season} -> {season}, sending new messages")
                new_message_ids = {}
                failed = False
                for tier in TIER_CHOICES:
                    try:
                        msg = await channel.send(contents[tier.value])
                        new_message_ids[tier.value] = msg.id
                    except discord.Forbidden:
                        print(f"[live_leaderboard] No permission to send rollover messages in channel {channel_id} ({key})")
                        failed = True
                        break
                    except Exception as e:
                        print(f"[live_leaderboard] Error sending rollover message ({key}): {e}")
                        failed = True
                        break

                if failed and not new_message_ids:
                    # Nothing sent — keep the old config and retry next hour.
                    continue

                config["messages"] = new_message_ids
                config["season"]   = season
                dirty              = True

        if to_remove:
            for key in to_remove:
                live.pop(key, None)
            dirty = True
            print(f"[live_leaderboard] Removed broken configs: {to_remove}")

        if dirty:
            save_live_leaderboards(server_id, live)

    @auto_update.before_loop
    async def before_auto_update(self):
        await self.bot.wait_until_ready()


# ==========================================
# Cycle bookkeeping + rendering
# ==========================================

class _CycleReport:
    """The KPI-5 instrument: what this server's cycle actually did.

    KPI-5 reads `guilds_processed == guilds_total − guilds_quarantined`, so the
    three counts and the reasons have to come out of ONE record per server per
    cycle — a skip that leaves no trace is exactly how a whole-server outage
    stayed invisible for three days. `skip_reasons` is never empty while
    `guilds_skipped > 0`: a count with no reason is a number nobody can act on.
    """

    def __init__(self, server_id: int, *, guilds_total: int) -> None:
        self.server_id = server_id
        self.guilds_total = guilds_total
        self._processed: list[str] = []
        self._skipped: list[str] = []

    def processed(self, guild_id: str) -> None:
        self._processed.append(guild_id)

    def skipped(self, guild_id: str, reason: str) -> None:
        self._skipped.append(f"{guild_id}: {reason}")

    def emit(self, *, season: int | None) -> None:
        emit_structured(
            logger, logging.INFO, CYCLE_EVENT,
            ts=_now(),
            server_id=self.server_id,
            season=season,
            guilds_total=self.guilds_total,
            guilds_processed=len(self._processed),
            guilds_skipped=len(self._skipped),
            skip_reasons=list(self._skipped),
        )


def _unusable_key_reason(server_id: int, guild_id: str) -> str:
    """Why `active_key` said no. Quarantine and absence need different words:
    one is the feature working, the other is a guild nobody finished
    registering, and an operator acts differently on each."""
    binding = load_guild_binding(server_id, guild_id)
    if binding.key_status == KeyStatus.QUARANTINED.value:
        return "quarantined"
    return "no_key_registered"


# The probe outcomes an operator has to act on. UNREACHABLE is deliberately
# absent: a Tacticus outage is transient and self-healing, and an alert record
# per guild per hour through an outage is how a channel gets muted — a muted
# channel defeats KPI-1 entirely.
_ALERTING_OUTCOMES = (
    ProbeOutcome.MISMATCH, ProbeOutcome.UNVERIFIABLE, ProbeOutcome.DEAD,
)


def _probe_line(guild_name: str, bound_before, snapshot: GuildSnapshot) -> str | None:
    """One summary line for a probe that needs saying, or None for silence.

    Pure rendering, kept out of the loop's control flow: the decision to record
    an alert and the words shown to the operator change for different reasons.
    """
    if snapshot.outcome is ProbeOutcome.MISMATCH:
        return (
            f"⚠️ **{guild_name}** — key mismatch: bound to "
            f"{_bound_label(bound_before)} but the key now resolves to "
            f"{_resolved_label(snapshot.identity)}. Data is still ingested this "
            f"slice — run `/update_guild_key` to correct it."
        )
    if snapshot.outcome is ProbeOutcome.UNVERIFIABLE:
        return (
            f"⚠️ **{guild_name}** — identity verification is offline: the guild "
            f"service answered without an identifier, so the key could not be "
            f"checked. The tag is never compared as a substitute."
        )
    if snapshot.outcome is ProbeOutcome.DEAD:
        return (
            f"❌ **{guild_name}** — the key was refused (HTTP {snapshot.status}). "
            f"Install a working one with `/update_guild_key`."
        )
    if snapshot.outcome is ProbeOutcome.UNREACHABLE:
        return (
            f"❌ **{guild_name}** — the guild service is unreachable "
            f"({snapshot.error}); the check is retried next cycle."
        )
    return None


def _bound_label(binding) -> str:
    return _identity_label(
        binding.tacticus_guild_name,
        binding.tacticus_guild_tag,
        binding.tacticus_guild_id,
    )


def _resolved_label(identity) -> str:
    return _identity_label(identity.name, identity.tag, identity.uuid)


def _identity_label(name: str | None, tag: str | None, uuid: str | None) -> str:
    """Name, tag and the first eight characters of the identifier.

    The comparison is on uuid alone (DDD-1), but a uuid pair tells an operator
    nothing about what to do next — and both guilds in the 2026-07-28 incident
    carried the 【UNDV】 alliance prefix, so the name alone is not enough
    either. All three, always.
    """
    return f"{name or '—'} 【{tag or '—'}】 ({(uuid or '—')[:8]})"


def _tacticus_headers(credential: str) -> dict:
    return {"accept": "application/json", "X-API-KEY": credential}


def _now() -> str:
    """The one clock this feature's records share.

    Reuses `bot.guild_keys`'s helper rather than re-deriving the format:
    KPI-1 subtracts a timestamp emitted HERE from one emitted THERE, and
    KPI-2 compares them as strings against `battle_hits.completed_on`. Two
    independently-written formatters is the failure that returns a wrong
    result set silently instead of erroring. Millisecond precision is
    load-bearing — KPI-1 asserts the difference is STRICTLY positive, and at
    whole-second resolution two records in the same second read as zero.
    """
    return guild_keys._utc_now()


async def setup_tasks(bot: commands.Bot, player_service: PlayerService):
    await bot.add_cog(TasksCog(bot, player_service))