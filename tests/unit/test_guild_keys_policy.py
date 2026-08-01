"""Unit tests for the guild key policy chokepoint (`bot/guild_keys.py`).

Why these live at the unit layer rather than in the acceptance suite: every
Slice-01 acceptance scenario for this module drives `_run_hourly_cycle`, which
cannot work until `bot/cogs/tasks_cog.py` is wired in step 03-03. The
behaviours below are therefore driven here, through the chokepoint's own
driving ports — `verify_and_resolve` and `active_key` — with REAL
collaborators:

  * a real `SqlAlchemyClusterRepository` on a real alembic-migrated SQLite
    database in `tmp_path`, rebound onto `bot.guilds.repo` (see
    `_repo_singleton_never_escapes_tmp_path` below and its acceptance-suite
    twin — the singleton is built at IMPORT time, so `monkeypatch.setenv` in a
    test is far too late to redirect it);
  * a real `logging` logger, read back through `caplog`;
  * a fake at the `bot.services.tacticus.guild_client` seam, whose answers are
    built by the REAL `parse_guild_snapshot` / the real dead-key taxonomy so a
    wrong classification cannot be hand-stubbed into passing.

No Hypothesis: it is not in `requirements.txt` and this feature adds no
dependency, so equivalence classes are covered by `parametrize` over the
production enums (`DEAD_KEY_STATUSES`, the transport-failure set) rather than
by generated inputs. The state-delta discipline is kept by comparing the whole
frozen `GuildBinding` with `==` — that is a strict universe over all eight
slots, not a single-property assert.
"""
from __future__ import annotations

import os

# `bot.guilds` evaluates `repo = build_repo()` at import time and reads the
# environment at that moment. Pin a harmless backend before any `bot.*` import
# so collection cannot construct a repository pointed at a live tree. Same
# precedent as `tests/unit/test_registration_validate_keys.py`.
os.environ.setdefault("SCRAPCODE_REPO_BACKEND", "json")

import base64
import inspect
import logging
from pathlib import Path

import pytest

from bot.services.tacticus.guild_client import (
    DEAD_KEY_STATUSES,
    GuildIdentity,
    GuildSnapshot,
    KeyStatus,
    ProbeOutcome,
    parse_guild_snapshot,
)

SERVER_ID = 1458181638453203099
GUILD_WB = "word_bearers"
WB_KEY = "wb-key"

FERNET_KEY = base64.urlsafe_b64encode(b"guild-key-integrity-unit-tests!!"[:32]).decode()

# The two identities from the 2026-07-28 incident. Guild identifiers, not
# credentials — Tacticus returns them to any holder of a key for the guild.
WORD_BEARERS = GuildIdentity(
    uuid="b64bdba4-36ac-4229-bd29-4b7b6ce7f44f",
    tag="EUVQZ",
    name="【UNDV】Word Bearers",
)
DARK_MECHANICUM = GuildIdentity(
    uuid="d71d583f-c970-4493-936f-178c21ab844c",
    tag="PXGQW",
    name="【UNDV】Dark Mechanicum",
)


# ---------------------------------------------------------------------------
# Storage — real SQLite, real alembic, inside tmp_path
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _repo_singleton_never_escapes_tmp_path(monkeypatch, tmp_path: Path):
    """Point `bot.guilds.repo` at `tmp_path` for EVERY test in this module.

    Unconditional and autouse: `JsonClusterRepository._server_path` mkdirs on
    READ as well as on write, so a single unguarded `load_guilds` against the
    import-time singleton creates `clusters/<server-id>/` at the repository
    root — on a machine holding a live JSON tree that is a write into
    production data.
    """
    import bot.guilds as guilds_mod
    from bot.repository import JsonClusterRepository
    monkeypatch.setattr(
        guilds_mod, "repo", JsonClusterRepository(base_path=tmp_path / "clusters")
    )
    yield


@pytest.fixture
def sqlite_repo(monkeypatch, tmp_path: Path):
    """A real repository on a real migrated database, bound to `bot.guilds`."""
    from alembic import command
    from alembic.config import Config

    import bot.db
    from bot.repository_sqlalchemy import SqlAlchemyClusterRepository

    db_path = tmp_path / "data" / "scrapcode.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    db_pkg = Path(bot.db.__file__).parent
    cfg = Config(str(db_pkg / "alembic.ini"))
    cfg.set_main_option("script_location", str(db_pkg / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "head")

    monkeypatch.setenv("SCRAPCODE_DB_KEY", FERNET_KEY)
    repo = SqlAlchemyClusterRepository(db_path=str(db_path), fernet_key=FERNET_KEY)
    import bot.guilds as guilds_mod
    monkeypatch.setattr(guilds_mod, "repo", repo)
    return repo


@pytest.fixture
def registered_guild(sqlite_repo):
    """`Given a registered guild` — no binding yet, which is the state every
    guild is in on the day Slice 01 deploys (DDD-8)."""
    from bot.guilds import save_guilds
    save_guilds(SERVER_ID, {
        GUILD_WB: {
            "name": "Word Bearers",
            "api_key": WB_KEY,
            "role_id": 1,
            "notification_channel_id": 4242,
            "member_role_ids": [],
        },
    })
    return SERVER_ID


@pytest.fixture
def bound_guild(registered_guild):
    """`Given a guild already bound to Word Bearers`."""
    from bot.guilds import save_guild_binding
    from bot.repository import GuildBinding
    binding = GuildBinding(
        tacticus_guild_id=WORD_BEARERS.uuid,
        tacticus_guild_tag=WORD_BEARERS.tag,
        tacticus_guild_name=WORD_BEARERS.name,
        identity_bound_at="2026-07-31T04:00:00Z",
    )
    save_guild_binding(SERVER_ID, GUILD_WB, binding)
    return binding


# ---------------------------------------------------------------------------
# The guild-service seam
# ---------------------------------------------------------------------------

def _ok_payload(identity: GuildIdentity, members=("u1", "u2"),
                drop_fields: tuple[str, ...] = ()) -> dict:
    guild: dict = {
        "guildId": identity.uuid,
        "guildTag": identity.tag,
        "name": identity.name,
        "members": [{"userId": m} for m in members],
    }
    for field in drop_fields:
        guild.pop(field, None)
    return {"guild": guild}


@pytest.fixture
def guild_service(monkeypatch):
    """Programmable stand-in for `fetch_guild_snapshot`, recording every call.

    Records the keys it was handed so a test can assert a call was NOT made —
    the actual observable for "no network happened", which asserting on stored
    state alone would not catch.
    """
    from bot.services.tacticus import guild_client

    class FakeGuildService:
        def __init__(self) -> None:
            self.answer: GuildSnapshot | None = None
            self.calls: list[str] = []

        def returns_ok(self, identity: GuildIdentity, members=("u1", "u2"),
                       drop_fields: tuple[str, ...] = ()) -> None:
            # Built by the REAL parser, so a scenario cannot assert against a
            # classification the production code would not actually produce.
            self.answer = parse_guild_snapshot(
                _ok_payload(identity, members, drop_fields)
            )

        def returns_status(self, status: int) -> None:
            if status in DEAD_KEY_STATUSES:
                self.answer = GuildSnapshot(
                    outcome=ProbeOutcome.DEAD,
                    status=status,
                    error=f"the key was refused with HTTP {status}",
                )
                return
            self.answer = GuildSnapshot(
                outcome=ProbeOutcome.UNREACHABLE,
                status=status,
                error=f"the guild service answered HTTP {status}",
            )

        def returns_transport_failure(self, name: str) -> None:
            self.answer = GuildSnapshot(
                outcome=ProbeOutcome.UNREACHABLE, error=name
            )

        async def __call__(self, api_key: str) -> GuildSnapshot:
            self.calls.append(api_key)
            if self.answer is None:
                raise AssertionError(
                    "the guild service was called on a path that declared no "
                    "answer — the test is exercising something it did not set up"
                )
            return self.answer

    fake = FakeGuildService()
    monkeypatch.setattr(guild_client, "fetch_guild_snapshot", fake)
    return fake


@pytest.fixture
def key_events(caplog):
    """Reader over the `guild.key.*` records — the KPI instrument."""
    caplog.set_level(logging.DEBUG)

    class Reader:
        @staticmethod
        def named(event: str) -> list:
            return [r for r in caplog.records if getattr(r, "event", None) == event]

        @staticmethod
        def all_events() -> list[str]:
            return [e for e in (getattr(r, "event", None) for r in caplog.records) if e]

        @staticmethod
        def any_named(*events: str) -> bool:
            return bool(set(Reader.all_events()) & set(events))

        @staticmethod
        def records() -> list:
            return [r for r in caplog.records if getattr(r, "event", "").startswith("guild.key.")]

    return Reader


def _binding():
    from bot.guilds import load_guild_binding
    return load_guild_binding(SERVER_ID, GUILD_WB)


# ===========================================================================
# Criterion 1 — the first successful probe adopts the identity
# ===========================================================================

async def test_first_successful_probe_adopts_the_identity_and_stamps_the_date(
    registered_guild, guild_service, key_events
):
    """Trust-on-first-use (DDD-8). The announcement IS the verification step:
    there is no historical record to reconstruct a binding from, so the bind
    has to be said out loud exactly once."""
    from datetime import datetime

    from bot.guild_keys import verify_and_resolve

    guild_service.returns_ok(WORD_BEARERS)
    before = _binding()
    assert before.is_unbound, "the fixture is not exercising the TOFU path"

    snapshot = await verify_and_resolve(SERVER_ID, GUILD_WB)

    after = _binding()
    assert after.tacticus_guild_id == WORD_BEARERS.uuid
    assert after.tacticus_guild_tag == WORD_BEARERS.tag
    assert after.tacticus_guild_name == WORD_BEARERS.name
    assert after.identity_bound_at is not None
    assert after.key_status == KeyStatus.ACTIVE.value
    assert snapshot.outcome is ProbeOutcome.MATCH

    (bound,) = key_events.named("guild.key.bound")
    assert bound.tacticus_guild_id == WORD_BEARERS.uuid

    # KPI-1's detection latency is `alerted_at − last_probe_ok_at`, so the
    # probe record's timestamp has to be a real, parseable instant — three
    # correctly-named records with an unparseable `ts` reproduce the
    # unfalsifiable-metric failure in a new place (DEVOPS U2).
    (probe_ok,) = key_events.named("guild.key.probe.ok")
    assert probe_ok.tacticus_guild_id == WORD_BEARERS.uuid
    assert datetime.fromisoformat(probe_ok.ts).tzinfo is not None
    assert len(probe_ok.ts) <= 32, "the timestamp does not fit the String(32) shape"


async def test_a_later_probe_refreshes_the_date_and_does_not_re_announce(
    bound_guild, guild_service, key_events
):
    """Criterion 2. An announcement on every cycle is alert fatigue by
    construction, and would bury the one announcement that matters."""
    from bot.guild_keys import verify_and_resolve

    guild_service.returns_ok(WORD_BEARERS)

    await verify_and_resolve(SERVER_ID, GUILD_WB)

    after = _binding()
    assert after.identity_bound_at > bound_guild.identity_bound_at
    assert after.tacticus_guild_id == WORD_BEARERS.uuid
    assert key_events.named("guild.key.bound") == [], (
        "a guild that was already bound was announced again"
    )
    assert key_events.named("guild.key.probe.ok")
    assert not key_events.any_named("guild.key.mismatch")


# ===========================================================================
# Criterion 3 — the comparison is on guildId ALONE
# ===========================================================================

@pytest.mark.parametrize(
    "resolved",
    [
        GuildIdentity(WORD_BEARERS.uuid, "WBRRS", WORD_BEARERS.name),
        GuildIdentity(WORD_BEARERS.uuid, WORD_BEARERS.tag, "【UNDV】Word Bearers Reborn"),
        GuildIdentity(WORD_BEARERS.uuid, "WBRRS", "【UNDV】Word Bearers Reborn"),
        GuildIdentity(WORD_BEARERS.uuid, None, None),
    ],
    ids=["retagged", "renamed", "both", "display-fields-absent"],
)
async def test_a_retag_or_a_rename_is_not_a_mismatch(
    bound_guild, guild_service, key_events, resolved: GuildIdentity
):
    """Criterion 3 / DDD-1. Guilds retag and rename routinely. If either
    tripped the lock, Slice 03 would quarantine healthy guilds on a cosmetic
    change and the operator would learn to ignore the alert."""
    from bot.guild_keys import verify_and_resolve

    guild_service.returns_ok(resolved)

    snapshot = await verify_and_resolve(SERVER_ID, GUILD_WB)

    assert snapshot.outcome is ProbeOutcome.MATCH
    assert not key_events.any_named("guild.key.mismatch")

    after = _binding()
    assert after.tacticus_guild_id == WORD_BEARERS.uuid
    assert after.tacticus_guild_tag == resolved.tag
    assert after.tacticus_guild_name == resolved.name


async def test_a_different_guild_id_is_a_mismatch_that_still_returns_the_data(
    bound_guild, guild_service, key_events
):
    """THE INCIDENT, replayed. Slice 01 reports and does not block, so the
    snapshot still carries the members — `test_slice_01_still_ingests_on_a_
    mismatch` is the acceptance twin of this assertion."""
    from bot.guild_keys import verify_and_resolve

    guild_service.returns_ok(DARK_MECHANICUM, members=("x1", "x2"))
    before = _binding()

    snapshot = await verify_and_resolve(SERVER_ID, GUILD_WB)

    assert snapshot.outcome is ProbeOutcome.MISMATCH
    assert snapshot.members == frozenset({"x1", "x2"}), (
        "Slice 01 reports without blocking — dropping the members here would "
        "turn a report into an enforcement the recovery path cannot undo yet"
    )

    (mismatch,) = key_events.named("guild.key.mismatch")
    assert mismatch.bound_id == WORD_BEARERS.uuid
    assert mismatch.observed_id == DARK_MECHANICUM.uuid
    assert mismatch.observed_tag == DARK_MECHANICUM.tag
    assert mismatch.observed_name == DARK_MECHANICUM.name

    assert _binding() == before, "a mismatch rewrote the binding"
    assert not key_events.any_named("guild.key.probe.ok", "guild.key.bound")
    assert not key_events.any_named("guild.key.quarantined")


# ===========================================================================
# Criterion 4 — dead / unreachable / unverifiable leave the binding alone
# ===========================================================================

async def test_a_response_without_a_guild_id_is_unverifiable_and_never_uses_the_tag(
    bound_guild, guild_service, key_events
):
    """DDD-10, the load-bearing negative. The failure this guards is not "we
    got it wrong", it is "we quietly got weaker": a fallback to comparing
    `guildTag` would leave every alert green while the guarantee evaporated —
    and both guilds in the incident share the 【UNDV】 prefix, so the tag
    comparison is exactly the check that would have looked reassuring."""
    from bot.guild_keys import verify_and_resolve

    # A well-formed body with the tag and the name present, and only the
    # identifier missing: the tag IS available to fall back to.
    guild_service.returns_ok(WORD_BEARERS, drop_fields=("guildId",))
    before = _binding()

    snapshot = await verify_and_resolve(SERVER_ID, GUILD_WB)

    assert snapshot.outcome is ProbeOutcome.UNVERIFIABLE
    (unverifiable,) = key_events.named("guild.key.unverifiable")
    assert unverifiable.reason == "guildId_absent"
    assert unverifiable.levelno == logging.ERROR, "an unverifiable probe is a loud alert"

    assert _binding() == before
    assert not key_events.any_named(
        "guild.key.mismatch", "guild.key.bound", "guild.key.quarantined",
        "guild.key.probe.ok",
    )


@pytest.mark.parametrize("status", sorted(DEAD_KEY_STATUSES), ids=lambda s: str(s))
async def test_a_refused_key_is_dead_and_is_never_quarantined(
    bound_guild, guild_service, key_events, status: int
):
    """Criterion 4 / DDD-6. A revoked key returns no data, so there is nothing
    to contaminate; quarantining it would add a recovery step for zero
    safety."""
    from bot.guild_keys import verify_and_resolve

    guild_service.returns_status(status)
    before = _binding()

    snapshot = await verify_and_resolve(SERVER_ID, GUILD_WB)

    assert snapshot.outcome is ProbeOutcome.DEAD
    (dead,) = key_events.named("guild.key.dead")
    assert dead.status == status
    assert dead.levelno == logging.ERROR

    assert _binding() == before
    assert _binding().key_status == KeyStatus.ACTIVE.value
    assert not key_events.any_named("guild.key.quarantined", "guild.key.bound")


@pytest.mark.parametrize(
    "failure", ["timeout", "connect_error", "http_500", "http_503"]
)
async def test_an_unreachable_probe_leaves_the_binding_byte_identical(
    bound_guild, guild_service, key_events, failure: str
):
    """Criterion 4 / DDD-6 — the decision that keeps a Tacticus outage from
    quarantining the entire cluster.

    Byte-identical, not "still active": an implementation that rewrote
    `identity_bound_at` on a failed probe would report a fresh verification
    date for a check that never happened, which is worse than no date. `==` on
    the frozen `GuildBinding` is the whole-universe assertion that says so.
    """
    from bot.guild_keys import verify_and_resolve

    if failure.startswith("http_"):
        guild_service.returns_status(int(failure.removeprefix("http_")))
    else:
        guild_service.returns_transport_failure(failure)
    before = _binding()

    snapshot = await verify_and_resolve(SERVER_ID, GUILD_WB)

    assert snapshot.outcome is ProbeOutcome.UNREACHABLE
    (unreachable,) = key_events.named("guild.key.unreachable")
    assert unreachable.levelno == logging.WARNING
    assert unreachable.reason

    assert _binding() == before
    assert not key_events.any_named(
        "guild.key.mismatch", "guild.key.quarantined", "guild.key.bound",
        "guild.key.probe.ok",
    )


async def test_a_guild_with_no_key_is_unreachable_and_is_never_probed(
    sqlite_repo, guild_service, key_events
):
    """A registered guild whose `api_key` is empty cannot be verified, and the
    one thing that must not happen is a probe with an empty credential.
    UNREACHABLE and not DEAD: nothing was refused, there is simply nothing to
    ask with, and the correct response is to retry next cycle."""
    from bot.guild_keys import verify_and_resolve
    from bot.guilds import save_guilds

    save_guilds(SERVER_ID, {
        GUILD_WB: {"name": "Word Bearers", "api_key": "", "role_id": 1,
                   "notification_channel_id": None, "member_role_ids": []},
    })
    before = _binding()

    snapshot = await verify_and_resolve(SERVER_ID, GUILD_WB)

    assert snapshot.outcome is ProbeOutcome.UNREACHABLE
    assert guild_service.calls == [], "an empty key was sent to Tacticus"
    assert key_events.named("guild.key.unreachable")
    assert _binding() == before


# ===========================================================================
# Criterion 5 — `active_key` is sync and makes no network call
# ===========================================================================

def test_active_key_is_synchronous():
    """DDD-7. Season discovery iterates candidate guilds and must be able to
    skip one cheaply; an async signature forces every candidate onto the event
    loop and invites a probe per candidate."""
    from bot.guild_keys import active_key
    assert not inspect.iscoroutinefunction(active_key)


@pytest.mark.parametrize(
    "key_status, expected_key",
    [(KeyStatus.ACTIVE.value, WB_KEY), (KeyStatus.QUARANTINED.value, None)],
    ids=["active", "quarantined"],
)
def test_active_key_reads_storage_only_and_refuses_a_quarantined_guild(
    bound_guild, guild_service, key_status: str, expected_key: str | None
):
    from dataclasses import replace

    from bot.guild_keys import active_key
    from bot.guilds import save_guild_binding

    save_guild_binding(
        SERVER_ID, GUILD_WB, replace(bound_guild, key_status=key_status)
    )

    assert active_key(SERVER_ID, GUILD_WB) == expected_key
    assert guild_service.calls == [], (
        "active_key made a network call — DDD-7 exists precisely so it does not"
    )


def test_active_key_is_none_for_a_guild_that_is_not_registered(sqlite_repo):
    from bot.guild_keys import active_key
    assert active_key(SERVER_ID, "no_such_guild") is None


# ===========================================================================
# KPI-6 — zero key values in logs, BY CONSTRUCTION
# ===========================================================================

@pytest.mark.parametrize(
    "program",
    [
        lambda s: s.returns_ok(WORD_BEARERS),
        lambda s: s.returns_ok(DARK_MECHANICUM),
        lambda s: s.returns_ok(WORD_BEARERS, drop_fields=("guildId",)),
        lambda s: s.returns_status(401),
        lambda s: s.returns_transport_failure("timeout"),
    ],
    ids=["match", "mismatch", "unverifiable", "dead", "unreachable"],
)
async def test_no_emitted_record_ever_carries_the_key(
    bound_guild, guild_service, key_events, program
):
    """KPI-6 is "0 key values in logs or Discord" and holds BY CONSTRUCTION,
    not by filtering: no `guild.key.*` record may carry an `api_key` field at
    all, and none may contain the key's characters anywhere in its message."""
    from bot.guild_keys import verify_and_resolve

    program(guild_service)
    await verify_and_resolve(SERVER_ID, GUILD_WB)

    records = key_events.records()
    assert records, "the probe emitted nothing at all"
    for record in records:
        assert not hasattr(record, "api_key"), (
            f"{record.event} carries an api_key field"
        )
        assert WB_KEY not in record.getMessage(), (
            f"{record.event} leaked the key into its message"
        )
        # Every record in the catalog carries these four (kpi-contracts.yaml
        # `required_fields`); a KPI query cannot correlate without them.
        for field in ("ts", "server_id", "guild_id", "key_ref"):
            assert hasattr(record, field), f"{record.event} is missing {field}"


def test_key_ref_is_the_first_eight_characters_of_the_hmac():
    """Not key material: `api_key_hmac` is an HKDF-SHA256 derivation keyed by
    SCRAPCODE_DB_KEY and is not reversible without that key. Eight characters
    is enough to follow one key across bind → mismatch → quarantine →
    update."""
    from bot.guild_keys import key_ref
    assert key_ref("0123456789abcdef" * 4) == "01234567"
    assert len(key_ref("a" * 64)) == 8


async def test_the_key_ref_is_stable_across_every_record_of_one_probe(
    bound_guild, guild_service, key_events, monkeypatch
):
    """The correlation ID only correlates if it is the same value everywhere
    the same key appears."""
    from bot.guild_keys import verify_and_resolve

    guild_service.returns_ok(WORD_BEARERS)
    await verify_and_resolve(SERVER_ID, GUILD_WB)

    refs = {r.key_ref for r in key_events.records()}
    assert len(refs) == 1
    (ref,) = refs
    assert len(ref) == 8 and ref != WB_KEY[:8]


# ===========================================================================
# Enforcement is Slice 03 — and says so out loud
# ===========================================================================

async def test_asking_for_enforcement_fails_loudly_instead_of_pretending(
    bound_guild, guild_service
):
    """`enforce=True` is Slice 03. Shipping the block before the recovery path
    (`/update_guild_key`, Slice 02) would make the first quarantine
    unrecoverable without an SSH session (ADR-008 D3). A caller that asks for
    protection this slice cannot give must be told, not quietly served an
    unenforced result."""
    from bot.guild_keys import verify_and_resolve

    guild_service.returns_ok(DARK_MECHANICUM)
    with pytest.raises(NotImplementedError):
        await verify_and_resolve(SERVER_ID, GUILD_WB, enforce=True)
