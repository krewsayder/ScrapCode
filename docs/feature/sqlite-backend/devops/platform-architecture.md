# Platform Architecture — feature `sqlite-backend` (DEVOPS wave)

> Concrete, operational deployment design for a **single Linux VM, single
> systemd-managed Python process**. No containers, no orchestration, no
> cloud, no metrics stack. The deployment target is reused as-is from the
> as-built baseline (brief §2.6). The only new infrastructure is the SQLite
> database file + the Fernet key + a backup timer.
>
> Author: Apex (nw-platform-architect), DEVOPS wave. Decisions 1–9 are
> recorded in `wave-decisions.md`. This document is the operational
> runbook the operator (Krewsayder) follows.

## 1. Deployment target (reused, not new)

| Aspect | Value | Source |
|--------|-------|--------|
| Host | Linux VM `discord-bot-vm`, user `krewsayder` | brief §2.6 |
| Project path | `/opt/discord-bot` (git checkout, deploy source `origin/main`) | brief §2.6 |
| Process manager | systemd, service `discord-bot` | brief §2.6 |
| Stack | Python 3.11 asyncio + `discord.py`; entrypoint `main.py` | brief §2.6 |
| Venv | `/opt/discord-bot/.venv/` (shipped with the repo, reused) | brief §2.6 |
| Logs | `discord.log` (FileHandler, append, utf-8) + `journalctl -u discord-bot` | brief §2.6 |
| Branching | GitHub Flow (short feature branches → PR → merge to `main`) | DEVOPS D8 |

**Verify before any deploy** (per brief §2.6 caveat — unit file not inspected
by the architecture baseline):
```
systemctl cat discord-bot       # ExecStart, WorkingDirectory, User=, Restart=
systemctl edit discord-bot       # any drop-in overrides
```
All runbook commands below assume `WorkingDirectory=/opt/discord-bot` and
`ExecStart=/opt/discord-bot/.venv/bin/python main.py`. If the unit differs,
adjust the `pip` path and the `systemctl restart` target accordingly.

## 2. SQLite file location

| Setting | Value |
|---------|-------|
| Default path | `/opt/discord-bot/data/scrapcode.db` |
| Env override | `SCRAPCODE_DB_PATH` (default `data/scrapcode.db`, relative to CWD = `WorkingDirectory`) |
| Journal mode | WAL (`PRAGMA journal_mode=wal`) — set on every connection by `bot/db/session.py` |
| Synchronous | `PRAGMA synchronous=NORMAL` |
| Foreign keys | `PRAGMA foreign_keys=ON` |
| Sidecar files | `scrapcode.db-wal`, `scrapcode.db-shm` (WAL sidecars; back these up together with the DB) |
| Gitignore | `data/` is already gitignored (`.gitignore` line 9); the DB file is therefore NOT tracked. Confirm after first run: `git status data/` shows nothing. |

The `data/` directory does NOT currently exist in the repo checkout —
only the `.gitignore` entry does (`.gitignore` line 9, `data/`). It is
created at runtime: by SQLite (which creates the parent path on first
`connect()` if needed, or by `bot/db/session.py` ensuring the directory
exists before opening the engine) and by the backup timer (which
creates `data/backups/` on its first run). The SQLite file lives there
to keep all runtime data under one gitignored path. **Do not** place
the DB beside the `clusters/` tree (ADR-006 D1 suggested `clusters.db`
as a default; this DEVOPS wave overrides that default to
`data/scrapcode.db` for cleaner separation of "JSON source tree" vs
"new DB artifact" — see `wave-decisions.md` §Upstream Changes).

## 3. Secrets & environment

New `.env` entries (extend the existing file; never commit):

| Var | Required | Purpose | Loss consequence |
|-----|----------|---------|------------------|
| `SCRAPCODE_DB_PATH` | No (defaulted) | SQLite file location | — |
| `SCRAPCODE_DB_KEY` | **Yes** (SQLite backend only) | Fernet key for `api_key` columns (ADR-006 D7) | All `api_key` columns unrecoverable; bot refuses to start (probe step 3 fails) |
| `SCRAPCODE_REPO_BACKEND` | No (default `sqlite` post-cutover) | `json` = rollback path | — |

`SCRAPCODE_DB_KEY` MUST be generated once and backed up with `DISCORD_TOKEN`
in the operator's `.env` backup. Generation:
```
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
The probe (ADR-006 D8) catches a wrong/rotated key at startup before any
real `api_key` is touched. `SCRAPCODE_DB_KEY` MUST NOT be logged (the
probe's structured log records only pass/fail, never the key).

## 4. `.venv` dependencies

Add to `requirements.txt` (installed into the existing reused venv):

```
sqlalchemy>=2.0
alembic
aiosqlite
cryptography
import-linter
pytest-archon
```

`cryptography` is required for Fernet (ADR-006 D7). `import-linter` and
`pytest-archon` are the architecture-enforcement tools (ADR-006 §
"Architecture enforcement") and run as part of the local test gate (see
`ci-cd-pipeline.md`).

**Install step (deploy runbook §8 step 3):**
```
cd /opt/discord-bot
.venv/bin/pip install -r requirements.txt
```

## 5. systemd integration

The existing `discord-bot` unit is reused unchanged. The startup sequence
inside `main.py` is extended (the unit file itself does not change):

1. `load_dotenv()` — reads `DISCORD_TOKEN`, `CHRONICL3R_APP_*`,
   `SCRAPCODE_DB_PATH`, `SCRAPCODE_DB_KEY`, `SCRAPCODE_REPO_BACKEND`.
2. Configure logging (existing `FileHandler` → `discord.log`; structured
   JSON records appended — see `observability-design.md`).
3. Composition root `bot/guilds.py:7` reads `SCRAPCODE_REPO_BACKEND`:
   - `sqlite` (default) → construct `SqlAlchemyClusterRepository`, run
     `probe()`. On **probe failure**, raise `health.startup.refused` and
     exit non-zero → systemd marks the unit `failed`.
   - `json` (rollback) → construct `JsonClusterRepository`, skip the probe.
4. `bot.start(token)`.

**Deploy mechanism = `systemctl restart discord-bot`** (DEVOPS D6:
Recreate). The probe is the auto-stop safety: a bad DB (corrupted, stale
alembic version, wrong Fernet key, read-only filesystem) refuses to
start, so a failed deploy auto-stops the service in `failed` state
rather than running a half-broken bot.

**Restart policy:** confirm the unit's `Restart=` setting on the host.
If `Restart=on-failure` is set, a probe failure will *not* loop-restart
(systemd gives up after `StartLimitBurst`). That is the desired behavior
for a refused startup — do not mask a probe failure with aggressive
restarts. If `Restart=always`, add a drop-in:
```
sudo systemctl edit discord-bot
# add:
[Service]
Restart=on-failure
StartLimitIntervalSec=300
StartLimitBurst=3
```
This caps restart attempts at 3 in 5 minutes so a probe-failure loop is
visible to the operator instead of being masked.

## 6. Backup strategy

SQLite online backup before the hourly update window, via a systemd
timer. The DB file is the single source of truth post-cutover; losing it
loses everything (no JSON fallback after the one-cycle retirement).

**Backup mechanism:** `VACUUM INTO` (a safe online backup that does not
block readers; WAL mode allows it during normal operation):
```
sqlite3 /opt/discord-bot/data/scrapcode.db "VACUUM INTO '/opt/discord-bot/data/backups/scrapcode-$(date +%Y%m%dT%H%M%S).db'"
```
(`.backup` is an equivalent alternative; `VACUUM INTO` produces a
single defragmented file.)

**systemd timer** (`discord-bot-backup.timer` + `discord-bot-backup.service`):
- Schedule: `OnCalendar=*-*-* 55:00` (5 minutes before the top-of-hour
  `auto_update` / `cap_detect` loops — brief §2.3 — so the backup is
  never racing an in-flight transaction).
- Retention: keep the last **N=24** backups (≈ 24 hours of hourly
  snapshots). A prune step in the service removes older files:
  ```
  find /opt/discord-bot/data/backups -name 'scrapcode-*.db' -mtime +1 -delete
  ```
- Quota: each backup is roughly the DB size; 24 hourly backups fit
  comfortably on a VM with a few GB free. Confirm disk capacity once.

**Restore drill (quarterly, per production-readiness skill):** pick a
recent backup, copy it to a staging path, point `SCRAPCODE_DB_PATH` at
it in a throwaway `.env`, start the bot in a test guild, run the probe +
one `/view_leaderboard`. Document the result in the runbook's "Restore
drill log" section. This is the only way to know backups are usable.

**Backup of `SCRAPCODE_DB_KEY`:** the Fernet key is NOT in the DB; it is
in `.env`. The operator's `.env` backup (alongside `DISCORD_TOKEN`) is
the key backup. A DB backup without the matching key is unrecoverable
for `api_key` columns (other tables still readable). Record the key's
location in the operator's secrets manifest.

## 7. Rollback runbook

**Trigger:** any of — probe failure after a deploy, parity report
mismatch (KPI-2), command regression (KPI-4), data-loss evidence (KPI-3).

**Mechanism:** env flip + restart (ADR-006 D9). The JSON tree is kept
read-only as a one-cycle fallback after cutover, so rollback is a
restart with a different env var.

```
# 1. On the VM:
cd /opt/discord-bot
# 2. Flip the singleton to the JSON impl:
sed -i 's/^SCRAPCODE_REPO_BACKEND=.*/SCRAPCODE_REPO_BACKEND=json/' .env
   # (or: edit .env and set SCRAPCODE_REPO_BACKEND=json)
# 3. Restart:
sudo systemctl restart discord-bot
# 4. Verify the bot is up on the JSON backend:
sudo journalctl -u discord-bot -n 30 --no-pager | grep -i 'logged in as'
sudo systemctl status discord-bot --no-pager
# 5. Confirm a command works end-to-end (on-demand leaderboard, fast feedback):
#    in Discord: /view_leaderboard — should render from JSON files
```

**Constraints:**
- The JSON tree must still be on disk at `/opt/discord-bot/clusters/`.
  Do NOT delete it during the one-cycle read-only fallback window
  (US-010). Deletion is a separate, explicit step after one full hourly
  cycle passes on SQLite with no rollback.
- Any writes that happened on SQLite during the failed cutover are NOT
  in the JSON tree. If the cutover ran for part of an hourly cycle
  before rollback, those hits/bombs are lost from the JSON view. This
  is acceptable for a single-cycle rollback; if a longer SQLite run
  preceded rollback, re-running the JSON→SQLite migration (Slice 03
  runbook §9) against the current JSON + a fresh DB is the way to
  re-capture parity. (The migration is idempotent — upsert-based.)
- The probe is **skipped** on the JSON backend, so a wrong
  `SCRAPCODE_DB_KEY` does not block rollback.

**Rollback to a previous DB backup** (if the SQLite data itself is
corrupted, not just the code):
```
sudo systemctl stop discord-bot
cp /opt/discord-bot/data/backups/scrapcode-<good-timestamp>.db /opt/discord-bot/data/scrapcode.db
# remove WAL sidecars so SQLite rebuilds them from the snapshot:
rm -f /opt/discord-bot/data/scrapcode.db-wal /opt/discord-bot/data/scrapcode.db-shm
# keep SCRAPCODE_REPO_BACKEND=sqlite; the backup already matches the head alembic version
sudo systemctl start discord-bot
```

## 8. Deploy runbook (standard Slice 04 cutover)

Preconditions (gated by the prior slices):
- Slice 01: ≥17 contract tests green against the JSON impl (KPI-1).
- Slice 02: contract tests green against BOTH impls (parametrized); probe
  passes locally against a copy of the prod data.
- Slice 03: JSON→SQLite migration run against a COPY of the prod
  `clusters/` tree (US-005); parity report exit 0 (KPI-2).
- Slice 04 code merged to `main` via GitHub Flow PR.

```
# === A. Pre-deploy (on the VM, before touching the service) ===
cd /opt/discord-bot

# A1. Snapshot current state (the JSON source-of-truth at cutover time):
sudo systemctl stop discord-bot
tar czf ~/clusters-pre-cutover-$(date +%Y%m%dT%H%M%S).tgz clusters/
# (this is in addition to the hourly DB backup; protects the JSON tree too)

# A2. Pull the cutover commit:
git fetch origin
git checkout main
git pull --ff-only
git log --oneline -1   # confirm the expected commit

# A3. Install new deps into the reused venv:
.venv/bin/pip install -r requirements.txt

# A4. Generate SCRAPCODE_DB_KEY (one-time; reuse if already set):
grep -q '^SCRAPCODE_DB_KEY=' .env || \
  python -c "from cryptography.fernet import Fernet; print('SCRAPCODE_DB_KEY=' + Fernet.generate_key().decode())" >> .env

# A5. Ensure SCRAPCODE_REPO_BACKEND is unset or =sqlite (the post-cutover default):
sed -i 's/^SCRAPCODE_REPO_BACKEND=.*/# SCRAPCODE_REPO_BACKEND=sqlite  # default post-cutover/' .env

# A6. Take a pre-cutover DB backup (the empty DB or the migrated DB from Slice 03):
sqlite3 data/scrapcode.db "VACUUM INTO 'data/backups/scrapcode-pre-cutover-$(date +%Y%m%dT%H%M%S).db'"

# === B. Cutover ===

# B1. Run the data migration against a COPY of the production JSON tree
#     (ADR-006 / DISCUSS US-005 AC: the migration NEVER reads
#     /opt/discord-bot/clusters/ directly). The migration writes to a
#     TEMP DB; on PASS, the temp DB is moved into place as the production
#     DB. This way a failed migration never touches the production DB
#     path (and the production clusters/ tree is never read directly).
cp -r clusters/ clusters-migration-copy/
.venv/bin/python -m bot.db.migrations_json_to_sqlite \
    --source clusters-migration-copy/ \
    --db data/scrapcode-migration-tmp.db \
    --report data/backups/parity-cutover-$(date +%Y%m%dT%H%M%S).json
MIGRATE_RC=$?
if [ $MIGRATE_RC -ne 0 ]; then
    echo "ABORT: migration failed (rc=$MIGRATE_RC). Production DB untouched. See parity report."
    rm -f data/scrapcode-migration-tmp.db
    rm -rf clusters-migration-copy/
    exit $MIGRATE_RC
fi
# Parity PASS — promote the temp DB to the production path:
mv data/scrapcode-migration-tmp.db data/scrapcode.db
rm -rf clusters-migration-copy/
# (the parity report JSON under data/backups/ is the KPI-2 artifact)

# B2. Start the service (probe runs at startup; failure = unit goes 'failed'):
sudo systemctl start discord-bot

# B3. Verify:
sudo journalctl -u discord-bot -n 50 --no-pager | grep -iE 'health.startup|logged in as'
sudo systemctl status discord-bot --no-pager   # expect Active: active (running)

# B4. Smoke check in Discord: /view_leaderboard, /view_bombs, /get_replay
#     (on-demand commands reflect new code immediately; live leaderboards lag ~1h)

# === C. Post-cutover (one-cycle observation) ===
# C1. Leave the JSON tree in place, READ-ONLY, for one full hourly cycle.
#     Do not delete clusters/ yet.
# C2. After one hourly auto_update + cap_detect cycle with no rollback trigger:
#     - Confirm KPI-3 grep: no path.write_text in bot/tracker.py
#     - Confirm KPI-4: snapshot diff of /view_leaderboard etc. == 0
# C3. Retire the JSON tree:
mv clusters/ clusters-retired-$(date +%Y%m%d)
#     (keep it tarred somewhere for one more cycle if paranoid; then delete)
# C4. Confirm the bot is still up and the next hourly cycle runs clean:
sudo journalctl -u discord-bot -n 100 --no-paper | grep -i error
```

## 9. Data migration runbook (Slice 03 detail, US-005)

The migration is **always run against a COPY of the production
`clusters/` tree**, never against `/opt/discord-bot/clusters/` directly
(ADR-006 constraint, DISCUSS constraint). This is true both for Slice 03
development and for the cutover-day run (step B1 above operates on the
production tree, but only after step A1 snapshotted it; the migration
itself is upsert-idempotent so a re-run is safe).

**Development / dry-run (off-VM or in a staging path):**
```
# 1. Copy prod data down (operator scp):
scp -r krewsayder@discord-bot-vm:/opt/discord-bot/clusters/ ~/scrapcode-dry-run/clusters/

# 2. Run the migration against the copy + a fresh DB:
cd ~/scrapcode-dry-run
python -m bot.db.migrations_json_to_sqlite --source clusters/ --db scrapcode-dry-run.db

# 3. Inspect the parity report (per-table JSON=N SQL=N PASS|MISMATCH).
#    Any MISMATCH exits non-zero and blocks Slice 04.
cat parity-report-*.json
```

**Cutover day (on the VM, step B1):**
```
# The migration NEVER reads /opt/discord-bot/clusters/ directly (ADR-006 /
# DISCUSS US-005 AC). It reads a copy; it writes to a temp DB that is moved
# into place on PASS so a failed migration cannot corrupt the production DB.
cp -r clusters/ clusters-migration-copy/
.venv/bin/python -m bot.db.migrations_json_to_sqlite \
    --source clusters-migration-copy/ \
    --db data/scrapcode-migration-tmp.db \
    --report data/backups/parity-cutover-$(date +%Y%m%dT%H%M%S).json
# on PASS (exit 0):
mv data/scrapcode-migration-tmp.db data/scrapcode.db
rm -rf clusters-migration-copy/
# on FAIL: rm data/scrapcode-migration-tmp.db; rm -rf clusters-migration-copy/
```

**Idempotency:** the migration is upsert-based (US-006 dedup logic);
running it twice against the same source + DB produces the same row
state (keep-max(damage) on conflict). Safe to re-run.

**Rollback of the migration:** the migration is an Alembic data revision
+ the one-shot script. To undo:
```
.venv/bin/alembic downgrade -1      # reverts the data revision
# OR, for a full reset:
rm /opt/discord-bot/data/scrapcode.db /opt/discord-bot/data/scrapcode.db-wal /opt/discord-bot/data/scrapcode.db-shm
.venv/bin/alembic upgrade head      # rebuilds the schema empty
```
Then re-run the migration if needed.

**Parity report format** (KPI-2 artifact):
```json
{
  "generated_at": "2026-07-18T...",
  "source": "clusters/",
  "db": "data/scrapcode.db",
  "tables": {
    "guilds":               {"json": 1, "sql": 1, "status": "PASS"},
    "player_registrations":  {"json": 7, "sql": 7, "status": "PASS"},
    "players":               {"json": 42, "sql": 42, "status": "PASS"},
    "battle_hits":           {"json": 311, "sql": 311, "status": "PASS"},
    "bomb_hits":             {"json": 89, "sql": 89, "status": "PASS"},
    "replay_entries":        {"json": 17, "sql": 17, "status": "PASS"},
    "replay_threads":        {"json": 9, "sql": 9, "status": "PASS"}
  },
  "overall": "PASS"
}
```
Any `MISMATCH` → `overall: "FAIL"` + exit 1.

## 10. Changed assumptions (back-propagation)

This DEVOPS wave overrides one DESIGN-wave assumption:

- **`SCRAPCODE_DB_PATH` default.** ADR-006 D1 / DESIGN `wave-decisions.md`
  record the default as `clusters.db` beside the existing `clusters/`
  tree. This DEVOPS wave changes the default to `data/scrapcode.db`
  (relative to `WorkingDirectory=/opt/discord-bot`). Rationale: the
  `data/` path is already gitignored (`.gitignore` line 9); placing the
  DB there keeps "JSON source tree" (`clusters/`) and "new DB artifact"
  (`data/`) visually separate, which matters during the one-cycle
  read-only fallback when both exist. The env var override still lets
  the operator pick any path. Recorded in `wave-decisions.md` §Upstream
  Changes; flagged here per the nw-devops back-propagation protocol.

No other DESIGN assumption is changed.