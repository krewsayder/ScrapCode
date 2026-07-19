"""One-shot JSON->SQLite data migration (US-005, US-007, ADR-006 D7/D10/D11/D12).

CLI contract (driven via subprocess by the acceptance tests):

    python -m bot.db.migrations_json_to_sqlite \
        --source <json-copy> \
        --db <tmp.db> \
        --report <parity.json>

Behavior:
  - Reads the operator-copied `clusters/` tree (never `/opt/discord-bot/clusters/`
    directly — DEVOPS constraint).
  - Runs `PlayerListMigrator.migrate` once per v1 `player_list.json`
    (ADR-006 D12). `players.last_validated` gets the `1970-01-01T00:00:00Z`
    epoch sentinel for migrated v1 rows.
  - Populates the 8 easy-entity tables + `battle_hits` + `bomb_hits`.
    `replay_threads` / `replay_entries` are left empty (replay is 03-03 /
    JP6-JP10); the parity report still includes them as 0/0 MATCH.
  - Fernet-encrypts `api_key` on insert via the SqlAlchemyClusterRepository
    save methods (ADR-006 D7) — the migration never handles Fernet directly.
  - Idempotent (upsert-based); `alembic downgrade` reverses it.
  - Emits a per-table parity report JSON with shape:
        {"source": "...", "db": "...",
         "tables": {<name>: {"json": N, "sql": M, "status": "PASS"|"MISMATCH"}},
         "overall": "PASS"|"FAIL"}
  - Exits 0 on `overall: "PASS"`, non-zero on any `MISMATCH` (loud fail with
    rollback: all data tables are TRUNCATED so the SQLite file is left in a
    rolled-back uncommitted state — schema + alembic_version preserved).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config as AlembicConfig

from bot.db.models import Base
from bot.models import Cluster, Guild
from bot.migrations.player_list_migrations import PlayerListMigrator

# Parity report covers the easy-entity tables + battle/bomb hit tables.
# Replay tables (03-03) are included as 0/0 so the report shape is stable
# across slices — JP1 asserts every table's status is PASS.
PARITY_TABLES: tuple[str, ...] = (
    "guilds",
    "guild_member_roles",
    "role_tiers",
    "player_registrations",
    "players",
    "live_leaderboards",
    "live_lb_messages",
    "battle_hits",
    "bomb_hits",
    "replay_threads",
    "replay_entries",
)

# FK-safe delete order (children before parents) for rollback.
_DATA_TABLES_DELETE_ORDER: tuple[str, ...] = (
    "live_lb_messages",
    "live_leaderboards",
    "replay_entries",
    "replay_threads",
    "bomb_hits",
    "battle_hits",
    "players",
    "player_registrations",
    "guild_member_roles",
    "guilds",
    "role_tiers",
    "clusters",
)


def diff_counts(json_counts: dict[str, int], sql_counts: dict[str, int]) -> dict[str, dict]:
    """Build the `tables` portion of the parity report from raw counts.

    Pure function (its own driving port at domain scope). For each table in
    the union of `json_counts` / `sql_counts`, returns `{json, sql, status}`
    where status is `PASS` when the counts are equal else `MISMATCH`.
    """
    tables: dict[str, dict] = {}
    for name in set(json_counts) | set(sql_counts):
        j = json_counts.get(name, 0)
        s = sql_counts.get(name, 0)
        tables[name] = {"json": j, "sql": s, "status": "PASS" if j == s else "MISMATCH"}
    return tables


def build_parity_report(source: str, db: str) -> dict[str, Any]:
    """Compute the parity report by reading the JSON tree and the SQL DB.

    Re-reads the source JSON tree (post-migration state — v1 player_list
    files have already been inverted in memory by `run_migration`; the
    on-disk JSON is untouched, so json_counts are derived from the raw tree
    the same way `run_migration` derived them) and the SQLite row counts.
    """
    json_counts = _compute_json_counts(Path(source))
    sql_counts = _compute_sql_counts(db)
    tables = diff_counts(json_counts, sql_counts)
    overall = "PASS" if all(t["status"] == "PASS" for t in tables.values()) else "FAIL"
    return {"source": str(source), "db": str(db), "tables": tables, "overall": overall}


def run_migration(*, source: str, db: str, report: str | None = None) -> int:
    """Run the JSON->SQLite migration end-to-end. Returns the process exit code.

    Phases: validate source -> apply schema -> populate via repo save methods
    -> compute parity -> on MISMATCH rollback (TRUNCATE all data tables) +
    write FAIL report + return 1; on PASS write report + return 0.
    """
    source_path = Path(source)
    if not source_path.exists():
        msg = f"migration source not found: {source_path}"
        print(msg, file=os.sys.stderr)  # noqa: SLF001 — stderr is the right stream here
        return 2

    # Stamp the schema (idempotent — `alembic upgrade head` is a no-op if already
    # at head). The repository's create_all() fallback is intentionally NOT
    # used here because the probe (D8 step 2) requires a stamped alembic_version.
    _apply_schema(db)

    from bot.repository_sqlalchemy import SqlAlchemyClusterRepository
    fernet_key = os.environ.get("SCRAPCODE_DB_KEY", "")
    repo = SqlAlchemyClusterRepository(db_path=db, fernet_key=fernet_key)

    json_counts = _populate(repo, source_path)
    sql_counts = _compute_sql_counts(db)
    tables = diff_counts(json_counts, sql_counts)
    overall = "PASS" if all(t["status"] == "PASS" for t in tables.values()) else "FAIL"
    parity = {"source": str(source_path), "db": str(db), "tables": tables, "overall": overall}

    if overall == "FAIL":
        _rollback_data(db)
    if report is not None:
        Path(report).write_text(json.dumps(parity, indent=2, sort_keys=True), encoding="utf-8")
    return 0 if overall == "PASS" else 1


# ---------------------------------------------------------------------------
# Schema apply (alembic upgrade head against the target db)
# ---------------------------------------------------------------------------

def _apply_schema(db: str) -> None:
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(cfg, "head")


# ---------------------------------------------------------------------------
# JSON-tree -> SqlAlchemyClusterRepository population. Returns the
# json-derived row counts per table (the parity oracle).
# ---------------------------------------------------------------------------

def _populate(repo, source_path: Path) -> dict[str, int]:
    """Populate the DB via repo save methods; return json-derived counts."""
    counts = {name: 0 for name in PARITY_TABLES}
    for server_dir in sorted(p for p in source_path.iterdir() if p.is_dir() and p.name.isdigit()):
        server_id = int(server_dir.name)
        counts = _populate_one_server(repo, server_dir, server_id, counts)
    return counts


def _populate_one_server(repo, server_dir: Path, server_id: int,
                          counts: dict[str, int]) -> dict[str, int]:
    guilds_file = server_dir / "guilds.json"
    if not guilds_file.exists():
        return counts
    raw = json.loads(guilds_file.read_text(encoding="utf-8"))
    raw_guilds = raw.get("guilds", {})
    role_tiers = raw.get("role_tiers", {})

    # ADR-006 D12: `update_channel_id` is dropped (not stored). `guild_id` is
    # normalized to lowercase — production data already uses lowercase slugs,
    # so this is a no-op for clean trees and a data-integrity guard against
    # case-variant slug collisions (JP4 fails loudly when two keys collapse).
    cluster_guilds: dict[str, Guild] = {}
    for gid, gdata in raw_guilds.items():
        norm = gid.lower()
        cluster_guilds[norm] = Guild(
            id=norm,
            name=gdata.get("name", norm),
            api_key=gdata.get("api_key", ""),
            role_id=gdata.get("role_id", 0),
            notification_channel_id=gdata.get("notification_channel_id"),
            member_role_ids=gdata.get("member_role_ids", []),
        )
    cluster = Cluster(discord_server_id=server_id, guilds=cluster_guilds,
                      update_channel_id=None, role_tiers=role_tiers)
    repo.save(cluster)
    counts["guilds"] += len(raw_guilds)
    counts["guild_member_roles"] += sum(len(g.get("member_role_ids", [])) for g in raw_guilds.values())
    counts["role_tiers"] += sum(len(v) for v in role_tiers.values())

    counts = _populate_player_registrations(repo, server_dir, server_id, counts)
    counts = _populate_capped_state(repo, server_dir, server_id, counts)
    counts = _populate_live_leaderboards(repo, server_dir, server_id, counts)
    counts = _populate_player_lists(repo, server_dir, server_id, cluster_guilds, counts)
    counts = _populate_season_hits(repo, server_dir, server_id, cluster_guilds, counts)
    return counts


def _populate_player_registrations(repo, server_dir: Path, server_id: int,
                                    counts: dict[str, int]) -> dict[str, int]:
    path = server_dir / "player_registrations.json"
    if not path.exists():
        return counts
    data = json.loads(path.read_text(encoding="utf-8"))
    repo.save_player_registrations(server_id, data)
    counts["player_registrations"] += len(data)
    return counts


def _populate_capped_state(repo, server_dir: Path, server_id: int,
                            counts: dict[str, int]) -> dict[str, int]:
    path = server_dir / "capped_state.json"
    if not path.exists():
        return counts
    data = json.loads(path.read_text(encoding="utf-8"))
    repo.save_capped_state(server_id, data)
    # capped_state is the `is_capped` column on player_registrations — no
    # separate row count; the json count for `capped_state` is not a table.
    return counts


def _populate_live_leaderboards(repo, server_dir: Path, server_id: int,
                                  counts: dict[str, int]) -> dict[str, int]:
    path = server_dir / "live_leaderboards.json"
    if not path.exists():
        return counts
    data = json.loads(path.read_text(encoding="utf-8"))
    repo.save_live_leaderboards(server_id, data)
    counts["live_leaderboards"] += len(data)
    counts["live_lb_messages"] += sum(len(s.get("messages", {})) for s in data.values())
    return counts


def _populate_player_lists(repo, server_dir: Path, server_id: int,
                            cluster_guilds: dict[str, Guild],
                            counts: dict[str, int]) -> dict[str, int]:
    for guild_id in cluster_guilds:
        plist_path = server_dir / guild_id / "player_list.json"
        if not plist_path.exists():
            continue
        raw = json.loads(plist_path.read_text(encoding="utf-8"))
        # ADR-006 D12: invoke PlayerListMigrator once per file. v1 -> v2
        # inversion sets last_validated=epoch for migrated rows.
        migrated, _was = PlayerListMigrator.migrate(raw)
        repo.save_player_list(server_id, guild_id, migrated)
        counts["players"] += len(migrated.get("players", {}))
    return counts


def _populate_season_hits(repo, server_dir: Path, server_id: int,
                            cluster_guilds: dict[str, Guild],
                            counts: dict[str, int]) -> dict[str, int]:
    for guild_id in cluster_guilds:
        data_dir = server_dir / guild_id / "data"
        if not data_dir.exists():
            continue
        for season_file in sorted(data_dir.glob("highest_hits_season_*.json")):
            season = _season_from_filename(season_file.name, "highest_hits_season_", ".json")
            entries = _season_file_to_battle_entries(season_file)
            repo.upsert_battle_hits(server_id, guild_id, season, entries)
            counts["battle_hits"] += len(entries)
        for season_file in sorted(data_dir.glob("highest_bombs_season_*.json")):
            season = _season_from_filename(season_file.name, "highest_bombs_season_", ".json")
            entries = _season_file_to_bomb_entries(season_file)
            repo.upsert_bomb_hits(server_id, guild_id, season, entries)
            counts["bomb_hits"] += len(entries)
    return counts


def _season_from_filename(name: str, prefix: str, suffix: str) -> int:
    return int(name[len(prefix):-len(suffix)])


def _season_file_to_battle_entries(path: Path) -> list[dict]:
    """Reshape on-disk `boss_hits` nested shape -> API-shape flat entries.

    The SqlAlchemyClusterRepository.upsert_battle_hits contract mirrors the
    `process_api_response` entry shape (unitId/userId/completedOn/...), so
    the migration inverts the on-disk nesting back to that flat shape.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries: list[dict] = []
    for boss_id, encounters in raw.get("boss_hits", {}).items():
        for encounter_index, tiers in encounters.items():
            for tier_key, hit_list in tiers.items():
                for hit in hit_list:
                    entries.append({
                        "unitId": boss_id,
                        "encounterIndex": encounter_index,
                        "tier_key": tier_key,
                        "userId": hit.get("user_id", ""),
                        "damage": hit.get("damage", 0),
                        "completedOn": hit.get("completed_on", ""),
                        "heroDetails": hit.get("hero_details", []),
                        "machineOfWarDetails": hit.get("machine_of_war"),
                        "encounterType": hit.get("encounterType"),
                    })
    return entries


def _season_file_to_bomb_entries(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries: list[dict] = []
    for boss_id, encounters in raw.get("boss_hits", {}).items():
        for encounter_index, tiers in encounters.items():
            for tier_key, hit_list in tiers.items():
                for hit in hit_list:
                    entries.append({
                        "unitId": boss_id,
                        "encounterIndex": encounter_index,
                        "tier_key": tier_key,
                        "userId": hit.get("user_id", ""),
                        "damage": hit.get("damage", 0),
                        "completedOn": hit.get("completed_on", ""),
                        "encounterType": hit.get("encounterType"),
                    })
    return entries


# ---------------------------------------------------------------------------
# JSON-tree count oracle (re-derived from the raw tree for the parity report)
# ---------------------------------------------------------------------------

def _compute_json_counts(source_path: Path) -> dict[str, int]:
    counts = {name: 0 for name in PARITY_TABLES}
    if not source_path.exists():
        return counts
    for server_dir in sorted(p for p in source_path.iterdir() if p.is_dir() and p.name.isdigit()):
        guilds_file = server_dir / "guilds.json"
        if not guilds_file.exists():
            continue
        raw = json.loads(guilds_file.read_text(encoding="utf-8"))
        raw_guilds = raw.get("guilds", {})
        role_tiers = raw.get("role_tiers", {})
        counts["guilds"] += len(raw_guilds)
        counts["guild_member_roles"] += sum(len(g.get("member_role_ids", [])) for g in raw_guilds.values())
        counts["role_tiers"] += sum(len(v) for v in role_tiers.values())

        reg_path = server_dir / "player_registrations.json"
        if reg_path.exists():
            counts["player_registrations"] += len(json.loads(reg_path.read_text(encoding="utf-8")))

        lb_path = server_dir / "live_leaderboards.json"
        if lb_path.exists():
            lb = json.loads(lb_path.read_text(encoding="utf-8"))
            counts["live_leaderboards"] += len(lb)
            counts["live_lb_messages"] += sum(len(s.get("messages", {})) for s in lb.values())

        for gid in raw_guilds:
            plist_path = server_dir / gid / "player_list.json"
            if plist_path.exists():
                raw_pl = json.loads(plist_path.read_text(encoding="utf-8"))
                migrated, _ = PlayerListMigrator.migrate(raw_pl)
                counts["players"] += len(migrated.get("players", {}))
            data_dir = server_dir / gid / "data"
            if data_dir.exists():
                for f in data_dir.glob("highest_hits_season_*.json"):
                    counts["battle_hits"] += len(_season_file_to_battle_entries(f))
                for f in data_dir.glob("highest_bombs_season_*.json"):
                    counts["bomb_hits"] += len(_season_file_to_bomb_entries(f))
    return counts


def _compute_sql_counts(db: str) -> dict[str, int]:
    counts = {name: 0 for name in PARITY_TABLES}
    if not Path(db).exists():
        return counts
    conn = sqlite3.connect(db)
    try:
        for name in PARITY_TABLES:
            row = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()  # noqa: S608
            counts[name] = int(row[0]) if row else 0
    finally:
        conn.close()
    return counts


# ---------------------------------------------------------------------------
# Rollback: TRUNCATE all data tables (FK-safe order) on parity MISMATCH.
# Schema + alembic_version are preserved — the SQLite file is left in a
# rolled-back uncommitted state (JP4).
# ---------------------------------------------------------------------------

def _rollback_data(db: str) -> None:
    if not Path(db).exists():
        return
    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        for table in _DATA_TABLES_DELETE_ORDER:
            conn.execute(f"DELETE FROM {table}")  # noqa: S608
        conn.execute("PRAGMA foreign_keys=ON")
        conn.commit()
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bot.db.migrations_json_to_sqlite",
        description="Migrate a copied clusters/ JSON tree into a SQLite DB.",
    )
    parser.add_argument("--source", required=True,
                        help="Path to the copied clusters/ tree")
    parser.add_argument("--db", required=True,
                        help="Path to the target SQLite file")
    parser.add_argument("--report", default=None,
                        help="Path to write the parity report JSON")
    args = parser.parse_args(argv)
    return run_migration(source=args.source, db=args.db, report=args.report)


if __name__ == "__main__":
    raise SystemExit(main())