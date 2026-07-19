"""Alembic env for the SQLite backend (ADR-006 D3).

Synchronous engine (Alembic migrations run synchronously; the async
aiosqlite driver is for the runtime path, not the migration path). The
target metadata is `bot.db.models.Base.metadata`; `alembic upgrade head`
creates every table declared in `bot/db/models.py`.

URL resolution order:
  1. `sqlalchemy.url` set on the Alembic Config (tests set this directly).
  2. `SCRAPCODE_DB_PATH` env var (file path -> `sqlite:///` URL).
  3. Default: `sqlite:///clusters.db` (relative to the repo root).

WAL pragmas (`journal_mode=WAL`, `synchronous=NORMAL`, `foreign_keys=ON`)
are set on every connection so migrations run under the same isolation
contract as the runtime path (ADR-006 D1).
"""
from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from bot.db.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolve_url() -> str:
    configured = config.get_main_option("sqlalchemy.url")
    if configured:
        return configured
    db_path = os.environ.get("SCRAPCODE_DB_PATH")
    if db_path:
        return f"sqlite:///{db_path}"
    return "sqlite:///clusters.db"


def _run_migrations(url: str) -> None:
    config.set_main_option("sqlalchemy.url", url)
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(
        url=_resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_name="sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    _run_migrations(_resolve_url())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()