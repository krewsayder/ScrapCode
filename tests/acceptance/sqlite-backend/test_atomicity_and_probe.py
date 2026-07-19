"""Atomicity + startup probe acceptance tests (US-008, US-010, ADR-006 D6/D8/D9).

Implements `acceptance/atomicity-and-probe.feature`. Drives the probe
through `bot.db.session.Database.probe()` — the 02-02 driving port (the
adapter's empirical Earned-Trust contract). `SqlAlchemyClusterRepository`
delegates to `Database.probe()` in 02-03; for 02-02 the probe is exercised
directly so the refusal paths can be isolated without the repository shell.

The 6 acceptance scenarios (AP1-AP6) cover the 4 probe steps' pass + the 4
refusal paths. The 4 unit tests decompose each step's observable outcome.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

RED = pytest.mark.skip(reason="RED scaffold — enable one at a time in DELIVER")


# ---------------------------------------------------------------------------
# Helpers / fixtures local to the probe driving port
# ---------------------------------------------------------------------------

def _alembic_config(db_path: Path):
    """Build an Alembic Config rooted at bot/db/alembic for the given DB path."""
    from alembic.config import Config
    import bot.db
    db_pkg = Path(bot.db.__file__).parent
    cfg = Config(str(db_pkg / "alembic.ini"))
    cfg.set_main_option("script_location", str(db_pkg / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


@pytest.fixture
def migrated_db(sqlite_db_path: Path, env_vars) -> Path:
    """Apply the alembic baseline (0001) to the test DB; return the db_path.

    This is the 'fresh SQLite database with the schema applied via
    alembic upgrade head' Given from AP1/AP2 in atomicity-and-probe.feature.
    """
    from alembic import command
    sqlite_db_path.parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(_alembic_config(sqlite_db_path), "head")
    return sqlite_db_path


def _make_database(db_path, fernet_key=None) -> "Database":
    from bot.db.session import Database
    return Database(db_path=str(db_path), fernet_key=fernet_key)


def _refused_records(caplog):
    return [r for r in caplog.records
            if getattr(r, "event", None) == "health.startup.refused"]


def _pass_steps(caplog):
    return {r.step for r in caplog.records
            if getattr(r, "event", None) == "db.probe.pass"}


# ---------------------------------------------------------------------------
# AP-1 (ENABLED): probe asserts WAL + alembic head.
# ---------------------------------------------------------------------------

def test_AP1_probe_asserts_wal_mode_and_alembic_head(migrated_db, env_vars, caplog):
    """@driving_port @real-io — AP1.

    Probe asserts journal_mode=WAL, alembic_version.version_num matches the
    compiled head, the Fernet round-trip succeeds, and the write-rollback
    path works. Observable: WAL mode is set on the DB file after probe.
    """
    db = _make_database(migrated_db, fernet_key=env_vars["SCRAPCODE_DB_KEY"])
    with caplog.at_level("INFO", logger="bot.db.session"):
        db.probe()

    assert {"wal_mode", "alembic_version", "fernet_roundtrip", "write_rollback"} <= _pass_steps(caplog)
    conn = sqlite3.connect(str(migrated_db))
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode == "wal"


# ---------------------------------------------------------------------------
# AP-2 (ENABLED): probe round-trips a known plaintext through Fernet.
# ---------------------------------------------------------------------------

def test_AP2_probe_round_trips_known_plaintext_through_fernet(migrated_db, env_vars, caplog):
    """@real-io @adapter-integration — AP2.

    The Fernet round-trip step passes with SCRAPCODE_DB_KEY before any real
    api_key is touched. Observable: probe returns + a db.probe.pass record
    names the fernet_roundtrip step.
    """
    db = _make_database(migrated_db, fernet_key=env_vars["SCRAPCODE_DB_KEY"])
    with caplog.at_level("INFO", logger="bot.db.session"):
        db.probe()

    assert "fernet_roundtrip" in _pass_steps(caplog)


# ---------------------------------------------------------------------------
# AP-3 (ENABLED): probe refuses on a stale alembic version.
# ---------------------------------------------------------------------------

def test_AP3_probe_refuses_on_stale_alembic_version(sqlite_db_path, env_vars, caplog):
    """@infrastructure-failure @kpi — AP3 — with-stale-config env."""
    # The with-stale-config env: a DB stamped at an older revision. The DB
    # must be in WAL mode so step 1 (wal_mode) passes and the refusal is
    # attributed to step 2 (alembic_version), matching the feature's
    # "refuses with a health.startup.refused event naming the stale-version
    # step". A clusters table is created so the write-rollback step would
    # also pass — the stale version is the ONLY failing step.
    sqlite_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(sqlite_db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE clusters (discord_server_id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
    conn.execute("INSERT INTO alembic_version VALUES ('0000_stale_baseline')")
    conn.commit()
    conn.close()

    from bot.db.session import Database, ProbeRefusedError
    db = Database(db_path=str(sqlite_db_path),
                  fernet_key=env_vars["SCRAPCODE_DB_KEY"])
    with caplog.at_level("INFO", logger="bot.db.session"):
        with pytest.raises(ProbeRefusedError) as exc:
            db.probe()

    refused = _refused_records(caplog)
    assert refused, "stale alembic version must emit a health.startup.refused event"
    assert refused[0].step == "alembic_version"
    assert refused[0].reason == "stale_alembic_version"
    assert "health.startup.refused" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# AP-4 (ENABLED): probe refuses on a wrong Fernet key.
# ---------------------------------------------------------------------------

def test_AP4_probe_refuses_on_wrong_fernet_key(migrated_db, monkeypatch, caplog):
    """@infrastructure-failure — AP4.

    SCRAPCODE_DB_KEY does not match the DB's api_key encryption key. The
    probe refuses at the Fernet round-trip step. Observable: a
    health.startup.refused record names the fernet_roundtrip step.
    """
    monkeypatch.setenv("SCRAPCODE_DB_KEY", "wrong-key-wrong-key-wrong-key-wrong-key-")
    from bot.db.session import Database, ProbeRefusedError
    db = Database(db_path=str(migrated_db))  # reads SCRAPCODE_DB_KEY from env

    with caplog.at_level("INFO", logger="bot.db.session"):
        with pytest.raises(ProbeRefusedError) as exc:
            db.probe()

    refused = _refused_records(caplog)
    assert refused, "wrong Fernet key must emit a health.startup.refused event"
    assert refused[0].step == "fernet_roundtrip"
    assert "health.startup.refused" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# AP-5 (ENABLED): probe refuses on a corrupted non-SQLite file.
# ---------------------------------------------------------------------------

def test_AP5_probe_refuses_on_corrupted_non_sqlite_file(sqlite_db_path, env_vars, caplog):
    """@infrastructure-failure — AP5.

    A non-SQLite file containing 'not a database' cannot be opened. The
    probe refuses. Observable: a health.startup.refused record names the
    wal_mode step (the first step that touches the file).
    """
    sqlite_db_path.parent.mkdir(parents=True, exist_ok=True)
    sqlite_db_path.write_text("not a database", encoding="utf-8")

    from bot.db.session import Database, ProbeRefusedError
    db = Database(db_path=str(sqlite_db_path),
                  fernet_key=env_vars["SCRAPCODE_DB_KEY"])
    with caplog.at_level("INFO", logger="bot.db.session"):
        with pytest.raises(ProbeRefusedError):
            db.probe()

    refused = _refused_records(caplog)
    assert refused, "corrupted DB must emit a health.startup.refused event"
    assert refused[0].step == "wal_mode"


# ---------------------------------------------------------------------------
# AP-6 (ENABLED): probe refuses on a read-only filesystem.
# ---------------------------------------------------------------------------

def test_AP6_probe_refuses_on_read_only_filesystem(migrated_db, env_vars, caplog, tmp_path):
    """@infrastructure-failure — AP6.

    The probe refuses at the write-and-rollback step on a read-only
    filesystem path. On Windows, POSIX chmod(0444) on a directory is not
    enforced for file creation, so the read-only condition cannot be
    reproduced — xfail with a named reason rather than skip silently.
    """
    import platform
    if platform.system() == "Windows":
        pytest.xfail("AP6 requires POSIX directory chmod(0444) semantics; "
                     "Windows ignores the read-only bit on directories for "
                     "file creation, so the read-only condition is not enforceable")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.xfail("AP6: running as root — chmod(0444) is ignored, "
                     "read-only filesystem cannot be enforced")

    ro_dir = tmp_path / "readonly"
    ro_dir.mkdir()
    ro_db = ro_dir / "scrapcode.db"
    shutil.copy(str(migrated_db), str(ro_db))
    ro_dir.chmod(0o444)

    from bot.db.session import Database, ProbeRefusedError
    db = Database(db_path=str(ro_db),
                  fernet_key=env_vars["SCRAPCODE_DB_KEY"])
    try:
        with caplog.at_level("INFO", logger="bot.db.session"):
            with pytest.raises(ProbeRefusedError) as exc:
                db.probe()
        refused = _refused_records(caplog)
        assert refused, "read-only fs must emit a health.startup.refused event"
        assert refused[0].step == "write_rollback"
    finally:
        ro_dir.chmod(0o755)


# ---------------------------------------------------------------------------
# Unit tests — decompose each probe step's observable outcome.
# Behavior budget: 6 behaviors (AP1-AP6) x 2 = 12; 4 unit tests used.
# ---------------------------------------------------------------------------

def test_unit_probe_wal_step_sets_journal_mode_to_wal(migrated_db, env_vars):
    """Unit: the WAL step leaves the DB file in WAL mode (observable via PRAGMA)."""
    db = _make_database(migrated_db, fernet_key=env_vars["SCRAPCODE_DB_KEY"])
    db.probe()
    conn = sqlite3.connect(str(migrated_db))
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode == "wal"


def test_unit_probe_alembic_step_version_matches_compiled_head(migrated_db, env_vars):
    """Unit: after alembic upgrade head, the DB version_num equals the compiled head."""
    db = _make_database(migrated_db, fernet_key=env_vars["SCRAPCODE_DB_KEY"])
    db.probe()
    conn = sqlite3.connect(str(migrated_db))
    db_rev = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    conn.close()
    from bot.db.session import _compiled_alembic_head
    assert db_rev == _compiled_alembic_head()


@pytest.mark.parametrize("plaintext", [
    b"scrapcode-probe-sentinel-v1",
    b"",
    b"a" * 1024,
    b"\x00\x01\x02 binary payload \xff",
])
def test_unit_probe_fernet_step_round_trips_known_plaintext(plaintext, env_vars):
    """Unit: the Fernet round-trip helper returns the original plaintext."""
    from bot.db.session import _fernet_roundtrip
    assert _fernet_roundtrip(env_vars["SCRAPCODE_DB_KEY"], plaintext) == plaintext


def test_unit_probe_write_rollback_step_leaves_no_row(migrated_db, env_vars):
    """Unit: the write-rollback step inserts then rolls back — no row remains."""
    db = _make_database(migrated_db, fernet_key=env_vars["SCRAPCODE_DB_KEY"])
    db.probe()
    conn = sqlite3.connect(str(migrated_db))
    count = conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0]
    conn.close()
    assert count == 0


# ---------------------------------------------------------------------------
# Remaining scenarios — RED scaffold until later DELIVER steps.
# ---------------------------------------------------------------------------

@RED
def test_crash_mid_transaction_leaves_db_in_pre_cycle_state(env_vars, sqlite_repo, tmp_path):
    """@infrastructure-failure @kpi @real-io — AP7 — KPI-3a crash injection."""
    raise AssertionError("RED scaffold: crash-injection harness not implemented")


def test_hourly_auto_update_write_is_one_transaction_per_guild(env_vars, sqlite_repo, make_tacticus_entry):
    """@property @real-io — AP8.

    Each guild's hourly writes (battle + bomb) commit as a single
    transaction (ADR-006 D6). A failure in guild B's writes does NOT roll
    back guild A's already-committed writes (cross-guild isolation), and a
    mid-guild failure rolls back that guild's whole write batch (within-guild
    atomicity — one transaction per guild).
    """
    import pytest
    from bot.models import Cluster, Guild
    from bot.tracker import process_api_response
    server = 1458181638453203099
    guild_a, guild_b = "guildA", "guildB"
    season = 94
    sqlite_repo.save(Cluster(
        discord_server_id=server,
        guilds={
            guild_a: Guild(id=guild_a, name="A", api_key="", role_id=0),
            guild_b: Guild(id=guild_b, name="B", api_key="", role_id=0),
        },
    ))
    # Guild A: valid battle + bomb entries — both succeed in one transaction.
    valid_a = {"entries": [
        make_tacticus_entry(damage=12000, user_id="u-a-battle"),
        make_tacticus_entry(damage_type="Bomb", damage=8000, user_id="u-a-bomb",
                            hero_details=[], machine_of_war=None),
    ]}
    process_api_response(valid_a, season, server, guild_a)
    # Guild B: a battle entry that would succeed, then a bomb entry that
    # fails mid-transaction (missing unitId raises KeyError inside
    # _bomb_params). With one-transaction-per-guild, guild B's battle
    # writes roll back WITH the failed bomb writes.
    bad_bomb = make_tacticus_entry(damage_type="Bomb", damage=8000,
                                   user_id="u-b-bomb", hero_details=[],
                                   machine_of_war=None)
    del bad_bomb["unitId"]
    invalid_b = {"entries": [
        make_tacticus_entry(damage=12000, user_id="u-b-battle"),
        bad_bomb,
    ]}
    with pytest.raises(KeyError):
        process_api_response(invalid_b, season, server, guild_b)
    # Cross-guild isolation: guild A's battle AND bomb survived guild B's failure.
    battle_a = sqlite_repo.load_battle_hits(server, guild_a, season)
    bomb_a = sqlite_repo.load_bomb_hits(server, guild_a, season)
    assert battle_a["boss_hits"]["Avatar"]["0"]["Legendary_0"], \
        "guild A's battle writes must survive guild B's failure"
    assert bomb_a["boss_hits"]["Avatar"]["0"]["Legendary_0"], \
        "guild A's bomb writes must survive guild B's failure"
    # Within-guild atomicity: guild B's battle writes rolled back with the
    # failed bomb writes (one transaction per guild).
    battle_b = sqlite_repo.load_battle_hits(server, guild_b, season)
    bomb_b = sqlite_repo.load_bomb_hits(server, guild_b, season)
    assert battle_b == {"boss_hits": {}}, \
        "guild B's battle writes must roll back with the failed bomb writes (one transaction per guild)"
    assert bomb_b == {"boss_hits": {}}, \
        "guild B's failed bomb writes must not leave partial state"


def test_scrapcode_repo_backend_selects_live_repository(monkeypatch, env_vars):
    """@driving_port — AP9 — composition root reads the env var."""
    monkeypatch.setenv("SCRAPCODE_REPO_BACKEND", "sqlite")
    import importlib, bot.guilds as guilds_mod
    importlib.reload(guilds_mod)
    from bot.repository_sqlalchemy import SqlAlchemyClusterRepository
    assert isinstance(guilds_mod.repo, SqlAlchemyClusterRepository)

    monkeypatch.setenv("SCRAPCODE_REPO_BACKEND", "json")
    importlib.reload(guilds_mod)
    from bot.repository import JsonClusterRepository
    assert isinstance(guilds_mod.repo, JsonClusterRepository)


def test_missing_sqlite_file_falls_back_to_json_for_one_cycle(monkeypatch, tmp_path):
    """@infrastructure-failure — AP10."""
    monkeypatch.setenv("SCRAPCODE_REPO_BACKEND", "sqlite")
    monkeypatch.setenv("SCRAPCODE_DB_PATH", str(tmp_path / "missing.db"))
    import importlib, bot.guilds as guilds_mod
    importlib.reload(guilds_mod)
    from bot.repository import JsonClusterRepository
    assert isinstance(guilds_mod.repo, JsonClusterRepository), \
        "missing SQLite file must fall back to JSON for one cycle"


# ---------------------------------------------------------------------------
# Unit tests — env-driven composition-root factory (ADR-006 D9) + the
# missing-file/missing-key fallback branch (ADR-006 D9 / DEVOPS safety net).
# Behavior budget: 4 behaviors (default-sqlite, =json, missing-key fallback,
# probe Protocol) x 2 = 8; 4 unit tests used.
# ---------------------------------------------------------------------------

def test_unit_build_repo_default_backend_is_sqlite(monkeypatch, tmp_path):
    """Unit: SCRAPCODE_REPO_BACKEND unset → factory defaults to sqlite
    (ADR-006 D9 post-cutover default) and constructs SqlAlchemyClusterRepository.
    The DB file need not pre-exist when its parent dir does not (first-run)."""
    from bot.guilds import build_repo
    from conftest import HERM_FERNET_KEY
    monkeypatch.delenv("SCRAPCODE_REPO_BACKEND", raising=False)
    monkeypatch.setenv("SCRAPCODE_DB_KEY", HERM_FERNET_KEY)
    monkeypatch.setenv("SCRAPCODE_DB_PATH", str(tmp_path / "fresh_dir" / "scrapcode.db"))
    repo = build_repo()
    from bot.repository_sqlalchemy import SqlAlchemyClusterRepository
    assert isinstance(repo, SqlAlchemyClusterRepository)


def test_unit_build_repo_json_backend_selects_json(monkeypatch):
    """Unit: SCRAPCODE_REPO_BACKEND=json → JsonClusterRepository; no SQLite
    probe is attempted (so a missing SCRAPCODE_DB_KEY is OK)."""
    from bot.guilds import build_repo
    monkeypatch.setenv("SCRAPCODE_REPO_BACKEND", "json")
    monkeypatch.delenv("SCRAPCODE_DB_KEY", raising=False)
    repo = build_repo()
    from bot.repository import JsonClusterRepository
    assert isinstance(repo, JsonClusterRepository)


def test_unit_build_repo_sqlite_missing_db_key_falls_back_to_json(monkeypatch, tmp_path, caplog):
    """Unit: SCRAPCODE_REPO_BACKEND=sqlite but SCRAPCODE_DB_KEY missing —
    fall back to JsonClusterRepository for one cycle (ADR-006 D9 safety net;
    the probe is skipped on the JSON path so a missing key does not block
    rollback). A loud warning is logged."""
    from bot.guilds import build_repo
    import logging
    monkeypatch.setenv("SCRAPCODE_REPO_BACKEND", "sqlite")
    monkeypatch.setenv("SCRAPCODE_DB_PATH", str(tmp_path / "missing.db"))
    monkeypatch.delenv("SCRAPCODE_DB_KEY", raising=False)
    with caplog.at_level(logging.WARNING, logger="bot.guilds"):
        repo = build_repo()
    from bot.repository import JsonClusterRepository
    assert isinstance(repo, JsonClusterRepository), \
        "missing SCRAPCODE_DB_KEY with backend=sqlite must fall back to JSON"
    assert any("SCRAPCODE_DB_KEY" in r.getMessage() for r in caplog.records), \
        "fallback must log a loud warning naming SCRAPCODE_DB_KEY"


def test_unit_composition_root_adapters_expose_probe_protocol():
    """Unit (architecture enforcement, ADR-006 §Architecture enforcement):
    every ClusterRepository adapter wired into bot.guilds exposes probe()
    (mypy Protocol + runtime check). The probe is the Earned-Trust gate
    (ADR-006 D8); the JSON impl's probe is a no-op (the probe is skipped
    on the JSON path)."""
    from bot.repository import JsonClusterRepository
    from bot.repository_sqlalchemy import SqlAlchemyClusterRepository
    for cls in (JsonClusterRepository, SqlAlchemyClusterRepository):
        assert callable(getattr(cls, "probe", None)), \
            f"{cls.__name__} must expose a callable probe() (ADR-006 D8 Protocol)"


def test_post_cutover_grep_finds_zero_json_write_helpers_in_retired_modules():
    """@kpi — AP11 — KPI-3c.

    SCOPE NOTE (04-03): the JSON write path is retired across ALL THREE
    bypass modules — bot/tracker.py (04-01), bot/embeds.py (04-02), and
    bot/cogs/replay_cog.py (04-03). The grep now covers all three. The
    replay-specific constants/helpers (REPLAY_INDEX_FILE, load_replay_index,
    save_replay_index, FORUM_CHANNELS, MAP_THREADS) are asserted absent
    from replay_cog.py by CS9; this test asserts the JSON-write helper
    patterns are absent from all three retired modules.

    PATTERN SCOPE NOTE (04-01): the `try_insert` pattern is DEFERRED to a
    later step. `bot/repository.py::JsonClusterRepository.upsert_battle_hits`
    / `upsert_bomb_hits` (the JSON rollback impl) import `try_insert` from
    `bot.tracker`, so the function must remain importable until that import
    is removed. `try_insert` is no longer called by `process_api_response`
    (the production write path now delegates to the repo upsert). The
    `try_insert` pattern is re-added to this grep once `bot/repository.py`
    is updated (out of scope for 04-03).
    """
    import bot.tracker as _tracker_mod
    import bot.embeds as _embeds_mod
    import bot.cogs.replay_cog as _replay_mod
    modules = [
        Path(_tracker_mod.__file__),
        Path(_embeds_mod.__file__),
        Path(_replay_mod.__file__),
    ]
    patterns = ["path.write_text", "load_json", "save_json", "replay_index.json"]
    for mod_path in modules:
        src = mod_path.read_text(encoding="utf-8")
        for pat in patterns:
            assert pat not in src, f"{pat} still present in {mod_path}"


def test_process_api_response_writes_to_battle_bomb_hits_via_repo(env_vars, sqlite_repo, make_tacticus_entry, tmp_path):
    """@driving_port @real-io — AP12.

    `process_api_response(api_data, season, discord_server_id, guild_id)`
    (new signature — `data_dir` replaced by the SQL partition key) upserts
    battle_hits and bomb_hits rows via the repository; no
    `highest_hits_season_*.json` or `highest_bombs_season_*.json` file is
    written to disk.
    """
    from bot.models import Cluster, Guild
    from bot.tracker import process_api_response
    server, guild, season = 1458181638453203099, "neuro", 94
    # Precondition: the guild row must exist (FK target for battle/bomb hits).
    sqlite_repo.save(Cluster(
        discord_server_id=server,
        guilds={guild: Guild(id=guild, name="Neuro", api_key="", role_id=0)},
    ))
    battle_entries = [make_tacticus_entry(damage=12000, user_id="u-battle")]
    bomb_entries = [make_tacticus_entry(damage_type="Bomb", damage=8000,
                                        user_id="u-bomb", hero_details=[],
                                        machine_of_war=None)]
    api_data = {"entries": battle_entries + bomb_entries}
    process_api_response(api_data, season, server, guild)
    battle = sqlite_repo.load_battle_hits(server, guild, season)
    bomb = sqlite_repo.load_bomb_hits(server, guild, season)
    assert battle["boss_hits"]["Avatar"]["0"]["Legendary_0"], \
        "battle_hits row not upserted via repo"
    assert bomb["boss_hits"]["Avatar"]["0"]["Legendary_0"], \
        "bomb_hits row not upserted via repo"
    # No JSON season files written to disk (the bypass is retired).
    json_files = list(tmp_path.rglob("highest_*_season_*.json"))
    assert json_files == [], f"JSON season files still written: {json_files}"