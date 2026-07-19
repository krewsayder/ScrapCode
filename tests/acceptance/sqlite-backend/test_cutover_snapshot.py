"""Cutover render snapshot acceptance tests (US-009, US-011, KPI-4).

Implements `acceptance/cutover-snapshot.feature`. The Discord commands
are too heavy to drive live; the closest testable proxy is
`bot/embeds.build_battle_messages` / `build_bomb_messages` driven against
the JSON-backed repo vs the SQLite-backed repo. Byte-identical output for
the same input is the KPI-4 gate (US-011).

The `embeds` builders call `bot.guilds.get_player_list` which uses the
module-level `repo` singleton. The fixtures monkeypatch `bot.guilds.repo`
to the JSON or SQLite impl — the same mechanism the production composition
root uses to flip the singleton (ADR-006 D9).

The SQLite side of each parity scenario is SEEDED (module-local override
of conftest's `sqlite_repo`) with the same data the JSON fixture
`tmp_clusters_tree` carries. Seeding uses the production write path
(`repo.upsert_battle_hits` / `upsert_bomb_hits`) + the production
entity writes (`repo.save` / `save_player_list`); the render uses the
production read path (`repo.load_*_hits`) + `embeds.build_*_messages`.
Parity therefore proves the production write→read→render round-trip
preserves JSON semantics — the seed is a PRECONDITION, not the
end-state (Mandate 7 / Fixture Theater prevention).
"""
from __future__ import annotations

from pathlib import Path

import pytest

RED = pytest.mark.skip(reason="RED scaffold — enable one at a time in DELIVER")

PROD_SERVER = 1458181638453203099
GUILD_NEURO = "neuro"
SEASON = 94


# ---------------------------------------------------------------------------
# Module-local SEEDED sqlite_repo override. The conftest `sqlite_repo` is
# unseeded (empty DB); the parity scenarios need the SQLite side to carry
# the same data the JSON fixture does, populated via the production write
# path. This is a PRECONDITION fixture (input data), not the expected
# end-state — the byte-identical render is computed by production code.
# ---------------------------------------------------------------------------

@pytest.fixture
def sqlite_repo(env_vars, tmp_clusters_tree):
    from bot.repository import JsonClusterRepository
    from bot.repository_sqlalchemy import SqlAlchemyClusterRepository
    from bot.tracker import get_tier_key

    json_repo = JsonClusterRepository(base_path=tmp_clusters_tree)
    sql_repo = SqlAlchemyClusterRepository(
        db_path=env_vars["SCRAPCODE_DB_PATH"],
        fernet_key=env_vars["SCRAPCODE_DB_KEY"],
    )
    # Cluster + player_list so get_player_list resolves display names + the
    # battle/bomb FK to guilds is satisfiable.
    sql_repo.save(json_repo.load(PROD_SERVER))
    for guild_id in ("neuro", "mech"):
        sql_repo.save_player_list(
            PROD_SERVER, guild_id, json_repo.load_player_list(PROD_SERVER, guild_id)
        )
    # Battle + bomb hits seeded via the production upsert path. The JSON
    # season file is the single source of truth; entries are reconstructed
    # in the upsert's tacticus-entry contract shape.
    sql_repo.upsert_battle_hits(
        PROD_SERVER, GUILD_NEURO, SEASON,
        _battle_entries_from_season(json_repo.load_battle_hits(PROD_SERVER, GUILD_NEURO, SEASON)),
    )
    sql_repo.upsert_bomb_hits(
        PROD_SERVER, GUILD_NEURO, SEASON,
        _bomb_entries_from_season(json_repo.load_bomb_hits(PROD_SERVER, GUILD_NEURO, SEASON)),
    )
    return sql_repo


def _battle_entries_from_season(season_data: dict) -> list[dict]:
    """Reconstruct upsert-contract battle entries from a loaded season dict.

    The JSON season shape is `{boss_hits: {boss: {enc: {tier: [hit]}}}}`;
    the upsert contract takes a flat list of tacticus-shaped entries.
    The round-trip is lossless because `load_battle_hits` returns the same
    hit fields the upsert consumes (data-dictionary §2.7).
    """
    entries = []
    for boss_id, encounters in season_data.get("boss_hits", {}).items():
        for e_index, tiers in encounters.items():
            for tier_key, hits in tiers.items():
                for hit in hits:
                    entries.append({
                        "unitId": boss_id,
                        "encounterIndex": int(e_index),
                        "tier_key": tier_key,
                        "encounterType": hit.get("encounterType", "Battle"),
                        "damage": hit["damage"],
                        "userId": hit["user_id"],
                        "completedOn": hit["completed_on"],
                        "heroDetails": hit.get("hero_details", []),
                        "machineOfWarDetails": hit.get("machine_of_war"),
                    })
    return entries


def _bomb_entries_from_season(season_data: dict) -> list[dict]:
    entries = []
    for boss_id, encounters in season_data.get("boss_hits", {}).items():
        for e_index, tiers in encounters.items():
            for tier_key, hits in tiers.items():
                for hit in hits:
                    entries.append({
                        "unitId": boss_id,
                        "encounterIndex": int(e_index),
                        "tier_key": tier_key,
                        "encounterType": hit.get("encounterType", "Bomb"),
                        "damage": hit["damage"],
                        "userId": hit["user_id"],
                        "completedOn": hit["completed_on"],
                    })
    return entries


def _render_battle(repo, server, guild, season, choice):
    """Drive `embeds.build_battle_messages` with `repo` as the live singleton."""
    import bot.guilds as guilds_mod
    original = guilds_mod.repo
    guilds_mod.repo = repo
    try:
        from bot.embeds import build_battle_messages
        data = repo.load_battle_hits(server, guild, season)
        return build_battle_messages(
            data=data, season=season, tier=choice,
            discord_server_id=server, guild_id=guild, guild_name=guild.title(),
        )
    finally:
        guilds_mod.repo = original


def _render_bomb(repo, server, guild, season, choice):
    import bot.guilds as guilds_mod
    original = guilds_mod.repo
    guilds_mod.repo = repo
    try:
        from bot.embeds import build_bomb_messages
        data = repo.load_bomb_hits(server, guild, season)
        return build_bomb_messages(
            data=data, season=season, tier=choice,
            discord_server_id=server, guild_id=guild, guild_name=guild.title(),
        )
    finally:
        guilds_mod.repo = original


# ---------------------------------------------------------------------------
# CS-1 (ENABLED — first scenario): Battle render byte-identity.
# ---------------------------------------------------------------------------

def test_battle_leaderboard_render_byte_identical_pre_post_cutover(
    json_repo, sqlite_repo, legendary_0_choice
):
    """@driving_port @kpi @real-io — CS1.

    RED scaffold: the SQLite impl raises AssertionError on construction,
    so this fails RED until DELIVER lands the real impl. Once it lands,
    the JSON-backed render and the SQLite-backed render must be
    byte-identical for the same input (KPI-4).
    """
    server, guild, season = 1458181638453203099, "neuro", 94
    json_render = _render_battle(json_repo, server, guild, season, legendary_0_choice)
    sqlite_render = _render_battle(sqlite_repo, server, guild, season, legendary_0_choice)
    assert json_render == sqlite_render
    assert json_render, "render must be non-empty for a populated season"


# ---------------------------------------------------------------------------
# Remaining scenarios skipped until DELIVER.
# ---------------------------------------------------------------------------

def test_bomb_leaderboard_render_byte_identical_pre_post_cutover(
    json_repo, sqlite_repo, legendary_0_choice
):
    """@kpi @real-io — CS2."""
    server, guild, season = 1458181638453203099, "neuro", 94
    json_render = _render_bomb(json_repo, server, guild, season, legendary_0_choice)
    sqlite_render = _render_bomb(sqlite_repo, server, guild, season, legendary_0_choice)
    assert json_render == sqlite_render


@RED
def test_replay_index_render_byte_identical_pre_post_cutover(json_repo, sqlite_repo):
    """@kpi @real-io — CS3."""
    # RED scaffold: replay rendering goes through replay_cog helpers that
    # still read replay_index.json pre-cutover; post-cutover reads
    # replay_entries via the repo.
    raise AssertionError("RED scaffold: replay render parity not implemented")


def test_empty_leaderboard_renders_same_no_entries_message(json_repo, sqlite_repo, legendary_0_choice):
    """@edge @real-io — CS4."""
    server, guild, season = 1458181638453203099, "neuro", 999  # no hits for season 999
    json_render = _render_battle(json_repo, server, guild, season, legendary_0_choice)
    sqlite_render = _render_battle(sqlite_repo, server, guild, season, legendary_0_choice)
    assert json_render == sqlite_render == []  # build_* returns [] when no entries


def test_player_marked_is_former_renders_same_suffix(json_repo, sqlite_repo, legendary_0_choice):
    """@edge @real-io — CS5.

    The fixture's `is_former` player 'Jonas Klein' (tacticus-uid-002) has a
    Bomb hit (not a Battle hit), so the parity is driven through the Bomb
    leaderboard render — the leaderboard that actually lists the player.
    """
    server, guild, season = 1458181638453203099, "neuro", 94
    json_render = _render_bomb(json_repo, server, guild, season, legendary_0_choice)
    sqlite_render = _render_bomb(sqlite_repo, server, guild, season, legendary_0_choice)
    assert "Jonas Klein (former)" in "\n".join(json_render)
    assert json_render == sqlite_render


@RED
def test_upload_replay_writes_replay_entries_row_not_json(sqlite_repo, tmp_path):
    """@driving_port @real-io — CS6."""
    # RED scaffold: drive replay_cog.upload_replay through the repo. The
    # scaffold cannot run the cog; the crafter implements a thin driving
    # harness via the repo + a synthetic Interaction.
    raise AssertionError("RED scaffold: upload_replay rewire not implemented")


@RED
def test_duplicate_upload_url_in_same_server_boss_map_rejected(sqlite_repo):
    """@infrastructure-failure — CS7."""
    raise AssertionError("RED scaffold: duplicate-URL rejection not implemented")


@RED
def test_delete_replay_removes_row_and_re_renders(sqlite_repo):
    """@driving_port — CS8."""
    raise AssertionError("RED scaffold: delete_replay rewire not implemented")


@RED
def test_replay_cog_helpers_and_forum_constants_removed():
    """@kpi — CS9."""
    replay_cog = Path(__import__("bot.cogs.replay_cog", fromlist=["x"]).__file__)
    src = replay_cog.read_text(encoding="utf-8")
    for pat in ("REPLAY_INDEX_FILE", "load_replay_index", "save_replay_index",
                "replay_index.json", "FORUM_CHANNELS", "MAP_THREADS"):
        assert pat not in src, f"{pat} still present in replay_cog.py"


@RED
def test_json_tree_not_modified_after_successful_cutover_cycle(json_repo, tmp_clusters_tree):
    """@property @real-io — CS10."""
    mtimes_before = {p: p.stat().st_mtime_ns
                     for p in tmp_clusters_tree.rglob("*.json")}
    # RED scaffold: run a full hourly cycle against the SQLite singleton,
    # then assert no JSON file mtime changed.
    mtimes_after = {p: p.stat().st_mtime_ns for p in tmp_clusters_tree.rglob("*.json")}
    assert mtimes_before == mtimes_after, "JSON tree was modified during the cutover cycle"