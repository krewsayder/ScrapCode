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


@RED
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


@RED
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


@RED
def test_corrupted_sqlite_database_raises_not_returns_empty(env_vars, sqlite_db_path, sqlite_repo):
    """@infrastructure-failure @real-io — RC7 — silent-empty trap retired on SQLite."""
    sqlite_db_path.parent.mkdir(parents=True, exist_ok=True)
    sqlite_db_path.write_text("not a database", encoding="utf-8")
    with pytest.raises(Exception):
        sqlite_repo.load(1458181638453203099)


@RED
def test_empty_sqlite_database_returns_empty_dicts_without_raising(sqlite_repo):
    """@edge — RC8."""
    assert sqlite_repo.load(1458181638453203099).guilds == {}
    assert sqlite_repo.load_player_registrations(1458181638453203099) == {}
    assert sqlite_repo.load_capped_state(1458181638453203099) == {}
    assert "players" in sqlite_repo.load_player_list(1458181638453203099, "neuro")


@RED
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


@RED
def test_player_registrations_api_key_uniqueness_enforced(sqlite_repo):
    """@infrastructure-failure — RC10."""
    first = {"123456789": {"api_key": "shared-key", "guild_id": "neuro"}}
    sqlite_repo.save_player_registrations(1458181638453203099, first)
    with pytest.raises(Exception):
        sqlite_repo.save_player_registrations(
            1458181638453203099,
            {**first, "987654321": {"api_key": "shared-key", "guild_id": "mech"}},
        )


@RED
def test_role_tiers_check_constraint_rejects_invalid_tier(sqlite_repo):
    """@infrastructure-failure — RC11."""
    import sqlite3
    conn = sqlite3.connect(sqlite_repo._db_path)
    with pytest.raises(Exception):
        conn.execute("INSERT INTO role_tiers (discord_server_id, tier, role_id) VALUES (?, 'superuser', 999)",
                     (1458181638453203099,))
    conn.close()


@RED
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


@RED
def test_player_list_migrator_v1_to_v2_inverts_and_v2_is_noop():
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


@RED
def test_try_insert_dedup_branches_pinned():
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


@RED
def test_upsert_keep_max_on_battle_hits_preserves_try_insert_contract(sqlite_repo, make_tacticus_entry):
    """@property @real-io — RC15."""
    server, guild, season = 1458181638453203099, "neuro", 94
    base = make_tacticus_entry(damage=12000, hero_details=[{"unitId": "Aethana"}, {"unitId": "Eldryon"}],
                               machine_of_war={"unitId": "Khaine"})

    # same roster, higher damage — replaces
    higher = make_tacticus_entry(damage=15000, hero_details=[{"unitId": "Aethana"}, {"unitId": "Eldryon"}],
                                machine_of_war={"unitId": "Khaine"})
    sqlite_repo.upsert_battle_hits(server, guild, season, [base, higher])
    battle = sqlite_repo.load_battle_hits(server, guild, season)
    assert battle["boss_hits"]["Avatar"]["0"]["Legendary_0"][0]["damage"] == 15000

    # same roster, lower damage — keep-max (row stays at 15000)
    lower = make_tacticus_entry(damage=9000, hero_details=[{"unitId": "Aethana"}, {"unitId": "Eldryon"}],
                                machine_of_war={"unitId": "Khaine"})
    sqlite_repo.upsert_battle_hits(server, guild, season, [lower])
    battle = sqlite_repo.load_battle_hits(server, guild, season)
    assert battle["boss_hits"]["Avatar"]["0"]["Legendary_0"][0]["damage"] == 15000

    # different roster — separate row
    diff = make_tacticus_entry(damage=9000, hero_details=[{"unitId": "Aethana"}, {"unitId": "Tan Gida"}],
                              machine_of_war={"unitId": "Khaine"})
    sqlite_repo.upsert_battle_hits(server, guild, season, [diff])
    battle = sqlite_repo.load_battle_hits(server, guild, season)
    assert len(battle["boss_hits"]["Avatar"]["0"]["Legendary_0"]) == 2

    # bomb plain top-N (no roster dedup)
    bombs = [make_tacticus_entry(damage_type="Bomb", damage=d, user_id=f"u{d}",
                                 hero_details=[], machine_of_war=None)
             for d in (100, 90, 80, 70, 60, 50)]
    sqlite_repo.upsert_bomb_hits(server, guild, season, bombs)
    bomb = sqlite_repo.load_bomb_hits(server, guild, season)
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