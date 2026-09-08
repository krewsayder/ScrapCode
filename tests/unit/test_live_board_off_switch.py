"""The cluster leaderboard's operator off switch.

WHY-NEW-FILE: tests/unit/test_live_board_off_switch.py
  CLOSEST-EXISTING: tests/unit/test_leaderboard_season_fall_through.py
  EXTENSION-COST: every property there is quantified over a CLUSTER of guilds
    in three key states, and its whole subject is which guild's key a command
    sends to Tacticus. These claims send no key at all — the switch is read
    and written with no probe, no season lookup and no Tacticus transport —
    and half of them drive `tasks_cog._refresh_live_leaderboards`, which that
    module has no harness for and no reason to grow one.
  PARALLEL-RATIONALE: different observable and a disjoint dependency set. That
    module's universe is `tacticus.credentials` + a refusal string; this one's
    is which Discord messages were edited, sent or left alone, and what shape
    the config takes in storage.

WHAT IS ACTUALLY AT RISK. A guild's live board stops on its own when the
guild's key is quarantined. The cluster board had no such state: it either
refreshed every hour or it stopped silently because no key could answer the
season — a board frozen mid-season that still reads as healthy, which is the
2026-07-28 failure shape. The switch adds a DELIBERATE version of that stop,
and the whole value of it is that the stop is recorded and visible.

Two claims carry the risk, and they are opposite halves of one iff:

  1. A board that is off is not touched. Not edited, not re-sent, not removed
     from the config. The user's requirement is that the posted messages stay
     exactly as they are, so "was any Discord call made at all" is the
     observable, not "did the content change".
  2. A board that is on is still refreshed. A skip written as an unconditional
     `continue`, or a default that reads absent-as-off, satisfies claim 1
     perfectly and pauses every board in production on deploy. That is why the
     enabled case is asserted through the same harness rather than assumed.

THE REPRESENTATION IS A THIRD CLAIM, and a quiet one. An enabled board is the
ABSENCE of the `enabled` key, in both backends. Writing `enabled: True` would
round-trip through the JSON repository and be dropped by the SQLite one, so
the two adapters would disagree about a config neither of them changed and
`test_every_abc_method_round_trips_through_both_impls` would start failing for
a reason nowhere near this feature. `test_enabling_never_writes_a_true_flag`
pins the invariant at the writer, which is the only place it can be broken.
"""
from __future__ import annotations

import asyncio
import os
import sys
from contextlib import contextmanager

# `bot.guilds` evaluates `repo = build_repo()` at import time and reads the
# environment at that moment. Pin a harmless backend and the two channel ids
# `config.py` casts with `int(os.getenv(...))` before any `bot.*` import.
# Precedent: tests/unit/test_leaderboard_season_fall_through.py.
os.environ.setdefault("SCRAPCODE_REPO_BACKEND", "json")
os.environ.setdefault("UPDATE_CHANNEL_ID", "0")
os.environ.setdefault("REPLAY_INDEX_CHANNEL_ID", "0")

import pytest  # noqa: E402

SEASON = 77
SERVER_ID = 4242
CHANNEL_ID = 7


# ===========================================================================
# The representation — absent means on, in both backends
# ===========================================================================

def test_enabling_never_writes_a_true_flag():
    """`enabled` appears in a config only when the board is OFF.

    The invariant that keeps a config byte-identical across the JSON and
    SQLite adapters. Asserted on the dict rather than through a repository
    because the writer is the only place it can be violated — both adapters
    faithfully store whatever they are handed, which is the problem.
    """
    from bot.guilds import live_board_enabled, set_live_board_enabled

    config: dict = {"channel_id": CHANNEL_ID, "messages": {}}

    set_live_board_enabled(config, False)
    assert config["enabled"] is False, "turning a board off must record it"
    assert not live_board_enabled(config)

    set_live_board_enabled(config, True)
    assert "enabled" not in config, (
        "enabling wrote a key instead of removing one. An enabled board is "
        "the ABSENCE of `enabled`; writing True survives the JSON round trip "
        "and is dropped by the SQLite one, which breaks repository parity."
    )
    assert live_board_enabled(config)


def test_a_config_with_no_flag_is_on():
    """Every board configured before the switch existed keeps updating.

    Defaulting the other way would pause the entire cluster on deploy — the
    exact silent freeze the switch exists to make impossible.
    """
    from bot.guilds import live_board_enabled

    assert live_board_enabled({"channel_id": CHANNEL_ID, "messages": {}})


@pytest.mark.parametrize("enabled", [True, False])
def test_the_switch_survives_the_sqlite_round_trip(tmp_path, enabled: bool):
    """The state is persisted, not just held in memory.

    A switch that lives only in the loaded dict comes back ON at the next bot
    restart, which for a board an operator deliberately paused is the same as
    not having the feature.
    """
    repo = _sqlite_repo(tmp_path)
    config = {"channel_id": CHANNEL_ID, "messages": {"Legendary_0": 999}, "season": SEASON}
    _set_enabled(config, enabled)

    repo.save_live_leaderboards(SERVER_ID, {"cluster": config})
    reloaded = repo.load_live_leaderboards(SERVER_ID)

    assert reloaded == {"cluster": config}, (
        "the config did not survive the round trip unchanged — an extra or "
        "missing `enabled` key here is a JSON/SQLite parity break"
    )


# ===========================================================================
# The refresh loop — the iff that makes the switch mean anything
# ===========================================================================

def test_a_board_that_is_off_is_left_completely_alone():
    """Claim 1. No edit, no send, and the config is not dropped.

    The requirement is that the posted messages stay exactly as they are, so
    the assertion is that NO Discord call was made — a refresh that re-sent
    identical content would leave the channel looking right and still be
    wrong, because the messages would be new ones and the old board's place
    in the channel history would be gone.

    `config_survived` is the slot that separates a pause from a teardown: the
    loop already removes configs whose channel has vanished, and a skip
    written into that branch would delete the board it was asked to pause.
    """
    surface = _refresh_with(enabled=False)

    assert surface == {
        "messages_edited": [],
        "messages_sent": [],
        "config_survived": True,
        "config_still_off": True,
        "season_recorded": SEASON,
    }, f"a board that is off was not left alone: {surface!r}"


def test_a_board_that_is_on_is_still_refreshed():
    """Claim 2. The other half of the iff, and the one a bad default breaks.

    Without this, an unconditional skip — or a default that reads a missing
    `enabled` as off — passes claim 1 and silently pauses every live board on
    every server.
    """
    surface = _refresh_with(enabled=True)

    assert surface["messages_edited"], (
        "an ENABLED board was not refreshed. The switch is inverted, or the "
        f"absent-means-on default is wrong: {surface!r}"
    )
    assert surface["config_survived"] and not surface["config_still_off"]


def test_the_switch_is_scope_agnostic():
    """A `guild:` board honours the flag too.

    The column and the loop are scope-agnostic by construction even though
    only the cluster board has commands driving it today. Pinning it here is
    what stops the guild-scoped commands, when they arrive, from finding a
    switch that silently only ever worked for one key.
    """
    surface = _refresh_with(enabled=False, scope_key="guild:neuro")

    assert surface["messages_edited"] == [] and surface["config_survived"], (
        f"a guild-scoped board ignored the off switch: {surface!r}"
    )


# ===========================================================================
# The commands
# ===========================================================================

def test_disabling_then_enabling_flips_the_stored_state():
    """The round trip an operator actually performs."""
    world = _run_command("disable_cluster_leaderboard", board={"season": SEASON})
    assert world.saved["cluster"]["enabled"] is False
    assert "turned off" in world.reply and "/enable_cluster_leaderboard" in world.reply

    world = _run_command(
        "enable_cluster_leaderboard", board={"season": SEASON, "enabled": False}
    )
    assert "enabled" not in world.saved["cluster"]
    assert "update again" in world.reply


def test_turning_off_preserves_the_channel_the_messages_and_the_season():
    """Nothing but the switch is rewritten.

    A pause that dropped `messages` would orphan the posted board — the
    re-enable would have no message ids to edit and would post a second set
    beneath the first.
    """
    world = _run_command(
        "disable_cluster_leaderboard",
        board={"season": SEASON, "messages": {"Legendary_0": 999}},
    )

    assert world.saved["cluster"] == {
        "channel_id": CHANNEL_ID,
        "messages": {"Legendary_0": 999},
        "season": SEASON,
        "enabled": False,
    }, f"turning the board off rewrote more than the switch: {world.saved!r}"


@pytest.mark.parametrize(
    "command,already",
    [("disable_cluster_leaderboard", False), ("enable_cluster_leaderboard", True)],
)
def test_a_no_op_flip_says_so_and_writes_nothing(command: str, already: bool):
    """Asking for the state it is already in is reported, not silently applied.

    The write matters as much as the words: `save_live_leaderboards` rewrites
    every board on the server, so a no-op that saved anyway would be a
    whole-table rewrite triggered by a command that changed nothing.
    """
    board = {"season": SEASON}
    if not already:
        board["enabled"] = False

    world = _run_command(command, board=board)

    assert world.saved is None, "a no-op flip wrote to storage"
    assert "already" in world.reply, (
        f"a no-op flip did not say the board was already in that state: {world.reply!r}"
    )


@pytest.mark.parametrize(
    "command", ["disable_cluster_leaderboard", "enable_cluster_leaderboard"]
)
def test_both_commands_refuse_when_no_cluster_board_exists(command: str):
    """A switch stored against a board that does not exist would be discarded
    by the next `/set_live_cluster_leaderboard`, which rebuilds the config
    from scratch. Refusing says so instead of appearing to work."""
    world = _run_command(command, board=None)

    assert world.saved is None, "a switch was stored for a board that does not exist"
    assert world.reply.startswith("❌")
    assert "/set_live_cluster_leaderboard" in world.reply, (
        f"the refusal does not name the command that creates the board: {world.reply!r}"
    )


# ===========================================================================
# Harness
# ===========================================================================

def _set_enabled(config: dict, enabled: bool) -> None:
    from bot.guilds import set_live_board_enabled

    set_live_board_enabled(config, enabled)


def _sqlite_repo(tmp_path):
    """A real migrated SQLite repository.

    Alembic runs BEFORE the repository is constructed — building it first
    creates the tables itself and the migration then collides with them.
    Precedent: tests/unit/test_guild_keys_policy.py::sqlite_repo.
    """
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from cryptography.fernet import Fernet

    import bot.db
    from bot.repository_sqlalchemy import SqlAlchemyClusterRepository

    db_path = tmp_path / "data" / "scrapcode.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    db_pkg = Path(bot.db.__file__).parent
    cfg = Config(str(db_pkg / "alembic.ini"))
    cfg.set_main_option("script_location", str(db_pkg / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "head")

    repo = SqlAlchemyClusterRepository(
        db_path=str(db_path), fernet_key=Fernet.generate_key().decode()
    )
    repo.save(_bare_cluster())
    return repo


def _bare_cluster():
    """`live_leaderboards.discord_server_id` is an FK to `clusters`."""
    from bot.repository import Cluster

    return Cluster(
        discord_server_id=SERVER_ID, guilds={}, update_channel_id=None, role_tiers={}
    )


def _refresh_with(*, enabled: bool, scope_key: str = "cluster") -> dict:
    """Run the real `_refresh_live_leaderboards` over one board."""
    tasks_cog = _tasks_cog()

    config: dict = {
        "channel_id": CHANNEL_ID,
        "messages": {"Legendary_0": 999},
        "season": SEASON,
    }
    if scope_key.startswith("guild:"):
        config["guild_id"] = scope_key.split(":", 1)[1]
    _set_enabled(config, enabled)

    channel = _FakeChannel()
    world = _World()
    guilds = {"neuro": {"name": "Neuro"}}

    with _storage(tasks_cog, {scope_key: config}, world):
        cog = tasks_cog.TasksCog.__new__(tasks_cog.TasksCog)
        cog.bot = _FakeBot(channel)
        asyncio.run(
            cog._refresh_live_leaderboards(SERVER_ID, SEASON, guilds)
        )

    # The config the loop worked on, as it stands after the pass. A save is
    # only made when something changed, so an unsaved board is read back from
    # the dict the loop was handed.
    final = (world.saved or {scope_key: config}).get(scope_key)
    return {
        "messages_edited": list(channel.edited),
        "messages_sent": list(channel.sent),
        "config_survived": final is not None,
        "config_still_off": final is not None and final.get("enabled") is False,
        "season_recorded": final and final.get("season"),
    }


def _run_command(name: str, *, board: dict | None) -> "_CommandWorld":
    """Invoke a real slash command callback against one stored cluster board."""
    admin_cog = _admin_cog()

    live: dict = {}
    if board is not None:
        live["cluster"] = {"channel_id": CHANNEL_ID, "messages": {}, **board}

    world = _CommandWorld()
    interaction = _FakeInteraction()

    originals = {
        (admin_cog, "load_live_leaderboards"): lambda server_id: live,
        (admin_cog, "save_live_leaderboards"): world.save,
    }
    previous = {
        (module, target): getattr(module, target) for module, target in originals
    }
    try:
        for (module, target), replacement in originals.items():
            setattr(module, target, replacement)
        cog = admin_cog.AdminCog.__new__(admin_cog.AdminCog)
        command = _find_command(admin_cog, name)
        asyncio.run(command.callback(cog, interaction))
    finally:
        for (module, target), original in previous.items():
            setattr(module, target, original)

    world.reply = interaction.reply_text
    return world


@contextmanager
def _storage(tasks_cog, live: dict, world: "_World"):
    """Replace the two storage ports the refresh reads and writes."""
    originals = {
        (tasks_cog, "load_live_leaderboards"): lambda server_id: live,
        (tasks_cog, "save_live_leaderboards"): world.save,
        (tasks_cog, "repo"): _FakeRepo(),
        (tasks_cog, "get_player_list"): lambda server_id, guild_id: {},
    }
    previous = {
        (module, target): getattr(module, target) for module, target in originals
    }
    try:
        for (module, target), replacement in originals.items():
            setattr(module, target, replacement)
        yield
    finally:
        for (module, target), original in previous.items():
            setattr(module, target, original)


class _World:
    def __init__(self) -> None:
        self.saved: dict | None = None

    def save(self, server_id, live) -> None:
        self.saved = live


class _CommandWorld:
    def __init__(self) -> None:
        self.saved: dict | None = None
        self.reply: str = ""

    def save(self, server_id, live) -> None:
        self.saved = live


class _FakeRepo:
    def load_battle_hits(self, server_id, guild_id, season):
        return {"boss_hits": {}}


class _FakeBot:
    def __init__(self, channel) -> None:
        self._channel = channel

    def get_channel(self, channel_id):
        return self._channel if channel_id == CHANNEL_ID else None


class _FakeChannel:
    """Records every Discord call the refresh made against this board."""

    id = CHANNEL_ID
    mention = f"<#{CHANNEL_ID}>"

    def __init__(self) -> None:
        self.edited: list[int] = []
        self.sent: list[str] = []

    async def fetch_message(self, message_id: int):
        return _FakeMessage(message_id, self)

    async def send(self, content: str, **kwargs):
        self.sent.append(content)
        return _FakeMessage(1234, self)


class _FakeMessage:
    def __init__(self, message_id: int, channel: _FakeChannel) -> None:
        self.id = message_id
        self._channel = channel

    async def edit(self, **kwargs):
        self._channel.edited.append(self.id)


class _FakeInteraction:
    def __init__(self) -> None:
        self.guild_id = SERVER_ID
        self.user = _FakeUser()
        self.response = _FakeResponse()
        self.followup = _FakeFollowup()

    @property
    def reply_text(self) -> str:
        return self.followup.messages[-1] if self.followup.messages else ""


class _FakeUser:
    """`require_tier` reads the caller's roles; an admin passes every tier."""

    id = 1
    guild_permissions = type("_Perms", (), {"administrator": True})()
    roles: list = []


class _FakeResponse:
    async def defer(self, *args, **kwargs) -> None:
        return None

    async def send_message(self, content: str, **kwargs) -> None:
        return None


class _FakeFollowup:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, content: str = "", **kwargs) -> None:
        self.messages.append(content)


def _find_command(admin_cog, name: str):
    for command in admin_cog.AdminCog.__cog_app_commands__:
        if command.name == name:
            return command
    raise AssertionError(
        f"no `{name}` command is registered on AdminCog — delete the command "
        "method and this harness errors, which is the port-to-port litmus test"
    )


def _admin_cog():
    from bot.cogs import admin_cog

    return admin_cog


def _tasks_cog():
    from bot.cogs import tasks_cog

    return tasks_cog


@pytest.fixture(scope="module", autouse=True)
def _leave_the_cog_modules_as_this_file_found_them():
    """Un-import the cogs once this module is done.

    They bind `repo`, `load_live_leaderboards` and friends BY VALUE at import
    time, and the acceptance suite patches `bot.guilds.repo` per test and
    depends on importing them afterwards. Precedent:
    tests/unit/test_leaderboard_season_fall_through.py.
    """
    yield
    for name in ("bot.cogs.admin_cog", "bot.cogs.tasks_cog"):
        sys.modules.pop(name, None)
        cogs_package = sys.modules.get("bot.cogs")
        if cogs_package is not None:
            attribute = name.rsplit(".", 1)[1]
            if getattr(cogs_package, attribute, None) is not None:
                delattr(cogs_package, attribute)
