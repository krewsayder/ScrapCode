import re
from pathlib import Path

from repository import JsonClusterRepository
from migrations.player_list_migrations import PlayerListMigrator

repo = JsonClusterRepository()

UUID_PATTERN = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE
)


# ==========================================
# GUILD REGISTRY
# ==========================================

def load_guilds(discord_server_id: int) -> dict:
    """Return {guild_id: {name, api_key, role_id, notification_channel_id}} for a server."""
    cluster = repo.load(discord_server_id)
    return {
        gid: {
            "name":                    g.name,
            "api_key":                 g.api_key,
            "role_id":                 g.role_id,
            "notification_channel_id": g.notification_channel_id,
        }
        for gid, g in cluster.guilds.items()
    }


def save_guilds(discord_server_id: int, guilds: dict) -> None:
    """Save guild registry from a {guild_id: guild_data_dict} mapping."""
    from models import Cluster, Guild
    cluster = repo.load(discord_server_id)
    cluster.guilds = {
        gid: Guild(
            id=gid,
            name=data["name"],
            api_key=data.get("api_key", ""),
            role_id=data.get("role_id", 0),
            notification_channel_id=data.get("notification_channel_id"),
        )
        for gid, data in guilds.items()
    }
    repo.save(cluster)


def get_guild_by_role(discord_server_id: int, role_id: int):
    """Find a guild by its leader role ID. Returns (guild_id, guild_data) or None."""
    for guild_id, guild_data in load_guilds(discord_server_id).items():
        if guild_data.get("role_id") == role_id:
            return guild_id, guild_data
    return None


def get_guild_data_path(discord_server_id: int, guild_id: str) -> Path:
    """Returns the data directory path for a guild, creating it if needed."""
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
# PLAYER API LIST
# ==========================================

def load_player_apis(discord_server_id: int, guild_id: str) -> dict:
    return repo.load_player_apis(discord_server_id, guild_id)


def save_player_apis(discord_server_id: int, guild_id: str, data: dict) -> None:
    repo.save_player_apis(discord_server_id, guild_id, data)


# ==========================================
# CAPPED STATE
# ==========================================

def load_capped_state(discord_server_id: int, guild_id: str) -> dict:
    return repo.load_capped_state(discord_server_id, guild_id)


def save_capped_state(discord_server_id: int, guild_id: str, data: dict) -> None:
    repo.save_capped_state(discord_server_id, guild_id, data)


# ==========================================
# LIVE LEADERBOARDS
# ==========================================

def load_live_leaderboards(discord_server_id: int) -> dict:
    return repo.load_live_leaderboards(discord_server_id)


def save_live_leaderboards(discord_server_id: int, data: dict) -> None:
    repo.save_live_leaderboards(discord_server_id, data)