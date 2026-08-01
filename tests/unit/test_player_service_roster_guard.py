"""Unit tests for the roster-write guard in `PlayerService` (criterion 5).

WHY THESE EXIST AND WHY THEY ARE HERE
-------------------------------------
The 2026-07-28 incident had two halves. The half everyone remembers is the
off-roster hits — 30/30 battle rows and 20/20 bomb rows for season 106. The
LARGER half was roster inversion: `refresh_guild` writes every member of the
fetched roster as `is_former: False` and flips everyone absent to
`is_former: True`, so an hourly cycle reading the wrong guild corrupted 60 of
the 67 `players` rows.

`parse_guild_snapshot` reads members tolerantly. A 200 response carrying a
`guildId` but no `members` key therefore yields a VALID identity with an EMPTY
member set — and an empty member set flips **every single player** to former,
silently, on a response that looked successful. That is the same failure shape
reached by a different route, and no acceptance scenario covers it.

They live at the unit layer rather than in the acceptance suite because every
Slice-01 acceptance scenario drives `_run_hourly_cycle`, which cannot work
until `bot/cogs/tasks_cog.py` is wired in step 03-03. Same reasoning, same
precedent as `tests/unit/test_guild_keys_policy.py`.

WHAT IS REAL HERE
-----------------
  * a real `JsonClusterRepository` rooted in `tmp_path`, rebound onto
    `bot.guilds.repo` — the singleton is built at IMPORT time, so
    `monkeypatch.setenv` inside a test is far too late to redirect it (see
    `_repo_singleton_never_escapes_tmp_path` below and its acceptance-suite
    twin);
  * the real `PlayerService`, driven through its real driving ports
    `refresh_guild` and `validate_if_stale`;
  * the real `parse_guild_snapshot`, so a snapshot a test calls "unusable"
    is one the production parser would actually have produced from that
    vendor body — a hand-built `GuildSnapshot` could assert against a shape
    Tacticus never sends;
  * a real `logging` logger, read back through `caplog`.

The ONE double is at the Chronicler HTTP boundary (`chronicl3rClient`), which
is a driven port. It records every call, because "no profile was fetched" is
the observable that separates "refused" from "fetched the data and then threw
it away" — the second still puts the wrong guild's roster in memory and in the
logs.

No Hypothesis: it is not in `requirements.txt` and this feature adds no
dependency, so the equivalence class of unusable snapshots is enumerated by
`parametrize` over the shapes the production parser can emit. The state-delta
discipline is kept by comparing the WHOLE player-list document with `==` —
a strict universe over `__meta__` and every player row, not a single-property
assert on the row a test happens to care about.
"""
from __future__ import annotations

import copy
import inspect
import logging
import os

# `bot.guilds` evaluates `repo = build_repo()` at import time and reads the
# environment at that moment. Pin a harmless backend before any `bot.*` import
# so collection cannot construct a repository pointed at a live tree. Same
# precedent as `tests/unit/test_guild_keys_policy.py`.
os.environ.setdefault("SCRAPCODE_REPO_BACKEND", "json")

from pathlib import Path

import pytest

from bot.services.tacticus.guild_client import (
    DEAD_KEY_STATUSES,
    GuildIdentity,
    GuildSnapshot,
    ProbeOutcome,
    parse_guild_snapshot,
)

SERVER_ID = 1458181638453203099
GUILD_WB = "word_bearers"

# The identity from the 2026-07-28 incident. A guild identifier, not a
# credential — Tacticus returns it to any holder of a key for the guild.
WORD_BEARERS = GuildIdentity(
    uuid="b64bdba4-36ac-4229-bd29-4b7b6ce7f44f",
    tag="EUVQZ",
    name="【UNDV】Word Bearers",
)

REFUSAL_EVENT = "player_list.refresh.refused"


# ---------------------------------------------------------------------------
# Storage — a real repository, inside tmp_path
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _repo_singleton_never_escapes_tmp_path(monkeypatch, tmp_path: Path):
    """Point `bot.guilds.repo` at `tmp_path` for EVERY test in this module.

    Unconditional and autouse: `JsonClusterRepository._server_path` mkdirs on
    READ as well as on write, so a single unguarded `load_player_list` against
    the import-time singleton creates `clusters/<server-id>/` at the repository
    root — on a machine holding a live JSON tree that is a write into
    production data.
    """
    import bot.guilds as guilds_mod
    from bot.repository import JsonClusterRepository
    monkeypatch.setattr(
        guilds_mod, "repo", JsonClusterRepository(base_path=tmp_path / "clusters")
    )
    yield


# ---------------------------------------------------------------------------
# The Chronicler seam — the only double
# ---------------------------------------------------------------------------

class RecordingChroniclerClient:
    """Stand-in for `chronicl3rClient`, recording every call it receives.

    Validates its inputs the way the real client does (a blank
    `tacticus_user_id` produces a 4xx, not a profile), so a permissive double
    cannot hide a wiring bug that production would crash on.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def register_user(self, tacticus_user_id: str) -> dict:
        return self._profile("register_user", tacticus_user_id)

    def get_profile(self, tacticus_user_id: str) -> dict:
        return self._profile("get_profile", tacticus_user_id)

    def _profile(self, method: str, tacticus_user_id: str) -> dict:
        assert tacticus_user_id, "the real client rejects a blank tacticus_user_id"
        self.calls.append((method, tacticus_user_id))
        return {"tacticus_display_nm": f"name-of-{tacticus_user_id}"}


@pytest.fixture
def chronicler() -> RecordingChroniclerClient:
    return RecordingChroniclerClient()


@pytest.fixture
def player_service(chronicler: RecordingChroniclerClient):
    from bot.services.chronicl3r.player_service import PlayerService
    return PlayerService(chronicler)


# ---------------------------------------------------------------------------
# Snapshot builders — every one routed through production code
# ---------------------------------------------------------------------------

def _usable_snapshot(*members: str) -> GuildSnapshot:
    """A 200 that resolves an identity AND carries members."""
    return parse_guild_snapshot({
        "guild": {
            "guildId": WORD_BEARERS.uuid,
            "guildTag": WORD_BEARERS.tag,
            "name": WORD_BEARERS.name,
            "members": [{"userId": m} for m in members],
        }
    })


def _members_key_absent() -> GuildSnapshot:
    """200 OK, a real guildId, and NO `members` key at all.

    UD-6, and the reason this whole module exists. `parse_guild_snapshot`
    reads members tolerantly, so this classifies MATCH with an empty roster —
    a response that looks entirely successful and would flip every player to
    former.
    """
    return parse_guild_snapshot({
        "guild": {
            "guildId": WORD_BEARERS.uuid,
            "guildTag": WORD_BEARERS.tag,
            "name": WORD_BEARERS.name,
        }
    })


def _members_present_but_empty() -> GuildSnapshot:
    """200 OK, a real guildId, `members: []`.

    Indistinguishable from `_members_key_absent` once parsed — deliberately,
    see the module the guard lives in for why the two are not separated.
    """
    return parse_guild_snapshot({
        "guild": {
            "guildId": WORD_BEARERS.uuid,
            "guildTag": WORD_BEARERS.tag,
            "name": WORD_BEARERS.name,
            "members": [],
        }
    })


def _no_guild_id() -> GuildSnapshot:
    """200 OK, a full member list, no `guildId`.

    The parser drops the members here: a roster whose owner cannot be
    established is one nobody may write.
    """
    return parse_guild_snapshot({
        "guild": {"members": [{"userId": "u1"}, {"userId": "u2"}]}
    })


def _unreachable() -> GuildSnapshot:
    """What `fetch_guild_snapshot` returns for a timeout or a 5xx."""
    return GuildSnapshot(
        outcome=ProbeOutcome.UNREACHABLE,
        error="ReadTimeout: the guild service did not answer",
    )


def _dead_key() -> GuildSnapshot:
    """What `fetch_guild_snapshot` returns for a revoked key."""
    status = DEAD_KEY_STATUSES[0]
    return GuildSnapshot(
        outcome=ProbeOutcome.DEAD,
        status=status,
        error=f"the key was refused with HTTP {status}",
    )


# `reason` is the operator's diagnosis, so it is asserted per shape rather
# than lumped into one string: "the response had no members" and "the response
# named no guild" send an operator to two different places.
UNUSABLE_SNAPSHOTS = [
    pytest.param(_members_key_absent, "members_empty", id="200-no-members-key"),
    pytest.param(_members_present_but_empty, "members_empty", id="200-members-empty"),
    pytest.param(_no_guild_id, "identity_absent", id="200-no-guildId"),
    pytest.param(_unreachable, "identity_absent", id="unreachable"),
    pytest.param(_dead_key, "identity_absent", id="dead-key"),
]


# ---------------------------------------------------------------------------
# Player-list helpers
# ---------------------------------------------------------------------------

def _seed_players(*user_ids: str) -> dict:
    """A player list as an hourly cycle leaves it: everyone current."""
    from bot.guilds import load_player_list, save_player_list
    data = load_player_list(SERVER_ID, GUILD_WB)
    for user_id in user_ids:
        data["players"][user_id] = {
            "display_name": f"name-of-{user_id}",
            "last_validated": "2026-07-28T04:00:00Z",
            "is_former": False,
        }
    save_player_list(SERVER_ID, GUILD_WB, data)
    return copy.deepcopy(data)


def _stored_player_list() -> dict:
    """The WHOLE document — `__meta__` and every row. The universe."""
    from bot.guilds import load_player_list
    return copy.deepcopy(load_player_list(SERVER_ID, GUILD_WB))


def _refusals(caplog) -> list:
    return [r for r in caplog.records if getattr(r, "event", None) == REFUSAL_EVENT]


# ===========================================================================
# Criterion 5 — an unusable snapshot cannot drive a roster write
# ===========================================================================

@pytest.mark.parametrize("build_snapshot,expected_reason", UNUSABLE_SNAPSHOTS)
async def test_an_unusable_snapshot_changes_not_one_player_row(
    player_service, chronicler, caplog, build_snapshot, expected_reason
):
    """The 60-of-67 half of the incident, closed at the thing that writes.

    Asserting on the whole document rather than on `is_former` alone: the
    damage was a wholesale rewrite, so the claim is "nothing moved", and a
    single-property assert cannot make that claim.
    """
    caplog.set_level(logging.DEBUG)
    before = _seed_players("u1", "u2", "u3")

    await player_service.refresh_guild(SERVER_ID, GUILD_WB, build_snapshot())

    assert _stored_player_list() == before, (
        "an unusable snapshot rewrote the player list — this is the roster "
        "inversion that corrupted 60 of 67 rows on 2026-07-28"
    )
    assert chronicler.calls == [], (
        "the Chronicler was called for a roster that was never usable; "
        "fetching and then discarding still puts the data in memory and logs"
    )


@pytest.mark.parametrize("build_snapshot,expected_reason", UNUSABLE_SNAPSHOTS)
async def test_a_refused_refresh_says_so_loudly(
    player_service, caplog, build_snapshot, expected_reason
):
    """A silent skip is how this class of bug survives for three days.

    One structured ERROR record through `bot/obs.py`, carrying the reason and
    the number of rows the refusal protected — the blast radius the operator
    would otherwise have to reconstruct from a diff.
    """
    caplog.set_level(logging.DEBUG)
    _seed_players("u1", "u2", "u3")

    await player_service.refresh_guild(SERVER_ID, GUILD_WB, build_snapshot())

    refusals = _refusals(caplog)
    assert len(refusals) == 1, (
        f"expected exactly one {REFUSAL_EVENT} record, got "
        f"{[getattr(r, 'event', r.getMessage()) for r in caplog.records]}"
    )
    record = refusals[0]
    assert record.levelno == logging.ERROR
    assert record.reason == expected_reason
    assert record.server_id == SERVER_ID
    assert record.guild_id == GUILD_WB
    assert record.known_players == 3, (
        "the record must carry how many rows the refusal protected — that "
        "number is the postmortem's '60 of 67'"
    )
    assert record.ts


async def test_an_unusable_snapshot_cannot_reach_a_write_through_staleness_either(
    player_service, chronicler, caplog
):
    """`validate_if_stale` is the hourly path the incident actually ran.

    With `STALE_AFTER_HOURS = 1` every cycle found the list stale and called
    `refresh_guild`, which is how a single bad response became 72 hours of
    corruption. The guard has to hold on THIS entry point, not only on the
    one an admin command uses.
    """
    caplog.set_level(logging.DEBUG)
    before = _seed_players("u1", "u2", "u3")

    await player_service.validate_if_stale(
        SERVER_ID, GUILD_WB, _members_key_absent()
    )

    assert _stored_player_list() == before
    assert chronicler.calls == []
    assert len(_refusals(caplog)) == 1


async def test_an_empty_player_list_is_not_seeded_from_an_unusable_snapshot(
    player_service, caplog
):
    """`validate_if_stale` refreshes unconditionally when the list is empty.

    That branch is the one a brand-new guild takes on its first cycle, and it
    is the one where "no members" looks least suspicious. It must still refuse.
    """
    caplog.set_level(logging.DEBUG)
    before = _stored_player_list()

    await player_service.validate_if_stale(
        SERVER_ID, GUILD_WB, _members_key_absent()
    )

    assert _stored_player_list() == before
    assert len(_refusals(caplog)) == 1


# ===========================================================================
# The guard must not eat the behaviour it protects
# ===========================================================================

async def test_a_credible_roster_still_refreshes_every_row(
    player_service, chronicler, caplog
):
    """The falsifier for every test above.

    Without this, `refresh_guild` could satisfy criterion 5 by returning
    immediately and never writing anything — a guard that blocks everything
    passes every refusal test and breaks the feature.
    """
    caplog.set_level(logging.DEBUG)
    _seed_players("u1", "u2", "u3")

    await player_service.refresh_guild(
        SERVER_ID, GUILD_WB, _usable_snapshot("u1", "u2", "u4")
    )

    players = _stored_player_list()["players"]
    assert set(players) == {"u1", "u2", "u3", "u4"}
    assert [players[u]["is_former"] for u in ("u1", "u2", "u4")] == [False, False, False]
    assert players["u3"]["is_former"] is True, (
        "a member genuinely absent from a credible roster is still flipped to "
        "former — the guard narrows the input, it does not change the diff"
    )
    assert _refusals(caplog) == []


async def test_a_single_member_roster_is_credible(player_service, caplog):
    """One member is the floor, not the exception.

    A guild whose key still works always contains at least the key-holder, so
    one member is a normal response — and treating "suspiciously small" as
    unusable would invent a second, fuzzier guard nobody can reason about.
    """
    caplog.set_level(logging.DEBUG)
    _seed_players("u1", "u2")

    await player_service.refresh_guild(SERVER_ID, GUILD_WB, _usable_snapshot("u1"))

    players = _stored_player_list()["players"]
    assert players["u1"]["is_former"] is False
    assert players["u2"]["is_former"] is True
    assert _refusals(caplog) == []


# ===========================================================================
# Criteria 1 and 2 — the call path, asserted at the signature
# ===========================================================================

def test_the_roster_fetch_is_gone_not_deprecated():
    """Criterion 1. A surviving `_fetch_roster` is a second, unguarded call
    path — the exact thing the move exists to remove."""
    from bot.services.chronicl3r.player_service import PlayerService
    assert not hasattr(PlayerService, "_fetch_roster")


@pytest.mark.parametrize("method_name", ["refresh_guild", "validate_if_stale"])
def test_no_caller_can_hand_these_a_key(method_name):
    """Criterion 2. Not a style check: while the parameter exists, a caller
    that still passes a key type-checks, runs, and silently re-opens the
    unguarded path. Removing it makes the old call site a TypeError."""
    from bot.services.chronicl3r.player_service import PlayerService
    parameters = inspect.signature(getattr(PlayerService, method_name)).parameters
    assert "api_key" not in parameters
    assert "snapshot" in parameters


def test_the_chronicler_calls_are_all_still_there():
    """Criterion 4. Only the Tacticus call leaves. Deleting a Chronicler call
    along with it would be a silent loss of the profile refresh that keeps
    display names current."""
    from bot.services.chronicl3r.player_service import PlayerService
    for method_name in (
        "get_or_register", "refresh_guild", "validate_if_stale",
        "ensure_player_in_list", "get_display_name",
    ):
        assert callable(getattr(PlayerService, method_name))
