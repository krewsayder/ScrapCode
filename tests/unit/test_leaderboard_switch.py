"""A guild's leaderboards can be switched off without dismantling them.

WHY-NEW-FILE: tests/unit/test_leaderboard_switch.py
  CLOSEST-EXISTING: tests/unit/test_live_board_new_tier_backfill.py
  EXTENSION-COST: that module's declared universe is "an existing board meeting
    a CHANGED TIER LIST on the next hourly pass", and its `_surface` is three
    slots about tier adoption. The switch is a different question asked of the
    same method — whether the pass happens AT ALL — and its interesting
    assertion is about the config that must SURVIVE a skip, which that module's
    surface does not carry. Hosting it there would mean widening a docstring
    that names its one transition, and a reader arriving at a Mythic-3
    regression would have to sort two universes apart.
  PARALLEL-RATIONALE: that file is about a board that must keep up with a
    change; this is about a board that must correctly do NOTHING and remain
    resumable.

WHY EXAMPLES AND NOT PROPERTIES. The claim is a branch, not a quantifier: for
one guild the pass either runs or it does not, and the failure modes are
specific and few — refreshing anyway, or skipping in a way that cannot be
undone. There is no input space worth sampling.

THE FAILURE THIS FILE EXISTS TO CATCH is the cheap spelling of the skip. The
obvious implementation is `to_remove.append(key)` — it stops the updates and
reads as tidy. It is also irreversible: the config is the only record of which
messages the board owns, so re-enabling would post a SECOND set beside the
originals and freeze the first forever. `test_a_disabled_board_keeps_its_config`
and `test_re_enabling_resumes_the_same_messages` are that pair.

DECLARED UNIVERSE. `_surface()` captures everything a pass does to the channel
and to storage, and each test asserts the whole dict:

    sent    — contents newly posted to the channel
    edited  — {message id: content} edited in place
    config  — the live-leaderboard config as it stands after the pass
"""
import asyncio
import os
import sys
from contextlib import contextmanager
from dataclasses import replace

import pytest

# Same import-time pinning as every cog test in this suite: `config` casts its
# channel ids with `int(os.getenv(...))` and `bot.guilds` builds the repository
# singleton, both at import.
os.environ.setdefault("UPDATE_CHANNEL_ID", "0")
os.environ.setdefault("REPLAY_INDEX_CHANNEL_ID", "0")
os.environ.setdefault("SCRAPCODE_REPO_BACKEND", "json")

# Safe at module scope, unlike a cog: `bot.repository` reads no environment and
# does not build the process-wide repository singleton. The env pins above
# still run first.
from bot.repository import LiveBoardConfig  # noqa: E402

SERVER_ID = 4242
SEASON = 77
ON_GUILD = "night_lords"
OFF_GUILD = "word_bearers"
CHANNEL_ID = 7

TIERS = [
    "Legendary_0", "Legendary_1", "Legendary_2", "Legendary_3", "Legendary_4",
    "Mythic", "Mythic_1", "Mythic_2",
]
OFF_MESSAGE_IDS = {tier: 2000 + i for i, tier in enumerate(TIERS)}
ON_MESSAGE_IDS = {tier: 3000 + i for i, tier in enumerate(TIERS)}

GUILD_NAMES = {ON_GUILD: "Night Lords", OFF_GUILD: "Word Bearers"}


# ===========================================================================
# The switch, at the hourly pass
# ===========================================================================

def test_a_disabled_guilds_board_is_not_touched():
    """No message sent, none edited — the board simply stops moving."""
    surface = _surface(disabled={OFF_GUILD})

    assert surface["sent"] == [], f"a disabled board posted: {surface['sent']!r}"
    assert not any(
        message_id in OFF_MESSAGE_IDS.values() for message_id in surface["edited"]
    ), f"a disabled board was edited: {surface['edited']!r}"


def test_a_disabled_board_keeps_its_config():
    """The skip is `continue`, not a removal — this is the whole design.

    A disabled board that dropped its config would be unrecoverable: the
    `messages` map is the only record of which Discord messages this board
    owns, so re-enabling would send a fresh set beside the originals and leave
    the originals frozen in the channel forever. Asserted byte-for-byte
    against the config that went in, because "kept but quietly rewritten" is
    the same defect wearing a subtler disguise.
    """
    surface = _surface(disabled={OFF_GUILD})

    assert surface["config"][f"guild:{OFF_GUILD}"] == LiveBoardConfig(
        channel_id=CHANNEL_ID,
        guild_id=OFF_GUILD,
        messages=OFF_MESSAGE_IDS,
        season=SEASON,
    ), (
        "the disabled board's config was altered or discarded; re-enabling it "
        f"can no longer resume the live messages: {surface['config']!r}"
    )


def test_an_enabled_sibling_is_unaffected():
    """One guild's switch is one guild's switch.

    The flags are read once per server per cycle and applied per board, so a
    lookup that fell through to the wrong default — or a skip spelled as
    `break` rather than `continue` — would take the whole server's boards down
    with it. That is the cross-guild containment failure `_contained_pass`
    exists to prevent one level up.
    """
    surface = _surface(disabled={OFF_GUILD})

    assert set(surface["edited"]) == set(ON_MESSAGE_IDS.values()), (
        "the enabled sibling's board did not refresh normally: "
        f"{surface['edited']!r}"
    )


def test_re_enabling_resumes_the_same_messages():
    """The pair to `test_a_disabled_board_keeps_its_config`.

    Kept config is only worth having if the next enabled pass USES it. Drives
    a disabled pass and then an enabled one against storage that persists, and
    asserts the second pass edited the original ids and sent nothing — which
    is what makes the switch a pause rather than a teardown.
    """
    with _a_world(disabled={OFF_GUILD}) as world:
        _refresh(world)
        world.enabled_everywhere()
        world.sent.clear()
        world.edited.clear()
        _refresh(world)

        assert world.sent == [], (
            f"re-enabling posted a second set of messages: {world.sent!r}"
        )
        assert set(world.edited) >= set(OFF_MESSAGE_IDS.values()), (
            "re-enabling did not resume the original messages: "
            f"{world.edited!r}"
        )


def test_a_disabled_guild_drops_out_of_the_cluster_board():
    """The switch means leaderboards, not "leaderboards except that one".

    The cluster board merges every guild, so without this the disabled guild's
    players stay ranked on a board in the same channel the officer just
    silenced. Driven through content rather than call counts: the cluster
    board renders player names, and the disabled guild's must not appear.
    """
    surface = _surface(disabled={OFF_GUILD}, with_cluster=True)
    cluster_content = " ".join(
        content for message_id, content in surface["edited"].items()
        if message_id in CLUSTER_MESSAGE_IDS.values()
    )

    assert "Nostraman" in cluster_content, (
        "the enabled guild is missing from the cluster board, so this scenario "
        f"is not exercising the merge: {cluster_content!r}"
    )
    assert "Bearer" not in cluster_content, (
        "a guild whose leaderboards are switched off is still ranked on the "
        f"cluster board: {cluster_content!r}"
    )


# ===========================================================================
# Harness — the real refresh body, replaced storage and Discord channel
# ===========================================================================

CLUSTER_MESSAGE_IDS = {tier: 4000 + i for i, tier in enumerate(TIERS)}

# One battle hit per guild, in the shape `build_cluster_messages` consumes.
# The display names are what the cluster assertions read, so they are distinct
# per guild and nothing else about the rows matters.
HITS = {
    ON_GUILD: {"boss_hits": {"boss-1": {"0": {"Legendary_0": [
        {"user_id": "uid-nl", "damage": 500, "completed_on": "2026-08-01T00:00:00Z",
         "encounterType": None, "hero_details": [], "machine_of_war": None},
    ]}}}},
    OFF_GUILD: {"boss_hits": {"boss-1": {"0": {"Legendary_0": [
        {"user_id": "uid-wb", "damage": 900, "completed_on": "2026-08-01T00:00:00Z",
         "encounterType": None, "hero_details": [], "machine_of_war": None},
    ]}}}},
}
PLAYER_NAMES = {ON_GUILD: {"uid-nl": "Nostraman"}, OFF_GUILD: {"uid-wb": "Bearer"}}


def _surface(*, disabled: set[str], with_cluster: bool = False) -> dict:
    with _a_world(disabled=disabled, with_cluster=with_cluster) as world:
        _refresh(world)
        return {
            "sent": list(world.sent),
            "edited": dict(world.edited),
            "config": {
                key: replace(config, messages=dict(config.messages))
                for key, config in world.live.items()
            },
        }


def _refresh(world) -> None:
    tasks_cog = _tasks_cog()
    cog = tasks_cog.TasksCog.__new__(tasks_cog.TasksCog)
    cog.bot = _FakeBot(world.channel)
    asyncio.run(
        cog._refresh_live_leaderboards(
            SERVER_ID, SEASON,
            {gid: {"name": name} for gid, name in GUILD_NAMES.items()},
        )
    )


@contextmanager
def _a_world(*, disabled: set[str], with_cluster: bool = False):
    tasks_cog = _tasks_cog()
    world = _World(disabled=disabled, with_cluster=with_cluster)

    originals = {
        "load_live_leaderboards": lambda server_id: world.live,
        "save_live_leaderboards": world.save,
        "list_leaderboard_flags": lambda server_id: world.flags,
        "get_player_list": lambda server_id, guild_id: PLAYER_NAMES[guild_id],
        "repo": _FakeRepo(),
    }
    saved = {name: getattr(tasks_cog, name) for name in originals}
    try:
        for name, replacement in originals.items():
            setattr(tasks_cog, name, replacement)
        yield world
    finally:
        for name, original in saved.items():
            setattr(tasks_cog, name, original)


class _World:
    def __init__(self, *, disabled: set[str], with_cluster: bool) -> None:
        self.flags = {gid: gid not in disabled for gid in GUILD_NAMES}
        # `LiveBoardConfig`, not dicts. The port stopped handing cogs raw dicts
        # when `cluster-board-control` landed (ADR-009 DDD-2), and a double
        # still returning the old shape fails at `config.is_enabled` before it
        # reaches anything this module is about. The two switches are
        # independent: `board_status` here stays ACTIVE throughout, because
        # what these scenarios exercise is the GUILD flag, not the board's own.
        self.live = {
            f"guild:{OFF_GUILD}": LiveBoardConfig(
                channel_id=CHANNEL_ID,
                guild_id=OFF_GUILD,
                messages=dict(OFF_MESSAGE_IDS),
                season=SEASON,
            ),
            f"guild:{ON_GUILD}": LiveBoardConfig(
                channel_id=CHANNEL_ID,
                guild_id=ON_GUILD,
                messages=dict(ON_MESSAGE_IDS),
                season=SEASON,
            ),
        }
        if with_cluster:
            self.live["cluster"] = LiveBoardConfig(
                channel_id=CHANNEL_ID,
                messages=dict(CLUSTER_MESSAGE_IDS),
                season=SEASON,
            )
        self.sent: list[str] = []
        self.edited: dict[int, str] = {}
        self.channel = _FakeChannel(self)

    def enabled_everywhere(self) -> None:
        self.flags = {gid: True for gid in GUILD_NAMES}

    def save(self, server_id, live) -> None:
        assert server_id == SERVER_ID
        self.live = live


class _FakeRepo:
    def load_battle_hits(self, server_id, guild_id, season):
        assert season == SEASON, "the refresh built a board for the wrong season"
        return HITS[guild_id]


class _FakeBot:
    def __init__(self, channel) -> None:
        self._channel = channel

    def get_channel(self, channel_id):
        return self._channel if channel_id == CHANNEL_ID else None


class _FakeChannel:
    id = CHANNEL_ID
    mention = f"<#{CHANNEL_ID}>"

    def __init__(self, world: "_World") -> None:
        self._world = world

    async def send(self, content: str, **kwargs):
        self._world.sent.append(content)
        return _FakeMessage(99999, self._world)

    async def fetch_message(self, message_id: int):
        return _FakeMessage(message_id, self._world)


class _FakeMessage:
    def __init__(self, message_id: int, world: "_World") -> None:
        self.id = message_id
        self._world = world

    async def edit(self, content: str, **kwargs):
        self._world.edited[self.id] = content
        return self


def _tasks_cog():
    """Import the cog LATE — it binds `repo` and the wrappers by value at
    import time, against whatever environment exists at that moment. Precedent:
    tests/unit/test_live_board_new_tier_backfill.py::_tasks_cog.
    """
    from bot.cogs import tasks_cog

    return tasks_cog


@pytest.fixture(scope="module", autouse=True)
def _leave_the_cog_module_as_this_file_found_it():
    """Un-import the cog so the next importer sees this file as absent —
    same reason as the sibling module that established this fixture."""
    yield
    sys.modules.pop("bot.cogs.tasks_cog", None)
    cogs_package = sys.modules.get("bot.cogs")
    if cogs_package is not None:
        stale_module = getattr(cogs_package, "tasks_cog", None)
        if stale_module is not None:
            delattr(cogs_package, "tasks_cog")
