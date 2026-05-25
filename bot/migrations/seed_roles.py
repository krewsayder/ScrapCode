"""
Migration: seed role_tiers (cluster-level) and member_role_ids (per-guild)

Run once from the project root:
    python -m migrations.seed_roles

Prints a dry-run summary first, then writes on confirmation.
Guildy-wuildy is intentionally left empty — configure via /set_guild_member_role.
"""

import json
from pathlib import Path

SERVER_ID = 1458181638453203099
FILE = Path(f"clusters/{SERVER_ID}/guilds.json")

ROLE_TIERS = {
    "admin": [
        1469651912503591149,  # Tech-Priest
        1473339150538117282,  # Guild Leader
        1475207689310310721,  # Dark Tech
    ],
    "officer": [
        1473338520369238088,  # Captain
    ],
}

VETERAN = 1469196739402535066  # Veteran of the Long War — shared across 4 guilds

# Matched case-insensitively against guild name
GUILD_MEMBER_ROLES: dict[str, list[int]] = {
    "word bearer": [1458188417266618378, VETERAN],
    "night lord":  [1474313344486866965, VETERAN],
    "red corsair": [1472670744583340042, VETERAN],
    "dark mech":   [1469867393483407523],
    # guildy-wuildy: left empty, configure via /set_guild_member_role
}


def migrate(write: bool = False) -> None:
    if not FILE.exists():
        print(f"ERROR: {FILE} not found.")
        return

    data = json.loads(FILE.read_text(encoding="utf-8"))

    data["role_tiers"] = ROLE_TIERS
    print("Cluster role_tiers:")
    for tier, ids in ROLE_TIERS.items():
        print(f"  {tier}: {ids}")

    print("\nGuild member_role_ids:")
    for guild_id, guild in data.get("guilds", {}).items():
        name_lower = guild["name"].lower()
        matched_roles = []
        for keyword, roles in GUILD_MEMBER_ROLES.items():
            if keyword in name_lower:
                matched_roles = roles
                break
        guild["member_role_ids"] = matched_roles
        status = str(matched_roles) if matched_roles else "(empty — configure manually)"
        print(f"  {guild['name']}: {status}")

    if not write:
        print("\nDRY RUN — pass write=True or use --write to apply.")
        return

    FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print("\nWritten.")


if __name__ == "__main__":
    import sys
    migrate(write="--write" in sys.argv)