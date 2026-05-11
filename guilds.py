import json
import re
from pathlib import Path

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
        return json.loads(GUILDS_FILE.read_text())
    except Exception:
        return {}


def save_guilds(guilds: dict):
    """Save the guild registry to disk."""
    GUILDS_FILE.write_text(json.dumps(guilds, indent=2))


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
# GUILD PLAYER LIST (name -> tacticus ID)
# ==========================================

def get_player_list(guild_id: str) -> dict:
    """Load a guild's player list. Returns empty dict if not uploaded yet."""
    path = get_guild_data_path(guild_id) / "player_list.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
        return {v: k for k, v in raw.items()}  # Flip {"Name": "ID"} -> {"ID": "Name"}
    except Exception:
        return {}


def save_player_list(guild_id: str, data: dict):
    """Save a player list for a guild."""
    path = get_guild_data_path(guild_id) / "player_list.json"
    path.write_text(json.dumps(data, indent=2))


def validate_player_list(data: dict) -> tuple[bool, dict, list[str]]:
    """Validate and clean a player list.

    - Rejects the whole file if it's not a dict or is completely empty.
    - Skips individual entries with invalid names or non-UUID user IDs.
    - Returns (is_valid, clean_data, skipped_warnings).

    is_valid: False only if the whole file is unusable.
    clean_data: dict of only the valid entries.
    skipped_warnings: list of human-readable messages about skipped entries.
    """
    if not isinstance(data, dict):
        return False, {}, ["File must be a JSON object."]

    if len(data) == 0:
        return False, {}, ["Player list is empty."]

    clean   = {}
    skipped = []

    for name, uid in data.items():
        if not isinstance(name, str) or not name.strip():
            skipped.append(f"Skipped invalid player name: {repr(name)}")
            continue

        if not isinstance(uid, str) or not UUID_PATTERN.match(uid.strip()):
            skipped.append(f"Skipped **{name}** — invalid user ID: `{uid}`")
            continue

        clean[name] = uid.strip()

    if not clean:
        return False, {}, ["No valid entries found. Make sure all user IDs are in UUID format."]

    return True, clean, skipped


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
        return json.loads(PLAYER_API_FILE.read_text())
    except Exception:
        return {}


def save_player_apis(data: dict):
    """Save the global player API key registry to disk."""
    PLAYER_API_FILE.write_text(json.dumps(data, indent=2))


# ==========================================
# CAPPED STATE (discord_id -> True if already pinged)
# ==========================================

def load_capped_state() -> dict:
    """Load the set of already-pinged capped players. Returns empty dict if file doesn't exist."""
    if not CAPPED_STATE_FILE.exists():
        return {}
    try:
        return json.loads(CAPPED_STATE_FILE.read_text())
    except Exception:
        return {}


def save_capped_state(data: dict):
    """Save the capped state to disk."""
    CAPPED_STATE_FILE.write_text(json.dumps(data, indent=2))


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
        return json.loads(LIVE_LEADERBOARDS_FILE.read_text())
    except Exception:
        return {}


def save_live_leaderboards(data: dict):
    """Save live leaderboard config to disk."""
    LIVE_LEADERBOARDS_FILE.write_text(json.dumps(data, indent=2))