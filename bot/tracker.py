import os

TRACKED_RARITIES = {"Legendary", "Mythic"}
TOP_N = 5


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

    Retained for the JSON rollback impl (`bot.repository.JsonClusterRepository`
    imports it) and for the tiebreak contract pin (RC14 /
    `bot/tests/test_tracker_tiebreak.py`). The SQLite write path
    (`process_api_response`) no longer calls this — the SQL upsert enforces
    keep-max(damage) (RC15). Removed from `bot.tracker` once the JSON impl's
    `try_insert` import is retired (04-04 / later cleanup).
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
                    entries.sort(key=lambda e: (-e["damage"], e.get("completed_on", "")))
                    return True
                else:
                    return False  # Lower damage with same roster — skip

        # Different roster (or new player) — insert if it qualifies for top N
        if len(entries) < TOP_N or damage > entries[-1]["damage"]:
            entries.append(new_entry)
            entries.sort(key=lambda e: (-e["damage"], e.get("completed_on", "")))
            del entries[TOP_N:]
            return True
        return False

    else:
        # Original logic for Bombs — no roster deduplication
        if len(entries) < TOP_N or damage > entries[-1]["damage"]:
            entries.append(new_entry)
            entries.sort(key=lambda e: (-e["damage"], e.get("completed_on", "")))
            del entries[TOP_N:]
            return True
        return False


def process_api_response(api_data: dict, season: int,
                          discord_server_id: int, guild_id: str) -> None:
    """Upsert Tacticus API entries into battle_hits / bomb_hits via the repo.

    Replaces the JSON season-file write path (ADR-006 D4 / ADR-007 / US-008).
    The `data_dir` parameter is gone — the SQL partition key
    `(season, discord_server_id, guild_id)` replaces it. Entries are filtered
    by tracked rarity (`get_tier_key`) and routed by `damageType` to the
    repo's `upsert_battle_hits` / `upsert_bomb_hits`. The in-memory
    `try_insert` dedup is retired from this path — the SQL upsert enforces
    keep-max(damage) (RC15). No `highest_*_season_*.json` file is written.
    """
    repo = _get_write_repo()
    battle_entries: list[dict] = []
    bomb_entries: list[dict] = []
    for entry in api_data.get("entries", []):
        if get_tier_key(entry) is None:
            continue
        damage_type = entry.get("damageType")
        if damage_type == "Battle":
            battle_entries.append(entry)
        elif damage_type == "Bomb":
            bomb_entries.append(entry)
    # One transaction per upsert type (each repo method opens one
    # session_scope). The single-transaction-per-guild wrap (ADR-006 D6) and
    # the crash-injection assertion land in 04-05 (AP7); the repo API is
    # extended then (off-limits to modify in 04-01).
    repo.upsert_battle_hits(discord_server_id, guild_id, season, battle_entries)
    repo.upsert_bomb_hits(discord_server_id, guild_id, season, bomb_entries)


def _get_write_repo():
    """Resolve the write-side ClusterRepository from SCRAPCODE_REPO_BACKEND.

    04-01 bridge: the composition-root singleton (`bot.guilds.repo`) is still
    `JsonClusterRepository` (flipped to env-driven SQLite in 04-04). The write
    path reads `SCRAPCODE_REPO_BACKEND` so `process_api_response` uses the
    SQLite impl when the operator has selected it, independent of the
    import-time singleton. 04-04 replaces this with `from bot.guilds import
    repo` once the singleton is env-driven.
    """
    backend = os.getenv("SCRAPCODE_REPO_BACKEND", "json")
    if backend == "sqlite":
        from bot.repository_sqlalchemy import SqlAlchemyClusterRepository
        return SqlAlchemyClusterRepository()
    from bot.guilds import repo
    return repo