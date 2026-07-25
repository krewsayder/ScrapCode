# Evolution — sqlite-backend

> **Status:** Feature complete in worktree `sqlite-backend` (branch
> `worktree-sqlite-backend`). Awaiting operator cutover on the production VM.
> **Dates:** 2026-07-18 → 2026-07-20.

## What changed

ScrapCode's data layer was migrated from flat per-server JSON files to an
embedded SQLite database, behind the existing `ClusterRepository` ABC. The
domain model is unchanged; the repository gained a second implementation
(`SqlAlchemyClusterRepository`) and the composition root selects it
env-driven (`SCRAPCODE_REPO_BACKEND`, default `sqlite`).

**Stack added** (`requirements.txt` → project `.venv`): SQLAlchemy 2.0.51,
Alembic 1.18.5, aiosqlite 0.22.1, cryptography 49.0.0 (Fernet at-rest
encryption of `api_key`).

## The components

- `bot/db/models.py` — 12 SQLAlchemy 2.0 declarative models (clusters,
  role_tiers, guilds, guild_member_roles, player_registrations, players,
  battle_hits, bomb_hits, replay_threads, replay_entries, live_leaderboards,
  live_lb_messages). `battle_hits_simple` and `update_channel_id` deliberately
  absent (ADR-006 D4/D12). `capped_state` is the `is_capped` column on
  `player_registrations` (D5).
- `bot/db/alembic/` — env + `0001_baseline_schema` + `0002_battle_hero_details`.
  `alembic upgrade head` creates the full schema on a fresh file.
- `bot/db/session.py` — `Database` engine factory (WAL pragmas, foreign_keys),
  `session_scope`, and the `probe()` 4-step startup health gate
  (WAL / alembic-head / Fernet round-trip / write-rollback) that refuses
  startup via `ProbeRefusedError` + a `health.startup.refused` log record.
- `bot/db/secrets.py` — Fernet encrypt/decrypt + HKDF-derived HMAC for the
  deterministic `api_key_hmac` uniqueness column.
- `bot/db/migrations_json_to_sqlite.py` — one-shot JSON→SQLite data migration
  with a row-count parity report; idempotent; loud-fail-on-mismatch with
  rollback; seeds `replay_threads` from the historical forum/map constants.
- `bot/repository_sqlalchemy.py` — the second ABC impl. `battle_hits` upsert
  is `ON CONFLICT … DO UPDATE SET damage = MAX(…)` (keep-max, preserves the
  `try_insert` contract); `upsert_guild_hits` wraps battle+bomb in one
  transaction per guild (ADR-006 D6); replay methods enforce per-tenant URL
  uniqueness.
- `bot/repository.py` — ABC extended with 4 ADR-007 season-hit methods + 6
  replay methods + `SupportsProbe` Protocol; `get_guild_data_path` removed
  (JSON-only, retired). `JsonClusterRepository` kept as the env-driven
  rollback impl.
- `bot/guilds.py` — `build_repo()` env-driven singleton with missing-file
  fallback to JSON for one cycle. `bot/tracker.py` write path + `bot/embeds.py`
  + the cogs' read paths rewired through the ABC. `bot/cogs/replay_cog.py`
  routes through `replay_entries`/`replay_threads` (the global
  `replay_index.json` leak is closed — ADR-004 §3). `main.py` retired
  `file_lock` (WAL transactions replace it) and added `RotatingFileHandler`
  (10 MB × 5).
- `pyproject.toml` — import-linter contracts (cogs → ABC only; no `bot.db.*`
  leakage).

## Decisions (ADR-006 / ADR-007)

See `docs/product/architecture/adr-006-sqlite-storage-backend.md` and
`adr-007-repo-read-methods-get-guild-data-path-deprecation.md`. Headlines:
storage stack (D1), repo-ABC swap seam (D2/D3), `battle_hits_simple` dropped
(D4), `capped_state` column (D5), WAL + one-txn-per-guild (D6), Fernet +
`api_key_hmac` (D7), `probe()` Earned-Trust gate (D8), env-driven singleton +
missing-file fallback (D9), `replay_threads` seed (D10), single-server replay
assignment — true multi-tenant deferred (D11), `update_channel_id` dropped +
`PlayerListMigrator` v1→v2 runs once (D12), OOP paradigm (D13).

## Verification at close

- Acceptance suite: 118 passed, 1 xfailed (AP6 read-only-fs — Windows
  POSIX-chmod limit, documented), 0 skipped. `bot/tests`: 22 passed.
- `__SCAFFOLD__` markers in production: 0. Integrity: all 13 DES steps
  complete. import-linter: 4/4 contracts kept.
- Phase-4 adversarial review: 5 findings — 2 fixed (TOCTOU on
  `upsert_replay_entry`, dead `import asyncio`), 1 documented follow-up
  (migration `guild_id` case-sensitivity — production-unreachable), 2
  optional L2 duplications noted. See
  `docs/feature/sqlite-backend/distill/wave-decisions.md` § "DELIVER Phase 4".

## Commits (this feature)

`b716ed2` → `530a335` (17 commits) on `worktree-sqlite-backend`, branched
from `docs/architecture-baseline`. Per-step TDD with DES-traced phases; the
`deliver/roadmap.json` + `deliver/execution-log.json` are gitignored by the
DES tooling and live on disk as the trace record.

## Operator cutover runbook

Authoritative version in
`docs/feature/sqlite-backend/devops/platform-architecture.md`. Synopsis:

1. Stop the bot (`systemctl stop discord-bot`). Tar-snapshot `clusters/`.
2. `git pull` + `pip install -r requirements.txt` into the venv.
3. Generate `SCRAPCODE_DB_KEY` (a Fernet key) into `.env`; set
   `SCRAPCODE_DB_PATH=data/scrapcode.db` and `SCRAPCODE_REPO_BACKEND=sqlite`.
4. `cp -r clusters/ clusters-migration-copy/` and run the migration against
   the COPY: `python -m bot.db.migrations_json_to_sqlite --source
   clusters-migration-copy --db data/scrapcode.db --report
   data/backups/parity-cutover.json` — exit 0 + `overall:"PASS"` required.
5. `systemctl start discord-bot` — the `probe()` runs at startup and refuses
   to start on a bad DB. Smoke-check `/view_leaderboard`, `/view_bombs`,
   `/get_replay` in Discord.
6. Keep `clusters/` read-only on disk for one observation cycle as the
   rollback fallback, then retire it.
7. **Rollback** (if needed): set `SCRAPCODE_REPO_BACKEND=json` in `.env`,
   `systemctl restart discord-bot`. The probe is skipped on the JSON backend.

## Known follow-ups

1. **Migration `guild_id` case-sensitivity** (review Finding 1). Apply the
   path-lookup fix (original-case dir for the filesystem path, lowercased slug
   for the save) on a Linux box with a mixed-case-slug fixture, or add a
   Linux-only CI job. Production-unreachable today (`register_guild`
   lowercases).
2. **`cap_detect` loop body untested.** The `import asyncio` regression
   slipped past the suite because no test exercises `tasks_cog.cap_detect`'s
   `asyncio.gather` path. Add an integration test that drives the loop body.
3. **Mutation testing.** Strategy is `pre-release` per `CLAUDE.md` (no
   mutation tool in the stack). Add `mutmut`/`cosmic-ray` to `requirements.txt`
   and run a per-feature gate before the next release if test-quality rigor
   is wanted sooner.
4. **Replay multi-tenancy** (ADR-006 D11). True per-server replay
   partitioning is deferred; the single production server's entries are all
   assigned to `PROD_SERVER_ID`. Address when a second Discord server adopts
   the bot.
5. **`docs/evolution/` archive + push.** This document is the archive; the
   branch (`worktree-sqlite-backend`) still needs to be merged to
   `docs/architecture-baseline` / `main` and pushed (operator's call).