"""Fixtures for the `guild-key-integrity` acceptance suite.

Follows the precedent set by `tests/acceptance/sqlite-backend/conftest.py`:
plain pytest + pytest-asyncio, real SQLite in `tmp_path`, real alembic, and a
programmable double at the httpx boundary. The `.feature` files under
`acceptance/` are the scenario SSOT; these modules are the executable specs.

What the doubles CANNOT model (Mandate 5 disclosure, self-review item 4):

  * `fake_guild_service` returns whatever it is told to. It cannot discover
    that Tacticus renamed or dropped `guildId` — only the `@requires_external`
    scenarios in `tacticus-guild-contract.feature` can, and only when they are
    actually run. This is the residual risk ADR-008 D1 accepts.
  * `fake_discord_channel` records message text. It cannot catch a Discord
    rate-limit, a permissions failure on the ping channel, or an embed that
    exceeds the field limit.
  * The suite runs one process. It cannot model two hourly cycles overlapping,
    which is why the alert-suppression scenarios advance a clock rather than
    racing.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from domain_types import (
    DARK_MECHANICUM,
    WORD_BEARERS,
    Environment,
    GuildIdentity,
    KeyStatus,
    ProbeOutcome,
)

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[3]
ENVIRONMENTS_YAML = (
    REPO_ROOT / "docs" / "feature" / "guild-key-integrity" / "environments.yaml"
)

PROD_SERVER_ID = 1458181638453203099
GUILD_WB = "word_bearers"
GUILD_DM = "dark_mechanicum"
SEASON = 106


# ---------------------------------------------------------------------------
# config.py env-var precondition.
#
# `config.py` reads UPDATE_CHANNEL_ID / REPLAY_INDEX_CHANNEL_ID at import time
# via `int(os.getenv(...))`, raising TypeError when either is unset. Any test
# that imports a cog imports config transitively. Setting harmless values
# before collection means the scenarios fail RED for the real reason
# (behaviour missing) rather than for an env-var TypeError — the wrong-reason
# RED the pre-DELIVER gate exists to catch.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True, scope="session")
def _config_env_precondition():
    os.environ.setdefault("UPDATE_CHANNEL_ID", "0")
    os.environ.setdefault("REPLAY_INDEX_CHANNEL_ID", "0")
    yield


# ---------------------------------------------------------------------------
# Storage — real SQLite, real alembic, real JSON tree
# ---------------------------------------------------------------------------

@pytest.fixture
def fernet_key() -> str:
    """A real Fernet key, generated deterministically so tests are hermetic."""
    import base64
    return base64.urlsafe_b64encode(b"guild-key-integrity-distill-32b!"[:32]).decode()


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
    """Alembic Config rooted at bot/db/alembic for the given DB path."""
    from alembic.config import Config
    import bot.db
    db_pkg = Path(bot.db.__file__).parent
    cfg = Config(str(db_pkg / "alembic.ini"))
    cfg.set_main_option("script_location", str(db_pkg / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


@pytest.fixture
def db_at_previous_head(sqlite_db_path: Path, env_vars) -> Path:
    """A database at revision 0002 — the head BEFORE this feature's revision.

    The `Given a copy of a cluster whose guilds were registered before this
    feature existed` precondition. Migration scenarios upgrade from here, so
    they test the real transition rather than a fresh create_all.
    """
    from alembic import command
    sqlite_db_path.parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(alembic_config(sqlite_db_path), "0002")
    return sqlite_db_path


@pytest.fixture
def migrated_db(sqlite_db_path: Path, env_vars) -> Path:
    """A database at the compiled head, including this feature's revision."""
    from alembic import command
    sqlite_db_path.parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(alembic_config(sqlite_db_path), "head")
    return sqlite_db_path


@pytest.fixture
def sqlite_repo(migrated_db: Path, fernet_key: str):
    from bot.repository_sqlalchemy import SqlAlchemyClusterRepository
    return SqlAlchemyClusterRepository(db_path=str(migrated_db), fernet_key=fernet_key)


@pytest.fixture
def json_repo(json_env_vars):
    from bot.repository import JsonClusterRepository
    return JsonClusterRepository()


# ---------------------------------------------------------------------------
# The guild-service double
# ---------------------------------------------------------------------------

@dataclass
class GuildServiceResponse:
    """One programmed answer from the guild service."""

    identity: GuildIdentity | None = None
    members: list[str] = field(default_factory=list)
    status: int = 200
    raises: BaseException | None = None
    drop_fields: tuple[str, ...] = ()

    def payload(self) -> dict:
        """Render the `/api/v1/guild` body, honouring `drop_fields`.

        `drop_fields` is how the `unverifiable` environment is built: a real
        recorded response with a key removed, never a hand-written stub. A
        stub would let the test pass against an implementation that reads a
        field name Tacticus does not actually use.
        """
        guild: dict = {
            "guildId": self.identity.uuid if self.identity else None,
            "guildTag": self.identity.tag if self.identity else None,
            "name": self.identity.name if self.identity else None,
            "members": [{"userId": m} for m in self.members],
        }
        for f in self.drop_fields:
            guild.pop(f, None)
        return {"guild": guild}


class FakeGuildService:
    """Programmable stand-in for `GET /api/v1/guild`, keyed by api_key.

    Records every call so a `Then` can assert that a request was NOT made —
    which is the actual observable for "the quarantined guild is refused".
    Asserting only that no row was written would pass against an
    implementation that fetches the data and then discards it, leaking the
    other guild's roster into memory and the logs.
    """

    def __init__(self) -> None:
        self._by_key: dict[str, GuildServiceResponse] = {}
        self._default: GuildServiceResponse | None = None
        self.calls: list[str] = []

    def program(self, api_key: str, response: GuildServiceResponse) -> None:
        self._by_key[api_key] = response

    def program_default(self, response: GuildServiceResponse) -> None:
        self._default = response

    def answer_for(self, api_key: str) -> GuildServiceResponse:
        self.calls.append(api_key)
        resp = self._by_key.get(api_key, self._default)
        if resp is None:
            raise AssertionError(
                f"FakeGuildService got an unprogrammed key {api_key[:8]}… — "
                "the scenario is exercising a path it did not declare"
            )
        if resp.raises is not None:
            raise resp.raises
        return resp

    def was_called_with(self, api_key: str) -> bool:
        return api_key in self.calls

    @property
    def call_count(self) -> int:
        return len(self.calls)


@pytest.fixture
def fake_guild_service() -> FakeGuildService:
    return FakeGuildService()


@pytest.fixture
def matching_guild(fake_guild_service: FakeGuildService):
    """`bound-matching`: the key resolves to the guild it is bound to."""
    fake_guild_service.program(
        "wb-key",
        GuildServiceResponse(identity=WORD_BEARERS, members=["u1", "u2", "u3"]),
    )
    return fake_guild_service


@pytest.fixture
def drifted_guild(fake_guild_service: FakeGuildService):
    """`bound-drifted`: THE INCIDENT. Bound to Word Bearers, resolves to
    Dark Mechanicum. Same key string, different answer — exactly what
    happened when the guild master changed guilds on 2026-07-28."""
    fake_guild_service.program(
        "wb-key",
        GuildServiceResponse(identity=DARK_MECHANICUM, members=["x1", "x2"]),
    )
    return fake_guild_service


@pytest.fixture
def unverifiable_guild(fake_guild_service: FakeGuildService):
    """`unverifiable`: 200 OK, well-formed guild, no `guildId`."""
    fake_guild_service.program(
        "wb-key",
        GuildServiceResponse(
            identity=WORD_BEARERS,
            members=["u1", "u2"],
            drop_fields=("guildId",),
        ),
    )
    return fake_guild_service


# ---------------------------------------------------------------------------
# Recorded vendor responses
# ---------------------------------------------------------------------------

@pytest.fixture
def recorded_guild_response() -> dict:
    """A real `/api/v1/guild` body, captured and scrubbed of member names.

    Used by `tacticus-guild-contract.feature`. Keeping this on disk rather
    than in a fixture function is deliberate: re-recording it after a vendor
    change is a file diff a reviewer can read.
    """
    return json.loads((FIXTURES / "guild_response_recorded.json").read_text("utf-8"))


# ---------------------------------------------------------------------------
# Discord double
# ---------------------------------------------------------------------------

class FakeChannel:
    """Captures posted message text so a `Then` can assert on it — including
    asserting that NOTHING was posted, which is the `bound-matching`
    environment's whole point."""

    def __init__(self, channel_id: int) -> None:
        self.id = channel_id
        self.messages: list[str] = []

    async def send(self, content: str = "", **kwargs) -> None:
        embed = kwargs.get("embed")
        self.messages.append(content or (getattr(embed, "description", "") or ""))

    @property
    def text(self) -> str:
        return "\n".join(self.messages)


@pytest.fixture
def update_channel() -> FakeChannel:
    return FakeChannel(channel_id=1)


@pytest.fixture
def ping_channel() -> FakeChannel:
    return FakeChannel(channel_id=2)


# ---------------------------------------------------------------------------
# Structured-log capture (the KPI instrument)
# ---------------------------------------------------------------------------

@pytest.fixture
def key_events(caplog):
    """Return a reader over `guild.key.*` / `auto_update.cycle` records.

    The KPI queries in `docs/product/kpi-contracts.yaml` run against these
    exact event names. Asserting on them here is what keeps the documented
    dashboard and the implementation in step — a renamed event breaks the
    test before it breaks the operator's grep.
    """
    import logging
    caplog.set_level(logging.DEBUG)

    class Reader:
        @staticmethod
        def named(event: str) -> list:
            return [r for r in caplog.records if getattr(r, "event", None) == event]

        @staticmethod
        def all_events() -> list[str]:
            return [
                e for e in (getattr(r, "event", None) for r in caplog.records)
                if e
            ]

        @staticmethod
        def any_named(*events: str) -> bool:
            present = set(Reader.all_events())
            return bool(present & set(events))

    return Reader


# ---------------------------------------------------------------------------
# Environment parametrization (Mandate 4)
# ---------------------------------------------------------------------------

def environment_names_from_devops_artifact() -> list[str]:
    """Parse `target_environments[].name` out of environments.yaml.

    Deliberately a regex and not PyYAML: this suite must not add a runtime
    dependency to read one list of eight strings, and `requirements.txt` has
    no yaml parser. If the file's shape changes enough to break this regex,
    the traceability test fails loudly, which is the correct outcome.
    """
    text = ENVIRONMENTS_YAML.read_text("utf-8")
    body = text.split("target_environments:", 1)[1].split("\ncoexistence_matrix:", 1)[0]
    return re.findall(r"^\s*-\s*name:\s*(\S+)\s*$", body, flags=re.MULTILINE)


@pytest.fixture(params=list(Environment), ids=lambda e: e.value)
def environment(request) -> Environment:
    return request.param
