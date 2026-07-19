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


@RED
def test_hourly_auto_update_write_is_one_transaction_per_guild(env_vars, sqlite_repo):
    """@property @real-io — AP8."""
    raise AssertionError("RED scaffold: one-transaction-per-guild not implemented")


@RED
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


@RED
def test_missing_sqlite_file_falls_back_to_json_for_one_cycle(monkeypatch, tmp_path):
    """@infrastructure-failure — AP10."""
    monkeypatch.setenv("SCRAPCODE_REPO_BACKEND", "sqlite")
    monkeypatch.setenv("SCRAPCODE_DB_PATH", str(tmp_path / "missing.db"))
    import importlib, bot.guilds as guilds_mod
    importlib.reload(guilds_mod)
    from bot.repository import JsonClusterRepository
    assert isinstance(guilds_mod.repo, JsonClusterRepository), \
        "missing SQLite file must fall back to JSON for one cycle"


@RED
def test_post_cutover_grep_finds_zero_json_write_helpers_in_retired_modules():
    """@kpi — AP11 — KPI-3c."""
    repo_root = Path(__import__("bot.tracker").__file__).parent.parent
    targets = [
        repo_root / "bot" / "tracker.py",
        repo_root / "bot" / "embeds.py",
        repo_root / "bot" / "cogs" / "replay_cog.py",
    ]
    patterns = ["path.write_text", "load_json", "save_json", "try_insert",
                "replay_index.json"]
    for target in targets:
        if not target.exists():
            continue
        src = target.read_text(encoding="utf-8")
        for pat in patterns:
            assert pat not in src, f"{pat} still present in {target}"


@RED
def test_process_api_response_writes_to_battle_bomb_hits_via_repo(env_vars, sqlite_repo, make_tacticus_entry):
    """@driving_port @real-io — AP12."""
    from bot.tracker import process_api_response
    entries = [make_tacticus_entry() for _ in range(25)]
    api_data = {"entries": entries}
    # New signature: (api_data, season, discord_server_id, guild_id)
    process_api_response(api_data, 94, 1458181638453203099, "neuro")
    battle = sqlite_repo.load_battle_hits(1458181638453203099, "neuro", 94)
    assert battle["boss_hits"]