"""Properties of the leaderboard commands' key selection (step 07-03).

WHY-NEW-FILE: tests/unit/test_leaderboard_season_fall_through.py
  CLOSEST-EXISTING: tests/unit/test_admin_cog_quarantine_refusal.py
  EXTENSION-COST: every property there is quantified over a single frozen
    `GuildBinding` handed to a pure function that returns a string; hosting
    these would attach an event loop, a fake Discord interaction, a fake
    channel and four patched storage ports to a module whose stated universe
    is one function's return value, and whose docstring takes that narrowness
    as the reason it exists apart in the first place.
  PARALLEL-RATIONALE: incompatible dependency set and a different observable.
    The claim below is about WHICH GUILD'S KEY a command sends to Tacticus
    across cluster orderings — it cannot be observed without running the async
    command through its storage ports, and the quantifier is over clusters,
    not over binding states.

WHY PROPERTIES AND NOT EXAMPLES. The defect is a single-point-of-failure:
`set_live_cluster_leaderboard` resolved the season from `next(iter(guilds))`
and aborted the whole cluster when that one key was unusable. THE ORDERING IS
THE WHOLE QUANTIFIER — the SPOF is invisible in any cluster where the
quarantined guild happens to sort second, so an example test that picked such
a fixture would pass with the bug fully present. The acceptance scenario pins
one ordering deliberately (`_register_two_guilds_quarantined_first`); this
quantifies over all of them, and over which guilds are quarantined, keyless,
or healthy.

Nothing about the commands is stubbed. The real `AdminCog` callbacks run, and
the quarantine decision runs through the real `bot.guild_keys.active_key`
policy — only the storage reads underneath it and the Tacticus transport above
it are replaced, which is the port boundary in both directions.

DECLARED UNIVERSE. `_surface()` captures every observable these commands
produce for this contract, and each property asserts the WHOLE dict rather
than one slot, so a fall-through that resolved a season while quietly sending
a quarantined guild's key, or writing no board, or describing the wrong guild
as keyless, fails here:

    reply.refused              — did the officer get a refusal at all
    reply.named_quarantined    — the guilds the reply calls quarantined
    reply.named_keyless        — the guilds the reply calls "no API key"
    reply.names_the_exit       — is `/update_guild_key` offered
    reply.names_deregistration — the route that destroys the raid history
    leaderboard.season         — the season written to the live board, if any
    tacticus.credentials       — every key the command actually sent
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from contextlib import contextmanager

# `bot.guilds` evaluates `repo = build_repo()` at import time and reads the
# environment at that moment. Pin a harmless backend and the two channel ids
# `config.py` casts with `int(os.getenv(...))` before any `bot.*` import, so
# collection can neither raise TypeError nor build a repository pointed at a
# live `clusters/` tree. Precedent: tests/unit/test_admin_cog_quarantine_refusal.py.
os.environ.setdefault("SCRAPCODE_REPO_BACKEND", "json")
os.environ.setdefault("UPDATE_CHANNEL_ID", "0")
os.environ.setdefault("REPLAY_INDEX_CHANNEL_ID", "0")

import pytest  # noqa: E402

hypothesis = pytest.importorskip(
    "hypothesis",
    reason="hypothesis is not installed — DISTILL pins it into requirements.txt",
)

import hypothesis.strategies as st  # noqa: E402
from hypothesis import given, settings  # noqa: E402

# Deselected from the 250-test baseline like the rest of the remediation work,
# so the "nothing that shipped has regressed" command stays an exact
# comparison.
pytestmark = [pytest.mark.property, pytest.mark.slice_05]

SEASON = 77

# `/deregister_guild`'s route. It destroys the guild's whole raid history
# (AC-009.4) and launders the quarantine on re-registration (AC-009.5), so no
# refusal may hand it to an officer.
_THE_DESTRUCTIVE_ROUTE = "remove the existing entry"

# The per-guild bullet the cluster refusal renders: "• `word_bearers` — ...".
_BULLET = re.compile(r"^• `([^`]+)` — (.*)$", re.MULTILINE)


# ---------------------------------------------------------------------------
# The cluster generator — ordering and health drawn independently
# ---------------------------------------------------------------------------

_GUILD_IDS = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789_", min_size=1, max_size=12
)

# Three states a guild's key can be in, and only the first is usable.
HEALTHY = "healthy"
QUARANTINED = "quarantined"
KEYLESS = "keyless"


@st.composite
def _clusters(draw):
    """A cluster as an ORDERED mapping of guild id -> key state.

    Order is drawn, not pinned, because order is the entire property: the SPOF
    only misbehaves when an unusable guild sorts first, and a generator that
    fixed the order would pass against the defect roughly half the time. The
    state of each guild is drawn independently of its position for the same
    reason — pairing them would quietly re-introduce a fixture.
    """
    guild_ids = draw(
        st.lists(_GUILD_IDS, unique=True, min_size=1, max_size=5)
    )
    states = draw(
        st.lists(
            st.sampled_from([HEALTHY, QUARANTINED, KEYLESS]),
            min_size=len(guild_ids), max_size=len(guild_ids),
        )
    )
    return dict(zip(guild_ids, states))


def _key_of(guild_id: str) -> str:
    return f"key-for-{guild_id}"


_SETTINGS = settings(max_examples=100, deadline=None)


# ===========================================================================
# Property 1 — AC-008.4 / AC-008.6 / KPI-5. The fall-through, as an iff.
# ===========================================================================

@given(cluster=_clusters())
@_SETTINGS
def test_the_cluster_season_resolves_from_any_usable_key_whatever_the_order(
    cluster: dict,
):
    """A cluster leaderboard is built whenever ANY guild is usable.

    Read one way this is KPI-5: 100% of unrelated guilds are unaffected by one
    guild's quarantine, so a quarantined guild — first, last or in the middle —
    never costs its healthy siblings the cluster board. Read the other way it
    is AC-008.6: when nothing is usable the fall-through ends in an explained
    refusal, never in a silent skip and never in an empty board.

    The credential slot is the half that a "does it refuse" assertion would
    miss: a fall-through that resolved a season by sending the QUARANTINED
    guild's key would satisfy every reply assertion while doing the one thing
    quarantine exists to stop.
    """
    usable = [gid for gid, state in cluster.items() if state == HEALTHY]

    surface = _run_cluster_command(cluster)

    if usable:
        assert surface == {
            "reply.refused": False,
            "reply.named_quarantined": set(),
            "reply.named_keyless": set(),
            "reply.names_the_exit": False,
            "reply.names_deregistration": False,
            "leaderboard.season": SEASON,
            "tacticus.credentials": surface["tacticus.credentials"],
        }, (
            "a cluster with a usable key did not produce a cluster board: "
            f"{surface!r} (cluster was {cluster!r})"
        )
        assert surface["tacticus.credentials"] in [[_key_of(gid)] for gid in usable], (
            "the season was resolved from a key that is not usable, or from "
            f"more than one request: {surface['tacticus.credentials']!r} "
            f"(usable guilds were {usable!r})"
        )
        return

    assert surface == {
        "reply.refused": True,
        "reply.named_quarantined": {
            gid for gid, state in cluster.items() if state == QUARANTINED
        },
        "reply.named_keyless": {
            gid for gid, state in cluster.items() if state == KEYLESS
        },
        "reply.names_the_exit": QUARANTINED in cluster.values(),
        "reply.names_deregistration": False,
        "leaderboard.season": None,
        "tacticus.credentials": [],
    }, (
        "a cluster with no usable key was refused for the wrong reason, or "
        f"asked Tacticus anyway: {surface!r} (cluster was {cluster!r})"
    )


# ===========================================================================
# Property 2 — AC-008.5 / AC-008.5b. The named guild, and only the named one.
# ===========================================================================

@given(cluster=_clusters(), pick=st.integers(min_value=0, max_value=4))
@_SETTINGS
def test_a_single_guilds_board_resolves_from_that_guild_and_names_its_refusal(
    cluster: dict, pick: int,
):
    """`/set_live_leaderboard` answers for the guild the officer NAMED.

    Two claims in one, because they are two halves of the same risk. The
    fall-through Property 1 adds lives in shared leaderboard code, and the
    cheapest way for it to go wrong is to start resolving THIS command's
    season from a sibling — which would build a guild's live board over data
    the bot has stopped updating, a product decision nobody has made (UI-9).
    So the credential slot pins the named guild's key and nothing else, for
    every ordering including the ones where an unusable guild sorts first.

    The other half is AC-008.5b: a quarantined guild HAS a key, and telling
    its officer it "has no API key set" routes them to `/register_guild` —
    the command that overwrites the roster (AC-008.1). The refusal must name
    the quarantine and the one exit, and must never name deregistration.
    """
    guild_ids = list(cluster)
    named = guild_ids[pick % len(guild_ids)]

    surface = _run_guild_command(cluster, named)

    if cluster[named] == HEALTHY:
        assert surface == {
            "reply.refused": False,
            "reply.named_quarantined": set(),
            "reply.named_keyless": set(),
            "reply.names_the_exit": False,
            "reply.names_deregistration": False,
            "leaderboard.season": SEASON,
            "tacticus.credentials": [_key_of(named)],
        }, (
            "a healthy guild's board was refused, or was resolved from a "
            f"sibling's key: {surface!r} (cluster was {cluster!r})"
        )
        return

    quarantined = cluster[named] == QUARANTINED
    assert surface == {
        "reply.refused": not quarantined,
        "reply.named_quarantined": {named} if quarantined else set(),
        "reply.named_keyless": set() if quarantined else {named},
        "reply.names_the_exit": quarantined,
        "reply.names_deregistration": False,
        "leaderboard.season": None,
        "tacticus.credentials": [],
    }, (
        f"an unusable guild (`{named}` is {cluster[named]}) was refused as the "
        f"wrong kind of problem: {surface!r}"
    )


# ===========================================================================
# Harness — real commands, real policy, replaced storage and transport
# ===========================================================================

def _run_cluster_command(cluster: dict) -> dict:
    return _run("set_live_cluster_leaderboard", cluster, {})


def _run_guild_command(cluster: dict, guild_id: str) -> dict:
    return _run("set_live_leaderboard", cluster, {"guild_id": guild_id})


def _run(command_name: str, cluster: dict, kwargs: dict) -> dict:
    """Invoke the real slash command against a generated cluster."""
    admin_cog = _admin_cog()
    interaction = _FakeInteraction()
    channel = _FakeChannel()

    with _cluster_in_storage(admin_cog, cluster) as tacticus:
        cog = admin_cog.AdminCog.__new__(admin_cog.AdminCog)
        command = _find_command(admin_cog, command_name)
        asyncio.run(command.callback(cog, interaction, channel=channel, **kwargs))
        saved = tacticus.live_leaderboards

    return _surface(interaction, saved, tacticus)


def _surface(interaction, saved: dict, tacticus) -> dict:
    """Every observable these commands produce for this contract."""
    reply = interaction.reply_text
    board = saved.get("cluster") or saved.get(
        next((key for key in saved if key.startswith("guild:")), "")
    ) or {}
    return {
        "reply.refused": reply.startswith("❌"),
        "reply.named_quarantined": _guilds_described_as(reply, "quarantined"),
        "reply.named_keyless": _guilds_described_as(reply, "no API key"),
        "reply.names_the_exit": "/update_guild_key" in reply,
        "reply.names_deregistration": _THE_DESTRUCTIVE_ROUTE in reply,
        "leaderboard.season": board.get("season"),
        "tacticus.credentials": list(tacticus.credentials),
    }


def _guilds_described_as(reply: str, words: str) -> set:
    """Which guilds the reply describes with `words`.

    Two shapes reach an officer and both are parsed here: the cluster
    refusal's per-guild bullets, and the single-guild refusal, which names its
    guild in backticks on its first line. Reading the guild out of the text
    rather than counting matches is what makes "the reply described the WRONG
    guild as keyless" a failure rather than an equal count.
    """
    described = {
        guild_id for guild_id, description in _BULLET.findall(reply)
        if words in description
    }
    if described:
        return described
    if words not in reply:
        return set()
    first_line = reply.splitlines()[0]
    return set(re.findall(r"`([^`]+)`", first_line))


@contextmanager
def _cluster_in_storage(admin_cog, cluster: dict):
    """Put the generated cluster behind the ports the commands read.

    Patched by hand rather than through the `monkeypatch` fixture: a
    function-scoped fixture inside a Hypothesis property is set up once and
    reused across every example, which Hypothesis rejects as a health check —
    and suppressing that check would make the patches leak between examples.

    The quarantine decision itself is NOT patched. `guild_keys.active_key`
    runs for real; only the storage read under it (`load_guild_binding`) and
    the Tacticus transport over it (`httpx.AsyncClient`) are replaced, which
    is the port boundary on both sides of the policy under test.
    """
    import httpx

    from bot import guild_keys
    from bot.repository import GuildBinding
    from bot.services.tacticus.guild_client import KeyStatus

    guilds = {
        guild_id: {
            "name": f"Guild {guild_id}",
            "api_key": "" if state == KEYLESS else _key_of(guild_id),
            "role_id": 1,
            "notification_channel_id": None,
            "member_role_ids": [],
        }
        for guild_id, state in cluster.items()
    }

    def _binding(server_id, guild_id):
        if cluster.get(guild_id) != QUARANTINED:
            return GuildBinding()
        return GuildBinding(
            tacticus_guild_id="11111111-1111-1111-1111-111111111111",
            tacticus_guild_tag="WB",
            tacticus_guild_name="Word Bearers",
            identity_bound_at="2026-07-31T04:00:00Z",
            key_status=KeyStatus.QUARANTINED.value,
            quarantine_reason=(
                "key drift: bound 【WB】 Word Bearers but resolves to 【DM】 "
                "Dark Mechanicum — observed=22222222-2222-2222-2222-222222222222"
            ),
            quarantined_at="2026-07-31T04:00:00.000Z",
        )

    world = _World()
    originals = {
        (admin_cog, "load_guilds"): lambda server_id: dict(guilds),
        (admin_cog, "load_guild_binding"): _binding,
        (admin_cog, "load_live_leaderboards"): lambda server_id: dict(world.live_leaderboards),
        (admin_cog, "save_live_leaderboards"): world.save,
        (admin_cog, "repo"): _FakeRepo(),
        (guild_keys, "load_guilds"): lambda server_id: dict(guilds),
        (guild_keys, "load_guild_binding"): _binding,
        (httpx, "AsyncClient"): lambda *args, **kwargs: _FakeTacticus(world),
    }
    saved = {
        (module, target): getattr(module, target) for module, target in originals
    }
    try:
        for (module, target), replacement in originals.items():
            setattr(module, target, replacement)
        yield world
    finally:
        for (module, target), original in saved.items():
            setattr(module, target, original)


class _World:
    """The writes and requests the command made."""

    def __init__(self) -> None:
        self.live_leaderboards: dict = {}
        self.credentials: list[str] = []

    def save(self, server_id, live) -> None:
        self.live_leaderboards = dict(live)


class _FakeRepo:
    """No raid rows for any guild — the board renders its "no data" content.

    Validates like the real repository would be asked to: a season is always
    an int by the time this is reached, and a command that got here with None
    resolved no season at all, which is the failure the property is about.
    """

    def load_battle_hits(self, server_id, guild_id, season):
        assert isinstance(season, int), "a board was built for a resolved season"
        return {}


class _FakeTacticus:
    """The current-season endpoint, recording every key it was sent."""

    def __init__(self, world: _World) -> None:
        self._world = world

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url: str, headers: dict | None = None, **kwargs):
        import httpx

        credential = (headers or {}).get("X-API-KEY", "")
        assert credential, "a request went out with no key at all"
        self._world.credentials.append(credential)
        return httpx.Response(
            200, json={"season": SEASON}, request=httpx.Request("GET", url)
        )


class _FakeInteraction:
    def __init__(self) -> None:
        self.guild_id = 4242
        self.response = _FakeResponse()
        self.followup = _FakeFollowup()

    @property
    def reply_text(self) -> str:
        return self.followup.messages[-1] if self.followup.messages else ""


class _FakeResponse:
    async def defer(self, *args, **kwargs) -> None:
        return None

    async def send_message(self, content: str, **kwargs) -> None:
        return None


class _FakeFollowup:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, content: str, **kwargs) -> None:
        self.messages.append(content)


class _FakeChannel:
    """Both leaderboard commands read `msg.id` from what `send` returns."""

    id = 7
    mention = "<#7>"

    async def send(self, content: str, **kwargs):
        return _FakeMessage()


class _FakeMessage:
    id = 1234


def _find_command(admin_cog, name: str):
    for command in admin_cog.AdminCog.__cog_app_commands__:
        if command.name == name:
            return command
    raise AssertionError(
        f"no `{name}` command is registered on AdminCog — delete the command "
        "method and this harness errors, which is the port-to-port litmus test"
    )


def _admin_cog():
    """Import `bot.cogs.admin_cog` LATE, and never at module scope.

    Importing any cog imports `bot.guilds`, which builds the process-wide
    `ClusterRepository` singleton from whatever environment exists AT THAT
    MOMENT. At collection time no fixture has run. Precedent:
    `tests/unit/test_auto_update_cycle_containment.py::_tasks_cog`.
    """
    from bot.cogs import admin_cog

    return admin_cog


@pytest.fixture(scope="module", autouse=True)
def _leave_the_cog_module_as_this_file_found_it():
    """Un-import the cog once this module's properties are done.

    `bot/cogs/admin_cog.py` binds `repo`, `load_guilds` and friends BY VALUE at
    import time. The acceptance suite patches `bot.guilds.repo` per test and
    depends on importing the cog afterwards to pick the patched object up.
    Dropping the module puts the next importer back in the position it holds
    when this file is absent.
    """
    yield
    sys.modules.pop("bot.cogs.admin_cog", None)
    cogs_package = sys.modules.get("bot.cogs")
    if cogs_package is not None:
        stale_module = getattr(cogs_package, "admin_cog", None)
        if stale_module is not None:
            delattr(cogs_package, "admin_cog")
