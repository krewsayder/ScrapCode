# Cutover runbook — JSON → SQLite (executable)

> **This is the executable runbook.** `platform-architecture.md` §8 is the
> DEVOPS-wave design document; its command block predates two fixes and will
> fail as written (it never sources `.env` before the migration, so the
> migration dies on an empty Fernet key). Follow **this** file.
>
> Target: single Linux VM, `/opt/discord-bot`, systemd unit `discord-bot`.
> Requires `main` at `53025c2` or later.

## Why the order matters

Three behaviours bite if you reorder the steps:

1. **The migration CLI does not read `.env`.** `run_migration` reads
   `SCRAPCODE_DB_KEY` from the *process environment*
   (`bot/db/migrations_json_to_sqlite.py`). Without the `set -a` source line in
   step 4 it fails on an empty Fernet key.
2. **The database must exist before the bot starts.** `build_repo()` in
   `bot/guilds.py` falls back to JSON (ADR-006 D9 safety net) if
   `SCRAPCODE_DB_PATH` is missing while its parent directory exists. Promote the
   DB (step 5) *before* `systemctl start`.
3. **Promote only on `PASS`.** The migration writes to a temp file; a failed run
   must never land on the production DB path.

You do **not** need to `mkdir data/` — `_apply_schema` creates it.

## Step 0 — Pre-flight

```bash
cd /opt/discord-bot
systemctl cat discord-bot     # confirm WorkingDirectory + ExecStart python path
git log --oneline -1          # note the current commit, for rollback
```

If `ExecStart` is not `/opt/discord-bot/.venv/bin/python main.py`, adjust the
`.venv/bin/...` paths below to match.

## Step 1 — Back up

```bash
sudo systemctl stop discord-bot
tar czf ~/scrapcode-pre-cutover-$(date +%Y%m%dT%H%M%S).tgz clusters/ .env
ls -lh ~/scrapcode-pre-cutover-*.tgz    # confirm it is non-trivial in size
```

Keep this. It is the rollback of last resort and it contains `.env`.

## Step 2 — Pull + dependencies

```bash
git pull --ff-only
git log --oneline -1          # expect 53025c2 or later
.venv/bin/pip install -r requirements.txt
```

New deps: SQLAlchemy 2.0, Alembic, aiosqlite, cryptography.

## Step 3 — Configure `.env`

```bash
# Guarantee a trailing newline so appends do not corrupt the last line:
[ -s .env ] && [ "$(tail -c1 .env)" != "" ] && echo >> .env

grep -q '^SCRAPCODE_DB_KEY=' .env || \
  .venv/bin/python -c "from cryptography.fernet import Fernet; print('SCRAPCODE_DB_KEY='+Fernet.generate_key().decode())" >> .env
grep -q '^SCRAPCODE_DB_PATH=' .env      || echo 'SCRAPCODE_DB_PATH=data/scrapcode.db' >> .env
grep -q '^SCRAPCODE_REPO_BACKEND=' .env || echo 'SCRAPCODE_REPO_BACKEND=sqlite' >> .env
```

Verify **without printing the key** — never `cat` or `tail` `.env` in a terminal
whose scrollback you might paste somewhere. This runs the same Fernet
round-trip as probe step 3, so a bad key fails here rather than midway through
the migration:

```bash
grep -E '^SCRAPCODE_(DB_PATH|REPO_BACKEND)=' .env
set -a; . ./.env; set +a
.venv/bin/python -c "
from cryptography.fernet import Fernet; import os
k = os.environ.get('SCRAPCODE_DB_KEY','')
f = Fernet(k.encode()); assert f.decrypt(f.encrypt(b'x')) == b'x'
print('fernet key valid, len', len(k))"
```

Expect the two `SCRAPCODE_` lines and `fernet key valid, len 44`.

**Back up `SCRAPCODE_DB_KEY` alongside `DISCORD_TOKEN`.** It is not in the
database. A DB backup without this key cannot decrypt any `api_key` column.
The step-1 tarball was taken *before* the key existed, so it does **not**
contain it — take a fresh snapshot now, and put the key in a password manager
rather than trusting this one VM:

```bash
cp .env ~/scrapcode-env-postkey-$(date +%Y%m%dT%H%M%S).bak
chmod 600 ~/scrapcode-env-postkey-*.bak
```

## Step 4 — Migrate

```bash
set -a; . ./.env; set +a      # REQUIRED — the migration reads the process env
cp -r clusters/ clusters-migration-copy/

.venv/bin/python -m bot.db.migrations_json_to_sqlite \
    --source clusters-migration-copy/ \
    --db data/scrapcode-tmp.db \
    --report data/parity-cutover.json
echo "migration exit=$?"                      # MUST be 0

grep '"overall"' data/parity-cutover.json     # MUST be "PASS"
```

**Gate.** If exit is non-zero or `overall` is not `PASS`, stop. The production
DB path is untouched. Read `data/parity-cutover.json` — it names the mismatched
table. (On a crash rather than a parity failure the report is not written at
all; the traceback is the diagnosis.)

To retry after fixing the cause, always start from a clean slate — the temp DB
holds a partial schema and partial rows, and re-running over it produces
misleading errors:

```bash
rm -f data/scrapcode-tmp.db data/parity-cutover.json
rm -rf clusters-migration-copy/
```

To abandon the cutover instead, run that cleanup and start the bot on JSON:
`sudo systemctl start discord-bot`. Nothing has changed — `clusters/` was only
ever read from a copy.

## Step 5 — Verify the database, then promote

Run this **before** promoting, while the file is still named `-tmp` and costs
nothing to discard. `probe()` (ADR-006 D8) is implemented but not wired into
startup, so nothing refuses a bad database at boot — this is that gate, by hand.

```bash
ls -la data/          # -wal / -shm must NOT be present; see below
.venv/bin/python - <<'PYEOF'
import sqlite3
c = sqlite3.connect('data/scrapcode-tmp.db')
print("integrity :", c.execute("PRAGMA integrity_check").fetchone()[0])
print("journal   :", c.execute("PRAGMA journal_mode").fetchone()[0])
print("alembic   :", c.execute("SELECT version_num FROM alembic_version").fetchone()[0])
for t in ("clusters", "guilds", "players", "player_registrations",
          "battle_hits", "bomb_hits", "replay_threads", "replay_entries"):
    print("   %-20s %d" % (t, c.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]))
c.close()
PYEOF
```

Expect `integrity : ok`, `journal : wal`, `alembic : 0002` (the compiled head),
and counts matching the parity report. If `scrapcode-tmp.db-wal` or `-shm`
exist, the migration did not close cleanly — do **not** move the `.db` alone,
or you promote a database missing its most recent writes.

Then promote:

```bash
mv data/scrapcode-tmp.db data/scrapcode.db
rm -rf clusters-migration-copy/
sudo systemctl start discord-bot
```

## Step 6 — Confirm you are actually on SQLite

**Do not use `grep ... discord.log` for this.** `build_repo()` runs at import
time, before `bot.run()` installs the `RotatingFileHandler`, so a D9 fallback
warning goes to stderr and lands in the journal — never in `discord.log`. A
clean grep there proves nothing. Same for `on_ready`, which uses `print`.

Check the file descriptors instead. This is positive evidence: if the bot had
taken the JSON fallback it would never open this file.

```bash
PID=$(systemctl show -p MainPID --value discord-bot)
sudo ls -l /proc/$PID/fd | grep -i scrapcode || echo 'NO DB HANDLE — ON JSON'
sudo journalctl -u discord-bot -n 50 --no-pager \
  | grep -iE 'logged in as|synced|falling back'
```

Expect descriptors for `scrapcode.db`, `-wal` and `-shm`; `Logged in as ...`;
and no `falling back`. (Quote that fallback string with **single** quotes if you
add an `&& echo` — `!!` inside double quotes is history-expanded by interactive
bash into your previous command.)

If you see `NO DB HANDLE`, you are on JSON: do not proceed and do not retire
`clusters/`. Check that `.env` has all three `SCRAPCODE_*` vars and that
`data/scrapcode.db` exists and is non-empty.

Then smoke-check in Discord: `/view_leaderboard`, `/view_bombs`, `/get_replay`.
On-demand commands reflect the new backend immediately; live leaderboards lag up
to an hour.

Expect `/view_bombs` to render **fewer** rows than it did on JSON — one line per
partition where identical copies used to repeat. That is the `bomb_hits`
uniqueness constraint doing its job, not missing data.

## Step 7 — Observation cycle

Leave `clusters/` in place, untouched, for one full hourly cycle
(`auto_update` + `cap_detect`). Then:

```bash
sudo journalctl -u discord-bot -n 200 --no-pager | grep -iE 'error|refused|falling back'
```

**Prove a durable write before retiring anything.** Row counts alone will not
show one: the repository writes by delete-and-reinsert and by upsert, so a
healthy cycle can leave every count unchanged while the WAL grows. Name a value
the bot must have written after cutover instead:

```bash
.venv/bin/python - <<'PYEOF'
import sqlite3
c = sqlite3.connect('data/scrapcode.db')
print("max players.last_validated:",
      c.execute("SELECT MAX(last_validated) FROM players").fetchone()[0])
c.close()
PYEOF
```

`last_validated` is stamped by `PlayerService.refresh_guild` on every successful
roster refresh. If the maximum is later than the moment you started the bot, the
write path is confirmed against disk. If it still matches migration time, the
loops have not written — find out why before giving up your rollback source.

Note that a failing Tacticus API produces quiet logs *and* no writes, so "an
hour with no errors" can mean the loops never ran. Check the timestamp, not the
silence.

Clean? Retire the JSON tree:

```bash
mv clusters/ clusters-retired-$(date +%Y%m%d)
```

Keep the directory (or the step-1 tarball) at least one more cycle before
deleting.

## Rollback

Any time before step 7 retires `clusters/`:

```bash
sed -i 's/^SCRAPCODE_REPO_BACKEND=.*/SCRAPCODE_REPO_BACKEND=json/' .env
sudo systemctl restart discord-bot
sudo journalctl -u discord-bot -n 30 --no-pager | grep -i 'logged in as'
```

Caveat: writes that landed in SQLite after cutover are not in the JSON tree. For
a same-cycle rollback that is at most one hour of hits. If SQLite ran longer,
re-run step 4 against the current `clusters/` into a fresh DB to re-establish
parity — the migration is upsert-based and idempotent.

If the SQLite file itself is corrupt rather than the code:

```bash
sudo systemctl stop discord-bot
cp data/backups/<good-snapshot>.db data/scrapcode.db
rm -f data/scrapcode.db-wal data/scrapcode.db-shm
sudo systemctl start discord-bot
```

## Known gaps at cutover time

- **`probe()` is not wired into startup.** `Database.probe()` (ADR-006 D8) is
  implemented and tested but nothing calls it during boot, so a corrupt or
  stale-schema DB will *not* refuse startup. Step 6's grep is your manual
  substitute. Wiring it is ~3 lines.
- Backup timer (`platform-architecture.md` §6) is not installed. Until it is,
  take manual snapshots:
  `sqlite3 data/scrapcode.db "VACUUM INTO 'data/backups/scrapcode-$(date +%Y%m%dT%H%M%S).db'"`
- Replay entries are all assigned to the single production server (ADR-006 D11).
