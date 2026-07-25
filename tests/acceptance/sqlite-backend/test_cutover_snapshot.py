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


def test_replay_index_render_byte_identical_pre_post_cutover(json_repo, sqlite_repo, env_vars):
    """@kpi @real-io — CS3.

    Pre-cutover: entries are read from `replay_index.json` via
    `JsonClusterRepository.load_replay_entries`. Post-cutover: entries are
    read from `replay_entries` via `SqlAlchemyClusterRepository.load_replay_entries`.
    The `build_index_message` renderer is a pure function shared by both
    paths, so byte-identical output proves the SQLite read preserves the
    JSON entry shape (data-dictionary §2.10) the renderer consumes.
    """
    from bot.cogs.replay_cog import build_index_message
    server, boss, map_name = 1458181638453203099, "Avatar", "GB_Khaine_01"
    # Seed the replay_threads row (FK target) + the SQLite side with the same
    # entry the JSON fixture carries, via the production write path (repo
    # upsert). This is a PRECONDITION (input data), not the expected
    # end-state — the byte-identical render is computed by production
    # `build_index_message`.
    _seed_replay_thread(env_vars["SCRAPCODE_DB_PATH"], server, boss, map_name,
                        forum_channel_id=1481592080940925062,
                        thread_id=1481592319894618304)
    sqlite_repo.upsert_replay_entry(server, boss, map_name, {
        "team": "Neuro", "tier": "Legendary 1", "position": "LHS",
        "damage": "1.33M", "url": "https://replay.example/abc",
        "comment": "", "submitted_by": "123456789",
    })
    json_entries   = json_repo.load_replay_entries(server, boss, map_name)
    sqlite_entries = sqlite_repo.load_replay_entries(server, boss, map_name)
    json_render   = build_index_message(json_entries)
    sqlite_render = build_index_message(sqlite_entries)
    assert json_render == sqlite_render
    assert json_render, "render must be non-empty for a populated entry"


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


@pytest.mark.asyncio
async def test_upload_replay_writes_replay_entries_row_not_json(sqlite_repo, env_vars, tmp_path, monkeypatch):
    """@driving_port @real-io — CS6.

    Drives `ReplayCog.upload_replay` through a synthetic Discord Interaction
    + fake bot. The cog routes through `bot.guilds.repo` (monkeypatched to the
    SQLite repo). Asserts a `replay_entries` row exists with
    `discord_server_id=1458181638453203099` and that NO `replay_index.json` is
    written (the retired JSON write path is gone — CS9 greps the cog source).
    """
    import bot.guilds as guilds_mod
    import bot.cogs.replay_cog as replay_mod
    from bot.cogs.replay_cog import ReplayCog
    from bot.repository_sqlalchemy import SqlAlchemyClusterRepository

    server, boss, map_name = 1458181638453203099, "Avatar", "GB_Khaine_01"
    _seed_replay_thread(env_vars["SCRAPCODE_DB_PATH"], server, boss, map_name,
                        forum_channel_id=1481592080940925062,
                        thread_id=1481592319894618304)

    # The conftest `tmp_clusters_tree` fixture writes a pre-existing
    # `replay_index.json` at tmp_path as INPUT data (the pre-cutover state).
    # Capture its content + mtime BEFORE the cog runs; upload_replay must NOT
    # touch it (the retired JSON write path is gone post-04-03).
    json_index_path = tmp_path / "replay_index.json"
    json_before = json_index_path.read_text(encoding="utf-8") if json_index_path.exists() else ""
    json_mtime_before = json_index_path.stat().st_mtime_ns if json_index_path.exists() else 0

    original_repo = guilds_mod.repo
    monkeypatch.setattr(guilds_mod, "repo", sqlite_repo)
    try:
        cog = ReplayCog(_FakeBot())
        interaction = _FakeInteraction(guild_id=server, user_id=123456789)
        await cog.upload_replay.callback(
            cog, interaction, boss=boss, map_name=map_name,
            team=_FakeChoice("Neuro", "Neuro"),
            tier=_FakeChoice("Legendary 1", "Legendary 1"),
            damage="1.33M", url="https://replay.example/new",
            position=_FakeChoice("LHS", "LHS"), comment=None,
        )
    finally:
        guilds_mod.repo = original_repo

    # A replay_entries row was inserted with the prod server id.
    rows = _replay_entries_rows(env_vars["SCRAPCODE_DB_PATH"], server, boss, map_name)
    assert any(r["url"] == "https://replay.example/new" for r in rows), \
        "upload_replay did not insert a replay_entries row"
    assert rows[-1]["discord_server_id"] == server, "row assigned to wrong server"
    # The pre-existing replay_index.json is UNCHANGED — upload_replay wrote to
    # replay_entries, not the retired JSON file.
    json_after = json_index_path.read_text(encoding="utf-8") if json_index_path.exists() else ""
    json_mtime_after = json_index_path.stat().st_mtime_ns if json_index_path.exists() else 0
    assert json_after == json_before, "replay_index.json content was modified by upload_replay"
    assert json_mtime_after == json_mtime_before, \
        "replay_index.json mtime was modified by upload_replay"
    # The success reply was sent.
    assert any("Replay submitted" in m for m in interaction.followup_messages), \
        interaction.followup_messages


@pytest.mark.asyncio
async def test_duplicate_upload_url_in_same_server_boss_map_rejected(sqlite_repo, env_vars, monkeypatch):
    """@infrastructure-failure — CS7.

    A duplicate URL in the same (server, boss, map_name) is rejected with the
    byte-for-byte existing duplicate-URL reply. No new row is inserted.
    """
    import bot.guilds as guilds_mod
    from bot.cogs.replay_cog import ReplayCog

    server, boss, map_name = 1458181638453203099, "Avatar", "GB_Khaine_01"
    _seed_replay_thread(env_vars["SCRAPCODE_DB_PATH"], server, boss, map_name,
                        forum_channel_id=1481592080940925062,
                        thread_id=1481592319894618304)
    # Pre-existing entry the duplicate collides with.
    sqlite_repo.upsert_replay_entry(server, boss, map_name, {
        "team": "Neuro", "tier": "Legendary 1", "position": "LHS",
        "damage": "1.33M", "url": "https://replay.example/abc",
        "comment": "", "submitted_by": "123456789",
    })

    original_repo = guilds_mod.repo
    monkeypatch.setattr(guilds_mod, "repo", sqlite_repo)
    try:
        cog = ReplayCog(_FakeBot())
        interaction = _FakeInteraction(guild_id=server, user_id=123456789)
        await cog.upload_replay.callback(
            cog, interaction, boss=boss, map_name=map_name,
            team=_FakeChoice("Neuro", "Neuro"),
            tier=_FakeChoice("Legendary 1", "Legendary 1"),
            damage="2.0M", url="https://replay.example/abc",
            position=None, comment=None,
        )
    finally:
        guilds_mod.repo = original_repo

    # Byte-for-byte the existing duplicate-URL reply.
    expected = "❌ This replay URL has already been submitted under **Avatar / GB_Khaine_01**."
    assert expected in interaction.followup_messages, interaction.followup_messages
    # No new row inserted — exactly one row for that URL.
    rows = [r for r in _replay_entries_rows(env_vars["SCRAPCODE_DB_PATH"], server, boss, map_name)
            if r["url"] == "https://replay.example/abc"]
    assert len(rows) == 1, f"duplicate insert leaked a second row: {rows}"


@pytest.mark.asyncio
async def test_delete_replay_removes_row_and_re_renders(sqlite_repo, env_vars, monkeypatch):
    """@driving_port — CS8.

    `/delete_replay` removes the `replay_entries` row and re-renders the index
    message with the remaining entries (the cog calls `load_replay_entries` +
    `_edit_index_message` after the delete).
    """
    import bot.guilds as guilds_mod
    from bot.cogs.replay_cog import ReplayCog

    server, boss, map_name = 1458181638453203099, "Avatar", "GB_Khaine_01"
    _seed_replay_thread(env_vars["SCRAPCODE_DB_PATH"], server, boss, map_name,
                        forum_channel_id=1481592080940925062,
                        thread_id=1481592319894618304,
                        index_message_id=999999)
    sqlite_repo.upsert_replay_entry(server, boss, map_name, {
        "team": "Neuro", "tier": "Legendary 1", "position": "LHS",
        "damage": "1.33M", "url": "https://replay.example/del",
        "comment": "", "submitted_by": "123456789",
    })
    sqlite_repo.upsert_replay_entry(server, boss, map_name, {
        "team": "Mech", "tier": "Legendary 1", "position": "RHS",
        "damage": "2.0M", "url": "https://replay.example/keep",
        "comment": "", "submitted_by": "999",
    })

    fake_bot = _FakeBot(thread_id=1481592319894618304, index_message_id=999999)
    original_repo = guilds_mod.repo
    monkeypatch.setattr(guilds_mod, "repo", sqlite_repo)
    try:
        cog = ReplayCog(fake_bot)
        interaction = _FakeInteraction(guild_id=server, user_id=123456789)
        await cog.delete_replay.callback(
            cog, interaction, boss=boss, map_name=map_name, url="https://replay.example/del",
        )
    finally:
        guilds_mod.repo = original_repo

    rows = _replay_entries_rows(env_vars["SCRAPCODE_DB_PATH"], server, boss, map_name)
    assert all(r["url"] != "https://replay.example/del" for r in rows), \
        "delete_replay did not remove the row"
    assert any(r["url"] == "https://replay.example/keep" for r in rows), \
        "delete_replay removed the wrong row"
    # The index message was re-rendered with the remaining entry.
    assert any("replay.example/keep" in c for c in fake_bot.edited_contents), \
        fake_bot.edited_contents
    assert any("Replay removed" in m for m in interaction.followup_messages), \
        interaction.followup_messages


# ---------------------------------------------------------------------------
# Fake Discord harness for driving ReplayCog without a Discord client.
# The cog only touches a small surface: interaction.response.defer,
# interaction.followup.send, interaction.guild_id, interaction.user.id,
# bot.get_channel/fetch_channel, forum.get_thread/archived_threads,
# thread.send/fetch_message, message.edit. The fakes record observable
# outcomes (followup messages, edited message contents) for assertions.
# ---------------------------------------------------------------------------

class _FakeChoice:
    def __init__(self, value, name):
        self.value = value
        self.name = name


class _FakeMessage:
    def __init__(self, msg_id, bot):
        self.id = msg_id
        self.bot = bot

    async def edit(self, content):
        self.bot.edited_contents.append(content)


class _FakeThread:
    def __init__(self, thread_id, bot, index_message_id=None):
        self.id = thread_id
        self.bot = bot
        self._next_id = 1000000
        if index_message_id is not None:
            self._messages = {index_message_id: _FakeMessage(index_message_id, bot)}
        else:
            self._messages = {}

    async def send(self, content):
        msg_id = self._next_id
        self._next_id += 1
        msg = _FakeMessage(msg_id, self.bot)
        self._messages[msg_id] = msg
        return msg

    async def fetch_message(self, msg_id):
        if msg_id in self._messages:
            return self._messages[msg_id]
        msg = _FakeMessage(msg_id, self.bot)
        self._messages[msg_id] = msg
        return msg


class _FakeForum:
    def __init__(self, thread):
        self._thread = thread

    def get_thread(self, thread_id):
        return self._thread if self._thread.id == thread_id else None

    async def archived_threads(self):
        if False:
            yield  # pragma: no cover — empty async generator


class _FakeBot:
    """Minimal bot that serves a single fake forum/thread for a thread id and
    records edited index-message contents for assertions."""
    def __init__(self, thread_id=1481592319894618304, index_message_id=None):
        self.edited_contents = []
        self._thread = _FakeThread(thread_id, self, index_message_id)
        self._forum = _FakeForum(self._thread)

    def get_channel(self, channel_id):
        return self._forum

    async def fetch_channel(self, channel_id):
        return self._forum


class _FakeResponse:
    async def defer(self, ephemeral=False):
        pass


class _FakeFollowup:
    def __init__(self):
        self.messages = []

    def send(self, content, ephemeral=False):
        self.messages.append(content)
        # The cog does not await followup.send's return; return a trivial awaitable.
        class _Done:
            def __await__(self_inner):
                return iter([])
        return _Done()


class _FakeUser:
    def __init__(self, user_id):
        self.id = user_id


class _FakeInteraction:
    def __init__(self, guild_id, user_id):
        self.guild_id = guild_id
        self.user = _FakeUser(user_id)
        self.response = _FakeResponse()
        self.followup = _FakeFollowup()
        # The cog's autocomplete reads interaction.namespace.boss; the commands
        # themselves do not touch namespace.
        class _Ns:
            boss = None
        self.namespace = _Ns()

    @property
    def followup_messages(self):
        return self.followup.messages


def _seed_replay_thread(db_path, server, boss, map_name, *, forum_channel_id,
                       thread_id, index_message_id=None):
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO replay_threads "
        "(discord_server_id, boss, map_name, forum_channel_id, thread_id, "
        "index_message_id) VALUES (?, ?, ?, ?, ?, ?)",
        (server, boss, map_name, forum_channel_id, thread_id, index_message_id),
    )
    conn.commit()
    conn.close()


def _replay_entries_rows(db_path, server, boss, map_name):
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT discord_server_id, boss, map_name, url, submitted_by "
        "FROM replay_entries WHERE discord_server_id=? AND boss=? AND map_name=?",
        (server, boss, map_name),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Unit tests (RED_UNIT) — per-tenant upload/get/delete delegation + the
# replay_threads thread-ID lookup. Driving port = the ClusterRepository ABC
# replay methods. Behavior budget: 4 behaviors (per-tenant insert, per-tenant
# duplicate rejection, per-tenant delete, thread-ID lookup from
# replay_threads) x 2 = max 8 unit tests.
# ---------------------------------------------------------------------------

def test_upsert_replay_entry_writes_per_tenant_row_load_replay_entries_reads_it(sqlite_repo, env_vars):
    """@driving_port @real-io — per-tenant insert + read delegation."""
    server, boss, map_name = 1458181638453203099, "Avatar", "GB_Khaine_01"
    _seed_replay_thread(env_vars["SCRAPCODE_DB_PATH"], server, boss, map_name,
                        forum_channel_id=1481592080940925062,
                        thread_id=1481592319894618304)
    sqlite_repo.upsert_replay_entry(server, boss, map_name, {
        "team": "Neuro", "tier": "Legendary 1", "position": "LHS",
        "damage": "1.33M", "url": "https://replay.example/u1",
        "comment": "c", "submitted_by": "123456789",
    })
    entries = sqlite_repo.load_replay_entries(server, boss, map_name)
    assert len(entries) == 1
    assert entries[0]["url"] == "https://replay.example/u1"
    assert entries[0]["team"] == "Neuro"


def test_upsert_replay_entry_rejects_duplicate_url_per_server_boss_map(sqlite_repo, env_vars):
    """@driving_port @infrastructure-failure — per-tenant duplicate rejection."""
    import pytest
    from bot.repository import DuplicateReplayUrlError
    server, boss, map_name = 1458181638453203099, "Avatar", "GB_Khaine_01"
    _seed_replay_thread(env_vars["SCRAPCODE_DB_PATH"], server, boss, map_name,
                        forum_channel_id=1481592080940925062,
                        thread_id=1481592319894618304)
    entry = {"team": "Neuro", "tier": "Legendary 1", "position": "LHS",
             "damage": "1.33M", "url": "https://replay.example/dup",
             "comment": "", "submitted_by": "1"}
    sqlite_repo.upsert_replay_entry(server, boss, map_name, entry)
    with pytest.raises(DuplicateReplayUrlError) as exc:
        sqlite_repo.upsert_replay_entry(server, boss, map_name, entry)
    assert exc.value.boss == boss and exc.value.map_name == map_name


def test_upsert_replay_entry_same_url_different_tenant_is_allowed(sqlite_repo, env_vars):
    """@driving_port @real-io — per-tenant scoping: the same URL under a
    different (server, boss, map) does NOT collide (ADR-006 D11 / ADR-004 §3)."""
    server, boss, map_name = 1458181638453203099, "Avatar", "GB_Khaine_01"
    _seed_replay_thread(env_vars["SCRAPCODE_DB_PATH"], server, boss, map_name,
                        forum_channel_id=1481592080940925062,
                        thread_id=1481592319894618304)
    other_server = 9876543210
    _seed_replay_thread(env_vars["SCRAPCODE_DB_PATH"], other_server, boss, map_name,
                        forum_channel_id=1481592080940925062,
                        thread_id=1481592319894618304)
    entry = {"team": "Neuro", "tier": "Legendary 1", "position": "LHS",
             "damage": "1.33M", "url": "https://replay.example/shared",
             "comment": "", "submitted_by": "1"}
    sqlite_repo.upsert_replay_entry(server, boss, map_name, entry)
    # Same URL under a different tenant must succeed (no global collision).
    sqlite_repo.upsert_replay_entry(other_server, boss, map_name, entry)
    assert len(sqlite_repo.load_replay_entries(server, boss, map_name)) == 1
    assert len(sqlite_repo.load_replay_entries(other_server, boss, map_name)) == 1


def test_delete_replay_entry_removes_only_matching_url(sqlite_repo, env_vars):
    """@driving_port — delete delegation removes the matching URL and no other."""
    server, boss, map_name = 1458181638453203099, "Avatar", "GB_Khaine_01"
    _seed_replay_thread(env_vars["SCRAPCODE_DB_PATH"], server, boss, map_name,
                        forum_channel_id=1481592080940925062,
                        thread_id=1481592319894618304)
    for url in ("https://replay.example/a", "https://replay.example/b"):
        sqlite_repo.upsert_replay_entry(server, boss, map_name, {
            "team": "Neuro", "tier": "Legendary 1", "position": "", "damage": "1M",
            "url": url, "comment": "", "submitted_by": "1",
        })
    assert sqlite_repo.delete_replay_entry(server, boss, map_name, "https://replay.example/a") is True
    remaining = sqlite_repo.load_replay_entries(server, boss, map_name)
    assert [e["url"] for e in remaining] == ["https://replay.example/b"]
    assert sqlite_repo.delete_replay_entry(server, boss, map_name, "https://replay.example/missing") is False


def test_get_replay_thread_returns_thread_id_from_replay_threads_table(sqlite_repo, env_vars):
    """@driving_port @real-io — thread-ID lookup is sourced from
    `replay_threads` (ADR-006 D10), not hardcoded constants."""
    server, boss, map_name = 1458181638453203099, "Avatar", "GB_Khaine_01"
    _seed_replay_thread(env_vars["SCRAPCODE_DB_PATH"], server, boss, map_name,
                        forum_channel_id=1481592080940925062,
                        thread_id=1481592319894618304,
                        index_message_id=999999)
    info = sqlite_repo.get_replay_thread(server, boss, map_name)
    assert info is not None
    assert info["thread_id"] == 1481592319894618304
    assert info["forum_channel_id"] == 1481592080940925062
    assert info["index_message_id"] == 999999
    # Unknown (boss, map) returns None.
    assert sqlite_repo.get_replay_thread(server, "Unknown", "nope") is None


def test_list_replay_threads_drives_autocomplete_from_replay_threads(sqlite_repo, env_vars):
    """@driving_port — `list_replay_threads` returns the {boss: {map_name}}
    tree the cog's boss/map autocomplete filters."""
    server = 1458181638453203099
    _seed_replay_thread(env_vars["SCRAPCODE_DB_PATH"], server, "Avatar", "GB_Khaine_01",
                        forum_channel_id=1481592080940925062, thread_id=1481592319894618304)
    _seed_replay_thread(env_vars["SCRAPCODE_DB_PATH"], server, "Avatar", "GB_Khaine_02",
                        forum_channel_id=1481592080940925062, thread_id=1481592399720611840)
    _seed_replay_thread(env_vars["SCRAPCODE_DB_PATH"], server, "Cawl", "GB_Belisarius_01",
                        forum_channel_id=1481592218891456583, thread_id=1481596799201317006)
    tree = sqlite_repo.list_replay_threads(server)
    assert set(tree["Avatar"]) == {"GB_Khaine_01", "GB_Khaine_02"}
    assert "GB_Belisarius_01" in tree["Cawl"]


def test_set_replay_thread_index_message_persists_index_message_id(sqlite_repo, env_vars):
    """@driving_port — recording the index message id survives a fresh read
    (the cog calls this after the first `thread.send`)."""
    server, boss, map_name = 1458181638453203099, "Avatar", "GB_Khaine_01"
    _seed_replay_thread(env_vars["SCRAPCODE_DB_PATH"], server, boss, map_name,
                        forum_channel_id=1481592080940925062,
                        thread_id=1481592319894618304)
    sqlite_repo.set_replay_thread_index_message(server, boss, map_name, 42424242)
    assert sqlite_repo.get_replay_thread(server, boss, map_name)["index_message_id"] == 42424242


def test_replay_cog_helpers_and_forum_constants_removed():
    """@kpi — CS9."""
    replay_cog = Path(__import__("bot.cogs.replay_cog", fromlist=["x"]).__file__)
    src = replay_cog.read_text(encoding="utf-8")
    for pat in ("REPLAY_INDEX_FILE", "load_replay_index", "save_replay_index",
                "replay_index.json", "FORUM_CHANNELS", "MAP_THREADS"):
        assert pat not in src, f"{pat} still present in replay_cog.py"


def test_json_tree_not_modified_after_successful_cutover_cycle(
    sqlite_repo, tmp_clusters_tree, env_vars, make_tacticus_entry
):
    """@property @real-io — CS10 — KPI-3c.

    After a successful cutover cycle against the SQLite backend, NO JSON
    file mtime in the clusters/ tree changed; the JSON tree remains intact
    as the read-only rollback fallback (DEVOPS one-cycle read-only fallback).

    The cycle is driven through the production write port
    `bot.tracker.process_api_response`, which resolves the write repo via
    `bot.guilds.build_repo()` (SCRAPCODE_REPO_BACKEND=sqlite) and upserts
    battle + bomb hits via `upsert_guild_hits` (one transaction per guild).
    The JSON tree is NOT touched on the SQLite path (KPI-3c).
    """
    import bot.tracker as tracker_mod

    mtimes_before = {p: p.stat().st_mtime_ns
                     for p in tmp_clusters_tree.rglob("*.json")}
    assert mtimes_before, "fixture must seed at least one JSON file"

    # A full hourly cycle: battle + bomb entries for the seeded guild.
    battle_entries = [make_tacticus_entry(damage=9999, user_id="u-cs10-battle")]
    bomb_entries = [make_tacticus_entry(damage_type="Bomb", damage=7777,
                                        user_id="u-cs10-bomb", hero_details=[],
                                        machine_of_war=None)]
    tracker_mod.process_api_response(
        {"entries": battle_entries + bomb_entries},
        SEASON, PROD_SERVER, GUILD_NEURO,
    )

    mtimes_after = {p: p.stat().st_mtime_ns
                    for p in tmp_clusters_tree.rglob("*.json")}
    assert set(mtimes_before.keys()) == set(mtimes_after.keys()), \
        "JSON file set changed during the cutover cycle"
    assert mtimes_before == mtimes_after, (
        "JSON tree was modified during the cutover cycle — "
        f"changed: {[p for p in mtimes_after if mtimes_after[p] != mtimes_before[p]]}"
    )