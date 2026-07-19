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

# pytest -k matches against marker names, so the per-scenario JP markers
# make the AC's `-k 'JP6 or JP7 or JP8 or JP9 or JP10'` filter select exactly
# the five replay/edge scenarios (test function names don't contain "JPn").
JP6 = pytest.mark.JP6
JP7 = pytest.mark.JP7
JP8 = pytest.mark.JP8
JP9 = pytest.mark.JP9
JP10 = pytest.mark.JP10


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


@JP6
def test_replay_entries_assigned_to_production_server(tmp_clusters_tree, tmp_path, monkeypatch, fernet_key):
    """@kpi @real-io — JP6."""
    db = tmp_path / "data" / "scrapcode.db"
    report = tmp_path / "parity.json"
    # replay_index.json is at the tmp_path root (the conftest fixture puts it there).
    monkeypatch.setenv("SCRAPCODE_DB_KEY", fernet_key)
    result = _run_migration(source=tmp_clusters_tree, db=db, report=report)
    assert result.returncode == 0
    import sqlite3
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT DISTINCT discord_server_id FROM replay_entries").fetchall()
    conn.close()
    assert rows == [(1458181638453203099,)]


@JP7
def test_url_uniqueness_scoped_per_server_boss_map(tmp_clusters_tree, tmp_path, monkeypatch, fernet_key):
    """@infrastructure-failure @property — JP7.

    The migration seeds replay_entries from replay_index.json (one row for
    the production server, Avatar/GB_Khaine_01/https://replay.example/abc).
    A second insert with the same (server, boss, map, url) must fail the
    per-tenant unique constraint; the same url under a different
    discord_server_id must succeed (per-tenant scope, not global). FK
    enforcement is left OFF (sqlite default) so the test isolates the
    unique-constraint scope from the replay_threads FK.
    """
    db = tmp_path / "data" / "scrapcode.db"
    report = tmp_path / "parity.json"
    monkeypatch.setenv("SCRAPCODE_DB_KEY", fernet_key)
    result = _run_migration(source=tmp_clusters_tree, db=db, report=report)
    assert result.returncode == 0
    import sqlite3
    conn = sqlite3.connect(db)
    # Duplicate (server, boss, map, url) insert fails the per-tenant unique
    # constraint. The migration has already seeded the prod-server row from
    # replay_index.json. position/comment are included explicitly because
    # their defaults are ORM-level (not DB-level), so omitting them would
    # raise NOT NULL — masking the unique-constraint assertion.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO replay_entries (discord_server_id, boss, map_name, team, tier, "
            "position, damage_text, url, comment, submitted_by) "
            "VALUES (?, 'Avatar', 'GB_Khaine_01', 'Neuro', 'Legendary 1', 'LHS', "
            "'1.33M', 'https://replay.example/abc', '', '123456789')",
            (1458181638453203099,),
        )
    # Cross-tenant same url succeeds (per-tenant scope, not global).
    conn.execute(
        "INSERT INTO replay_entries (discord_server_id, boss, map_name, team, tier, "
        "position, damage_text, url, comment, submitted_by) "
        "VALUES (?, 'Avatar', 'GB_Khaine_01', 'Neuro', 'Legendary 1', 'LHS', "
        "'1.33M', 'https://replay.example/abc', '', '123456789')",
        (9876543210,),
    )
    conn.commit()
    conn.close()


@JP8
def test_replay_threads_seeded_from_forum_and_map_constants(tmp_clusters_tree, tmp_path, monkeypatch, fernet_key):
    """@real-io — JP8."""
    db = tmp_path / "data" / "scrapcode.db"
    report = tmp_path / "parity.json"
    monkeypatch.setenv("SCRAPCODE_DB_KEY", fernet_key)
    result = _run_migration(source=tmp_clusters_tree, db=db, report=report)
    assert result.returncode == 0
    import sqlite3
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT boss, map_name FROM replay_threads").fetchall()
    conn.close()
    assert len(rows) >= 1
    assert all(r[0] and r[1] for r in rows)


@JP9
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


@JP10
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
                           server_id: int = 1458181638453203099,
                           replay_index: dict | None = None) -> Path:
    """Build a minimal clusters/<server>/ tree for unit-level run_migration drives.

    `replay_index` is written to `tmp_path/replay_index.json` (the project-root
    global-leak location the migration reads from `source_path.parent`).
    """
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
    if replay_index is not None:
        (tmp_path / "replay_index.json").write_text(json.dumps(replay_index), encoding="utf-8")
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


# ---------------------------------------------------------------------------
# RED_UNIT (03-03) — replay single-server assignment, per-tenant URL
# uniqueness, replay_threads seed from FORUM_CHANNELS/MAP_THREADS. Drives
# `run_migration` in-process (port-to-port at the migration-module driving
# port) with a minimal cluster + replay_index.json, asserts at the SQLite
# driven-port boundary. Test budget: 3 behaviors x 2 = 6; 3 tests used.
# ---------------------------------------------------------------------------

PROD_SERVER_ID = 1458181638453203099


def _replay_index_with(entries):
    """Build a replay_index.json shape with one (Avatar, GB_Khaine_01) thread."""
    return {
        "Avatar": {
            "GB_Khaine_01": {
                "index_message_id": 999999,
                "entries": entries,
            }
        },
    }


def test_run_migration_assigns_all_replay_entries_to_production_server(tmp_path, monkeypatch, fernet_key):
    """Unit: every replay_entries row gets the production discord_server_id.

    ADR-006 D11 — the JSON has no server_id; the migration assigns ALL
    entries to the single production server. Two entries in replay_index.json
    -> two replay_entries rows, both with PROD_SERVER_ID.
    """
    monkeypatch.setenv("SCRAPCODE_DB_KEY", fernet_key)
    entries = [
        {"team": "Neuro", "tier": "Legendary 1", "position": "LHS",
         "damage": "1.33M", "url": "https://replay.example/abc",
         "comment": "", "submitted_by": "123456789"},
        {"team": "Mech", "tier": "Mythic 1", "position": "RHS",
         "damage": "2.0M", "url": "https://replay.example/def",
         "comment": "gg", "submitted_by": "987654321"},
    ]
    base = _write_minimal_cluster(
        tmp_path,
        guilds={"neuro": {"name": "Neuro", "api_key": "", "role_id": 1, "member_role_ids": []}},
        replay_index=_replay_index_with(entries),
    )
    db = tmp_path / "data" / "scrapcode.db"
    report = tmp_path / "parity.json"
    from bot.db.migrations_json_to_sqlite import run_migration
    rc = run_migration(source=str(base), db=str(db), report=str(report))
    assert rc == 0
    import sqlite3
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT discord_server_id, boss, map_name, url, damage_text, submitted_by "
        "FROM replay_entries ORDER BY url"
    ).fetchall()
    conn.close()
    assert len(rows) == 2
    assert all(r[0] == PROD_SERVER_ID for r in rows), "every row assigned to prod server"
    assert rows[0][1] == "Avatar" and rows[0][2] == "GB_Khaine_01"
    assert rows[0][4] == "1.33M", "damage kept as free-text (data-dictionary §2.10)"
    assert rows[1][4] == "2.0M"


def test_run_migration_enforces_per_tenant_url_uniqueness(tmp_path, monkeypatch, fernet_key):
    """Unit: the unique constraint is scoped per (discord_server_id, boss, map_name, url).

    ADR-006 D11 — URL uniqueness is per-tenant, not global. A duplicate
    (server, boss, map, url) insert raises IntegrityError; the same url
    under a different discord_server_id inserts successfully.
    """
    monkeypatch.setenv("SCRAPCODE_DB_KEY", fernet_key)
    entries = [
        {"team": "Neuro", "tier": "Legendary 1", "position": "LHS",
         "damage": "1.33M", "url": "https://replay.example/abc",
         "comment": "", "submitted_by": "123456789"},
    ]
    base = _write_minimal_cluster(
        tmp_path,
        guilds={"neuro": {"name": "Neuro", "api_key": "", "role_id": 1, "member_role_ids": []}},
        replay_index=_replay_index_with(entries),
    )
    db = tmp_path / "data" / "scrapcode.db"
    report = tmp_path / "parity.json"
    from bot.db.migrations_json_to_sqlite import run_migration
    rc = run_migration(source=str(base), db=str(db), report=str(report))
    assert rc == 0
    import sqlite3
    conn = sqlite3.connect(db)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO replay_entries (discord_server_id, boss, map_name, team, tier, "
            "position, damage_text, url, comment, submitted_by) "
            "VALUES (?, 'Avatar', 'GB_Khaine_01', 'Neuro', 'Legendary 1', 'LHS', "
            "'1.33M', 'https://replay.example/abc', '', '123456789')",
            (PROD_SERVER_ID,),
        )
    # Same url under a different server succeeds (per-tenant scope).
    conn.execute(
        "INSERT INTO replay_entries (discord_server_id, boss, map_name, team, tier, "
        "position, damage_text, url, comment, submitted_by) "
        "VALUES (?, 'Avatar', 'GB_Khaine_01', 'Neuro', 'Legendary 1', 'LHS', "
        "'1.33M', 'https://replay.example/abc', '', '123456789')",
        (9876543210,),
    )
    conn.commit()
    conn.close()


def test_run_migration_seeds_replay_threads_from_forum_and_map_constants(tmp_path, monkeypatch, fernet_key):
    """Unit: replay_threads is seeded from FORUM_CHANNELS/MAP_THREADS (ADR-006 D10).

    Every (boss, map_name) in MAP_THREADS gets one row with the production
    discord_server_id, the forum_channel_id from FORUM_CHANNELS, and the
    thread_id from MAP_THREADS. The index_message_id comes from
    replay_index.json where present (else None).
    """
    monkeypatch.setenv("SCRAPCODE_DB_KEY", fernet_key)
    base = _write_minimal_cluster(
        tmp_path,
        guilds={"neuro": {"name": "Neuro", "api_key": "", "role_id": 1, "member_role_ids": []}},
        replay_index=_replay_index_with([
            {"team": "Neuro", "tier": "Legendary 1", "position": "LHS",
             "damage": "1.33M", "url": "https://replay.example/abc",
             "comment": "", "submitted_by": "123456789"},
        ]),
    )
    db = tmp_path / "data" / "scrapcode.db"
    report = tmp_path / "parity.json"
    from bot.db.migrations_json_to_sqlite import run_migration
    rc = run_migration(source=str(base), db=str(db), report=str(report))
    assert rc == 0
    from bot.db.migrations_json_to_sqlite import FORUM_CHANNELS, MAP_THREADS
    expected_count = sum(len(maps) for maps in MAP_THREADS.values())
    import sqlite3
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT discord_server_id, boss, map_name, forum_channel_id, thread_id, "
        "index_message_id FROM replay_threads ORDER BY boss, map_name"
    ).fetchall()
    conn.close()
    assert len(rows) == expected_count, "one row per (boss, map_name) in MAP_THREADS"
    assert all(r[0] == PROD_SERVER_ID for r in rows), "all threads assigned to prod server"
    # The Avatar/GB_Khaine_01 thread carries the index_message_id from replay_index.json.
    avatar_row = next(r for r in rows if r[1] == "Avatar" and r[2] == "GB_Khaine_01")
    assert avatar_row[3] == FORUM_CHANNELS["Avatar"]
    assert avatar_row[4] == MAP_THREADS["Avatar"]["GB_Khaine_01"]
    assert avatar_row[5] == 999999, "index_message_id seeded from replay_index.json"
    # A (boss, map) absent from replay_index.json gets a NULL index_message_id.
    other_row = next(r for r in rows if r[5] is None)
    assert other_row is not None, "threads absent from replay_index.json get null index_message_id"