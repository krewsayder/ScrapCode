import json
from pathlib import Path

TRACKED_RARITIES = {"Legendary", "Mythic"}
TOP_N = 5

def load_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except: pass
    return {"boss_hits": {}}

def save_json(path: Path, data: dict):
    path.write_text(json.dumps(data, indent=2), encoding='utf-8')

def get_tier_key(entry: dict) -> str | None:
    rarity = entry.get("rarity")
    if rarity not in TRACKED_RARITIES:
        return None
    if rarity == "Mythic":
        try:
            tier = int(entry.get("set"))
            if tier == 0:
                return "Mythic"
            if tier == 1:
                return "Mythic_1"
        except (TypeError, ValueError):
            pass
        return None
    try:
        tier = int(entry.get("set"))
        if 0 <= tier <= 4:
            return f"Legendary_{tier}"
    except (TypeError, ValueError):
        pass
    return None

def get_roster_key(entry: dict) -> tuple:
    """Returns a hashable key representing a player + roster combination.
    Heroes are sorted so order doesn't matter. MoW is included."""
    user_id = entry.get("user_id", "")
    heroes = tuple(sorted(h.get("unitId", "") for h in entry.get("hero_details", [])))
    mow = entry.get("machine_of_war") or {}
    mow_id = mow.get("unitId", "") if mow else ""
    return (user_id, heroes, mow_id)

def try_insert(entries: list, new_entry: dict, check_roster: bool = False) -> bool:
    """Insert new_entry into entries if it qualifies.

    If check_roster is True (Battle hits):
      - Same player + same roster: only keep the higher damage hit.
      - Same player + different roster: allow as a separate entry.
    If check_roster is False (Bomb hits): original top-N logic, no deduplication.
    """
    damage = new_entry["damage"]

    if check_roster:
        new_key = get_roster_key(new_entry)

        # Check if this exact player+roster is already in the list
        for i, existing in enumerate(entries):
            if get_roster_key(existing) == new_key:
                # Same player, same roster — only keep the higher damage
                if damage > existing["damage"]:
                    entries[i] = new_entry
                    entries.sort(key=lambda e: e["damage"], reverse=True)
                    return True
                else:
                    return False  # Lower damage with same roster — skip

        # Different roster (or new player) — insert if it qualifies for top N
        if len(entries) < TOP_N or damage > entries[-1]["damage"]:
            entries.append(new_entry)
            entries.sort(key=lambda e: e["damage"], reverse=True)
            del entries[TOP_N:]
            return True
        return False

    else:
        # Original logic for Bombs — no roster deduplication
        if len(entries) < TOP_N or damage > entries[-1]["damage"]:
            entries.append(new_entry)
            entries.sort(key=lambda e: e["damage"], reverse=True)
            del entries[TOP_N:]
            return True
        return False

def process_api_response(api_data: dict, season: int, data_dir: Path = Path(".")):
    BATTLE_DETAILED_FILE = data_dir / f"highest_hits_season_{season}.json"
    BATTLE_SIMPLE_FILE   = data_dir / f"highest_hits_simple_season_{season}.json"
    BOMB_FILE            = data_dir / f"highest_bombs_season_{season}.json"

    battle_detailed = load_json(BATTLE_DETAILED_FILE)
    battle_simple = load_json(BATTLE_SIMPLE_FILE)
    bombs = load_json(BOMB_FILE)

    for entry in api_data.get("entries", []):
        tier_key = get_tier_key(entry)
        if tier_key is None:
            continue

        damage_type = entry.get("damageType")
        if damage_type not in ("Battle", "Bomb"):
            continue

        boss_id = str(entry["unitId"])
        damage = entry["damageDealt"]
        e_index = str(entry.get("encounterIndex", 0))

        if damage_type == "Battle":
            det_root = battle_detailed["boss_hits"].setdefault(boss_id, {}).setdefault(e_index, {})
            sim_root = battle_simple["boss_hits"].setdefault(boss_id, {}).setdefault(e_index, {})

            det_list = det_root.setdefault(tier_key, [])
            sim_list = sim_root.setdefault(tier_key, [])

            detailed_entry = {
                "encounterType": entry.get("encounterType"),
                "damage": damage,
                "user_id": entry["userId"],
                "completed_on": entry["completedOn"],
                "hero_details": entry.get("heroDetails", []),
                "machine_of_war": entry.get("machineOfWarDetails"),
            }
            simple_entry = {
                "damage": damage,
                "user_id": entry["userId"],
                "completed_on": entry["completedOn"],
                "encounter_type": entry.get("encounterType"),
            }

            # check_roster=True enforces the per-player per-roster deduplication
            if try_insert(det_list, detailed_entry, check_roster=True):
                try_insert(sim_list, simple_entry, check_roster=False)  # simple has no hero_details so no roster check
                print(f"[Battle] Updated {boss_id} Index {e_index} [{tier_key}]")

        elif damage_type == "Bomb":
            bomb_root = bombs["boss_hits"].setdefault(boss_id, {}).setdefault(e_index, {})
            bomb_list = bomb_root.setdefault(tier_key, [])

            bomb_entry = {
                "encounterType": entry.get("encounterType"),
                "damage": damage,
                "user_id": entry["userId"],
                "completed_on": entry["completedOn"],
            }

            if try_insert(bomb_list, bomb_entry, check_roster=False):
                print(f"[Bomb] Updated {boss_id} Index {e_index} [{tier_key}]")

    save_json(BATTLE_DETAILED_FILE, battle_detailed)
    save_json(BATTLE_SIMPLE_FILE, battle_simple)
    save_json(BOMB_FILE, bombs)