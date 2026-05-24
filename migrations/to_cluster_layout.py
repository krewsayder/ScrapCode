"""
Migration: flat root files -> clusters/{discord_server_id}/ layout

Run once from the project root:
    python -m migrations.to_cluster_layout

Old files are left untouched. Verify the clusters/ directory looks correct
before removing them.
"""

import json
import os
import shutil
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DISCORD_SERVER_ID  = 1458181638453203099
CAP_CHANNEL_ID_STR = os.getenv("CAP_CHANNEL_ID")
CAP_CHANNEL_ID     = int(CAP_CHANNEL_ID_STR) if CAP_CHANNEL_ID_STR else None

BASE       = Path(__file__).parent.parent
CLUSTERS   = BASE / "clusters"
SERVER_DIR = CLUSTERS / str(DISCORD_SERVER_ID)


def migrate():
    SERVER_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Target directory: {SERVER_DIR}")

    # --- guilds.json ---
    old_guilds_file = BASE / "guilds.json"
    guilds = {}
    if old_guilds_file.exists():
        guilds = json.loads(old_guilds_file.read_text(encoding="utf-8"))
        print(f"Loaded {len(guilds)} guilds from guilds.json")
    else:
        print("WARNING: guilds.json not found, skipping")

    if CAP_CHANNEL_ID:
        print(f"Seeding notification_channel_id={CAP_CHANNEL_ID} for all guilds (from CAP_CHANNEL_ID in .env)")
    else:
        print("WARNING: CAP_CHANNEL_ID not found in .env — notification_channel_id will be null for all guilds")

    new_guilds_data = {
        "update_channel_id": None,
        "guilds": {
            gid: {
                "name":                    gdata["name"],
                "api_key":                 gdata.get("api_key", ""),
                "role_id":                 gdata.get("role_id", 0),
                "notification_channel_id": CAP_CHANNEL_ID,
            }
            for gid, gdata in guilds.items()
        },
    }
    new_guilds_file = SERVER_DIR / "guilds.json"
    new_guilds_file.write_text(json.dumps(new_guilds_data, indent=2), encoding="utf-8")
    print(f"Wrote {new_guilds_file}")

    # --- player_api_list.json -> cluster-level player_registrations.json ---
    # Format: {discord_id: {api_key, guild_id}}
    old_players_file = BASE / "player_api_list.json"
    registrations = {}
    unassigned = {}

    if old_players_file.exists():
        raw = json.loads(old_players_file.read_text(encoding="utf-8"))
        for discord_id, data in raw.items():
            if isinstance(data, dict):
                guild_id = data.get("guild_id")
                api_key  = data.get("api_key", "")
            else:
                guild_id = None
                api_key  = data

            if guild_id and guild_id in guilds:
                registrations[discord_id] = {"api_key": api_key, "guild_id": guild_id}
            else:
                unassigned[discord_id] = {"api_key": api_key, "original_guild_id": guild_id}

        print(f"Migrated {len(registrations)} player registrations")
        if unassigned:
            print(f"WARNING: {len(unassigned)} players had no valid guild_id and were skipped:")
            for did, info in unassigned.items():
                print(f"  discord_id={did}  original_guild_id={info['original_guild_id']}")
    else:
        print("WARNING: player_api_list.json not found, skipping")

    reg_file = SERVER_DIR / "player_registrations.json"
    reg_file.write_text(json.dumps(registrations, indent=2), encoding="utf-8")
    print(f"Wrote {reg_file}")

    # --- capped_state.json -> cluster-level ---
    old_capped_file = BASE / "capped_state.json"
    capped_state = {}

    if old_capped_file.exists():
        capped_state = json.loads(old_capped_file.read_text(encoding="utf-8"))
        # Keep only players that made it into registrations
        capped_state = {k: v for k, v in capped_state.items() if k in registrations}
        print(f"Migrated {len(capped_state)} capped state entries")
    else:
        print("WARNING: capped_state.json not found, skipping")

    capped_file = SERVER_DIR / "capped_state.json"
    capped_file.write_text(json.dumps(capped_state, indent=2), encoding="utf-8")
    print(f"Wrote {capped_file}")

    # --- per-guild: player_list.json + season data files ---
    old_data_root = BASE / "data"

    for guild_id in guilds:
        guild_dir = SERVER_DIR / guild_id
        guild_dir.mkdir(parents=True, exist_ok=True)

        old_guild_dir = old_data_root / guild_id
        if old_guild_dir.exists():
            old_pl = old_guild_dir / "player_list.json"
            if old_pl.exists():
                shutil.copy2(old_pl, guild_dir / "player_list.json")
                print(f"  {guild_id}/player_list.json")

            data_dir = guild_dir / "data"
            data_dir.mkdir(exist_ok=True)
            for f in old_guild_dir.iterdir():
                if f.is_file() and f.name != "player_list.json":
                    shutil.copy2(f, data_dir / f.name)
                    print(f"  {guild_id}/data/{f.name}")
        else:
            print(f"  WARNING: data/{guild_id}/ not found, no season data copied")

    # --- live_leaderboards.json ---
    old_lb_file = BASE / "live_leaderboards.json"
    if old_lb_file.exists():
        shutil.copy2(old_lb_file, SERVER_DIR / "live_leaderboards.json")
        print("Copied live_leaderboards.json")
    else:
        print("WARNING: live_leaderboards.json not found, skipping")

    print("\nMigration complete.")
    print("Old files are untouched — verify clusters/ then remove them manually.")


if __name__ == "__main__":
    migrate()