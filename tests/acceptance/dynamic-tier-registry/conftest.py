"""Fixtures for the `dynamic-tier-registry` acceptance suite.

Follows the precedent of `tests/acceptance/sqlite-backend/conftest.py` and
`tests/acceptance/guild-key-integrity/conftest.py`: plain pytest +
pytest-asyncio, real SQLite in `tmp_path`, real alembic, programmable doubles
at the Discord and Tacticus boundaries. The `.feature` files under
`acceptance/` are the scenario SSOT; the test modules are the executable specs.

What the doubles CANNOT model (Mandate 5 disclosure, self-review item 4):

  * `FakeLiveChannel` records sends and edits. It cannot produce a real Discord
    rate-limit, and it cannot reproduce the failure shape that actually
    duplicates a live board in public — a send that SUCCEEDS at Discord after
    the local state has concluded it failed. `SendFailure
    .SENT_THEN_PERSIST_FAILED` simulates the consequence, not the cause. Only
    the Slice 03 production run against a real channel closes that gap, which
    is why the slice brief requires it.
  * Nothing here can tell us Tacticus changed the shape of `set`, or shipped a
    rarity nobody has seen. A double returns what it is told. The instrument
    for that is `unrecognised_rarities` in production, not a test.
  * The suite runs one process and one cycle at a time. It cannot model two
    hourly refreshes overlapping, which is the other way a live board could
    duplicate. The rollover-race scenarios sequence the two paths inside one
    cycle instead, which is the interleaving the code can actually produce.
  * `MAX_CHOICES` is asserted against our own constant. Discord's real limit is
    Discord's, and a change to it reaches us as a rejected command sync, not as
    a failing test.
"""
from __future__ import annotations

# ──────────────────────────────────────────────────────────────────────────
# PRE-IMPORT env default. MUST precede any `import bot.*`.
#
# `bot/guilds.py` evaluates `repo = build_repo()` at IMPORT time, and Slice 07
# of `guild-key-integrity` made `build_repo` REFUSE to start when
# backend=sqlite is requested without a Fernet key. Defaulting to `json` here
# keeps collection working; per-test fixtures override the singleton by
# monkeypatching `bot.guilds.repo` directly. Same precedent as
# tests/acceptance/guild-key-integrity/conftest.py:49.
# ──────────────────────────────────────────────────────────────────────────
import os

os.environ.setdefault("SCRAPCODE_REPO_BACKEND", "json")

from pathlib import Path

import pytest

# Constants live in `tier_types`, not here, and the test modules import them
# from there. Two suites in this project ship a bare `conftest` module, so
# `sys.modules["conftest"]` holds whichever was collected last — and `SEASON`
# is 107 here and 106 in `guild-key-integrity`. A test module doing
# `from conftest import SEASON` in a combined run binds the wrong one silently
# and asserts against the wrong season. See the `tier_types` docstring.
from tier_types import (  # noqa: F401
    ENVIRONMENTS_YAML,
    GUILD_DM,
    GUILD_WB,
    MYTHIC_3_KEY,
    PRE_FEATURE_TIERS,
    PROD_SERVER_ID,
    REPO_ROOT,
    SEASON,
    Environment,
    LiveConfigShape,
    SendFailure,
    environment_names_from_devops_artifact,
)


# ---------------------------------------------------------------------------
# config.py env-var precondition.
#
# `config.py` reads UPDATE_CHANNEL_ID / REPLAY_INDEX_CHANNEL_ID at import time
# via `int(os.getenv(...))`, raising TypeError when either is unset. Any test
# importing a cog imports config transitively. Setting harmless values before
# collection means scenarios fail RED for the real reason (behaviour missing)
# rather than for an env-var TypeError — the wrong-reason RED the pre-DELIVER
# gate exists to catch.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True, scope="session")
def _config_env_precondition():
    os.environ.setdefault("UPDATE_CHANNEL_ID", "0")
    os.environ.setdefault("REPLAY_INDEX_CHANNEL_ID", "0")
    yield


# ---------------------------------------------------------------------------
# Storage — real SQLite, real alembic, real JSON tree
#
# NOTE: unlike `guild-key-integrity`, this feature adds NO alembic revision
# (ADR-009 D4 freezes stored tier keys). There is deliberately no
# `db_at_previous_head` fixture and no migration scenario: there is no
# migration to rehearse, and a fixture implying otherwise would invite someone
# to write one.
# ---------------------------------------------------------------------------

@pytest.fixture
def fernet_key() -> str:
    import base64
    return base64.urlsafe_b64encode(b"dynamic-tier-registry-distill-32!"[:32]).decode()


@pytest.fixture
def sqlite_db_path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "scrapcode.db"


@pytest.fixture
def env_vars(monkeypatch, sqlite_db_path: Path, fernet_key: str):
    monkeypatch.setenv("SCRAPCODE_REPO_BACKEND", "sqlite")
    monkeypatch.setenv("SCRAPCODE_DB_PATH", str(sqlite_db_path))
    monkeypatch.setenv("SCRAPCODE_DB_KEY", fernet_key)
    yield


@pytest.fixture
def json_env_vars(monkeypatch, tmp_path: Path):
    """The ADR-006 D9 rollback path: file-based storage, no Fernet key."""
    monkeypatch.setenv("SCRAPCODE_REPO_BACKEND", "json")
    monkeypatch.delenv("SCRAPCODE_DB_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "clusters").mkdir(exist_ok=True)
    yield tmp_path


def alembic_config(db_path: Path):
    from alembic.config import Config
    import bot.db
    db_pkg = Path(bot.db.__file__).parent
    cfg = Config(str(db_pkg / "alembic.ini"))
    cfg.set_main_option("script_location", str(db_pkg / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


@pytest.fixture
def migrated_db(sqlite_db_path: Path, env_vars) -> Path:
    from alembic import command
    sqlite_db_path.parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(alembic_config(sqlite_db_path), "head")
    return sqlite_db_path


@pytest.fixture(autouse=True)
def _repo_singleton_never_escapes_tmp_path(monkeypatch, tmp_path: Path):
    """Point `bot.guilds.repo` at tmp_path for EVERY test in this suite.

    `bot/guilds.py` builds its repository singleton at IMPORT time, long before
    any `monkeypatch.setenv` in a fixture runs. Without this, a test driving a
    cog reaches whichever repository was built at first import — and with no
    Fernet key present, the composition root's safety net is a
    `JsonClusterRepository` whose base_path is the REAL `clusters/` tree. A
    full-suite run then writes to production data.

    Autouse and unconditional, so a test that forgets to request a repository
    fixture still cannot reach the real tree. Inherited verbatim in intent from
    `guild-key-integrity` conftest, where this was found the hard way (UD-3).
    """
    import bot.guilds as guilds_mod
    import bot.permissions as permissions_mod
    from bot.repository import JsonClusterRepository
    tmp_repo = JsonClusterRepository(base_path=tmp_path / "clusters")
    monkeypatch.setattr(guilds_mod, "repo", tmp_repo)
    # `bot/permissions.py` does `from bot.guilds import repo` BY VALUE, so
    # `check_tier` reads the import-time singleton rather than the patched one.
    monkeypatch.setattr(permissions_mod, "repo", tmp_repo)
    yield


@pytest.fixture
def sqlite_repo(migrated_db: Path, fernet_key: str, monkeypatch):
    from bot.repository_sqlalchemy import SqlAlchemyClusterRepository
    repo = SqlAlchemyClusterRepository(db_path=str(migrated_db), fernet_key=fernet_key)
    import bot.guilds as guilds_mod
    monkeypatch.setattr(guilds_mod, "repo", repo)
    return repo


@pytest.fixture
def json_repo(json_env_vars, monkeypatch):
    from bot.repository import JsonClusterRepository
    repo = JsonClusterRepository()
    import bot.guilds as guilds_mod
    monkeypatch.setattr(guilds_mod, "repo", repo)
    return repo


@pytest.fixture
def registered_guilds(sqlite_repo):
    """`Given a registered guild with a healthy Tacticus key`.

    Two guilds, so scenarios about one guild not affecting another have
    something to say.
    """
    from bot.guilds import save_guilds
    save_guilds(PROD_SERVER_ID, {
        GUILD_WB: {
            "name": "Word Bearers",
            "api_key": "wb-key",
            "role_id": 1,
            "notification_channel_id": 4242,
            "member_role_ids": [],
        },
        GUILD_DM: {
            "name": "Dark Mechanicum",
            "api_key": "dm-key",
            "role_id": 2,
            "notification_channel_id": None,
            "member_role_ids": [],
        },
    })
    return PROD_SERVER_ID


# ---------------------------------------------------------------------------
# The Tacticus entry builder
# ---------------------------------------------------------------------------

@pytest.fixture
def make_entry():
    """A RAW Tacticus entry — `rarity` and `set` exactly as given.

    Deliberately NOT `sqlite-backend`'s `make_tacticus_entry`, which stamps
    `entry["tier_key"] = get_tier_key(entry)` on the way out. That is correct
    for a fixture feeding the repository, and wrong for every scenario in this
    suite: the parse rule is the thing under test, so a builder that runs it
    for us would be asserting the parser against itself.

    `set_` is passed through UNTOUCHED, including `None`, `"two"` and `-1` —
    the malformed cases exist to prove the counters, and a builder that
    sanitised them would make them unwritable.
    """
    _SENTINEL = object()

    def _make(*, rarity="Mythic", set_=_SENTINEL, damage_type="Battle",
              unit_id="Avatar", encounter_index=0, damage=12000,
              user_id="tacticus-uid-001", completed_on="2026-08-15T10:00:00Z",
              encounter_type=None, hero_details=None, machine_of_war=None):
        entry = {
            "unitId": unit_id,
            "encounterIndex": encounter_index,
            "rarity": rarity,
            "damageType": damage_type,
            "damageDealt": damage,
            "userId": user_id,
            "completedOn": completed_on,
            "encounterType": encounter_type or damage_type,
            "heroDetails": hero_details if hero_details is not None else [{"unitId": "Aethana"}],
            "machineOfWarDetails": machine_of_war,
        }
        # `set` ABSENT is a real vendor case and is not the same as `set: null`.
        # The sentinel is what lets a scenario express the difference.
        if set_ is not _SENTINEL:
            entry["set"] = set_
        return entry
    return _make


@pytest.fixture
def api_response(make_entry):
    """`{"entries": [...]}` — the shape `process_api_response` consumes."""
    def _make(entries):
        return {"entries": list(entries)}
    return _make


@pytest.fixture
def seed_hits(sqlite_repo, registered_guilds):
    """Write rows at a GIVEN tier key, bypassing the parser.

    The `Given rows exist with tier_key "Mythic_2"` precondition. Bypassing the
    parser is the point: Slice 02's and Slice 04's scenarios are about the READ
    path, and routing their setup through the rule they are meant to be
    independent of would couple them to it.
    """
    def _seed(tier_key: str, *, guild_id: str = GUILD_WB, season: int = SEASON,
              count: int = 3, damage_type: str = "Battle"):
        import bot.guilds as guilds_mod
        entries = []
        for i in range(count):
            entries.append({
                "unitId": "Avatar",
                "encounterIndex": 0,
                "rarity": "Mythic",
                "set": 2,
                "damageType": damage_type,
                "damage": 12000 - (i * 100),
                "damageDealt": 12000 - (i * 100),
                "userId": f"tacticus-uid-{i:03d}",
                "completedOn": "2026-08-15T10:00:00Z",
                "encounterType": damage_type,
                "heroDetails": [{"unitId": "Aethana"}],
                "machineOfWarDetails": None,
                "tier_key": tier_key,
            })
        if damage_type == "Bomb":
            guilds_mod.repo.upsert_bomb_hits(PROD_SERVER_ID, guild_id, season, entries)
        else:
            guilds_mod.repo.upsert_battle_hits(PROD_SERVER_ID, guild_id, season, entries)
        return entries
    return _seed


# ---------------------------------------------------------------------------
# Discord doubles
# ---------------------------------------------------------------------------

class FakeChannel:
    """Captures posted message text so a `Then` can assert on it — including
    asserting that NOTHING was posted, which is AC-002.3's whole point."""

    def __init__(self, channel_id: int) -> None:
        self.id = channel_id
        self.messages: list[str] = []

    async def send(self, content: str = "", **kwargs):
        embed = kwargs.get("embed")
        self.messages.append(content or (getattr(embed, "description", "") or ""))
        return _FakeMessage(len(self.messages))

    @property
    def text(self) -> str:
        return "\n".join(self.messages)


class _FakeMessage:
    def __init__(self, message_id: int) -> None:
        self.id = message_id
        self.content = ""
        self.edits: list[str] = []

    async def edit(self, *, content: str = "", **kwargs):
        self.content = content
        self.edits.append(content)


class FakeLiveChannel(FakeChannel):
    """A live-leaderboard channel that can refuse a send.

    Records sends AND edits separately, because Slice 03's whole question is
    which of the two happened. An implementation that edits an existing message
    when it should have sent a new one, or sends when it should have edited,
    produces a board that looks right for one cycle and wrong forever after.

    `fail_on` programs a refusal for a specific message index so a scenario can
    fail the SECOND of three sends — the partial-failure case, which is the one
    that leaves the `messages` map inconsistent.
    """

    def __init__(self, channel_id: int) -> None:
        super().__init__(channel_id)
        self.sent: list[_FakeMessage] = []
        self._messages_by_id: dict[int, _FakeMessage] = {}
        self.failure: SendFailure | None = None
        self.fail_after: int | None = None

    def program_failure(self, failure: SendFailure, *, after: int = 0) -> None:
        self.failure = failure
        self.fail_after = after

    async def send(self, content: str = "", **kwargs):
        import discord
        if self.failure is not None and len(self.sent) >= (self.fail_after or 0):
            if self.failure is SendFailure.FORBIDDEN:
                raise discord.Forbidden(_FakeResponse(403), "missing permissions")
            if self.failure is SendFailure.RATE_LIMITED:
                raise discord.HTTPException(_FakeResponse(429), "rate limited")
            # SENT_THEN_PERSIST_FAILED: the send SUCCEEDS. The refusal is
            # injected by the scenario at the persist seam, not here — that is
            # the distinction the enum exists to preserve.
        msg = _FakeMessage(1000 + len(self.sent))
        msg.content = content
        self.sent.append(msg)
        self._messages_by_id[msg.id] = msg
        self.messages.append(content)
        return msg

    async def fetch_message(self, message_id: int):
        import discord
        if message_id not in self._messages_by_id:
            raise discord.NotFound(_FakeResponse(404), "unknown message")
        return self._messages_by_id[message_id]

    def adopt(self, message_id: int, content: str = "") -> _FakeMessage:
        """Pretend a message with this id already exists in the channel.

        For `Given a live leaderboard config whose messages already cover
        Legendary 1..Mythic 2` — the state a real channel is in before
        reconciliation runs for the first time.
        """
        msg = _FakeMessage(message_id)
        msg.content = content
        self._messages_by_id[message_id] = msg
        return msg


class _FakeResponse:
    """Minimal stand-in for the `aiohttp` response `discord.HTTPException` wants."""

    def __init__(self, status: int) -> None:
        self.status = status
        self.reason = ""


@pytest.fixture
def update_channel() -> FakeChannel:
    return FakeChannel(channel_id=1)


@pytest.fixture
def live_channel() -> FakeLiveChannel:
    return FakeLiveChannel(channel_id=2)


@pytest.fixture
def live_config():
    """Build a live-leaderboard config row for either key shape.

    `covering` names the tier VALUES that already have a message id, so a
    scenario states the gap it means rather than implying it by omission.
    """
    def _make(shape: LiveConfigShape, *, channel_id: int = 2, season: int = SEASON,
              covering: tuple[str, ...] | None = None, guild_id: str = GUILD_WB):
        keys = covering if covering is not None else tuple(k for k, _ in PRE_FEATURE_TIERS)
        key = f"guild:{guild_id}" if shape is LiveConfigShape.PER_GUILD else "cluster"
        return {
            key: {
                "channel_id": channel_id,
                "season": season,
                "messages": {value: 1000 + i for i, value in enumerate(keys)},
            }
        }
    return _make


@pytest.fixture
def live_config_missing_mythic_3(live_config):
    """The `live-board-incomplete` environment, per-guild shape.

    Covers the seven pre-feature tiers and NOT `Mythic_2` — which is exactly
    the state a production server is in the hour after Slice 02 registers the
    tier.
    """
    return live_config(
        LiveConfigShape.PER_GUILD,
        covering=tuple(k for k, _ in PRE_FEATURE_TIERS),
    )


# ---------------------------------------------------------------------------
# Structured-log capture (the KPI instrument)
# ---------------------------------------------------------------------------

@pytest.fixture
def cycle_events(caplog):
    """A reader over `auto_update.cycle` / `live_board.*` records.

    The queries in `docs/product/kpi-contracts.yaml` grep these exact event
    names and read these exact fields. Asserting on them here is what keeps the
    documented dashboard and the implementation in step — a renamed event or a
    dropped field breaks a test before it breaks the operator's grep, which is
    the whole reason the contract file names emitters and fields rather than
    just targets.
    """
    import logging
    caplog.set_level(logging.DEBUG)

    class Reader:
        @staticmethod
        def named(event: str) -> list:
            return [r for r in caplog.records if getattr(r, "event", None) == event]

        @staticmethod
        def latest(event: str):
            records = Reader.named(event)
            assert records, f"no `{event}` record was emitted"
            return records[-1]

        @staticmethod
        def clear() -> None:
            """Forget everything captured so far.

            For multi-cycle scenarios asserting about the SECOND cycle only.
            Without it, a scenario that clears the channel between cycles still
            sees cycle one's records and asserts against both.
            """
            caplog.clear()

        @staticmethod
        def all_events() -> list[str]:
            return [e for e in (getattr(r, "event", None) for r in caplog.records) if e]

        @staticmethod
        def any_named(*events: str) -> bool:
            return bool(set(Reader.all_events()) & set(events))

    return Reader


# ---------------------------------------------------------------------------
# Environment parametrization (Mandate 4)
#
# `environment_names_from_devops_artifact` lives in `tier_types` and is
# re-exported above, so the traceability test imports it from there rather than
# from a module name three suites share.
# ---------------------------------------------------------------------------

@pytest.fixture(params=list(Environment), ids=lambda e: e.value)
def environment(request) -> Environment:
    return request.param
