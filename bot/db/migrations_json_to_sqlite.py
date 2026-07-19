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

# ADR-006 D11: the global `replay_index.json` has no server_id, so the
# migration assigns EVERY replay entry to the one production discord_server_id.
# True multi-tenant replay partitioning is deferred (recorded in wave-decisions.md).
PROD_SERVER_ID = 1458181638453203099

# ADR-006 D10: the historical hardcoded forum/thread-ID constants
# that lived in bot/cogs/replay_cog.py are now the seed source for
# replay_threads. 04-03 removed them from the cog (CS9) and inlined
# them here -- the migration is the only remaining consumer (the cog
# reads thread IDs from replay_threads post-cutover). Single source
# of truth preserved; values are the exact originals.
FORUM_CHANNELS = {'Avatar': 1481592080940925062,
 'Cawl': 1481592218891456583,
 'Ghaz': 1481592447845929061,
 'Magnus': 1481592865800065037,
 'Mortarion': 1481592902059819059,
 'Riptide': 1481593074659622912,
 'Rogal Dorn': 1481593105831690291,
 'Screamer-Killer': 1481593216229707926,
 'Szarekh': 1481593184248266792,
 'Tervigon': 1481593333192069212,
 'Tyrant': 1481593363898695680}

MAP_THREADS = {'Tervigon': {'GB_01': 1481676928090898475,
              'GB_02': 1481676970612883527,
              'GB_03': 1481677016473534464,
              'GB_04': 1481677055337693365,
              'GB_05': 1481677104038023258,
              'GB_06': 1481677151622135921,
              'GB_support_01': 1481677960657375353,
              'GB_support_02': 1481677995541397715,
              'GB_support_03': 1481678034489839626,
              'GB_support_04': 1481678181223366797,
              'GB_support_05': 1481678254871023800,
              'GB_support_06': 1481678289730011146},
 'Tyrant': {'GB_01': 1481677221348507772,
            'GB_02': 1481677275597639790,
            'GB_03': 1481677478711005296,
            'GB_04': 1481677514018656457,
            'GB_05': 1481677621606613083,
            'GB_06': 1481677662199222344,
            'GB_support_01': 1481678326757331027,
            'GB_support_02': 1481678362274693252,
            'GB_support_03': 1481678396483436554,
            'GB_support_04': 1481678430368956496,
            'GB_support_05': 1481678466716799183,
            'GB_support_06': 1481678504574844979},
 'Avatar': {'GB_Khaine_01': 1481592319894618304,
            'GB_Khaine_02': 1481592399720611840,
            'GB_Khaine_03': 1481595045185589320,
            'GB_Khaine_04': 1481595099925188660,
            'Aethana GB_Khaine_support_01': 1481595238060392639,
            'Eldryon GB_Khaine_support_02': 1481595289839075349,
            'Aethana GB_Khaine_support_03': 1481595332054749256,
            'Eldryon GB_Khaine_support_04': 1481595381124173864,
            'Aethana GB_Khaine_support_05': 1481595429111201795,
            'Eldryon GB_Khaine_support_06': 1481595469665669182},
 'Cawl': {'GB_Belisarius_01': 1481596799201317006,
          'GB_Belisarius_02': 1481596839865094226,
          'GB_Belisarius_03': 1481596895716708404,
          'GB_Belisarius_04': 1481596928599916606,
          'Tan Gida GB_Belisarius_support_01': 1481596967225262173,
          'Actus GB_Belisarius_support_02': 1481597012750241882,
          'Tan Gida GB_Belisarius_support_03': 1481597055670550538,
          'Actus GB_Belisarius_support_04': 1481597096782991484,
          'Tan Gida GB_Belisarius_support_05': 1481597132329979915,
          'Actus GB_Belisarius_support_06': 1481597165045289081},
 'Ghaz': {'GB_Dakka_01': 1481597329130786899,
          'GB_Dakka_02': 1481597359443021885,
          'GB_Dakka_03': 1481597414111711365,
          'GB_Dakka_03_1': 1481597446063919136,
          'GB_Dakka_04': 1481597494038233128,
          'GB_Dakka_05': 1481597528939167844,
          'Gibba GB_Dakka_support_01': 1481597584459038730,
          'Tanksmasha GB_Dakka_support_02': 1481597621134164001,
          'Gibba GB_Dakka_support_03': 1481597653023195206,
          'Tanksmasha GB_Dakka_support_04': 1481597684987990056,
          'Tanksmasha GB_Dakka_support_04_1': 1481597735416102972,
          'Gibba GB_Dakka_support_05': 1481597769310277733,
          'Gibba GB_Dakka_support_05_1': 1481597810892738631,
          'Tanksmasha GB_Dakka_support_06': 1481597843784339597},
 'Mortarion': {'GB_Mortarion_01': 1481631207966900226,
               'GB_Mortarion_02': 1481631262266490900,
               'GB_Mortarion_03': 1481631296093683832,
               'GB_Mortarion_04': 1481631337302458461,
               'Rotbone GB_Mortarion_support_01': 1481631415203401880,
               'Corrodius GB_Mortarion_support_02': 1481631454890037330,
               'Rotbone GB_Mortarion_support_03': 1481631494953898075,
               'Corrodius GB_Mortarion_support_04': 1481631543825793144,
               'Rotbone GB_Mortarion_support_05': 1481631587618525308,
               'Corrodius GB_Mortarion_support_06': 1481631616928452628},
 'Riptide': {'GB_Riptide_01': 1481633511898222726,
             'GB_Riptide_02': 1481633548854362245,
             'GB_Riptide_03': 1481633589455224842,
             'Sho GB_Riptide_support_01': 1481633675014570066,
             'Sho GB_Riptide_support_02': 1481633721613291530,
             'Sho GB_Riptide_support_03': 1481633751652896829,
             'Sho GB_Riptide_support_04': 1481633778156961882,
             'Revas GB_Riptide_support_01': 1481633844594741268,
             'Revas GB_Riptide_support_02': 1481633874470637640,
             'Revas GB_Riptide_support_03': 1481633904916955226,
             'Revas GB_Riptide_support_04': 1481633937045590178},
 'Magnus': {'GB_Magnus_01': 1481628315767803934,
            'GB_Magnus_02': 1481628397636681738,
            'GB_Magnus_03': 1481628448140034080,
            'GB_Magnus_04': 1481628501575467078,
            'Abraxas GB_Magnus_support_01': 1481628609838841948,
            'Thaumachus GB_Magnus_support_02': 1481628689253793802,
            'Abraxas GB_Magnus_support_03': 1481628746606968925,
            'Thaumachus GB_Magnus_support_04': 1481628804236705975,
            'Abraxas GB_Magnus_support_05': 1481628867943989332,
            'Thaumachus GB_Magnus_support_06': 1481628946289397760},
 'Rogal Dorn': {'GB_RogalDorn_01': 1481631818703699989,
                'GB_RogalDorn_02': 1481631865642287166,
                'GB_RogalDorn_03': 1481631917261717515,
                'GB_RogalDorn_04': 1481631944188887223,
                'GB_RogalDorn_05': 1481631968776028261,
                'GB_RogalDorn_06': 1486177123147452518,
                'Sibyll GB_RogalDorn_support_01': 1481632100045033663,
                'Thad GB_RogalDorn_support_02': 1481632131234005093,
                'Sibyll GB_RogalDorn_support_03': 1481632160908709989,
                'Thad GB_RogalDorn_support_04': 1481632195088093184,
                'Sibyll GB_RogalDorn_support_05': 1481632224037048382,
                'Thad GB_RogalDorn_support_06': 1481632252772483163},
 'Screamer-Killer': {'GB_Screamer_01': 1481640960050860135,
                     'GB_Screamer_02': 1481640998449844244,
                     'GB_Screamer_03': 1481641030129422497,
                     'GB_Screamer_04': 1481641065189474504,
                     'Neuro GB_Screamer_support_01': 1481641151386882272,
                     'Neuro GB_Screamer_support_02': 1481641275966099476,
                     'Neuro GB_Screamer_support_03': 1481641362968285376,
                     'Neuro GB_Screamer_support_04': 1481641448188284939,
                     'Neuro GB_Screamer_support_05': 1481641558326513686,
                     'Neuro GB_Screamer_support_06': 1481641695941496915,
                     'Neuro GB_Screamer_support_07': 1481641806373589103,
                     'Neuro GB_Screamer_support_08': 1481641912652795936,
                     'Winged Prime GB_Screamer_support_01': 1481641206718009435,
                     'Winged Prime GB_Screamer_support_02': 1481641316206247976,
                     'Winged Prime GB_Screamer_support_03': 1481641409361481880,
                     'Winged Prime GB_Screamer_support_04': 1481641496506536138,
                     'Winged Prime GB_Screamer_support_05': 1481641626945196063,
                     'Winged Prime GB_Screamer_support_06': 1481641756507377674,
                     'Winged Prime GB_Screamer_support_07': 1481641863059476480,
                     'Winged Prime GB_Screamer_support_08': 1481641951349440563},
 'Szarekh': {'GB_SK_01': 1481671657293877288,
             'GB_SK_02': 1481671700021117061,
             'GB_SK_03': 1481671744350584873,
             'GB_SK_04': 1481671790072823880,
             'Left GB_SK_support_01': 1481671844137271349,
             'Left GB_SK_support_02': 1481671881802121342,
             'Left GB_SK_support_03': 1481671948248547328,
             'Left GB_SK_support_04': 1481672061364732227,
             'Left GB_SK_support_05': 1481672094717579455,
             'Left GB_SK_support_06': 1481672129056608470,
             'Left GB_SK_support_07': 1481672167459524830,
             'Left GB_SK_support_08': 1481672208395931648,
             'Right GB_SK_support_01': 1481672258224259143,
             'Right GB_SK_support_02': 1481672302813909254,
             'Right GB_SK_support_03': 1481672336901148815,
             'Right GB_SK_support_04': 1481672385756401815,
             'Right GB_SK_support_05': 1481672424960299029,
             'Right GB_SK_support_06': 1481672461647876258,
             'Right GB_SK_support_07': 1481672531067801600,
             'Right GB_SK_support_08': 1481672568674058271}}



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

    json_counts = _populate(repo, source_path, db)
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

def _populate(repo, source_path: Path, db: str) -> dict[str, int]:
    """Populate the DB via repo save methods; return json-derived counts."""
    counts = {name: 0 for name in PARITY_TABLES}
    for server_dir in sorted(p for p in source_path.iterdir() if p.is_dir() and p.name.isdigit()):
        server_id = int(server_dir.name)
        counts = _populate_one_server(repo, server_dir, server_id, counts)
    counts = _populate_replay(source_path, db, counts)
    return counts


# ---------------------------------------------------------------------------
# Replay population (ADR-006 D10/D11, US-007).
#
# `replay_index.json` is the global tenancy leak (ADR-004 §3): it lives at
# the project root, not under clusters/<server>/. The migration reads it from
# `source_path.parent` (the operator copies it alongside the clusters/ tree).
# Every entry is assigned to PROD_SERVER_ID (D11 — the JSON has no server_id).
# `replay_threads` is seeded from the hardcoded FORUM_CHANNELS / MAP_THREADS
# constants in bot.cogs.replay_cog (D10 — single source; 04-03 removes them
# from the cog). Seeding is conditional on replay_index.json existence so an
# empty source (JP10) produces 0/0 for replay tables too.
# ---------------------------------------------------------------------------

def _load_replay_constants():
    """Return the local FORUM_CHANNELS / MAP_THREADS seed constants.

    04-03 inlined the historical hardcoded constants (formerly in
    `bot.cogs.replay_cog`) into this migration module — they are the seed
    source for `replay_threads` and have no other consumer post-cutover
    (ADR-006 D10). The cog reads thread IDs from `replay_threads` via the
    repository; this module is the single source of truth for the seed.
    """
    return FORUM_CHANNELS, MAP_THREADS


def _populate_replay(source_path: Path, db: str,
                     counts: dict[str, int]) -> dict[str, int]:
    replay_index_path = source_path.parent / "replay_index.json"
    if not replay_index_path.exists():
        return counts
    replay_index = json.loads(replay_index_path.read_text(encoding="utf-8"))
    forum_channels, map_threads = _load_replay_constants()
    conn = sqlite3.connect(db)
    try:
        _seed_replay_threads(conn, forum_channels, map_threads, replay_index)
        entry_count = _insert_replay_entries(conn, replay_index)
    finally:
        conn.close()
    counts["replay_threads"] = sum(len(maps) for maps in map_threads.values())
    counts["replay_entries"] = entry_count
    return counts


def _seed_replay_threads(conn, forum_channels: dict, map_threads: dict,
                         replay_index: dict) -> None:
    for boss, maps in map_threads.items():
        forum_channel_id = forum_channels.get(boss)
        for map_name, thread_id in maps.items():
            index_message_id = _lookup_index_message_id(replay_index, boss, map_name)
            conn.execute(
                "INSERT OR IGNORE INTO replay_threads "
                "(discord_server_id, boss, map_name, forum_channel_id, thread_id, "
                "index_message_id) VALUES (?, ?, ?, ?, ?, ?)",
                (PROD_SERVER_ID, boss, map_name, forum_channel_id, thread_id,
                 index_message_id),
            )
    conn.commit()


def _lookup_index_message_id(replay_index: dict, boss: str, map_name: str):
    node = replay_index.get(boss, {}).get(map_name, {})
    return node.get("index_message_id")


def _insert_replay_entries(conn, replay_index: dict) -> int:
    count = 0
    for boss, maps in replay_index.items():
        for map_name, node in maps.items():
            index_message_id = node.get("index_message_id")
            for entry in node.get("entries", []):
                conn.execute(
                    "INSERT OR IGNORE INTO replay_entries "
                    "(discord_server_id, boss, map_name, team, tier, position, "
                    "damage_text, url, comment, submitted_by, index_message_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (PROD_SERVER_ID, boss, map_name,
                     entry.get("team", ""), entry.get("tier", ""),
                     entry.get("position", ""), entry.get("damage", ""),
                     entry.get("url", ""), entry.get("comment", ""),
                     str(entry.get("submitted_by", "")), index_message_id),
                )
                count += 1
    conn.commit()
    return count


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
    counts = _compute_replay_json_counts(source_path, counts)
    return counts


def _compute_replay_json_counts(source_path: Path,
                                counts: dict[str, int]) -> dict[str, int]:
    """Count replay rows the migration will seed (mirrors `_populate_replay`).

    `replay_threads` count = number of (boss, map) pairs in MAP_THREADS when
    replay_index.json exists (else 0 — JP10 empty source). `replay_entries`
    count = total entries across the replay_index.json tree.
    """
    replay_index_path = source_path.parent / "replay_index.json"
    if not replay_index_path.exists():
        return counts
    replay_index = json.loads(replay_index_path.read_text(encoding="utf-8"))
    _forum_channels, map_threads = _load_replay_constants()
    counts["replay_threads"] = sum(len(maps) for maps in map_threads.values())
    counts["replay_entries"] = sum(
        len(node.get("entries", []))
        for maps in replay_index.values()
        for node in maps.values()
    )
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