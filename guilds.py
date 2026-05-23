import json
import re
from pathlib import Path

from migrations.player_list_migrations import PlayerListMigrator

_BASE             = Path(__file__).parent
GUILDS_FILE       = _BASE / "guilds.json"
PLAYER_API_FILE   = _BASE / "player_api_list.json"
CAPPED_STATE_FILE = _BASE / "capped_state.json"
DATA_DIR          = _BASE / "data"

# Tacticus user IDs are standard UUIDs: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
UUID_PATTERN = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE
)


# ==========================================
# GUILD REGISTRY
# ==========================================

def load_guilds() -> dict:
    """Load the guild registry. Returns empty dict if file doesn't exist."""
    if not GUILDS_FILE.exists():
        return {}
    try:
        return json.loads(GUILDS_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def save_guilds(guilds: dict):
    """Save the guild registry to disk."""
    GUILDS_FILE.write_text(json.dumps(guilds, indent=2), encoding='utf-8')


def get_guild_by_role(role_id: int) -> tuple[str, dict] | None:
    """Find a guild by its leader role ID. Returns (guild_id, guild_data) or None."""
    for guild_id, guild_data in load_guilds().items():
        if guild_data.get("role_id") == role_id:
            return guild_id, guild_data
    return None


def get_guild_data_path(guild_id: str) -> Path:
    """Returns the data directory path for a guild, creating it if needed."""
    path = DATA_DIR / guild_id
    path.mkdir(parents=True, exist_ok=True)
    return path


# ==========================================
# GUILD PLAYER LIST (v2 schema)
# ==========================================

def load_player_list(guild_id: str) -> dict:
    """Load the raw v2 player list structure, running migration if needed.
    Returns the full {__meta__, players} dict. Writes back if migrated."""
    path = get_guild_data_path(guild_id) / "player_list.json"
    if not path.exists():
        return {"__meta__": {"version": PlayerListMigrator.CURRENT_VERSION}, "players": {}}
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
        data, was_migrated = PlayerListMigrator.migrate(raw)
        if was_migrated:
            path.write_text(json.dumps(data, indent=2), encoding='utf-8')
        return data
    except Exception:
        return {"__meta__": {"version": PlayerListMigrator.CURRENT_VERSION}, "players": {}}


def get_player_list(guild_id: str) -> dict:
    """Return {tacticus_id: display_name} for use in embeds/leaderboards.
    Appends ' (former)' for players no longer in the guild roster."""
    players = load_player_list(guild_id).get("players", {})
    result = {}
    for uid, entry in players.items():
        name = entry.get("display_name", uid[:8])
        if entry.get("is_former"):
            name += " (former)"
        result[uid] = name
    return result


def save_player_list(guild_id: str, data: dict):
    """Save a v2 player list for a guild. Expects {__meta__, players} structure."""
    path = get_guild_data_path(guild_id) / "player_list.json"
    path.write_text(json.dumps(data, indent=2), encoding='utf-8')


# ==========================================
# PLAYER API LIST (discord_id -> tacticus api key)
# ==========================================

def load_player_apis() -> dict:
    """Load the global player API key registry. Returns empty dict if file doesn't exist.
    Format: {discord_id: {"api_key": str, "name": str}}
    """
    if not PLAYER_API_FILE.exists():
        return {}
    try:
        return json.loads(PLAYER_API_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def save_player_apis(data: dict):
    """Save the global player API key registry to disk."""
    PLAYER_API_FILE.write_text(json.dumps(data, indent=2), encoding='utf-8')


# ==========================================
# CAPPED STATE (discord_id -> True if already pinged)
# ==========================================

def load_capped_state() -> dict:
    """Load the set of already-pinged capped players. Returns empty dict if file doesn't exist."""
    if not CAPPED_STATE_FILE.exists():
        return {}
    try:
        return json.loads(CAPPED_STATE_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def save_capped_state(data: dict):
    """Save the capped state to disk."""
    CAPPED_STATE_FILE.write_text(json.dumps(data, indent=2), encoding='utf-8')


# ==========================================
# LIVE LEADERBOARDS (channel + message IDs)
# ==========================================

LIVE_LEADERBOARDS_FILE = Path("live_leaderboards.json")


def load_live_leaderboards() -> dict:
    """Load live leaderboard config. Returns empty dict if file doesn't exist.
    Format:
    {
      "guild:guild_one": {"channel_id": 123, "messages": {"Legendary_0": 456, ...}},
      "cluster":         {"channel_id": 123, "messages": {"Legendary_0": 456, ...}}
    }
    """
    if not LIVE_LEADERBOARDS_FILE.exists():
        return {}
    try:
        return json.loads(LIVE_LEADERBOARDS_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def save_live_leaderboards(data: dict):
    """Save live leaderboard config to disk."""
    LIVE_LEADERBOARDS_FILE.write_text(json.dumps(data, indent=2), encoding='utf-8')