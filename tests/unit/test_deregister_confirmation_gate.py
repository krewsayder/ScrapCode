"""Properties of `/deregister_guild`'s confirmation gate (step 08-05).

WHY-NEW-FILE: tests/unit/test_deregister_confirmation_gate.py
  CLOSEST-EXISTING: tests/unit/test_quarantine_tombstone_history.py
  EXTENSION-COST: that module's universe is partitioned target/sibling and
    quantifies WHICH binding states leave a tombstone — the CASCADE is a
    declared constant there, asserted alongside but not the axis under
    test. The claim here is the gate itself: the counts the operator reads
    BEFORE confirming equal the rows the CASCADE destroys AFTER confirming,
    and nothing moves until the button is taken. Extending the tombstone
    module means every generated example re-declares the tombstone axis it
    does not vary, and drags the sibling half of a partitioned universe into
    a property that has no sibling.
  PARALLEL-RATIONALE: different lifecycle. The tombstone properties observe
    WHAT SURVIVES the CASCADE; these observe the gate that decides WHETHER
    the CASCADE fires at all. A strict delta over "the deletion is deferred"
    cannot be shared with a surface whose deletion is declared immediate.

WHAT IS BEING QUANTIFIED. AC-009.4's reply is a GOLDEN assertion in the
acceptance suite (exact operator-facing strings, the absence of "left
intact"). The golden assertion pins ONE fixture's counts; it cannot
quantify over guild state. The adjacent slot the golden cannot reach is
the INVARIANT behind the words: for every guild state, the counts stated
BEFORE confirmation equal the rows destroyed AFTER it, and the deletion is
deferred until the button is taken. Two properties:

  1. The pre-confirmation reply states the exact counts that the CASCADE
     will destroy, and NO row is deleted until confirmation is taken.
  2. After confirmation, the rows destroyed equal the counts that were
     stated, and the quarantine tombstone is written exactly when the
     binding was quarantined (08-03's invariant, preserved by the gate).

DECLARED UNIVERSE, strict:

    players      — roster rows for the named guild
    battle_hits  — battle hit rows for the named guild (all seasons)
    bomb_hits    — bomb hit rows for the named guild (all seasons)
    guilds       — the registry row the command deletes
    bindings     — the binding the CASCADE destroys
    history      — tombstone rows (the one thing that may survive)

PARADIGM. Property-based over guild state (varied player/hit counts and
quarantine state), per the step's test paradigm. The reply-text assertion
is exempt (golden) and lives in the acceptance suite.
"""
from __future__ import annotations

import os

# `bot.guilds` evaluates `repo = build_repo()` at import time and reads the
# environment at that moment. Pin a harmless backend before any `bot.*` import
# so collection cannot construct a repository pointed at a live tree. Same
# precedent as `tests/unit/test_quarantine_tombstone_history.py`.
os.environ.setdefault("SCRAPCODE_REPO_BACKEND", "json")

# `bot/cogs/admin_cog.py` imports `config`, which reads these two at import
# time and `int()`s them unconditionally. Neutral values, so a cog imported
# here cannot inherit a channel id from a developer's `.env`.
os.environ.setdefault("UPDATE_CHANNEL_ID", "0")
os.environ.setdefault("REPLAY_INDEX_CHANNEL_ID", "0")

import base64  # noqa: E402
import sqlite3  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

hypothesis = pytest.importorskip(
    "hypothesis",
    reason="hypothesis is not installed — DISTILL pins it into requirements.txt",
)

import hypothesis.strategies as st  # noqa: E402
from hypothesis import given, settings  # noqa: E402

# Deselected from the 250-test baseline for the same reason the slice-06
# acceptance module is: these belong to the remediation slice, and the
# baseline command is the "nothing that shipped has regressed" gate.
pytestmark = [pytest.mark.property, pytest.mark.slice_06]

SERVER_ID = 1458181638453203099
GUILD_TARGET = "word_bearers"

FERNET_KEY = base64.urlsafe_b64encode(b"guild-key-integrity-unit-tests!!"[:32]).decode()

UNIVERSE = (
    "players",
    "battle_hits",
    "bomb_hits",
    "guilds",
    "bindings",
    "history",
)

_CONFIRMATION_WORDS = (
    "confirm", "yes", "proceed", "delete", "destroy", "deregister",
)

_TAGS = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ", min_size=3, max_size=5)
_NAMES = st.text(min_size=1, max_size=24).filter(lambda candidate: candidate.strip())
_UUIDS = st.uuids().map(str)


# ===========================================================================
# Storage — one migrated database, reset per example
# ===========================================================================

@pytest.fixture(scope="module")
def storage(tmp_path_factory):
    """One migrated database for the whole module.

    Module-scoped deliberately: Hypothesis rejects function-scoped fixtures
    under `@given`, and running alembic per generated example would put a
    schema migration inside the inner loop of a property test. Every example
    calls `_reset` before it acts, so the state it enters on is fully
    determined by that example.
    """
    from alembic import command
    from alembic.config import Config

    import bot.db
    from bot.repository_sqlalchemy import SqlAlchemyClusterRepository

    db_path = tmp_path_factory.mktemp("deregister-gate") / "data" / "scrapcode.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    db_pkg = Path(bot.db.__file__).parent
    cfg = Config(str(db_pkg / "alembic.ini"))
    cfg.set_main_option("script_location", str(db_pkg / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "head")

    repo = SqlAlchemyClusterRepository(db_path=str(db_path), fernet_key=FERNET_KEY)
    return _Storage(repo=repo, db_path=db_path)


class _Storage:
    def __init__(self, *, repo, db_path: Path) -> None:
        self.repo = repo
        self.db_path = db_path


@pytest.fixture(scope="module")
def live_repo(storage):
    """Point `bot.guilds.repo` at the migrated database for this module."""
    import bot.guilds as guilds_mod

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(guilds_mod, "repo", storage.repo)
        yield storage


# ===========================================================================
# Seeding — a guild with varied counts and an optional quarantine
# ===========================================================================

@st.composite
def guild_states(draw):
    """A guild state the gate must hold for: counts, identity, quarantine."""
    return {
        "players": draw(st.integers(min_value=0, max_value=5)),
        "battle_hits": draw(st.integers(min_value=0, max_value=5)),
        "bomb_hits": draw(st.integers(min_value=0, max_value=5)),
        "tag": draw(_TAGS),
        "name": draw(_NAMES),
        "bound_uuid": draw(_UUIDS),
        "quarantined": draw(st.booleans()),
        "observed_uuid": draw(_UUIDS),
    }


def _reset(storage: _Storage, state: dict) -> None:
    """Put the guild into the declared state before an example acts."""
    from bot.models import Cluster, Guild
    from bot.repository import GuildBinding
    from bot.services.tacticus.guild_client import KeyStatus

    conn = sqlite3.connect(str(storage.db_path))
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        for table in ("guild_key_quarantine_history", "guild_key_bindings",
                      "players", "battle_hits", "bomb_hits", "guilds"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()

    storage.repo.save(Cluster(
        discord_server_id=SERVER_ID,
        guilds={
            GUILD_TARGET: Guild(
                id=GUILD_TARGET, name=state["name"],
                api_key="wb-key-not-in-any-reply", role_id=1,
            ),
        },
    ))
    _seed_players(storage.db_path, state["players"])
    _seed_hits(storage.db_path, "battle_hits", state["battle_hits"])
    _seed_hits(storage.db_path, "bomb_hits", state["bomb_hits"])

    binding = GuildBinding(
        tacticus_guild_id=state["bound_uuid"],
        tacticus_guild_tag=state["tag"],
        tacticus_guild_name=state["name"],
        identity_bound_at="2026-07-30T04:00:00.000Z",
        key_status=(
            KeyStatus.QUARANTINED.value if state["quarantined"]
            else KeyStatus.ACTIVE.value
        ),
        quarantine_reason=(
            f"key drift: bound 【{state['tag']}】 but resolves to 【DRIFT】 "
            f"— observed={state['observed_uuid']}"
            if state["quarantined"] else ""
        ),
        quarantined_at="2026-07-31T04:00:00.000Z" if state["quarantined"] else None,
    )
    storage.repo.save_guild_binding(SERVER_ID, GUILD_TARGET, binding)


def _seed_players(db_path: Path, count: int) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        for i in range(count):
            conn.execute(
                "INSERT INTO players (discord_server_id, guild_id, tacticus_user_id, "
                "display_name, last_validated, is_former) VALUES (?, ?, ?, ?, ?, 0)",
                (SERVER_ID, GUILD_TARGET, f"uid-{i}", f"Player {i}",
                 "2026-07-31T04:00:00Z"),
            )
        conn.commit()
    finally:
        conn.close()


def _seed_hits(db_path: Path, table: str, count: int) -> None:
    """Seed `count` hit rows across one season for the named guild."""
    if not count:
        return
    conn = sqlite3.connect(str(db_path))
    try:
        for i in range(count):
            if table == "battle_hits":
                conn.execute(
                    "INSERT INTO battle_hits (discord_server_id, guild_id, season, "
                    "boss_id, encounter_index, tier_key, user_id, damage, completed_on, "
                    "hero_roster_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (SERVER_ID, GUILD_TARGET, 1, f"boss-{i}", "0", "legendary",
                     f"uid-{i}", 1000 + i, "2026-07-18T10:00:00Z", "Avatar"),
                )
            else:
                conn.execute(
                    "INSERT INTO bomb_hits (discord_server_id, guild_id, season, "
                    "boss_id, encounter_index, tier_key, user_id, damage, completed_on, "
                    "encounter_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (SERVER_ID, GUILD_TARGET, 1, f"boss-{i}", "0", "legendary",
                     f"uid-{i}", 500 + i, "2026-07-18T10:00:00Z", "Bomb"),
                )
        conn.commit()
    finally:
        conn.close()


def _capture(db_path: Path) -> dict:
    """Snapshot every declared universe slot for the named guild."""
    conn = sqlite3.connect(str(db_path))
    try:
        def _count(table: str, guild_id: str = GUILD_TARGET) -> int:
            return conn.execute(
                f"SELECT COUNT(*) FROM {table} "
                "WHERE discord_server_id = ? AND guild_id = ?",
                (SERVER_ID, guild_id),
            ).fetchone()[0]

        return {
            "players": _count("players"),
            "battle_hits": _count("battle_hits"),
            "bomb_hits": _count("bomb_hits"),
            "guilds": _count("guilds"),
            "bindings": _count("guild_key_bindings"),
            "history": _count("guild_key_quarantine_history"),
        }
    finally:
        conn.close()


def _stated_counts(reply: str, state: dict) -> dict:
    """The counts the pre-confirmation reply states, parsed back out.

    The reply is operator-facing prose; the invariant under test is that
    the stated number for each slot equals the seeded count. The acceptance
    suite checks `str(count) in reply`; this property checks the STRONGER
    claim that the stated counts are exactly the seeded counts (parsed from
    the reply by the same numbers the operator reads).
    """
    import re

    pattern = re.compile(
        r"•\s*(\d+)\s+(players|battle hit rows|bomb hit rows)"
    )
    # `findall` yields (number, label) tuples; key by label so the lookup
    # is by what the slot is, not by its count.
    found = {label: int(number) for number, label in pattern.findall(reply)}
    return {
        "players": found.get("players", -1),
        "battle_hits": found.get("battle hit rows", -1),
        "bomb_hits": found.get("bomb hit rows", -1),
    }


# ===========================================================================
# Driving the real cog callback — no seam that bypasses it
# ===========================================================================

class _Response:
    def __init__(self, interaction) -> None:
        self._interaction = interaction

    async def send_message(self, content="", *, embed=None, ephemeral=False, **kwargs):
        self._interaction.replies.append(content)
        self._interaction._offer(kwargs.get("view"))

    async def defer(self, *, ephemeral=False, **kwargs):
        return None

    def is_done(self) -> bool:
        return bool(self._interaction.replies)


class _Followup:
    def __init__(self, interaction) -> None:
        self._interaction = interaction

    async def send(self, content="", *, embed=None, ephemeral=False, **kwargs):
        self._interaction.replies.append(content)
        self._interaction._offer(kwargs.get("view"))


class _Interaction:
    """Captures every reply AND every view offered alongside one.

    Mirrors the acceptance suite's `_FakeInteraction`: `view=` is the seam
    AC-009.4's confirmation travels through, so a double that drops it
    cannot tell "the command paused" from "the command did not pause".
    """

    def __init__(self) -> None:
        self.guild_id = SERVER_ID
        self.replies: list[str] = []
        self.views: list = []
        self.extras: dict = {}
        self.response = _Response(self)
        self.followup = _Followup(self)

    def _offer(self, view) -> None:
        if view is not None:
            self.views.append(view)

    @property
    def all_replies(self) -> str:
        return "\n".join(self.replies)


async def _confirm_if_awaiting(interaction: _Interaction) -> None:
    """Press the confirmation button if the command offered one."""
    for view in interaction.views:
        for child in getattr(view, "children", ()):
            label = " ".join(
                str(getattr(child, attr, "") or "")
                for attr in ("label", "custom_id")
            ).lower()
            if any(word in label for word in _CONFIRMATION_WORDS):
                await child.callback(interaction)
                return


async def _invoke(command_name: str, interaction, /, **kwargs) -> None:
    from bot.cogs.admin_cog import AdminCog
    from bot.services.chronicl3r.player_service import PlayerService

    cog = AdminCog.__new__(AdminCog)
    cog.player_service = PlayerService(_ChroniclerClient())
    for cmd in AdminCog.__cog_app_commands__:
        if cmd.name == command_name:
            await cmd.callback(cog, interaction, **kwargs)
            return
    raise AssertionError(f"no `{command_name}` command is registered on AdminCog")


class _ChroniclerClient:
    def authenticate(self) -> None:
        return None

    def register_user(self, tacticus_user_id: str) -> dict:
        return self.get_profile(tacticus_user_id)

    def get_profile(self, tacticus_user_id: str) -> dict:
        return {
            "tacticus_user_id": tacticus_user_id,
            "tacticus_display_nm": f"player-{tacticus_user_id}",
        }


# ===========================================================================
# Property 1 — the reply states the truth, and nothing is deleted yet
# ===========================================================================

@settings(max_examples=50, deadline=None)
@given(state=guild_states())
async def test_the_reply_states_the_counts_and_defers_the_deletion(live_repo, state):
    """For every guild state, the counts stated before confirmation equal
    the rows present, and NO row is deleted until the button is taken.

    Both halves are the gate. A reply that states the wrong counts lies in
    the same reassuring direction as the old "left intact" message. A
    command that states the right counts and deletes anyway is worse: it
    tells the truth and then ignores it. The state is read BEFORE the
    deletion, so the counts the operator reads are the rows that WILL be
    destroyed; `before == after_invoke` is the deferral.
    """
    _reset(live_repo, state)
    before = _capture(live_repo.db_path)

    interaction = _Interaction()
    await _invoke("deregister_guild", interaction, guild_id=GUILD_TARGET)

    reply = interaction.all_replies
    assert "left intact" not in reply, (
        f"the reply still claims the data survives: {reply!r}"
    )
    assert _stated_counts(reply, state) == {
        "players": state["players"],
        "battle_hits": state["battle_hits"],
        "bomb_hits": state["bomb_hits"],
    }, (
        f"the reply does not state the exact counts that will be destroyed: "
        f"{reply!r}"
    )
    assert _capture(live_repo.db_path) == before, (
        "the guild's history was destroyed before the admin confirmed"
    )


# ===========================================================================
# Property 2 — after confirmation, the destroyed rows equal the stated counts
# ===========================================================================

@settings(max_examples=50, deadline=None)
@given(state=guild_states())
async def test_confirmation_destroys_exactly_what_was_stated(live_repo, state):
    """After the button is taken, the CASCADE destroys exactly the rows the
    reply stated, and the tombstone is written exactly when the binding was
    quarantined.

    The tombstone half is 08-03's invariant, preserved by the gate: the
    deletion and the tombstone write move together inside the confirmation
    callback, so a confirmation that is never taken leaves no tombstone
    behind for a guild that was not destroyed.
    """
    from bot.services.tacticus.guild_client import KeyStatus

    _reset(live_repo, state)
    before = _capture(live_repo.db_path)

    interaction = _Interaction()
    await _invoke("deregister_guild", interaction, guild_id=GUILD_TARGET)
    await _confirm_if_awaiting(interaction)

    after = _capture(live_repo.db_path)
    assert after["players"] == 0, "players survived the CASCADE"
    assert after["battle_hits"] == 0, "battle hits survived the CASCADE"
    assert after["bomb_hits"] == 0, "bomb hits survived the CASCADE"
    assert after["guilds"] == 0, "the guild row survived deregistration"
    assert after["bindings"] == 0, "the binding survived the CASCADE"
    assert after["players"] == 0 and before["players"] == state["players"], (
        "the destroyed player count does not match the stated count"
    )
    if state["quarantined"]:
        assert after["history"] == before["history"] + 1, (
            "a quarantined binding did not leave a tombstone when the gate fired"
        )
    else:
        assert after["history"] == before["history"], (
            "a non-quarantined binding left a tombstone — the gate tombstoned a "
            "guild that was never quarantined"
        )