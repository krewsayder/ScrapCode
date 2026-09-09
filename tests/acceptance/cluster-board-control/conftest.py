"""Fixtures for the `cluster-board-control` acceptance suite.

FIXTURES ONLY. Constants and doubles live in `board_domain_types.py`, and that
split is load-bearing rather than stylistic: `from conftest import X` resolves
through `sys.path`, which pytest shares across every rootless test directory in
a run, so the name binds to whichever suite was imported first. The suite
directories are hyphenated and therefore cannot be packages, so the collision
cannot be namespaced away — see `board_domain_types.py`'s docstring and UD-10
in `guild-key-integrity`'s `distill/upstream-issues.md`.

Nothing imports this module. pytest injects fixtures by name.

Mechanisms are inherited from `docs/architecture/atdd-infrastructure-policy.md`
(`--policy=inherit`, zero rows appended — every port this feature touches was
already recorded by `sqlite-backend` and `guild-key-integrity`):

  Discord slash command   -> direct callback invocation + interaction double
  @tasks.loop background  -> direct await of the loop body, decorator bypassed
  ClusterRepository       -> real adapter, constructed by the test
  Alembic CLI             -> real upgrade/downgrade against a tmp_path DB
  SQLite / JSON adapters  -> real, both — ADR-006 D9's rollback path is only
                             protected while it stays exercised
  Discord channel send    -> FakeChannel, capturing text so "nothing was
                             posted" is assertable
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# `bot.guilds` evaluates `repo = build_repo()` at import time and reads the
# environment at that moment. Pin a harmless backend and the two channel ids
# `config.py` casts with `int(os.getenv(...))` before any `bot.*` import, so
# collection can neither raise TypeError nor build a repository pointed at the
# live `clusters/` tree. Precedent: tests/acceptance/guild-key-integrity/conftest.py.
os.environ.setdefault("SCRAPCODE_REPO_BACKEND", "json")
os.environ.setdefault("UPDATE_CHANNEL_ID", "0")
os.environ.setdefault("REPLAY_INDEX_CHANNEL_ID", "0")

from board_domain_types import (  # noqa: E402
    FERNET_KEY,
    SERVER_ID,
    FakeChannel,
    FakeInteraction,
    alembic_config,
)


# ---------------------------------------------------------------------------
# Real storage — both adapters
# ---------------------------------------------------------------------------

@pytest.fixture
def sqlite_db_path(tmp_path: Path) -> Path:
    path = tmp_path / "data" / "scrapcode.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def migrated_db(sqlite_db_path: Path) -> Path:
    """A database at alembic head.

    Alembic runs BEFORE any repository is constructed — building one first
    creates the tables itself and the migration then collides with them.
    Precedent: tests/unit/test_guild_keys_policy.py::sqlite_repo.
    """
    from alembic import command

    command.upgrade(alembic_config(sqlite_db_path), "head")
    return sqlite_db_path


@pytest.fixture
def db_before_the_switch(sqlite_db_path: Path) -> Path:
    """A database at the revision BEFORE `board_status` was added.

    KPI-4's precondition: boards configured by a bot that predates the switch.
    Pinned to an ABSOLUTE revision, never to a distance from head — `-1` means
    "one step back from wherever head is" and silently changes meaning every
    time a revision lands. That exact defect cost `guild-key-integrity` a red
    build (AC-006.2, 2026-08-03).
    """
    from alembic import command

    command.upgrade(alembic_config(sqlite_db_path), "0004")
    return sqlite_db_path


@pytest.fixture
def sqlite_repo(migrated_db: Path):
    from bot.repository_sqlalchemy import SqlAlchemyClusterRepository

    repo = SqlAlchemyClusterRepository(db_path=str(migrated_db), fernet_key=FERNET_KEY)
    repo.save(_bare_cluster())
    return repo


@pytest.fixture
def json_repo(tmp_path: Path):
    from bot.repository import JsonClusterRepository

    base = tmp_path / "clusters"
    base.mkdir(parents=True, exist_ok=True)
    repo = JsonClusterRepository(base_path=base)
    repo.save(_bare_cluster())
    return repo


@pytest.fixture(params=["json", "sqlite"], ids=["json", "sqlite"])
def either_repo(request, json_repo, sqlite_repo):
    """Both real adapters, parametrized.

    The parity scenario (AC-004.4 / KPI-5) asserts they agree; every other
    scenario taking this fixture asserts the behaviour is adapter-neutral,
    which is what makes ADR-006 D9's JSON rollback path a real option rather
    than a documented intention.
    """
    return json_repo if request.param == "json" else sqlite_repo


def _bare_cluster():
    """`live_leaderboards.discord_server_id` is an FK to `clusters`."""
    from bot.repository import Cluster

    return Cluster(
        discord_server_id=SERVER_ID, guilds={}, update_channel_id=None, role_tiers={}
    )


# ---------------------------------------------------------------------------
# Discord doubles
# ---------------------------------------------------------------------------

@pytest.fixture
def board_channel() -> FakeChannel:
    return FakeChannel()


@pytest.fixture
def officer() -> FakeInteraction:
    return FakeInteraction(is_officer=True)


@pytest.fixture
def non_officer() -> FakeInteraction:
    return FakeInteraction(is_officer=False)


@pytest.fixture
def board_events(caplog):
    """Reader over `live_board.*` structured records.

    Asserts on `record.event`, not on the rendered line: `emit_structured`
    attaches the fields via `extra=` precisely so a reader does not re-parse
    JSON out of a log message. Same shape as `guild-key-integrity`'s
    `key_events` fixture, and for the same reason — the operator's grep and
    these tests have to break together.
    """
    import logging

    caplog.set_level(logging.DEBUG)

    class Reader:
        @staticmethod
        def named(event: str) -> list:
            return [r for r in caplog.records if getattr(r, "event", None) == event]

        @staticmethod
        def clear() -> None:
            caplog.clear()

    return Reader()


# ---------------------------------------------------------------------------
# Cog access — imported LATE, never at module scope
# ---------------------------------------------------------------------------

@pytest.fixture
def admin_cog():
    """Import `bot.cogs.admin_cog` LATE.

    Importing any cog imports `bot.guilds`, which builds the process-wide
    ClusterRepository singleton from whatever environment exists AT THAT
    MOMENT. At collection time no fixture has run.
    """
    from bot.cogs import admin_cog as module

    return module


@pytest.fixture
def tasks_cog():
    from bot.cogs import tasks_cog as module

    return module


@pytest.fixture(scope="module", autouse=True)
def _leave_the_cog_modules_as_this_suite_found_them():
    """Un-import the cogs once this module's tests are done.

    They bind `repo`, `load_live_leaderboards` and friends BY VALUE at import
    time, and other suites patch `bot.guilds.repo` per test then import the
    cogs afterwards to pick the patched object up.
    """
    yield
    for name in ("bot.cogs.admin_cog", "bot.cogs.tasks_cog"):
        sys.modules.pop(name, None)
        package = sys.modules.get("bot.cogs")
        if package is not None:
            attribute = name.rsplit(".", 1)[1]
            if getattr(package, attribute, None) is not None:
                delattr(package, attribute)
