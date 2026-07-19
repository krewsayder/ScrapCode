"""Repository contract acceptance tests (US-001, US-002, US-003, US-004, US-006).

Implements `acceptance/repository-contract.feature`. Driven through the
ClusterRepository ABC (the port) — both impls are parametrized via the
`impl_pair` fixture. The JSON parametrization is green (real impl); the
SQLite parametrization is RED (scaffold raises AssertionError) and flips
green once DELIVER lands the real `SqlAlchemyClusterRepository`.

One scenario is enabled per the skill's one-test-at-a-time rule; the rest
are marked `pytest.mark.skip` with a "RED scaffold" reason. The DELIVER
crafter enables them one at a time as the SQLite impl is filled in.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bot.models import Cluster, Guild
from bot.repository import ClusterRepository, JsonClusterRepository
from bot.migrations.player_list_migrations import PlayerListMigrator
from bot.tracker import try_insert, TOP_N


# ---------------------------------------------------------------------------
# Schema-step driving port: a fresh SQLite file upgraded to the Alembic
# baseline (ADR-006 D3). Used by the schema-constraint scenarios RC10/RC11,
# which assert behavior at the SQL-constraint boundary (the driving port for
# a schema step). The repo adapter impl lands in 02-03.
# ---------------------------------------------------------------------------

@pytest.fixture
def alembic_upgraded_db(tmp_path: Path) -> Path:
    """Run `alembic upgrade head` against a fresh SQLite file; return its path."""
    from alembic.config import Config
    from alembic import command

    repo_root = Path(__import__("bot").__file__).resolve().parent.parent
    ini_path = repo_root / "bot" / "db" / "alembic.ini"
    db_path = tmp_path / "schema.db"
    cfg = Config(str(ini_path))
    cfg.set_main_option("script_location", str(repo_root / "bot" / "db" / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    command.upgrade(cfg, "head")
    return db_path

# ---------------------------------------------------------------------------
# RC-1 (ENABLED — first scenario): every ABC method round-trips through the
# repository, parametrized over both impls.
# ---------------------------------------------------------------------------

def test_every_abc_method_round_trips_through_the_repository(impl_pair, tmp_clusters_tree):
    """@driving_port @kpi — RC1.

    The SQLite impl (scaffold) raises AssertionError on construction, so
    this test fails RED on the sqlite parametrization. The JSON
    parametrization passes (real impl + real JSON-backed impls of the 4
    new ADR-007 methods). That is the correct RED state: the contract is
    defined by the JSON impl and the SQLite impl must satisfy it.
    """
    repo = impl_pair
    server = 1458181638453203099

    # load_guilds round-trip via save + load
    original = repo.load(server)
    assert isinstance(original, Cluster)
    repo.save(original)
    reloaded = repo.load(server)
    assert set(reloaded.guilds.keys()) == {"neuro", "mech"}
    assert reloaded.role_tiers == {"admin": [111], "officer": [222]}

    # player_registrations
    regs = {"123456789": {"api_key": "tacticus-abc", "guild_id": "neuro"}}
    repo.save_player_registrations(server, regs)
    assert repo.load_player_registrations(server) == regs

    # capped_state
    capped = {"123456789": True}
    repo.save_capped_state(server, capped)
    assert repo.load_capped_state(server) == capped

    # player_list (v2)
    plist = {
        "__meta__": {"version": 2},
        "players": {
            "tacticus-uid-001": {
                "display_name": "Maria Santos",
                "last_validated": "2026-07-18T10:00:00Z",
                "is_former": False,
            }
        },
    }
    repo.save_player_list(server, "neuro", plist)
    assert repo.load_player_list(server, "neuro") == plist

    # live_leaderboards
    lbs = {"cluster": {"channel_id": 888, "messages": {"Legendary_0": 333333}, "season": 94}}
    repo.save_live_leaderboards(server, lbs)
    assert repo.load_live_leaderboards(server) == lbs

    # list_server_ids
    ids = set(repo.list_server_ids())
    assert 1458181638453203099 in ids

    # The 4 new ADR-007 methods round-trip battle + bomb hits
    repo.upsert_battle_hits(server, "neuro", 94, [])
    repo.upsert_bomb_hits(server, "neuro", 94, [])
    battle = repo.load_battle_hits(server, "neuro", 94)
    bomb = repo.load_bomb_hits(server, "neuro", 94)
    assert "boss_hits" in battle
    assert "boss_hits" in bomb


# ---------------------------------------------------------------------------
# The remaining scenarios are skipped until DELIVER enables them one at a time.
# ---------------------------------------------------------------------------

RED = pytest.mark.skip(reason="RED scaffold — enable one at a time in DELIVER")


def test_four_new_abc_methods_round_trip_battle_and_bomb_hits(impl_pair, make_tacticus_entry):
    """@driving_port — RC2."""
    repo = impl_pair
    entries = [make_tacticus_entry(damage_type="Battle", damage=12000, rarity="Legendary", set_=0)]
    repo.upsert_battle_hits(1458181638453203099, "neuro", 94, entries)
    battle = repo.load_battle_hits(1458181638453203099, "neuro", 94)
    assert battle["boss_hits"]["Avatar"]["0"]["Legendary_0"][0]["damage"] == 12000


def test_RC3_player_list_v2_round_trips_without_invoking_the_migrator(json_repo):
    """RC3 — v2 file should not be rewritten on load."""
    server = 1458181638453203099
    path = json_repo._guild_path(server, "neuro") / "player_list.json"
    before_mtime = path.stat().st_mtime_ns
    loaded = json_repo.load_player_list(server, "neuro")
    after_mtime = path.stat().st_mtime_ns
    assert loaded["__meta__"]["version"] == 2
    assert "tacticus-uid-001" in loaded["players"]
    assert before_mtime == after_mtime, "v2 file was rewritten (migrator should be a noop)"


def test_load_player_list_returns_v2_dict_shape_cogs_expect(sqlite_repo):
    """@driving_port — RC4."""
    plist = sqlite_repo.load_player_list(1458181638453203099, "neuro")
    assert plist["__meta__"]["version"] == 2
    assert "players" in plist


def test_RC5_list_server_ids_enumerates_only_numeric_dirs(json_repo, tmp_clusters_tree):
    """RC5 — only numeric server directories."""
    # Add a stray non-numeric entry beside the server dir
    (tmp_clusters_tree / ".gitkeep").write_text("", encoding="utf-8")
    ids = sorted(json_repo.list_server_ids())
    assert ids == [1458181638453203099, 9876543210] if 9876543210 in ids else ids
    assert 1458181638453203099 in ids
    assert all(isinstance(i, int) for i in ids)


def test_RC6_silent_empty_on_corruption_pinned_as_trap_to_retire(json_repo, tmp_clusters_tree):
    """@infrastructure-failure — RC6 — JSON impl silent-empty; RETIRE in Slice 02.

    The JSON impl's `_read_json` swallows the parse exception and returns an
    empty dict, so a truncated `guilds.json` is indistinguishable from a freshly
    initialized cluster (ADR-002 §4: silent-empty-on-corruption trap). This
    pins that behavior as the contract Slice 02 retires when the SQLite impl
    raises on corruption instead (RC7).
    """
    server = 1458181638453203099
    guilds_file = tmp_clusters_tree / str(server) / "guilds.json"
    guilds_file.write_text("{broken", encoding="utf-8")
    cluster = json_repo.load(server)
    assert cluster.discord_server_id == server
    assert cluster.guilds == {}
    # Annotated: this is the behavior Slice 02 retires for the SQLite impl.


def test_corrupted_sqlite_database_raises_not_returns_empty(env_vars, sqlite_db_path, sqlite_repo):
    """@infrastructure-failure @real-io — RC7 — silent-empty trap retired on SQLite."""
    sqlite_db_path.parent.mkdir(parents=True, exist_ok=True)
    sqlite_db_path.write_text("not a database", encoding="utf-8")
    with pytest.raises(Exception):
        sqlite_repo.load(1458181638453203099)


def test_empty_sqlite_database_returns_empty_dicts_without_raising(sqlite_repo):
    """@edge — RC8."""
    assert sqlite_repo.load(1458181638453203099).guilds == {}
    assert sqlite_repo.load_player_registrations(1458181638453203099) == {}
    assert sqlite_repo.load_capped_state(1458181638453203099) == {}
    assert "players" in sqlite_repo.load_player_list(1458181638453203099, "neuro")


def test_api_key_encrypted_at_rest_decrypted_on_read(env_vars, sqlite_repo):
    """@real-io @adapter-integration — RC9."""
    cluster = Cluster(
        discord_server_id=1458181638453203099,
        guilds={"neuro": Guild(id="neuro", name="Neuro", api_key="tacticus-neuro-key",
                                role_id=999)},
    )
    sqlite_repo.save(cluster)
    # Direct sqlite3 query returns ciphertext, not plaintext
    import sqlite3
    conn = sqlite3.connect(env_vars["SCRAPCODE_DB_PATH"])
    row = conn.execute("SELECT api_key FROM guilds WHERE guild_id='neuro'").fetchone()
    conn.close()
    assert row[0] != "tacticus-neuro-key"
    # The repo decrypts on read
    loaded = sqlite_repo.load(1458181638453203099)
    assert loaded.guilds["neuro"].api_key == "tacticus-neuro-key"


def test_player_registrations_api_key_uniqueness_enforced(alembic_upgraded_db):
    """@infrastructure-failure @real-io — RC10.

    Schema step driving port: an alembic-upgraded SQLite database. A second
    `player_registrations` row with the same `api_key_hmac` value fails with
    a UNIQUE-constraint violation on the `api_key_hmac` column (ADR-006 D7:
    the 1:1 binding the non-deterministic Fernet ciphertext cannot enforce).
    """
    import sqlite3
    conn = sqlite3.connect(alembic_upgraded_db)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("INSERT INTO clusters (discord_server_id) VALUES (?)",
                 (1458181638453203099,))
    conn.execute(
        "INSERT INTO guilds (discord_server_id, guild_id, name, api_key, api_key_hmac, role_id) "
        "VALUES (?, 'neuro', 'Neuro', 'cipher-A', 'hmac-shared', 999)",
        (1458181638453203099,),
    )
    conn.execute(
        "INSERT INTO guilds (discord_server_id, guild_id, name, api_key, api_key_hmac, role_id) "
        "VALUES (?, 'mech', 'Mech', 'cipher-B', 'hmac-mech', 888)",
        (1458181638453203099,),
    )
    conn.execute(
        "INSERT INTO player_registrations (discord_id, discord_server_id, guild_id, api_key, api_key_hmac, is_capped) "
        "VALUES ('123456789', ?, 'neuro', 'cipher-A', 'hmac-shared', 0)",
        (1458181638453203099,),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO player_registrations (discord_id, discord_server_id, guild_id, api_key, api_key_hmac, is_capped) "
            "VALUES ('987654321', ?, 'mech', 'cipher-A', 'hmac-shared', 0)",
            (1458181638453203099,),
        )
    conn.close()


def test_role_tiers_check_constraint_rejects_invalid_tier(alembic_upgraded_db):
    """@infrastructure-failure @real-io — RC11.

    Schema step driving port: an alembic-upgraded SQLite database. An INSERT
    into `role_tiers` with `tier='superuser'` fails with a CHECK-constraint
    violation (data-dictionary §2.1: `tier IN ('admin','officer')`).
    """
    import sqlite3
    conn = sqlite3.connect(alembic_upgraded_db)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("INSERT INTO clusters (discord_server_id) VALUES (?)",
                 (1458181638453203099,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO role_tiers (discord_server_id, tier, role_id) VALUES (?, 'superuser', 999)",
            (1458181638453203099,),
        )
    conn.close()


def test_guild_with_empty_api_key_round_trips(impl_pair):
    """@edge — RC12."""
    repo = impl_pair
    cluster = Cluster(
        discord_server_id=1458181638453203099,
        guilds={"mech": Guild(id="mech", name="Mech", api_key="", role_id=888)},
    )
    repo.save(cluster)
    loaded = repo.load(1458181638453203099)
    assert loaded.guilds["mech"].api_key == ""


def test_RC13_player_list_migrator_v1_to_v2_inverts_and_v2_is_noop():
    """@property — RC13."""
    v1 = {"Maria Santos": "tacticus-uid-001"}
    v2, was_migrated = PlayerListMigrator.migrate(v1)
    assert was_migrated is True
    assert v2 == {
        "__meta__": {"version": 2},
        "players": {
            "tacticus-uid-001": {
                "display_name": "Maria Santos",
                "last_validated": "1970-01-01T00:00:00Z",
                "is_former": False,
            }
        },
    }
    v2_again, was_migrated_again = PlayerListMigrator.migrate(v2)
    assert was_migrated_again is False
    assert v2_again == v2


def test_RC14_try_insert_dedup_branches_pinned():
    """@property — RC14 — the contract the SQL upsert must preserve."""

    def _entry(uid, damage, completed_on, *, heroes=None, mow=None):
        return {
            "damage": damage,
            "user_id": uid,
            "completed_on": completed_on,
            "hero_details": [{"unitId": h} for h in (heroes or [])],
            "machine_of_war": {"unitId": mow} if mow else None,
        }

    # same-roster-equal keeps first
    entries = []
    assert try_insert(entries, _entry("A", 100, "2026-07-18T10:00:00Z", heroes=["X"], mow="Y"),
                      check_roster=True) is True
    assert try_insert(entries, _entry("A", 100, "2026-07-18T11:00:00Z", heroes=["X"], mow="Y"),
                      check_roster=True) is False
    assert len(entries) == 1
    assert entries[0]["completed_on"] == "2026-07-18T10:00:00Z"

    # same-roster-higher replaces
    assert try_insert(entries, _entry("A", 150, "2026-07-18T12:00:00Z", heroes=["X"], mow="Y"),
                      check_roster=True) is True
    assert entries[0]["damage"] == 150

    # different-roster inserts separately
    assert try_insert(entries, _entry("A", 90, "2026-07-18T13:00:00Z", heroes=["Z"], mow="Y"),
                      check_roster=True) is True
    assert len(entries) == 2

    # top-N truncation drops the lowest when a higher hit arrives
    entries = []
    for d in (100, 90, 80, 70, 60):
        assert try_insert(entries, _entry(uid := f"u{d}", d, "2026-07-18T10:00:00Z"),
                          check_roster=False) is True
    assert len(entries) == TOP_N
    assert try_insert(entries, _entry("u75", 75, "2026-07-18T11:00:00Z"),
                      check_roster=False) is True
    assert len(entries) == TOP_N
    assert all(e["damage"] != 60 for e in entries)
    assert any(e["damage"] == 75 for e in entries)


def test_upsert_keep_max_on_battle_hits_preserves_try_insert_contract(battle_hits_sqlite_repo, make_tacticus_entry):
    """@property @real-io — RC15."""
    server, guild, season = 1458181638453203099, "neuro", 94
    base = make_tacticus_entry(damage=12000, hero_details=[{"unitId": "Aethana"}, {"unitId": "Eldryon"}],
                               machine_of_war={"unitId": "Khaine"})

    # same roster, higher damage — replaces
    higher = make_tacticus_entry(damage=15000, hero_details=[{"unitId": "Aethana"}, {"unitId": "Eldryon"}],
                                machine_of_war={"unitId": "Khaine"})
    battle_hits_sqlite_repo.upsert_battle_hits(server, guild, season, [base, higher])
    battle = battle_hits_sqlite_repo.load_battle_hits(server, guild, season)
    assert battle["boss_hits"]["Avatar"]["0"]["Legendary_0"][0]["damage"] == 15000

    # same roster, lower damage — keep-max (row stays at 15000)
    lower = make_tacticus_entry(damage=9000, hero_details=[{"unitId": "Aethana"}, {"unitId": "Eldryon"}],
                                machine_of_war={"unitId": "Khaine"})
    battle_hits_sqlite_repo.upsert_battle_hits(server, guild, season, [lower])
    battle = battle_hits_sqlite_repo.load_battle_hits(server, guild, season)
    assert battle["boss_hits"]["Avatar"]["0"]["Legendary_0"][0]["damage"] == 15000

    # different roster — separate row
    diff = make_tacticus_entry(damage=9000, hero_details=[{"unitId": "Aethana"}, {"unitId": "Tan Gida"}],
                              machine_of_war={"unitId": "Khaine"})
    battle_hits_sqlite_repo.upsert_battle_hits(server, guild, season, [diff])
    battle = battle_hits_sqlite_repo.load_battle_hits(server, guild, season)
    assert len(battle["boss_hits"]["Avatar"]["0"]["Legendary_0"]) == 2

    # bomb plain top-N (no roster dedup)
    bombs = [make_tacticus_entry(damage_type="Bomb", damage=d, user_id=f"u{d}",
                                 hero_details=[], machine_of_war=None)
             for d in (100, 90, 80, 70, 60, 50)]
    battle_hits_sqlite_repo.upsert_bomb_hits(server, guild, season, bombs)
    bomb = battle_hits_sqlite_repo.load_bomb_hits(server, guild, season)
    flat = bomb["boss_hits"]["Avatar"]["0"]["Legendary_0"]
    assert len(flat) == TOP_N
    assert [e["damage"] for e in flat] == [100, 90, 80, 70, 60]


@RED
def test_battle_hits_simple_dropped_from_schema_and_tracker():
    """@kpi — RC16."""
    bot_tracker = Path(__import__("bot.tracker").__file__)
    src = bot_tracker.read_text(encoding="utf-8")
    assert "BATTLE_SIMPLE_FILE" not in src, "battle_hits_simple write still present in tracker.py"
    # The SQLite schema (inspected via the models) MUST NOT declare a
    # battle_hits_simple table.
    from bot.db import models
    assert not hasattr(models, "BattleHitSimpleRow"), "battle_hits_simple table must not exist"


# ---------------------------------------------------------------------------
# Step 02-01 unit tests — schema-shape declarations (driving port = the ORM
# metadata). Behavior budget: 5 behaviors (12 model classes declared;
# player_registrations.api_key_hmac UNIQUE NOT NULL; role_tiers CHECK;
# no update_channel_id column anywhere; no battle_hits_simple table) -> max 10
# unit tests. Behavior 2 (UNIQUE) and 3 (CHECK) are exercised end-to-end by
# RC10/RC11 above; these unit tests pin the metadata declarations that make
# those acceptance tests pass.
# ---------------------------------------------------------------------------

EXPECTED_MODEL_CLASSES = (
    "ClusterRow",
    "GuildRow",
    "GuildMemberRoleRow",
    "RoleTierRow",
    "PlayerRegistrationRow",
    "PlayerRow",
    "BattleHitRow",
    "BombHitRow",
    "ReplayEntryRow",
    "ReplayThreadRow",
    "LiveLeaderboardRow",
    "LiveLbMessageRow",
)


def test_models_declare_all_twelve_orm_classes_without_scaffold_markers():
    """@driving_port — the schema metadata declares the 12 ORM tables and
    carries zero `__SCAFFOLD__` markers (ADR-006 D3)."""
    from bot.db import models
    for name in EXPECTED_MODEL_CLASSES:
        assert hasattr(models, name), f"missing model class: {name}"
    assert not hasattr(models, "BattleHitSimpleRow"), "BattleHitSimpleRow must not exist (ADR-006 D4)"
    assert not hasattr(models, "__SCAFFOLD__"), "__SCAFFOLD__ marker still present in bot.db.models"
    # Each model class contributes a __table__ to the metadata.
    base = models.Base
    table_names = set(base.metadata.tables.keys())
    expected_tables = {
        "clusters", "role_tiers", "guilds", "guild_member_roles",
        "player_registrations", "players", "battle_hits", "bomb_hits",
        "replay_entries", "replay_threads", "live_leaderboards", "live_lb_messages",
    }
    assert expected_tables <= table_names, f"missing tables: {expected_tables - table_names}"
    assert "battle_hits_simple" not in table_names, "battle_hits_simple table must not be declared"


def test_player_registrations_api_key_hmac_is_unique_and_not_null():
    """@driving_port — the `api_key_hmac` column on `player_registrations`
    is declared UNIQUE NOT NULL (ADR-006 D7) and `is_capped` is a column
    (ADR-006 D5)."""
    from bot.db.models import PlayerRegistrationRow
    cols = PlayerRegistrationRow.__table__.columns
    hmac_col = cols["api_key_hmac"]
    assert hmac_col.nullable is False, "api_key_hmac must be NOT NULL"
    assert hmac_col.unique is True, "api_key_hmac must be UNIQUE"
    assert "is_capped" in cols, "is_capped column missing (ADR-006 D5)"


def test_role_tiers_check_constraint_pins_admin_and_officer_only():
    """@driving_port — `role_tiers.tier` has a CHECK constraint restricting
    values to `admin` / `officer` (data-dictionary §2.1)."""
    from bot.db.models import RoleTierRow
    tier_col = RoleTierRow.__table__.columns["tier"]
    check_sql = " ".join(str(c.sqltext).upper() for c in RoleTierRow.__table__.constraints
                         if c.__class__.__name__ == "CheckConstraint")
    assert "TIER" in check_sql, "no CHECK constraint referencing tier"
    assert "ADMIN" in check_sql and "OFFICER" in check_sql, "CHECK must allow admin and officer"


def test_no_update_channel_id_column_in_any_table():
    """@driving_port — ADR-006 D12: `update_channel_id` is dropped from the
    SQL schema. No table in the metadata declares it."""
    from bot.db.models import Base
    for table_name, table in Base.metadata.tables.items():
        assert "update_channel_id" not in table.columns, (
            f"table {table_name} declares update_channel_id (ADR-006 D12 violation)"
        )


def test_alembic_baseline_creates_all_tables_on_fresh_sqlite_file(alembic_upgraded_db):
    """@driving_port @real-io — `alembic upgrade head` against a fresh
    SQLite file creates all 12 tables and leaves `battle_hits_simple`
    absent (ADR-006 D3/D4)."""
    import sqlite3
    conn = sqlite3.connect(alembic_upgraded_db)
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    conn.close()
    tables = {r[0] for r in rows}
    expected = {
        "clusters", "role_tiers", "guilds", "guild_member_roles",
        "player_registrations", "players", "battle_hits", "bomb_hits",
        "replay_entries", "replay_threads", "live_leaderboards", "live_lb_messages",
    }
    assert expected <= tables, f"alembic missed tables: {expected - tables}"
    assert "battle_hits_simple" not in tables, "battle_hits_simple was created by alembic (ADR-006 D4)"


# ---------------------------------------------------------------------------
# Step 02-03 — parametrized-contract seeding.
# RC1 (test_every_abc_method_round_trips...) asserts neuro/mech exist after
# load+save+load. The JSON parametrization gets those from tmp_clusters_tree;
# the SQLite parametrization needs the same data, so the sqlite side of
# impl_pair is a SEEDED repo (populated from the JSON tree via the repo's own
# save methods — not the one-shot migration script, which is 03-XX). The
# other scenarios (RC4/RC7/RC8/RC9/RC12) use the UNSEEDED `sqlite_repo`
# fixture from conftest: RC8 asserts an empty DB, RC7 corrupts the DB, RC9
# saves its own cluster, RC4/RC12 do not depend on pre-existing rows. This
# fixture overrides conftest's `impl_pair` for this module only (pytest
# fixture lookup: test-module > conftest).
# ---------------------------------------------------------------------------

PROD_SERVER = 1458181638453203099


@pytest.fixture
def seeded_sqlite_repo(env_vars, tmp_clusters_tree):
    """SQLite repo seeded from the synthetic JSON tree (RC1 contract parity)."""
    from bot.repository_sqlalchemy import SqlAlchemyClusterRepository
    from bot.repository import JsonClusterRepository
    repo = SqlAlchemyClusterRepository(
        db_path=env_vars["SCRAPCODE_DB_PATH"],
        fernet_key=env_vars["SCRAPCODE_DB_KEY"],
    )
    json_repo = JsonClusterRepository(base_path=tmp_clusters_tree)
    repo.save(json_repo.load(PROD_SERVER))
    repo.save_player_registrations(PROD_SERVER, json_repo.load_player_registrations(PROD_SERVER))
    repo.save_capped_state(PROD_SERVER, json_repo.load_capped_state(PROD_SERVER))
    for guild_id in ("neuro", "mech"):
        repo.save_player_list(PROD_SERVER, guild_id, json_repo.load_player_list(PROD_SERVER, guild_id))
    repo.save_live_leaderboards(PROD_SERVER, json_repo.load_live_leaderboards(PROD_SERVER))
    return repo


@pytest.fixture(params=["json", "sqlite"], ids=["json", "sqlite"])
def impl_pair(request, json_repo, seeded_sqlite_repo):
    """Module-local override of conftest.impl_pair: the sqlite parametrization
    uses the SEEDED repo so RC1's neuro/mech assertions hold."""
    return json_repo if request.param == "json" else seeded_sqlite_repo


@pytest.fixture
def battle_hits_sqlite_repo(sqlite_repo):
    """SQLite repo with a minimal cluster + guild row so the battle_hits /
    bomb_hits FK to guilds is satisfiable. This is a PRECONDITION fixture
    (the guild must exist before any hit can be written), not the expected
    end-state — the hits themselves come from the production upsert path."""
    from bot.models import Cluster, Guild
    sqlite_repo.save(Cluster(
        discord_server_id=PROD_SERVER,
        guilds={"neuro": Guild(id="neuro", name="Neuro", api_key="", role_id=0)},
    ))
    return sqlite_repo


# ---------------------------------------------------------------------------
# Step 02-03 unit tests — `bot.db.secrets` crypto helper (driving port = the
# pure-function public API; Fernet + HKDF-derived HMAC). Behavior budget:
# 3 behaviors (Fernet round-trip; HMAC determinism; empty-string NULL-safety)
# x 2 = max 6 unit tests. The __meta__.version shim and empty-DB-returns-empty
# branch are covered end-to-end by RC4 / RC8 through the ABC driving port;
# duplicating them here would be Testing Theater.
# ---------------------------------------------------------------------------

# Hermetic Fernet key — mirrors tests/acceptance/sqlite-backend/conftest.py
# _SECRETS_FERNET_KEY (32 url-safe base64-encoded bytes; sha256-derived).
_SECRETS_FERNET_KEY = "uvP1WBf4y1Ycqc1WZz-6baPp1uBwqaesNDmUL6fXfXU="


@pytest.mark.parametrize("plaintext", [
    "tacticus-neuro-key",
    "a",
    "tacticus-neuro-key with spaces and unicode: ñ é ü",
])
def test_fernet_encrypt_decrypt_round_trips_plaintext(plaintext):
    """@driving_port — `encrypt_api_key` then `decrypt_api_key` returns the
    original plaintext (ADR-006 D7). Pure function = its own driving port."""
    from bot.db.secrets import encrypt_api_key, decrypt_api_key
    ciphertext = encrypt_api_key(plaintext, _SECRETS_FERNET_KEY)
    assert ciphertext != plaintext, "encrypt_api_key returned plaintext"
    assert decrypt_api_key(ciphertext, _SECRETS_FERNET_KEY) == plaintext


def test_api_key_hmac_is_deterministic_and_distinct_for_different_plaintexts():
    """@driving_port — same plaintext + key yields the same HMAC; different
    plaintexts yield distinct HMACs (ADR-006 D7: deterministic uniqueness)."""
    from bot.db.secrets import api_key_hmac
    h1 = api_key_hmac("tacticus-neuro-key", _SECRETS_FERNET_KEY)
    h2 = api_key_hmac("tacticus-neuro-key", _SECRETS_FERNET_KEY)
    h3 = api_key_hmac("tacticus-mech-key", _SECRETS_FERNET_KEY)
    assert h1 is not None
    assert h1 == h2, "api_key_hmac is not deterministic"
    assert h1 != h3, "api_key_hmac does not distinguish plaintexts"


def test_encrypt_api_key_empty_string_returns_empty_and_hmac_is_none():
    """@driving_port — an empty api_key round-trips as empty ciphertext and a
    NULL HMAC (RC12 NULL-safety: the guilds.api_key_hmac UNIQUE NULLABLE
    column allows multiple empty-key guilds; do NOT encrypt the empty string)."""
    from bot.db.secrets import encrypt_api_key, decrypt_api_key, api_key_hmac
    assert encrypt_api_key("", _SECRETS_FERNET_KEY) == ""
    assert decrypt_api_key("", _SECRETS_FERNET_KEY) == ""
    assert api_key_hmac("", _SECRETS_FERNET_KEY) is None


# ---------------------------------------------------------------------------
# Step 03-01 unit tests — battle_hits / bomb_hits upsert-keep-max + read
# ordering on SqlAlchemyClusterRepository (driving port = the ABC methods
# upsert_battle_hits / load_battle_hits / upsert_bomb_hits / load_bomb_hits).
# Behavior budget: 3 behaviors (roster_key normalizes hero order; keep-max
# stores the max-damage entry's completed_on; equal-damage tiebreak reads
# earliest completed_on first) x 2 = max 6 unit tests. The keep-max damage
# replacement, different-roster separate row, and bomb plain top-N behaviors
# are covered end-to-end by RC15; duplicating them here would be Testing
# Theater. These 3 pin behaviors RC15 does NOT assert.
# ---------------------------------------------------------------------------

def test_battle_hits_roster_key_normalizes_hero_order_so_same_set_dedups(battle_hits_sqlite_repo, make_tacticus_entry):
    """@driving_port @real-io — same heroes in a different order produce the
    same roster_key, so the ON CONFLICT upsert dedups them to a single row
    (the roster_key is order-independent per data-dictionary §2.7)."""
    server, guild, season = 1458181638453203099, "neuro", 94
    forward = make_tacticus_entry(damage=12000, user_id="u1",
                                  hero_details=[{"unitId": "Aethana"}, {"unitId": "Eldryon"}],
                                  machine_of_war={"unitId": "Khaine"})
    reverse = make_tacticus_entry(damage=15000, user_id="u1",
                                  hero_details=[{"unitId": "Eldryon"}, {"unitId": "Aethana"}],
                                  machine_of_war={"unitId": "Khaine"})
    battle_hits_sqlite_repo.upsert_battle_hits(server, guild, season, [forward, reverse])
    battle = battle_hits_sqlite_repo.load_battle_hits(server, guild, season)
    flat = battle["boss_hits"]["Avatar"]["0"]["Legendary_0"]
    assert len(flat) == 1, "same heroes different order did not dedup via roster_key"
    assert flat[0]["damage"] == 15000


def test_battle_hits_keep_max_stores_the_max_damage_entry_completed_on(battle_hits_sqlite_repo, make_tacticus_entry):
    """@driving_port @real-io — when a higher-damage hit replaces a lower one
    on the same roster, the stored completed_on follows the max-damage entry
    (preserves the try_insert contract pinned by RC14: same-roster-higher
    replaces the whole entry, not just damage)."""
    server, guild, season = 1458181638453203099, "neuro", 94
    lower = make_tacticus_entry(damage=10000, user_id="u1", completed_on="2026-07-18T10:00:00Z",
                                hero_details=[{"unitId": "Aethana"}], machine_of_war={"unitId": "Khaine"})
    higher = make_tacticus_entry(damage=15000, user_id="u1", completed_on="2026-07-18T12:00:00Z",
                                 hero_details=[{"unitId": "Aethana"}], machine_of_war={"unitId": "Khaine"})
    battle_hits_sqlite_repo.upsert_battle_hits(server, guild, season, [lower, higher])
    battle = battle_hits_sqlite_repo.load_battle_hits(server, guild, season)
    entry = battle["boss_hits"]["Avatar"]["0"]["Legendary_0"][0]
    assert entry["damage"] == 15000
    assert entry["completed_on"] == "2026-07-18T12:00:00Z", (
        "completed_on must follow the max-damage entry, not the first insert"
    )


def test_battle_hits_read_tiebreaks_equal_damage_by_earliest_completed_on(battle_hits_sqlite_repo, make_tacticus_entry):
    """@driving_port @real-io — equal-damage hits across distinct rosters
    are read ordered by completed_on ASC (the tiebreak pinned by
    bot/tests/test_tracker_tiebreak.py and data-dictionary §2.7)."""
    server, guild, season = 1458181638453203099, "neuro", 94
    later = make_tacticus_entry(damage=10000, user_id="u1", completed_on="2026-07-18T12:00:00Z",
                                hero_details=[{"unitId": "Aethana"}], machine_of_war={"unitId": "Khaine"})
    earlier = make_tacticus_entry(damage=10000, user_id="u2", completed_on="2026-07-18T09:00:00Z",
                                  hero_details=[{"unitId": "Eldryon"}], machine_of_war={"unitId": "Khaine"})
    battle_hits_sqlite_repo.upsert_battle_hits(server, guild, season, [later, earlier])
    battle = battle_hits_sqlite_repo.load_battle_hits(server, guild, season)
    flat = battle["boss_hits"]["Avatar"]["0"]["Legendary_0"]
    assert [e["completed_on"] for e in flat] == ["2026-07-18T09:00:00Z", "2026-07-18T12:00:00Z"], (
        "equal-damage ties must read earliest completed_on first"
    )