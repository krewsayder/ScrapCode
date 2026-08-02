import logging
import os
import re
from pathlib import Path

from bot.repository import (
    ClusterRepository,
    GuildBinding,
    JsonClusterRepository,
    SupportsProbe,
)
from bot.migrations.player_list_migrations import PlayerListMigrator

logger = logging.getLogger(__name__)


def build_repo() -> ClusterRepository:
    """Construct the live ClusterRepository from SCRAPCODE_REPO_BACKEND
    (ADR-006 D9 — env-driven singleton; rollback = restart with =json).

    Selection order:
      1. `SCRAPCODE_REPO_BACKEND=json` → JsonClusterRepository (rollback
         path; the probe is skipped so a missing/invalid SCRAPCODE_DB_KEY
         does not block a JSON-backend rollback).
      2. `SCRAPCODE_REPO_BACKEND=sqlite` (the post-cutover default) →
         SqlAlchemyClusterRepository, UNLESS the safety net fires:
           - SCRAPCODE_DB_KEY missing/empty → fall back to JSON for one cycle
             (the SQLite impl cannot operate without the Fernet key).
           - SCRAPCODE_DB_PATH file missing AND its parent directory exists
             (i.e., the file was supposed to be there but is gone — deleted
             or corrupted) → fall back to JSON for one cycle. A first-run
             path whose parent dir does not yet exist constructs the SQLite
             impl (which creates both the dir and the file via create_all).

    The safety net keeps the JSON tree as the one-cycle read-only fallback
    (US-010). A loud WARNING is logged on each fallback so the operator
    sees it in `discord.log` / journalctl.
    """
    backend = os.getenv("SCRAPCODE_REPO_BACKEND", "sqlite")
    if backend == "json":
        return JsonClusterRepository()
    db_path = os.getenv("SCRAPCODE_DB_PATH", "data/scrapcode.db")
    fernet_key = os.getenv("SCRAPCODE_DB_KEY", "")
    if not fernet_key:
        logger.warning(
            "SCRAPCODE_DB_KEY missing — falling back to JsonClusterRepository "
            "for one cycle (SCRAPCODE_REPO_BACKEND=sqlite, ADR-006 D9 safety net)"
        )
        return JsonClusterRepository()
    if Path(db_path).parent.exists() and not Path(db_path).exists():
        logger.warning(
            "SCRAPCODE_DB_PATH=%s missing — falling back to JsonClusterRepository "
            "for one cycle (SCRAPCODE_REPO_BACKEND=sqlite, ADR-006 D9 safety net)",
            db_path,
        )
        return JsonClusterRepository()
    from bot.repository_sqlalchemy import SqlAlchemyClusterRepository
    return SqlAlchemyClusterRepository()


repo: SupportsProbe = build_repo()

UUID_PATTERN = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE
)


# ==========================================
# GUILD REGISTRY
# ==========================================

def load_guilds(discord_server_id: int) -> dict:
    """Return {guild_id: {name, api_key, role_id, notification_channel_id, member_role_ids}} for a server."""
    cluster = repo.load(discord_server_id)
    return {
        gid: {
            "name":                    g.name,
            "api_key":                 g.api_key,
            "role_id":                 g.role_id,
            "notification_channel_id": g.notification_channel_id,
            "member_role_ids":         g.member_role_ids,
        }
        for gid, g in cluster.guilds.items()
    }


def save_guilds(discord_server_id: int, guilds: dict) -> None:
    from bot.models import Cluster, Guild
    cluster = repo.load(discord_server_id)
    cluster.guilds = {
        gid: Guild(
            id=gid,
            name=data["name"],
            api_key=data.get("api_key", ""),
            role_id=data.get("role_id", 0),
            notification_channel_id=data.get("notification_channel_id"),
            member_role_ids=data.get("member_role_ids", []),
        )
        for gid, data in guilds.items()
    }
    repo.save(cluster)


# ==========================================
# GUILD KEY BINDINGS  (feature: guild-key-integrity)
#
# These are thin wrappers over the ClusterRepository binding methods, in the
# same shape as every other wrapper in this module — brief §4.1 and ADR-004
# rule 1 make this the sanctioned cog-facing layer.
#
# Binding state lives in its own `guild_key_bindings` table, NOT on `Guild`
# (DDD-4). That is deliberate: `save_guilds` above rebuilds each `Guild` from
# a five-key dict, so any binding field threaded through the dataclass would
# be overwritten with a None default by the next unrelated admin command.
# Keeping it out of `Guild` makes that clobber structurally impossible rather
# than merely avoided.
#
# This module MUST NOT import `bot.guild_keys` or `httpx` — every cog imports
# this layer, and pulling policy or an HTTP client in here is the import cycle
# DDD-3 is shaped to avoid. Enforced by
# tests/acceptance/guild-key-integrity/test_architecture_chokepoint.py.
# ==========================================

def load_guild_binding(discord_server_id: int, guild_id: str) -> GuildBinding:
    """Return the guild's identity binding, or an unbound placeholder.

    Never returns None: an unbound guild is a real, expected state (every
    guild is unbound on the day Slice 01 deploys), and a None return would
    push that check onto seven call sites. On the JSON backend this always
    returns unbound — the feature degrades to inert there (ADR-006 D9).
    """
    return repo.load_guild_binding(discord_server_id, guild_id)


def save_guild_binding(discord_server_id: int, guild_id: str,
                       binding: GuildBinding) -> None:
    """Persist the guild's identity binding.

    No-ops on the JSON backend rather than raising, so a rollback under time
    pressure leaves a bot that runs.
    """
    repo.save_guild_binding(discord_server_id, guild_id, binding)


def replace_guild_key(discord_server_id: int, guild_id: str, api_key: str) -> None:
    """Swap a guild's `api_key` (and `api_key_hmac` where the backend has one)
    in one transaction, touching nothing else (ADR-006 D7 / AC-003.2).

    The targeted key-swap path — the ONLY sanctioned write for a key
    replacement. `save_guilds` rebuilds every guild row and CASCADE-deletes
    absent ones, which is the wrong tool for a key swap: this command's whole
    justification is that players and hit rows are byte-identical before and
    after, so this method touches only the two key columns and cannot reach a
    dependent row even by accident.
    """
    repo.replace_guild_key(discord_server_id, guild_id, api_key)


def list_guild_bindings(discord_server_id: int) -> dict[str, GuildBinding]:
    """Return {guild_id: binding} for every guild that has one."""
    return repo.list_guild_bindings(discord_server_id)


def add_cluster_role(discord_server_id: int, tier: str, role_id: int) -> None:
    cluster = repo.load(discord_server_id)
    existing = cluster.role_tiers.get(tier, [])
    if role_id not in existing:
        cluster.role_tiers[tier] = existing + [role_id]
    repo.save(cluster)


def add_guild_member_role(discord_server_id: int, guild_id: str, role_id: int) -> None:
    cluster = repo.load(discord_server_id)
    guild = cluster.guilds.get(guild_id)
    if guild and role_id not in guild.member_role_ids:
        guild.member_role_ids = guild.member_role_ids + [role_id]
    repo.save(cluster)


def get_guild_by_role(discord_server_id: int, role_id: int):
    for guild_id, guild_data in load_guilds(discord_server_id).items():
        if guild_data.get("role_id") == role_id:
            return guild_id, guild_data
    return None


def get_guild_data_path(discord_server_id: int, guild_id: str) -> Path:
    return repo.get_guild_data_path(discord_server_id, guild_id)


# ==========================================
# GUILD PLAYER LIST (v2 schema)
# ==========================================

def load_player_list(discord_server_id: int, guild_id: str) -> dict:
    return repo.load_player_list(discord_server_id, guild_id)


def get_player_list(discord_server_id: int, guild_id: str) -> dict:
    """Return {tacticus_id: display_name} for use in embeds/leaderboards."""
    players = load_player_list(discord_server_id, guild_id).get("players", {})
    result = {}
    for uid, entry in players.items():
        name = entry.get("display_name", uid[:8])
        if entry.get("is_former"):
            name += " (former)"
        result[uid] = name
    return result


def save_player_list(discord_server_id: int, guild_id: str, data: dict) -> None:
    repo.save_player_list(discord_server_id, guild_id, data)


# ==========================================
# PLAYER REGISTRATIONS  {discord_id: {api_key, guild_id}}
# ==========================================

def load_player_registrations(discord_server_id: int) -> dict:
    return repo.load_player_registrations(discord_server_id)


def save_player_registrations(discord_server_id: int, data: dict) -> None:
    repo.save_player_registrations(discord_server_id, data)


# ==========================================
# CAPPED STATE  {discord_id: bool}
# ==========================================

def load_capped_state(discord_server_id: int) -> dict:
    return repo.load_capped_state(discord_server_id)


def save_capped_state(discord_server_id: int, data: dict) -> None:
    repo.save_capped_state(discord_server_id, data)


# ==========================================
# LIVE LEADERBOARDS
# ==========================================

def load_live_leaderboards(discord_server_id: int) -> dict:
    return repo.load_live_leaderboards(discord_server_id)


def save_live_leaderboards(discord_server_id: int, data: dict) -> None:
    repo.save_live_leaderboards(discord_server_id, data)