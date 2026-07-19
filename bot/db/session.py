"""SQLite session factory + startup probe (ADR-006 D1 / D7 / D8).

This module is the Earned-Trust gate for the SQLite backend. It provides:

  - `Database` — the SQLAlchemy 2.0 engine factory. Reads `SCRAPCODE_DB_PATH`
    (default `clusters.db`) and `SCRAPCODE_DB_KEY` (a Fernet key) from env.
    Sets WAL pragmas (`journal_mode=WAL; synchronous=NORMAL; foreign_keys=ON`)
    on every runtime connection via a `connect` event listener.
  - `Database.session_scope()` — a synchronous context manager yielding a
    SQLAlchemy `Session`. The async (aiosqlite) session_scope the runtime
    path uses lands in 02-03 alongside the repository; for 02-02 the probe
    drives the engine directly through raw sqlite3 connections so the four
    refusal paths can be isolated to a named step.
  - `Database.probe()` — the 4-step startup health gate (ADR-006 D8):
      (1) assert `PRAGMA journal_mode` is `wal`
      (2) assert `alembic_version.version_num` matches the compiled head
      (3) round-trip a known plaintext through Fernet with `SCRAPCODE_DB_KEY`
      (4) insert + roll back a throwaway row in `clusters`
    On any step failure, the probe emits a structured `health.startup.refused`
    log record naming the failing step and raises `ProbeRefusedError`; the
    composition root (02-04) refuses to start the bot on that signal.

The probe is SKIPPED when `SCRAPCODE_REPO_BACKEND=json` (rollback path); the
composition root makes that decision, not this module.

Structured log records are emitted via the `bot.db.session` logger with
`extra={"structured": True, "event": ..., "step": ...}`. The Slice-04 JSON
formatter (observability-design.md §2) renders them as single-line JSON;
until then the message itself is a JSON string so the operator can grep
`health.startup.refused` in `discord.log` today.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger("bot.db.session")

PROBE_PLAINTEXT = b"scrapcode-probe-sentinel-v1"
PROBE_SENTINEL_SERVER_ID = 0


class ProbeRefusedError(Exception):
    """Raised when a probe step fails.

    The composition root catches this, leaves the bot unstarted, and lets
    systemd mark the unit `failed`. The message carries the structured
    event name so a caller that logs `str(exc)` surfaces
    `health.startup.refused` without extra work.
    """

    def __init__(self, step: str, reason: str, detail: str = "") -> None:
        self.step = step
        self.reason = reason
        self.detail = detail
        message = f"health.startup.refused step={step} reason={reason}"
        if detail:
            message += f" detail={detail}"
        super().__init__(message)


def _compiled_alembic_head() -> str:
    """Return the alembic head revision compiled into this binary.

    Walks `bot/db/alembic` with alembic's `ScriptDirectory` so the probe
    compares the DB's `alembic_version.version_num` against the actual head,
    not a hardcoded string.
    """
    db_pkg_dir = Path(__file__).parent
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(db_pkg_dir / "alembic"))
    script_dir = ScriptDirectory.from_config(cfg)
    return script_dir.get_current_head()


def _fernet_roundtrip(fernet_key: str, plaintext: bytes) -> bytes:
    """Encrypt then decrypt `plaintext` with `fernet_key`; return the plaintext.

    Raises `ValueError` if `fernet_key` is not a valid Fernet key (32
    url-safe base64-encoded bytes) and `cryptography.fernet.InvalidToken` if
    the ciphertext does not decrypt back. The probe wraps both into a
    `health.startup.refused` refusal naming the `fernet_roundtrip` step.
    """
    fernet = Fernet(fernet_key.encode() if isinstance(fernet_key, str) else fernet_key)
    ciphertext = fernet.encrypt(plaintext)
    return fernet.decrypt(ciphertext)


def _emit_structured(level: int, event: str, **fields) -> None:
    """Emit one structured log record as a JSON message + `extra` payload.

    The Slice-04 JSON formatter reads `extra={"structured": True, ...}` and
    renders the record as JSON; until then the JSON message string is what
    lands in `discord.log` so `grep health.startup.refused` works today.
    """
    payload = {"event": event, **fields}
    extra = {"structured": True, "event": event, **fields}
    logger.log(level, json.dumps(payload, sort_keys=True), extra=extra)


class Database:
    """SQLAlchemy engine factory + the ADR-006 D8 startup probe.

    The probe is the adapter's empirical contract demonstration (principle
    12 — Earned Trust): it refuses to let the bot depend on a SQLite file
    that is not in WAL mode, is stamped at a stale alembic revision, cannot
    round-trip a Fernet ciphertext with `SCRAPCODE_DB_KEY`, or cannot
    transact (read-only filesystem / corrupted write path).
    """

    def __init__(self, db_path: str | None = None, fernet_key: str | None = None) -> None:
        self._db_path: str = db_path or os.getenv("SCRAPCODE_DB_PATH", "clusters.db")
        self._fernet_key: str = fernet_key or os.getenv("SCRAPCODE_DB_KEY", "")
        self._engine: Engine | None = None
        self._session_factory: sessionmaker | None = None

    # ------------------------------------------------------------------
    # Engine + session_scope (runtime path; the probe uses raw sqlite3
    # so each step can be attributed to a named refusal step).
    # ------------------------------------------------------------------

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            url = f"sqlite:///{self._db_path}"
            self._engine = create_engine(url, future=True)
            _wire_wal_pragmas(self._engine)
        return self._engine

    @contextmanager
    def session_scope(self) -> Iterator[Session]:
        """Yield a SQLAlchemy `Session` and commit/rollback on exit.

        Synchronous for 02-02 (the probe path). The async (aiosqlite)
        session_scope the repository uses at runtime lands in 02-03.
        """
        if self._session_factory is None:
            self._session_factory = sessionmaker(bind=self.engine, future=True)
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Probe — ADR-006 D8 (Earned Trust)
    # ------------------------------------------------------------------

    def probe(self) -> None:
        """Run the 4-step startup health gate; raise `ProbeRefusedError` on failure."""
        _emit_structured(
            logging.INFO,
            "db.probe.start",
            backend="sqlite",
            db_path=self._db_path,
        )
        self._step_wal_mode()
        self._step_alembic_version()
        self._step_fernet_roundtrip()
        self._step_write_rollback()

    def _refuse(self, step: str, reason: str, detail: str = "") -> None:
        """Emit the refusal record and raise. Never returns."""
        _emit_structured(
            logging.ERROR,
            "health.startup.refused",
            step=step,
            reason=reason,
            detail=detail,
        )
        raise ProbeRefusedError(step, reason, detail)

    def _step_wal_mode(self) -> None:
        try:
            conn = sqlite3.connect(self._ro_uri(), uri=True)
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            conn.close()
        except sqlite3.DatabaseError as exc:
            self._refuse("wal_mode", "open_failed", detail=str(exc))
            return
        except sqlite3.OperationalError as exc:
            self._refuse("wal_mode", "open_failed", detail=str(exc))
            return
        if mode != "wal":
            self._refuse("wal_mode", "not_wal", detail=f"journal_mode={mode}")
            return
        _emit_structured(logging.INFO, "db.probe.pass", step="wal_mode", value=mode)

    def _step_alembic_version(self) -> None:
        head = _compiled_alembic_head()
        try:
            conn = sqlite3.connect(self._ro_uri(), uri=True)
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
            conn.close()
        except sqlite3.DatabaseError as exc:
            self._refuse("alembic_version", "query_failed", detail=str(exc))
            return
        if row is None:
            self._refuse("alembic_version", "missing_version_row", detail="no rows")
            return
        db_rev = row[0]
        if db_rev != head:
            self._refuse(
                "alembic_version",
                "stale_alembic_version",
                detail=f"head={head} db={db_rev}",
            )
            return
        _emit_structured(
            logging.INFO,
            "db.probe.pass",
            step="alembic_version",
            head=head,
            db=db_rev,
        )

    def _step_fernet_roundtrip(self) -> None:
        try:
            decrypted = _fernet_roundtrip(self._fernet_key, PROBE_PLAINTEXT)
        except (ValueError, InvalidToken, TypeError) as exc:
            self._refuse("fernet_roundtrip", "key_invalid", detail=str(exc))
            return
        if decrypted != PROBE_PLAINTEXT:
            self._refuse(
                "fernet_roundtrip",
                "roundtrip_mismatch",
                detail="decrypted != plaintext",
            )
            return
        _emit_structured(logging.INFO, "db.probe.pass", step="fernet_roundtrip")

    def _step_write_rollback(self) -> None:
        try:
            conn = sqlite3.connect(self._rw_uri(), uri=True)
            conn.execute("BEGIN")
            conn.execute(
                "INSERT INTO clusters (discord_server_id) VALUES (?)",
                (PROBE_SENTINEL_SERVER_ID,),
            )
            conn.execute("ROLLBACK")
            count = conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0]
            conn.close()
        except sqlite3.DatabaseError as exc:
            self._refuse("write_rollback", "write_failed", detail=str(exc))
            return
        if count != 0:
            self._refuse("write_rollback", "rollback_leaked", detail=f"count={count}")
            return
        _emit_structured(logging.INFO, "db.probe.pass", step="write_rollback")

    # ------------------------------------------------------------------
    # URI helpers
    # ------------------------------------------------------------------

    def _ro_uri(self) -> str:
        return f"file:{self._db_path}?mode=ro"

    def _rw_uri(self) -> str:
        return f"file:{self._db_path}?mode=rw"


def _wire_wal_pragmas(engine: Engine) -> None:
    """Set WAL pragmas on every connection opened by `engine` (ADR-006 D1)."""

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()