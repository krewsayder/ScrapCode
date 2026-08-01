"""Structured single-line JSON log records.

RED scaffold created by DISTILL (Mandate 7). DELIVER implements it — by
promoting `bot/db/session.py::_emit_structured` here verbatim, not by writing
something new.

Why this module exists (DEVOPS U1): the only implementation of the project's
structured-record format is private to `bot/db/session.py`, which imports
alembic, cryptography and sqlalchemy at module scope. `bot/guild_keys.py` is
imported by three cogs and a service, so importing the session module from it
would make the entire SQLite stack a hard dependency of the policy layer —
including on the `SCRAPCODE_REPO_BACKEND=json` rollback path, where SQLAlchemy
is meant to be untouched (ADR-006 D9).

Duplicating the helper was the alternative and was rejected: two independently
maintained JSON log schemas is precisely the failure structured logging exists
to prevent, and every KPI query in `docs/product/kpi-contracts.yaml` assumes
one shape.

Imports `json` and `logging`. Nothing else, ever — that constraint is the
whole point of the module.
"""
from __future__ import annotations

__SCAFFOLD__ = True


def emit_structured(level: int, event: str, **fields) -> None:
    """Emit one single-line JSON record.

    The message body is `json.dumps(payload, sort_keys=True)` so the log is
    greppable; the same fields are attached to the record as attributes so a
    test can assert on `record.event` without re-parsing the line.
    """
    raise AssertionError("Not yet implemented — RED scaffold")
