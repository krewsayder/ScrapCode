# Observability Design — feature `sqlite-backend` (DEVOPS wave)

> DEVOPS D5 = structured JSON logs to the existing `discord.log` + the
> ADR-006 `probe()` startup health gate + KPI instrumentation emitted as
> logs/test artifacts. **No new metrics stack.** The deployment is one
> process on one VM serving one Discord server; a metrics stack
> (Prometheus, Datadog, etc.) would violate simplest-solution-first.
> The operator reads the log file and the journalctl output.

## 1. Logging infrastructure (reused, extended)

The existing `main.py` configures a `logging.FileHandler` writing
`discord.log` (append, utf-8), and systemd captures stderr to journald
(brief §2.6). This wave EXTENDS that setup; it does not replace it.

- **No new log file.** Probe results, migration progress, and hourly
  transaction outcomes go to the existing `discord.log` (and therefore
  also to `journalctl -u discord-bot`).
- **No new log handler.** The `FileHandler` is reused. A JSON formatter
  is added for the new structured records only — the existing
  free-text records (`on_ready` banner, etc.) keep their format so the
  operator's existing `grep` habits still work.
- **No new log level.** The probe's `health.startup.refused` event is
  logged at `ERROR`. Normal operation records are `INFO`. Per-table
  parity mismatches are `ERROR`.

## 2. Structured JSON record format

New records emitted by `bot/db/session.py`, the migration script, and
the SQLite repository are written as single-line JSON to `discord.log`.
The formatter is a small `logging.Formatter` subclass that detects
records carrying an `extra={"structured": True, "event": ...}` payload
and emits JSON for those, plain text for everything else.

Schema (one line per record):
```json
{"ts": "2026-07-18T10:00:00Z", "level": "INFO", "event": "db.probe.start", "step": "wal_mode", "backend": "sqlite"}
{"ts": "2026-07-18T10:00:00Z", "level": "INFO", "event": "db.probe.pass", "step": "wal_mode", "value": "wal"}
{"ts": "2026-07-18T10:00:00Z", "level": "INFO", "event": "db.probe.pass", "step": "alembic_version", "head": "abc123", "db": "abc123"}
{"ts": "2026-07-18T10:00:00Z", "level": "INFO", "event": "db.probe.pass", "step": "fernet_roundtrip"}
{"ts": "2026-07-18T10:00:00Z", "level": "INFO", "event": "db.probe.pass", "step": "write_rollback"}
{"ts": "2026-07-18T10:00:00Z", "level": "ERROR", "event": "health.startup.refused", "step": "alembic_version", "head": "abc123", "db": "old_rev", "reason": "stale_alembic_version"}
```

Fields:
- `ts` — ISO8601 UTC.
- `level` — `INFO` (normal) / `ERROR` (refusal / mismatch) /
  `WARNING` (degraded but running, e.g. a single-guild transaction
  rolled back and the cycle continued).
- `event` — dotted name; the catalog below.
- `step` — for the probe, which of the 4 steps.
- `backend` — `sqlite` or `json` (so a JSON-backend rollback is visible
  in the log without ambiguity).
- Plus event-specific fields (`value`, `head`, `db`, `reason`, `guild`,
  `table`, `json_count`, `sql_count`).

`SCRAPCODE_DB_KEY` is NEVER logged. The probe's Fernet-roundtrip record
records only pass/fail, never the key or the ciphertext.

## 3. Event catalog

| Event | Level | Emitted by | When | Key fields |
|-------|-------|-----------|------|------------|
| `db.probe.start` | INFO | `bot/db/session.py` | composition root, probe begins | `backend`, `db_path` |
| `db.probe.pass` | INFO | `bot/db/session.py` | each of the 4 probe steps passes | `step` |
| `health.startup.refused` | ERROR | `bot/db/session.py` | any probe step fails | `step`, `reason` |
| `db.probe.skipped` | INFO | `bot/db/session.py` | `SCRAPCODE_REPO_BACKEND=json` | `backend: "json"` |
| `db.migration.start` | INFO | migration script | begins the JSON→SQLite run | `source`, `db` |
| `db.migration.table` | INFO | migration script | each table populated | `table`, `rows` |
| `db.migration.parity` | INFO / ERROR | migration script | each table's parity check | `table`, `json_count`, `sql_count`, `status` |
| `db.migration.done` | INFO | migration script | overall PASS | `overall`, `elapsed_ms` |
| `db.migration.failed` | ERROR | migration script | overall FAIL (any MISMATCH) | `overall`, `failing_tables` |
| `db.tx.commit` | INFO | `bot/repository_sqlalchemy.py` | each per-guild hourly transaction commits | `guild`, `cycle`, `rows_written` |
| `db.tx.rollback` | WARNING | `bot/repository_sqlalchemy.py` | a per-guild hourly transaction rolls back (crash / error) | `guild`, `cycle`, `reason` |
| `db.tx.start` | DEBUG | `bot/repository_sqlalchemy.py` | each per-guild hourly transaction begins | `guild`, `cycle` (DEBUG off by default; enable for diagnosis) |

## 4. Health check = the probe

The ADR-006 D8 `probe()` is the health gate. There is no separate liveness
or readiness endpoint — the bot is a single Discord process, not an HTTP
service. The probe runs at startup; if it passes, the bot is healthy
enough to serve. If it fails, the bot refuses to start (systemd `failed`).

**Operational "is it healthy right now" checks (manual, no dashboard):**
```
# Is the bot process up?
sudo systemctl status discord-bot --no-pager | grep Active

# Did the last startup probe pass?
sudo journalctl -u discord-bot -n 200 --no-pager | grep 'db.probe.pass\|health.startup.refused' | tail -6

# Did the last hourly cycle commit cleanly?
sudo journalctl -u discord-bot --since '1 hour ago' --no-pager | grep 'db.tx.commit\|db.tx.rollback'
```

These three commands are the operator's "dashboard." They cover: process
up, last startup healthy, last hourly cycle clean. Anything more would
be over-engineering for a single-VM bot.

## 5. Alerting

**There is no automated alerting in this wave** (DEVOPS D7 = No
continuous learning; no existing monitoring/alerting to build on). The
operator monitors via:
- `systemctl status discord-bot` after every deploy.
- The `discord.log` file tail during the one-cycle observation window
  post-cutover (platform-architecture.md §8 step C2).
- The hourly DB backup timer's last result:
  `systemctl status discord-bot-backup.timer`.

If the operator later wants alerting, the cheapest path is a
`#systemd-unit-failure` notification via systemd's `OnFailure=` + a
small `notify-send`-to-Discord-webhook shim. That is a future feature,
not this wave. Recorded as a deferred consideration in
`wave-decisions.md`.

## 6. KPI instrumentation as logs / test artifacts

KPI measurement artifacts are NOT metrics; they are the cutover-gate
evidence (KPI-1..KPI-4). They are produced by `pytest` runs and the
migration script, stored as files under `data/backups/`, and the
summary lines are pasted into the slice exit report. The structured log
records above are the runtime audit trail that supplements them (e.g.
`db.migration.parity` records in the log corroborate the parity report
JSON file). See `kpi-instrumentation.md` for the per-KPI commands and
artifacts.

## 7. Log retention

The existing `discord.log` grows unbounded (FileHandler append, no
rotation per brief §2.6). This wave does NOT introduce rotation — it is
out of scope for DEVOPS (no code changes this wave). The operator's
existing practice (manual truncate when the file gets large) continues
until Slice 04. **High-priority follow-up folded into DELIVER Slice 04:**
add `logging.handlers.RotatingFileHandler` with 10 MB × 5 files at the
same `main.py` site where Slice 04 already extends logging with the
structured-JSON formatter and retires `file_lock` — a one-line change
in the same diff. Recorded in `wave-decisions.md` §Deferred
considerations so the software-crafter picks it up with no extra
slice.

## 8. What is deliberately NOT here

- No Prometheus / Grafana / Datadog (DEVOPS D5; simplest-solution-first
  for a single VM).
- No distributed tracing (single process; no spans to trace across).
- No liveness/readiness HTTP endpoints (Discord bot, not a web service).
- No log aggregation shipper (one VM, one log file; `journalctl` + `tail`
  are sufficient).
- No SLO/SLI dashboard (DEVOPS D7 = No; the feature is a one-shot
  cutover, not an ongoing service with a steady-state traffic profile).
- No automated alerting (D7; manual operator monitoring during the
  one-cycle observation window).