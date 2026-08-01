"""Structured single-line JSON log records.

Why this module exists (DEVOPS U1): the only implementation of the project's
structured-record format was private to `bot/db/session.py`, which imports
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
whole point of the module. There is deliberately no `from __future__ import
annotations` either: this module needs no deferred annotations, and the
absence of every non-stdlib-core import statement is the property a reader
(and an import check) can verify at a glance.
"""

import json
import logging


def emit_structured(logger: logging.Logger, level: int, event: str, **fields) -> None:
    """Emit one structured log record as a JSON message + `extra` payload.

    `logger` is caller-supplied rather than a module-level logger shared by
    every caller. Two reasons: the record keeps landing under the emitting
    module's logger name (`bot.db.session` records stay `bot.db.session`
    records, so existing filters and handler config are untouched), and a
    single shared `bot.obs` logger would erase the origin of every record in
    the file the operator greps.

    The message body is `json.dumps(payload, sort_keys=True)` so the log is
    greppable — `grep health.startup.refused discord.log` keeps working. The
    same fields are ALSO attached to the record via `extra=` so a reader can
    assert on `record.event` without re-parsing the line. The Slice-04 JSON
    formatter (observability-design.md §2) renders `extra={"structured": True,
    ...}` records as JSON; until then the JSON message string is what lands in
    `discord.log`.
    """
    payload = {"event": event, **fields}
    extra = {"structured": True, "event": event, **fields}
    logger.log(level, json.dumps(payload, sort_keys=True), extra=extra)
