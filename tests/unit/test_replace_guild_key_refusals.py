"""Property tests for the two refusals `replace_guild_key` owes its callers (08-01).

WHY-NEW-FILE: tests/unit/test_replace_guild_key_refusals.py
  CLOSEST-EXISTING: tests/unit/test_guild_keys_quarantine_gate.py
  EXTENSION-COST: that module's module-scoped fixture patches
    `bot.guilds.repo` AND `guild_client.fetch_guild_snapshot`, seeds ONE
    guild, and declares a ten-slot universe of `binding.*` plus `probe.calls`.
    Every property here needs a SECOND guild holding a colliding key and
    dependent player/battle/bomb rows to prove untouched — so extending it
    means widening that universe for all of its properties (each of which
    would then have to declare the new slots) and re-seeding a probe recorder
    none of these properties use.
  PARALLEL-RATIONALE: different port. Those properties enter through the
    policy chokepoint `verify_and_resolve` and observe the binding; these
    enter through the DRIVEN port `ClusterRepository.replace_guild_key` and
    observe storage. The two universes have no slot in common, and a strict
    delta assertion cannot be shared between surfaces that observe different
    things.

WHAT IS BEING QUANTIFIED. `replace_guild_key` is the ONLY sanctioned write
path for a guild's key (DDD-3, ADR-006 D7). Two things it must never do, and
neither is a claim about one input:

  1. It must never ERASE a key. `encrypt_api_key("")` returns `""` and
     `api_key_hmac("")` returns `None` — both correct in isolation, both
     documented in `bot/db/secrets.py`, because that is how several keyless
     guilds coexist under a NULLABLE UNIQUE constraint. Composed, they turn
     the write path into an erase path. "Today's only caller validates first"
     is the reasoning that left `verify_and_resolve` ungated in slice 05, so
     the guard is asserted at the method, over every string that would blank
     the row rather than over the one someone thought of.
  2. It must never let a raw `IntegrityError` out. `guilds.api_key_hmac` is
     UNIQUE table-global, so installing a key a sibling already holds raises
     from SQLAlchemy with the bound parameters inlined — the Fernet
     ciphertext and the full 64-hex hmac — straight into `main.py`'s generic
     handler, which prints it and sends it to Discord. The refusal must be
     typed, must name the holder so an admin can act, and must carry no key
     material at all.

DECLARED UNIVERSE, strict. Every property compares the FULL observable
storage surface the roadmap names:

    api_key               — the target guild's stored Fernet ciphertext
    api_key_hmac          — the target guild's stored uniqueness fingerprint
    players               — every player row in the database
    battle_hits           — every battle hit row
    bomb_hits             — every bomb hit row
    guild_key_bindings    — every binding row

The last four are counted TABLE-GLOBAL rather than for the target guild
alone. That is the point of the criterion "a key write cannot CASCADE to a
dependent row": a refusal implemented as a DELETE-then-reinsert, or a guard
placed after the write, shows up here as a count that moved on some other
guild's rows. `_assert_state_delta` is strict — a slot with no declared
predicate must be byte-identical — so the two key slots are the only ones any
property is allowed to move, and they must move together.
"""
from __future__ import annotations

import os

# `bot.guilds` evaluates `repo = build_repo()` at import time and reads the
# environment at that moment. Pin a harmless backend before any `bot.*`
# import so collection cannot construct a repository pointed at a live tree.
# Same precedent as `tests/unit/test_guild_keys_quarantine_gate.py`.
os.environ.setdefault("SCRAPCODE_REPO_BACKEND", "json")

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
GUILD_SIBLING = "dark_mechanicum"
TARGET_KEY = "wb-key"
SIBLING_KEY = "dm-key"
SEASON = 106

FERNET_KEY = base64.urlsafe_b64encode(b"guild-key-integrity-unit-tests!!"[:32]).decode()

UNIVERSE = (
    "api_key",
    "api_key_hmac",
    "players",
    "battle_hits",
    "bomb_hits",
    "guild_key_bindings",
)

_ROW_COUNT_SLOTS = ("players", "battle_hits", "bomb_hits", "guild_key_bindings")

# Strings that would BLANK the row. `""` is the documented one; the whitespace
# variants are the same erasure wearing a disguise — a key that survives
# `strip()` as nothing can never authenticate, so accepting one destroys the
# guild's real key exactly as thoroughly while leaving junk behind.
blank_keys = st.text(alphabet=" \t\n\r\v\f", max_size=6)

# Strings that are real keys: not blank, and not one of the two the fixture
# already installed (an example equal to those would be testing "writing the
# key that is already there", which is a different property and not this one).
#
# `min_size=12` IS LOAD-BEARING and was found by Hypothesis, not chosen up
# front. The disclosure property detects a leak by substring search, and a
# generated key of `"P"` is a substring of "That API key is already
# registered to guild ..." by coincidence — the search has no discriminating
# power at that length, so the property failed against an implementation that
# discloses nothing. Twelve characters of arbitrary text appearing inside a
# sixty-character refusal by chance is not a case worth reserving budget for,
# and a real Tacticus key is far longer than that. The assertion is unchanged;
# only inputs on which it cannot decide are excluded.
real_keys = st.text(min_size=12, max_size=48).filter(
    lambda candidate: candidate.strip() and candidate not in (TARGET_KEY, SIBLING_KEY)
)


# ===========================================================================
# Storage — one migrated database, reset to a known baseline per example
# ===========================================================================

@pytest.fixture(scope="module")
def storage(tmp_path_factory):
    """One migrated database for the whole module, seeded with the shape the
    universe describes: two guilds in one cluster, plus a player, a battle
    hit, a bomb hit and a binding whose survival every property asserts.

    Module-scoped deliberately: Hypothesis rejects function-scoped fixtures
    under `@given`, and running alembic per generated example would put a
    schema migration inside the inner loop of a property test. Every example
    calls `_reset_keys` before it acts, so the state it enters on is fully
    determined by that example rather than inherited from the previous one.
    """
    from alembic import command
    from alembic.config import Config

    import bot.db
    from bot.models import Cluster, Guild
    from bot.repository import GuildBinding
    from bot.repository_sqlalchemy import SqlAlchemyClusterRepository

    db_path = tmp_path_factory.mktemp("replace-guild-key") / "data" / "scrapcode.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    db_pkg = Path(bot.db.__file__).parent
    cfg = Config(str(db_pkg / "alembic.ini"))
    cfg.set_main_option("script_location", str(db_pkg / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "head")

    repo = SqlAlchemyClusterRepository(db_path=str(db_path), fernet_key=FERNET_KEY)
    repo.save(Cluster(
        discord_server_id=SERVER_ID,
        guilds={
            GUILD_TARGET: Guild(
                id=GUILD_TARGET, name="Word Bearers", api_key=TARGET_KEY, role_id=1,
            ),
            GUILD_SIBLING: Guild(
                id=GUILD_SIBLING, name="Dark Mechanicum", api_key=SIBLING_KEY, role_id=2,
            ),
        },
    ))
    repo.save_guild_binding(SERVER_ID, GUILD_TARGET, GuildBinding(
        tacticus_guild_id="0a1b2c3d-4e5f-6071-8293-a4b5c6d7e8f9",
        tacticus_guild_tag="WBRS",
        tacticus_guild_name="Word Bearers",
        identity_bound_at="2026-07-31T04:00:00Z",
    ))
    _seed_dependent_rows(db_path)
    return _Storage(repo=repo, db_path=db_path)


class _Storage:
    """The repository under test and the file its rows live in.

    The file is carried alongside because the universe is read with plain
    `sqlite3`, outside the ORM and outside the repository: the question a
    refusal has to answer is whether the ROWS moved, and asking through a
    repository that filters by server id could not distinguish "the row is
    unchanged" from "the row is invisible to this reader".
    """

    def __init__(self, *, repo, db_path: Path) -> None:
        self.repo = repo
        self.db_path = db_path


def _seed_dependent_rows(db_path: Path) -> None:
    """One player, one battle hit and one bomb hit — the rows a key write is
    forbidden to reach. Written with raw SQL because they are test scaffolding
    for a count, not a behaviour under test, and because going through the hit
    repositories would couple these properties to the leaderboard load shapes.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO players (discord_server_id, guild_id, tacticus_user_id, "
            "display_name, last_validated, is_former) VALUES (?, ?, ?, ?, ?, 0)",
            (SERVER_ID, GUILD_TARGET, "u1", "Player One", "2026-07-31T04:00:00Z"),
        )
        conn.execute(
            "INSERT INTO battle_hits (discord_server_id, guild_id, season, boss_id, "
            "encounter_index, tier_key, user_id, damage, completed_on, hero_roster_key) "
            "VALUES (?, ?, ?, 'szarekh', '1', 'Tier 1', 'u1', 1000, "
            "'2026-07-31T04:00:00Z', 'roster-a')",
            (SERVER_ID, GUILD_TARGET, SEASON),
        )
        conn.execute(
            "INSERT INTO bomb_hits (discord_server_id, guild_id, season, boss_id, "
            "encounter_index, tier_key, user_id, damage, completed_on) "
            "VALUES (?, ?, ?, 'szarekh', '1', 'Tier 1', 'u1', 50, "
            "'2026-07-31T04:00:00Z')",
            (SERVER_ID, GUILD_TARGET, SEASON),
        )
        conn.commit()
    finally:
        conn.close()


def _reset_keys(db_path: Path, *, target: str, sibling: str) -> None:
    """Put both guilds' key columns back to a declared state before an example.

    Raw SQL on purpose, and it writes BOTH columns together: setup must not go
    through the method under test (a guard under construction would silently
    reshape the precondition), and writing one column without the other would
    manufacture the desync the production code cannot produce.
    """
    from bot.db.secrets import api_key_hmac, encrypt_api_key

    conn = sqlite3.connect(str(db_path))
    try:
        # Both rows are blanked first: assigning the sibling a key the target
        # still holds would trip the UNIQUE constraint during setup.
        conn.execute(
            "UPDATE guilds SET api_key = '', api_key_hmac = NULL "
            "WHERE discord_server_id = ?",
            (SERVER_ID,),
        )
        for guild_id, plaintext in ((GUILD_TARGET, target), (GUILD_SIBLING, sibling)):
            conn.execute(
                "UPDATE guilds SET api_key = ?, api_key_hmac = ? "
                "WHERE discord_server_id = ? AND guild_id = ?",
                (
                    encrypt_api_key(plaintext, FERNET_KEY),
                    api_key_hmac(plaintext, FERNET_KEY),
                    SERVER_ID,
                    guild_id,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _capture(db_path: Path) -> dict:
    """Snapshot every declared universe slot."""
    conn = sqlite3.connect(str(db_path))
    try:
        api_key, hmac_value = conn.execute(
            "SELECT api_key, api_key_hmac FROM guilds "
            "WHERE discord_server_id = ? AND guild_id = ?",
            (SERVER_ID, GUILD_TARGET),
        ).fetchone()
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in _ROW_COUNT_SLOTS
        }
    finally:
        conn.close()
    return {"api_key": api_key, "api_key_hmac": hmac_value, **counts}


# ===========================================================================
# State-delta helpers — strict universe, one predicate per slot
# ===========================================================================

class _Unchanged:
    def __repr__(self) -> str:
        return "unchanged()"


def unchanged() -> _Unchanged:
    return _Unchanged()


def set_to(value):
    """The slot holds exactly `value` afterwards."""
    return ("set_to", value)


def rewritten_to(plaintext: str):
    """The slot holds a NEW Fernet ciphertext that decrypts to `plaintext`.

    Not `set_to`: Fernet is non-deterministic, so the ciphertext of a known
    plaintext is not a value a test can pin. Asserting the round-trip AND that
    the stored bytes differ from before is what distinguishes "the key was
    replaced" from "the column was left alone".
    """
    return ("rewritten_to", plaintext)


def _assert_state_delta(before: dict, after: dict, expected: dict) -> None:
    """Strict: every universe slot without a declared predicate is unchanged.

    Strict is the whole point. The bug class this guards is "the code did the
    right thing to the slot the test looked at, and something else to the one
    it did not" — an implicit-unchanged assertion over the declared universe
    turns that from invisible into a failure.
    """
    from bot.db.secrets import decrypt_api_key

    assert set(before) == set(UNIVERSE) == set(after), (
        "the capture drifted from the declared universe — a slot was added or "
        "removed without being declared, so nothing asserts on it"
    )
    for slot in UNIVERSE:
        predicate = expected.get(slot, unchanged())
        if isinstance(predicate, _Unchanged):
            assert after[slot] == before[slot], (
                f"{slot} moved during an operation that declared it unchanged: "
                f"{before[slot]!r} -> {after[slot]!r}"
            )
            continue
        kind, operand = predicate
        if kind == "set_to":
            assert after[slot] == operand, (
                f"{slot} is {after[slot]!r}, expected {operand!r}"
            )
        elif kind == "rewritten_to":
            assert after[slot] != before[slot], (
                f"{slot} was not rewritten — the write did not happen"
            )
            assert decrypt_api_key(after[slot], FERNET_KEY) == operand, (
                f"{slot} does not decrypt to the key that was installed"
            )
        else:  # pragma: no cover — a typo in a predicate name
            raise AssertionError(f"unknown predicate {kind!r} for slot {slot!r}")


def _forbidden_disclosures(api_key: str) -> dict[str, str]:
    """Everything a refusal is forbidden to carry, named for the failure message.

    The hmac and the ciphertext are the ones a plaintext-only assertion
    misses: they are what SQLAlchemy inlines into an `IntegrityError`, because
    the hmac is the column the violated constraint is on.
    """
    from bot.db.secrets import api_key_hmac, encrypt_api_key

    return {
        "plaintext key": api_key,
        "api_key_hmac": api_key_hmac(api_key, FERNET_KEY) or "",
        "Fernet ciphertext": encrypt_api_key(api_key, FERNET_KEY),
    }


# ===========================================================================
# Property 1 — AC-009.7: a write that would blank the row is refused whole
# ===========================================================================

@settings(max_examples=40, deadline=None)
@given(blank_key=blank_keys)
def test_a_key_write_that_would_blank_the_row_is_refused_and_changes_nothing(
    storage, blank_key: str,
):
    """No string that strips to nothing may reach the columns.

    Quantified rather than pinned because the erasure is not a property of
    `""` — it is a property of every key the guild cannot authenticate with.
    `""` blanks both columns outright; `" "` and `"\\n"` replace a working key
    with one that no probe will ever accept, which destroys the same thing and
    leaves a plausible-looking row behind. A guard written as `if not api_key`
    passes on the first example and fails on the rest.
    """
    _reset_keys(storage.db_path, target=TARGET_KEY, sibling=SIBLING_KEY)
    before = _capture(storage.db_path)

    with pytest.raises(ValueError):
        storage.repo.replace_guild_key(SERVER_ID, GUILD_TARGET, blank_key)

    _assert_state_delta(before, _capture(storage.db_path), expected={})


# ===========================================================================
# Property 2 — AC-009.1/AC-009.2 / KPI-6: a collision is typed, named, mute
# ===========================================================================

@settings(max_examples=40, deadline=None)
@given(shared_key=real_keys)
def test_a_key_a_sibling_already_holds_is_refused_typed_and_without_disclosure(
    storage, shared_key: str,
):
    """A UNIQUE violation on `api_key_hmac` becomes a domain refusal.

    Three claims, all on the same call, because a fix that satisfies one
    without the others is the failure mode: the exception must be a typed
    domain error (no raw `IntegrityError` leaves the adapter), it must name
    the guild that holds the key (otherwise the admin is told "no" and cannot
    act), and its rendered text must contain no key material and no SQL —
    `main.py`'s handler interpolates `{error}` into a Discord message and a
    log line, so whatever the exception says IS what is disclosed.
    """
    from bot.repository import GuildKeyAlreadyRegisteredError

    _reset_keys(storage.db_path, target=TARGET_KEY, sibling=shared_key)
    before = _capture(storage.db_path)

    with pytest.raises(GuildKeyAlreadyRegisteredError) as refusal:
        storage.repo.replace_guild_key(SERVER_ID, GUILD_TARGET, shared_key)

    assert refusal.value.guild_id == GUILD_SIBLING, (
        "the refusal does not name the guild that already holds the key, so "
        "the admin cannot act on it"
    )
    rendered = str(refusal.value)
    for label, material in _forbidden_disclosures(shared_key).items():
        assert material and material not in rendered, (
            f"the {label} is rendered by the refusal: {rendered!r}"
        )
    for sql_marker in ("INSERT INTO", "UPDATE ", "[parameters:", "IntegrityError"):
        assert sql_marker not in rendered, (
            f"raw SQL ({sql_marker!r}) is rendered by the refusal: {rendered!r}"
        )

    _assert_state_delta(before, _capture(storage.db_path), expected={})


# ===========================================================================
# Property 3 — ADR-006 D7: a legitimate write moves the two key slots together
# ===========================================================================

@settings(max_examples=40, deadline=None)
@given(new_key=real_keys)
def test_a_legitimate_key_write_moves_both_key_slots_and_nothing_else(
    storage, new_key: str,
):
    """The regression guard the two refusals must not eat.

    `api_key` and `api_key_hmac` are written in ONE transaction (ADR-006 D7):
    a row whose ciphertext and fingerprint disagree passes every read and
    fails every uniqueness check. And `replace_guild_key`'s whole
    justification over `save` is that dependent rows are byte-identical
    before and after — so the strict universe here is the assertion that the
    targeted swap stayed targeted, not decoration.
    """
    from bot.db.secrets import api_key_hmac

    _reset_keys(storage.db_path, target=TARGET_KEY, sibling=SIBLING_KEY)
    before = _capture(storage.db_path)

    storage.repo.replace_guild_key(SERVER_ID, GUILD_TARGET, new_key)

    _assert_state_delta(before, _capture(storage.db_path), expected={
        "api_key": rewritten_to(new_key),
        "api_key_hmac": set_to(api_key_hmac(new_key, FERNET_KEY)),
    })
