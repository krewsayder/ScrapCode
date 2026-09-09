"""The startup probe must pass against a database that already has data.

Regression test for the 2026-08-04 production outage. `_step_write_rollback`
inserted a sentinel row, rolled it back, then asserted `COUNT(*) FROM clusters
== 0`. That assertion holds only on an empty database. Every existing probe
test builds a freshly-migrated (therefore empty) DB, so the suite was green
while the check was unsatisfiable on any real deployment: production had one
registered cluster, the probe read that row as its own leaked sentinel, and
refused with `rollback_leaked count=1` on every boot.

The step's actual claim is "a write can be opened and undone", which is a
statement about RESIDUE, not about absolute row count. These tests pin it to
that claim by seeding rows first.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from cryptography.fernet import Fernet


def _alembic_config(db_path: Path):
    from alembic.config import Config

    import bot.db

    db_pkg = Path(bot.db.__file__).parent
    cfg = Config(str(db_pkg / "alembic.ini"))
    cfg.set_main_option("script_location", str(db_pkg / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


@pytest.fixture
def populated_db(tmp_path: Path) -> Path:
    """A migrated database holding real clusters — i.e. production's shape."""
    from alembic import command

    db_path = tmp_path / "scrapcode.db"
    command.upgrade(_alembic_config(db_path), "head")

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executemany(
        "INSERT INTO clusters (discord_server_id) VALUES (?)",
        [(1458181638453203099,), (1234567890123456789,)],
    )
    conn.commit()
    conn.close()
    return db_path


def _probe(db_path: Path, caplog):
    from bot.db.session import Database

    db = Database(db_path=str(db_path), fernet_key=Fernet.generate_key().decode())
    with caplog.at_level("INFO", logger="bot.db.session"):
        db.probe()


def _events(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records]


def test_probe_passes_when_clusters_already_hold_rows(populated_db, caplog):
    """The step under test — this is the one that reproduced the outage."""
    _probe(populated_db, caplog)

    joined = " ".join(_events(caplog))
    assert '"step": "write_rollback"' in joined and '"db.probe.pass"' in joined
    assert "health.startup.refused" not in joined, (
        "probe refused against a populated database — the residue check is "
        "absolute again rather than relative"
    )


def test_probe_leaves_the_seeded_rows_untouched(populated_db, caplog):
    """The rollback must not take the pre-existing rows down with it."""
    _probe(populated_db, caplog)

    conn = sqlite3.connect(str(populated_db))
    remaining = conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0]
    sentinel = conn.execute(
        "SELECT COUNT(*) FROM clusters WHERE discord_server_id = 0"
    ).fetchone()[0]
    conn.close()

    assert remaining == 2, "probe destroyed pre-existing cluster rows"
    assert sentinel == 0, "probe leaked its sentinel row into a real table"


def test_probe_still_refuses_when_the_rollback_genuinely_leaks(populated_db, caplog, monkeypatch):
    """The relaxed check must not become vacuous.

    Neuter the ROLLBACK so the sentinel really does survive, and require the
    probe to catch it. Without this, `after != before or sentinel` could be
    weakened to always-true and both tests above would still pass.
    """
    import bot.db.session as session_mod

    real_connect = sqlite3.connect

    class _NoRollbackConn:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, *args):
            if sql.strip().upper() == "ROLLBACK":
                return self._inner.execute("COMMIT")
            return self._inner.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def _connect(*args, **kwargs):
        return _NoRollbackConn(real_connect(*args, **kwargs))

    monkeypatch.setattr(session_mod.sqlite3, "connect", _connect)

    with pytest.raises(session_mod.ProbeRefusedError):
        _probe(populated_db, caplog)

    joined = " ".join(_events(caplog))
    assert "rollback_leaked" in joined
