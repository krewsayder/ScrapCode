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

tail -3 .env                  # eyeball the result
```

**Back up `SCRAPCODE_DB_KEY` alongside `DISCORD_TOKEN`.** It is not in the
database. A DB backup without this key cannot decrypt any `api_key` column.

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
table. Clean up with `rm -f data/scrapcode-tmp.db; rm -rf clusters-migration-copy/`
and restart the bot on JSON (`sudo systemctl start discord-bot`); nothing has
changed yet.

## Step 5 — Promote and start

```bash
mv data/scrapcode-tmp.db data/scrapcode.db
rm -rf clusters-migration-copy/
sudo systemctl start discord-bot
```

## Step 6 — Confirm you are actually on SQLite

```bash
grep -i 'falling back to JsonClusterRepository' discord.log && echo "!! STILL ON JSON !!"
sudo systemctl status discord-bot --no-pager
sudo journalctl -u discord-bot -n 50 --no-pager | grep -i 'logged in as'
```

**The grep must find nothing.** If it prints, you are running on JSON: do not
proceed, and do not retire `clusters/`. Check that `.env` has all three
`SCRAPCODE_*` vars and that `data/scrapcode.db` exists and is non-empty.

Then smoke-check in Discord: `/view_leaderboard`, `/view_bombs`, `/get_replay`.
On-demand commands reflect the new backend immediately; live leaderboards lag up
to an hour.

## Step 7 — Observation cycle

Leave `clusters/` in place, untouched, for one full hourly cycle
(`auto_update` + `cap_detect`). Then:

```bash
sudo journalctl -u discord-bot -n 200 --no-pager | grep -iE 'error|refused|falling back'
```

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
