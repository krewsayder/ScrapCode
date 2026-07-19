"""JSON->SQLite data migration acceptance tests (US-005, US-007).

Implements `acceptance/json-to-sqlite-parity.feature`. Driven through the
JSON->SQLite migration CLI as a real subprocess (Mandate: verify exit code
+ stdout + arg handling). The migration is a RED scaffold — the subprocess
exits non-zero with an AssertionError message until DELIVER lands the real
impl in `bot/db/migrations_json_to_sqlite.py`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

RED = pytest.mark.skip(reason="RED scaffold — enable one at a time in DELIVER")


def _run_migration(*, source: Path, db: Path, report: Path | None = None,
                   env: dict | None = None) -> subprocess.CompletedProcess:
    # The subprocess must resolve `import bot.*` — add the worktree root to
    # PYTHONPATH (3 levels up from this test module).
    worktree_root = Path(__file__).resolve().parents[3]
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    existing = full_env.get("PYTHONPATH", "")
    full_env["PYTHONPATH"] = f"{worktree_root}{os.pathsep}{existing}" if existing else str(worktree_root)
    cmd = [sys.executable, "-m", "bot.db.migrations_json_to_sqlite",
           "--source", str(source), "--db", str(db)]
    if report is not None:
        cmd += ["--report", str(report)]
    return subprocess.run(cmd, capture_output=True, text=True, env=full_env)


# ---------------------------------------------------------------------------
# JP-1 (ENABLED — first scenario): row-count parity via real subprocess.
# ---------------------------------------------------------------------------

def test_easy_entity_row_counts_match_json_derived_counts(
    tmp_clusters_tree, tmp_path, monkeypatch, fernet_key
):
    """@kpi @driving_port @real-io — JP1.

    RED scaffold: the migration subprocess raises AssertionError on
    `main()`, so it exits non-zero. This test asserts exit 0 + parity
    PASS, so it fails RED until the real impl lands.
    """
    db = tmp_path / "data" / "scrapcode.db"
    report = tmp_path / "parity.json"
    monkeypatch.setenv("SCRAPCODE_DB_KEY", fernet_key)
    result = _run_migration(source=tmp_clusters_tree, db=db, report=report)
    assert result.returncode == 0, f"migration failed: {result.stdout}\n{result.stderr}"
    assert report.exists(), "parity report was not written"
    parity = json.loads(report.read_text(encoding="utf-8"))
    assert parity["overall"] == "PASS"
    for table, counts in parity["tables"].items():
        assert counts["json"] == counts["sql"], f"{table} mismatch"
        assert counts["status"] == "PASS"


# ---------------------------------------------------------------------------
# Remaining scenarios skipped until DELIVER.
# ---------------------------------------------------------------------------

def test_v1_player_list_inverted_to_v2_exactly_once(tmp_clusters_tree, tmp_path, monkeypatch, fernet_key):
    """@real-io — JP2."""
    db = tmp_path / "data" / "scrapcode.db"
    report = tmp_path / "parity.json"
    monkeypatch.setenv("SCRAPCODE_DB_KEY", fernet_key)
    first = _run_migration(source=tmp_clusters_tree, db=db, report=report)
    assert first.returncode == 0
    # mech's v1 list (Aiko Tanaka -> uid-003) becomes a v2 row with epoch sentinel
    import sqlite3
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT display_name, last_validated, is_former FROM players "
        "WHERE tacticus_user_id='tacticus-uid-003'"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "Aiko Tanaka"
    assert row[1] == "1970-01-01T00:00:00Z"
    assert row[2] == 0
    # idempotent: second run does not change the players table
    second = _run_migration(source=tmp_clusters_tree, db=db, report=report)
    assert second.returncode == 0


def test_api_key_values_encrypted_on_insert_not_plaintext(tmp_clusters_tree, tmp_path, monkeypatch, fernet_key):
    """@real-io @adapter-integration — JP3."""
    db = tmp_path / "data" / "scrapcode.db"
    report = tmp_path / "parity.json"
    monkeypatch.setenv("SCRAPCODE_DB_KEY", fernet_key)
    result = _run_migration(source=tmp_clusters_tree, db=db, report=report)
    assert result.returncode == 0
    raw = db.read_bytes()
    assert b"tacticus-neuro-key" not in raw, "plaintext api_key leaked into the DB file"
    import sqlite3
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT api_key FROM guilds WHERE guild_id='neuro'").fetchone()
    conn.close()
    assert row[0] != "tacticus-neuro-key"


def test_parity_mismatch_fails_the_migration_loudly(tmp_path, monkeypatch, fernet_key):
    """@infrastructure-failure — JP4."""
    # Two guilds whose slugs collide after normalization (both -> "neuro").
    base = tmp_path / "clusters"
    server_dir = base / "1458181638453203099"
    server_dir.mkdir(parents=True)
    (server_dir / "guilds.json").write_text(json.dumps({
        "update_channel_id": None,
        "role_tiers": {},
        "guilds": {
            "Neuro": {"name": "Neuro", "api_key": "", "role_id": 1, "member_role_ids": []},
            "neuro": {"name": "neuro", "api_key": "", "role_id": 2, "member_role_ids": []},
        },
    }), encoding="utf-8")
    db = tmp_path / "data" / "scrapcode.db"
    report = tmp_path / "parity.json"
    monkeypatch.setenv("SCRAPCODE_DB_KEY", fernet_key)
    result = _run_migration(source=base, db=db, report=report)
    assert result.returncode != 0
    assert report.exists()
    parity = json.loads(report.read_text(encoding="utf-8"))
    assert parity["overall"] == "FAIL"
    assert parity["tables"]["guilds"]["status"] == "MISMATCH"


def test_migration_is_idempotent(tmp_clusters_tree, tmp_path, monkeypatch, fernet_key):
    """@property — JP5."""
    db = tmp_path / "data" / "scrapcode.db"
    report = tmp_path / "parity.json"
    monkeypatch.setenv("SCRAPCODE_DB_KEY", fernet_key)
    first = _run_migration(source=tmp_clusters_tree, db=db, report=report)
    assert first.returncode == 0
    first_parity = json.loads(report.read_text(encoding="utf-8"))
    second = _run_migration(source=tmp_clusters_tree, db=db, report=report)
    assert second.returncode == 0
    second_parity = json.loads(report.read_text(encoding="utf-8"))
    assert first_parity == second_parity


@RED
def test_replay_entries_assigned_to_production_server(tmp_clusters_tree, tmp_path, monkeypatch):
    """@kpi @real-io — JP6."""
    db = tmp_path / "data" / "scrapcode.db"
    report = tmp_path / "parity.json"
    # replay_index.json is at the tmp_path root (the conftest fixture puts it there).
    monkeypatch.setenv("SCRAPCODE_DB_KEY", "test-key")
    result = _run_migration(source=tmp_clusters_tree, db=db, report=report)
    assert result.returncode == 0
    import sqlite3
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT DISTINCT discord_server_id FROM replay_entries").fetchall()
    conn.close()
    assert rows == [(1458181638453203099,)]


@RED
def test_url_uniqueness_scoped_per_server_boss_map(tmp_path, monkeypatch):
    """@infrastructure-failure @property — JP7."""
    db = tmp_path / "data" / "scrapcode.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    # Apply schema + insert one row, then attempt a duplicate and a cross-tenant row.
    # RED scaffold: real impl lands in DELIVER.
    import sqlite3
    conn = sqlite3.connect(db)
    # Schema must exist (provided by the migration); for the scaffold test we
    # assert the unique-constraint behavior post-migration.
    with pytest.raises(Exception):
        # duplicate (server, boss, map, url) insert should fail
        conn.execute(
            "INSERT INTO replay_entries (discord_server_id, boss, map_name, url) "
            "VALUES (?, 'Avatar', 'GB_Khaine_01', 'https://replay.example/abc')",
            (1458181638453203099,),
        )
    # cross-tenant same url should succeed
    conn.execute(
        "INSERT INTO replay_entries (discord_server_id, boss, map_name, url) "
        "VALUES (?, 'Avatar', 'GB_Khaine_01', 'https://replay.example/abc')",
        (9876543210,),
    )
    conn.close()


@RED
def test_replay_threads_seeded_from_forum_and_map_constants(tmp_clusters_tree, tmp_path, monkeypatch):
    """@real-io — JP8."""
    db = tmp_path / "data" / "scrapcode.db"
    report = tmp_path / "parity.json"
    monkeypatch.setenv("SCRAPCODE_DB_KEY", "test-key")
    result = _run_migration(source=tmp_clusters_tree, db=db, report=report)
    assert result.returncode == 0
    import sqlite3
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT boss, map_name FROM replay_threads").fetchall()
    conn.close()
    assert len(rows) >= 1
    assert all(r[0] and r[1] for r in rows)


@RED
def test_migration_against_missing_source_fails_loudly(tmp_path, monkeypatch):
    """@infrastructure-failure — JP9."""
    missing = tmp_path / "does-not-exist"
    db = tmp_path / "data" / "scrapcode.db"
    report = tmp_path / "parity.json"
    monkeypatch.setenv("SCRAPCODE_DB_KEY", "test-key")
    result = _run_migration(source=missing, db=db, report=report)
    assert result.returncode != 0
    assert not db.exists() or db.stat().st_size == 0
    assert str(missing) in result.stderr or str(missing) in result.stdout


@RED
def test_migration_with_empty_clusters_tree_produces_empty_schema(tmp_path, monkeypatch):
    """@edge — JP10."""
    empty_source = tmp_path / "clusters"
    empty_source.mkdir()
    db = tmp_path / "data" / "scrapcode.db"
    report = tmp_path / "parity.json"
    monkeypatch.setenv("SCRAPCODE_DB_KEY", "test-key")
    result = _run_migration(source=empty_source, db=db, report=report)
    assert result.returncode == 0
    parity = json.loads(report.read_text(encoding="utf-8"))
    assert parity["overall"] == "PASS"
    for counts in parity["tables"].values():
        assert counts["json"] == 0 and counts["sql"] == 0
    import sqlite3
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    conn.close()
    assert row is not None, "schema not applied on empty source"


# ---------------------------------------------------------------------------
# Unit tests (RED_UNIT) — drive `run_migration` in-process (port-to-port at
# the migration-module driving port) with parametrized input variations.
# Distinct from the subprocess-driven ATs: these exercise the parity-count
# diff function, the v1->v2 inversion path, and the Fernet-encrypt-on-insert
# path with controlled, smaller-grained inputs.
# ---------------------------------------------------------------------------

from bot.db.migrations_json_to_sqlite import diff_counts  # noqa: E402


def _write_minimal_cluster(tmp_path: Path, *, guilds: dict, player_list: dict | None = None,
                           server_id: int = 1458181638453203099) -> Path:
    """Build a minimal clusters/<server>/ tree for unit-level run_migration drives."""
    base = tmp_path / "clusters"
    server_dir = base / str(server_id)
    server_dir.mkdir(parents=True)
    (server_dir / "guilds.json").write_text(json.dumps({
        "update_channel_id": None,
        "role_tiers": {},
        "guilds": guilds,
    }), encoding="utf-8")
    if player_list is not None:
        for guild_id, plist in player_list.items():
            gdir = server_dir / guild_id
            gdir.mkdir(parents=True, exist_ok=True)
            (gdir / "player_list.json").write_text(json.dumps(plist), encoding="utf-8")
    return base


@pytest.mark.parametrize("json_counts, sql_counts, expected_status, expected_overall", [
    ({"guilds": 2}, {"guilds": 2}, {"guilds": "PASS"}, "PASS"),
    ({"guilds": 2}, {"guilds": 1}, {"guilds": "MISMATCH"}, "FAIL"),
    ({"guilds": 2, "players": 3}, {"guilds": 2, "players": 3},
     {"guilds": "PASS", "players": "PASS"}, "PASS"),
    ({"guilds": 2, "players": 3}, {"guilds": 2, "players": 2},
     {"guilds": "PASS", "players": "MISMATCH"}, "FAIL"),
    ({}, {}, {}, "PASS"),
])
def test_diff_counts_labels_match_and_mismatch(json_counts, sql_counts,
                                                expected_status, expected_overall):
    """Pure-function test: diff_counts returns PASS/MISMATCH per table + overall."""
    tables = diff_counts(json_counts, sql_counts)
    for table, status in expected_status.items():
        assert tables[table]["status"] == status
        assert tables[table]["json"] == json_counts[table]
        assert tables[table]["sql"] == sql_counts[table]
    overall = "PASS" if all(t["status"] == "PASS" for t in tables.values()) else "FAIL"
    assert overall == expected_overall


@pytest.mark.parametrize("v1_players, expected_v2_rows", [
    ({"Aiko Tanaka": "tacticus-uid-003"}, [("Aiko Tanaka", "1970-01-01T00:00:00Z", 0)]),
    ({"Name One": "uid-1", "Name Two": "uid-2"},
     [("Name One", "1970-01-01T00:00:00Z", 0), ("Name Two", "1970-01-01T00:00:00Z", 0)]),
    ({}, []),
])
def test_run_migration_inverts_v1_player_list_to_v2(tmp_path, monkeypatch, fernet_key,
                                                     v1_players, expected_v2_rows):
    """Unit test: run_migration invokes PlayerListMigrator v1->v2 once per v1 file."""
    monkeypatch.setenv("SCRAPCODE_DB_KEY", fernet_key)
    base = _write_minimal_cluster(tmp_path, guilds={"g1": {"name": "G1", "api_key": "",
                                                            "role_id": 1, "member_role_ids": []}},
                                   player_list={"g1": v1_players})
    db = tmp_path / "data" / "scrapcode.db"
    report = tmp_path / "parity.json"
    from bot.db.migrations_json_to_sqlite import run_migration
    rc = run_migration(source=str(base), db=str(db), report=str(report))
    assert rc == 0
    import sqlite3
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT display_name, last_validated, is_former FROM players "
        "ORDER BY display_name"
    ).fetchall()
    conn.close()
    assert rows == expected_v2_rows


@pytest.mark.parametrize("api_key_plaintext", ["", "tacticus-neuro-key", "tk-neuro-42", "long-key-1234567890"])
def test_run_migration_encrypts_api_key_on_insert(tmp_path, monkeypatch, fernet_key,
                                                   api_key_plaintext):
    """Unit test: run_migration stores api_key as Fernet ciphertext, not plaintext."""
    monkeypatch.setenv("SCRAPCODE_DB_KEY", fernet_key)
    base = _write_minimal_cluster(tmp_path, guilds={
        "neuro": {"name": "Neuro", "api_key": api_key_plaintext,
                  "role_id": 1, "member_role_ids": []},
    })
    db = tmp_path / "data" / "scrapcode.db"
    report = tmp_path / "parity.json"
    from bot.db.migrations_json_to_sqlite import run_migration
    rc = run_migration(source=str(base), db=str(db), report=str(report))
    assert rc == 0
    raw = db.read_bytes()
    if api_key_plaintext:
        assert api_key_plaintext.encode() not in raw, "plaintext api_key leaked into DB file"
    import sqlite3
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT api_key FROM guilds WHERE guild_id='neuro'").fetchone()
    conn.close()
    assert row is not None
    stored = row[0]
    if api_key_plaintext:
        # Fernet ciphertext decrypts back to the plaintext with the same key.
        from cryptography.fernet import Fernet
        assert Fernet(fernet_key.encode()).decrypt(stored.encode()).decode() == api_key_plaintext
        assert stored != api_key_plaintext
    else:
        # Empty api_key is stored as empty string (RC12 — not encrypted).
        assert stored == ""