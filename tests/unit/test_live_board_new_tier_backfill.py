"""A live board adopts a newly-added tier in place, without a re-run (Mythic 3).

WHY-NEW-FILE: tests/unit/test_live_board_new_tier_backfill.py
  CLOSEST-EXISTING: tests/unit/test_leaderboard_season_fall_through.py
  EXTENSION-COST: that module drives `AdminCog`'s two SETUP commands through a
    fake interaction, and its universe (`_surface`) is seven slots about
    refusals, named guilds and Tacticus credentials — none of which exist on
    this path. `_refresh_live_leaderboards` takes no interaction, sends no
    refusal and asks Tacticus nothing; hosting this would mean a second
    surface, a second harness and a fake bot inside a module whose docstring
    states its universe as the commands' reply text.
  PARALLEL-RATIONALE: opposite ends of the board's life. That file is about
    CREATING a board; this is about an EXISTING board surviving a change to
    the tier list underneath it.

WHY AN EXAMPLE AND NOT A PROPERTY. The claim is about one specific transition —
a config written under the old seven-tier list meeting the new eight-tier list
on the next hourly pass. There is no meaningful quantifier: the interesting
input is precisely "the tier set grew", and the failure mode is a single
branch (`if not msg_id: continue`) that skipped it forever.

DECLARED UNIVERSE. `_surface()` captures everything the refresh does to the
channel and to storage, and the test asserts the WHOLE dict, so a fix that
posted the new tier while ALSO abandoning the seven live messages — which is
what re-running `/set_live_leaderboard` does — fails here just as loudly as
the original skip.

    sent          — contents newly posted to the channel
    edited        — {message id: content} edited in place
    saved         — the messages dict persisted back, if a save happened
"""
import asyncio
import os
import sys
from contextlib import contextmanager

import pytest

# Cogs import `config`, which casts both channel ids with `int(os.getenv(...))`
# at import time, and `bot.guilds`, which builds the process-wide repository
# from the environment AT THAT MOMENT. Pin both before any `bot.*` import.
os.environ.setdefault("UPDATE_CHANNEL_ID", "0")
os.environ.setdefault("REPLAY_INDEX_CHANNEL_ID", "0")
os.environ.setdefault("SCRAPCODE_REPO_BACKEND", "json")

SERVER_ID = 4242
SEASON = 77
GUILD_ID = "word_bearers"
CHANNEL_ID = 7

# The board this server set up before Mythic 3 shipped: seven tiers, seven
# message ids, all still live in the channel.
THE_SEVEN = [
    "Legendary_0", "Legendary_1", "Legendary_2", "Legendary_3", "Legendary_4",
    "Mythic", "Mythic_1",
]
EXISTING_MESSAGE_IDS = {tier: 1000 + i for i, tier in enumerate(THE_SEVEN)}

# The tier that had no message and was skipped every hour, forever.
THE_NEW_TIER = "Mythic_2"
NEW_MESSAGE_ID = 9999


def test_a_new_tier_is_adopted_without_touching_the_existing_messages():
    """Mythic 3 appears on the next hourly pass, and only Mythic 3 is new.

    Before the fix, `message_ids.get(tier.value)` returned None for `Mythic_2`
    and the loop hit a bare `continue` — so the tier that ingest had just
    started storing was invisible in Discord until an officer re-ran
    `/set_live_leaderboard`. Mid-season that re-run is not a neutral act: it
    posts a fresh set of eight and repoints the config, abandoning the seven
    messages members have scrolled to and pinned.

    So both halves are asserted as one dict. Exactly one message is sent, it
    carries the new tier's content, the other seven are EDITED at their
    existing ids, and the persisted `messages` map is the old seven plus one —
    same ids, so nothing that was live stops updating.
    """
    surface = _run_refresh()

    assert surface == {
        "sent": [f"📊 **Word Bearers — Mythic 3 — No data yet**"],
        "edited": {
            EXISTING_MESSAGE_IDS[tier]: f"📊 **Word Bearers — {label} — No data yet**"
            for tier, label in zip(
                THE_SEVEN,
                ["Legendary 1", "Legendary 2", "Legendary 3", "Legendary 4",
                 "Legendary 5", "Mythic 1", "Mythic 2"],
            )
        },
        "saved": {**EXISTING_MESSAGE_IDS, THE_NEW_TIER: NEW_MESSAGE_ID},
    }, (
        "the refresh either skipped the new tier, or re-posted the whole board "
        f"and orphaned the live messages: {surface!r}"
    )


def test_the_adoption_is_persisted_so_the_next_hour_edits_instead_of_posting():
    """The new message id is written back — otherwise it posts a duplicate hourly.

    The skip-branch fix is only half a fix if `dirty` is not set: the config on
    disk would still lack `Mythic_2`, so every subsequent pass would send
    another Mythic 3 message and the channel would fill with one new board line
    an hour. Running the refresh twice against storage that actually persists
    is the only way to observe that, so this drives it twice.
    """
    with _a_board_missing_the_new_tier() as world:
        _refresh(world)
        first_pass_sent = list(world.sent)
        world.sent.clear()
        _refresh(world)

        assert first_pass_sent and not world.sent, (
            "the second hourly pass posted Mythic 3 again — the adopted message "
            f"id was not persisted: {world.sent!r}"
        )
        assert world.live["guild:" + GUILD_ID]["messages"][THE_NEW_TIER] == NEW_MESSAGE_ID
        assert world.edited_ids.count(NEW_MESSAGE_ID) == 1, (
            "the second pass did not edit the adopted message in place"
        )


# ===========================================================================
# Harness — the real refresh body, replaced storage and Discord channel
# ===========================================================================

def _run_refresh() -> dict:
    with _a_board_missing_the_new_tier() as world:
        _refresh(world)
        return {
            "sent": list(world.sent),
            "edited": dict(world.edited),
            "saved": dict(world.saved.get("guild:" + GUILD_ID, {}).get("messages", {})),
        }


def _refresh(world) -> None:
    """Run the real `_refresh_live_leaderboards` against the fake world."""
    tasks_cog = _tasks_cog()
    cog = tasks_cog.TasksCog.__new__(tasks_cog.TasksCog)
    cog.bot = _FakeBot(world.channel)
    asyncio.run(
        cog._refresh_live_leaderboards(
            SERVER_ID, SEASON, {GUILD_ID: {"name": "Word Bearers"}}
        )
    )


@contextmanager
def _a_board_missing_the_new_tier():
    """A same-season board carrying only the seven pre-Mythic-3 messages.

    `load_battle_hits` returns `{}` on purpose: with no `boss_hits` the refresh
    takes its "No data yet" content path, which keeps `bot.embeds` out of the
    test entirely. The tier list, the skip branch and the save are what is
    under test — how a populated board renders is `bot/embeds.py`'s contract,
    pinned elsewhere.
    """
    tasks_cog = _tasks_cog()
    world = _World()

    originals = {
        "load_live_leaderboards": lambda server_id: world.live,
        "save_live_leaderboards": world.save,
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
    """Storage and the channel, and everything they were asked to do."""

    def __init__(self) -> None:
        self.live = {
            f"guild:{GUILD_ID}": {
                "channel_id": CHANNEL_ID,
                "guild_id": GUILD_ID,
                "messages": dict(EXISTING_MESSAGE_IDS),
                "season": SEASON,
            }
        }
        self.saved: dict = {}
        self.sent: list[str] = []
        self.edited: dict[int, str] = {}
        self.edited_ids: list[int] = []
        self.channel = _FakeChannel(self)

    def save(self, server_id, live) -> None:
        assert server_id == SERVER_ID
        # The cog mutates `live` in place and hands back the same object, so
        # snapshot the messages dict rather than aliasing it — otherwise the
        # second pass would rewrite the first pass's recorded result.
        self.live = live
        self.saved = {
            key: {**config, "messages": dict(config.get("messages", {}))}
            for key, config in live.items()
        }


class _FakeRepo:
    """No raid rows — the board renders its "No data yet" content."""

    def load_battle_hits(self, server_id, guild_id, season):
        assert season == SEASON, "the refresh built a board for the wrong season"
        return {}


class _FakeBot:
    def __init__(self, channel) -> None:
        self._channel = channel

    def get_channel(self, channel_id):
        return self._channel if channel_id == CHANNEL_ID else None


class _FakeChannel:
    id = CHANNEL_ID
    mention = f"<#{CHANNEL_ID}>"

    def __init__(self, world: _World) -> None:
        self._world = world

    async def send(self, content: str, **kwargs):
        self._world.sent.append(content)
        return _FakeMessage(NEW_MESSAGE_ID, self._world)

    async def fetch_message(self, message_id: int):
        if message_id not in self._world.edited and message_id not in (
            set(EXISTING_MESSAGE_IDS.values()) | {NEW_MESSAGE_ID}
        ):
            import discord

            raise discord.NotFound(_FakeResponse(), "unknown message")
        return _FakeMessage(message_id, self._world)


class _FakeMessage:
    def __init__(self, message_id: int, world: _World) -> None:
        self.id = message_id
        self._world = world

    async def edit(self, content: str, **kwargs):
        self._world.edited[self.id] = content
        self._world.edited_ids.append(self.id)
        return self


class _FakeResponse:
    """The minimum `discord.NotFound` reads off an HTTP response."""

    status = 404
    reason = "Not Found"


def _tasks_cog():
    """Import `bot.cogs.tasks_cog` LATE, and never at module scope.

    Importing the cog imports `bot.guilds`, which builds the process-wide
    `ClusterRepository` singleton from whatever environment exists AT THAT
    MOMENT. At collection time no fixture has run. Precedent:
    tests/unit/test_auto_update_cycle_containment.py::_tasks_cog.
    """
    from bot.cogs import tasks_cog

    return tasks_cog


@pytest.fixture(scope="module", autouse=True)
def _leave_the_cog_module_as_this_file_found_it():
    """Un-import the cog once this module's tests are done.

    `bot/cogs/tasks_cog.py` binds `repo`, `load_live_leaderboards` and friends
    BY VALUE at import time. The acceptance suite patches `bot.guilds.repo` per
    test and depends on importing the cog afterwards to pick the patched object
    up. Dropping the module puts the next importer back in the position it
    holds when this file is absent.
    """
    yield
    sys.modules.pop("bot.cogs.tasks_cog", None)
    cogs_package = sys.modules.get("bot.cogs")
    if cogs_package is not None:
        stale_module = getattr(cogs_package, "tasks_cog", None)
        if stale_module is not None:
            delattr(cogs_package, "tasks_cog")
