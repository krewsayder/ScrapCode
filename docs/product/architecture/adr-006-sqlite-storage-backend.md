# ADR-006: SQLite via SQLAlchemy 2.0 + Alembic + aiosqlite is the storage successor

- **Status:** Accepted — DESIGN wave, feature `sqlite-backend`
- **Date:** 2026-07-18
- **Supersedes:** the "accepted successor" note in
  [ADR-002](adr-002-storage-backend-json-legacy.md) (ADR-002 remains valid for
  the as-built JSON layer and the migration-source framing; this ADR records
  the concrete successor technology and the cutover decisions)
- **Related:** [ADR-004](adr-004-multi-tenancy-isolation.md),
  [data-dictionary.md §4](data-dictionary.md#4-migration-mapping-backend-agnostic),
  [ADR-007](adr-007-repo-read-methods-get-guild-data-path-deprecation.md)

## Context

ADR-002 accepted SQLite as the successor to the per-server JSON layer and
pinned the non-atomic-write + silent-empty-read data-loss trap that the
migration must retire. The data dictionary §4 enumerates the backend-agnostic
table mapping. The DISCUSS wave (`docs/feature/sqlite-backend/discuss/`)
decomposed the work into 11 stories across 4 carpaccio slices and deferred 5
decisions plus the `api_key` secrets-store choice to DESIGN. This ADR records
the concrete successor technology and resolves the deferred decisions.

Quality-attribute priorities for this feature, in order:
**atomicity > parity/zero-regression > testability > maintainability >
time-to-market**. Scalability is NOT a priority — the deployment is one
process on one VM serving one Discord server (brief §2.6, ADR-004).

The `ClusterRepository` ABC (`bot/repository.py`) is the dependency-inversion
seam: it already abstracts the JSON impl, and the SQLite impl slots in as a
second implementation behind the same interface (principle: extend existing,
do not create new). A codebase audit during DESIGN surfaced a contradiction
in brief §4 ("`JsonClusterRepository` is the only reader/writer of the
per-server tree except `bot/tracker.py`…"): the season files are ALSO read
directly by `bot/embeds.py::load_leaderboard_file`, called from
`view_cog.py`, `admin_cog.py`, and `tasks_cog.py` via
`repo.get_guild_data_path(...)` (5 call sites total, not 1). That
contradiction and its resolution are recorded in
[ADR-007](adr-007-repo-read-methods-get-guild-data-path-deprecation.md); this
ADR depends on it.

## Decision

### D1 — Storage stack

1. **SQLite (WAL mode)** as the relational backend, single file at a path
   from `.env` (`SCRAPCODE_DB_PATH`, default `clusters.db` beside the existing
   `clusters/` tree). WAL mode is set on every connection
   (`PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA foreign_keys=ON`)
   so the two hourly task loops (`cap_detect`, `auto_update`) can read/write
   concurrently without a process-wide lock (see D6).
2. **SQLAlchemy 2.0** (ORM, declarative models) as the data-access layer.
   License: MIT (https://docs.sqlalchemy.org/).
3. **Alembic** for schema + data migrations. License: MIT
   (https://alembic.sqlalchemy.org/). One baseline revision (schema) + one
   data-migration revision (JSON → SQLite) + the `replay_threads` seed (D5).
   A `schema_migrations` table replaces the per-file `__meta__.version` scheme
   (data-dictionary §4).
4. **aiosqlite** as the async driver so the transaction commit does not block
   the discord.py event loop. License: MIT (https://aiosqlite.readthedocs.io/).
5. **cryptography.Fernet** for the `api_key` columns (see D7). License:
   Apache 2.0 / BSD (https://cryptography.io/).

All five are mature OSS with active maintenance; no proprietary component is
introduced (OSS-first principle).

### D2 — Architecture pattern

**Modular monolith with dependency-inversion (ports-and-adapters).** The
`ClusterRepository` ABC is the port; `JsonClusterRepository` and the new
`SqlAlchemyClusterRepository` are the two driven adapters. The domain model
(`bot/models.py` `Cluster` / `Guild` dataclasses) is unchanged. This matches
the as-built pattern (ADR-002 §4: "the repository is already abstract") and
the team size (single operator-dev, single process). Simpler alternatives
were considered and rejected — see "Alternatives."

### D3 — Component boundaries (all dependencies point inward toward the domain)

| Component | Responsibility | Dependency direction |
|-----------|----------------|----------------------|
| `bot/db/models.py` (NEW) | SQLAlchemy 2.0 declarative ORM models for the 8 easy entities + `battle_hits` + `bomb_hits` + `replay_entries` + `replay_threads` (per data-dictionary §4). | Imports `sqlalchemy` only. Imported by `bot/db/session.py` and the Alembic env. NOT importable from cogs. |
| `bot/db/session.py` (NEW) | `Database` factory: builds the async SQLAlchemy engine + session factory, sets WAL pragmas, runs the startup probe (D8), exposes a `session_scope()` context manager. Reads `SCRAPCODE_DB_PATH` / `SCRAPCODE_DB_KEY` from env. | Imports `sqlalchemy`, `aiosqlite`, `bot/db/models.py`. Imported by the composition root (`bot/guilds.py`) only. |
| `bot/db/alembic/` (NEW) | Alembic env + baseline schema revision + data-migration revision (JSON → SQLite) + `replay_threads` seed (D5). | Imports `bot/db/models.py`. Run via `alembic upgrade head`; not imported at runtime. |
| `bot/db/migrations_json_to_sqlite.py` (NEW, one-shot) | Reads the operator-copied `clusters/` tree, runs `PlayerListMigrator._migrate_v1_to_v2` once per v1 file, populates the 8 easy-entity tables + `battle_hits` + `bomb_hits` + `replay_entries` + `replay_threads`, emits the per-table parity report. Fernet-encrypts `api_key` on insert. Idempotent + rollback-able via `alembic downgrade`. | Imports `bot/db/models.py`, `bot/migrations/player_list_migrations.py`, `cryptography.fernet`. NOT imported at runtime. |
| `bot/repository_sqlalchemy.py` (NEW) | `SqlAlchemyClusterRepository(ClusterRepository)` — second impl behind the existing ABC. Implements the 11 existing ABC methods + the new read methods from ADR-007 (`load_battle_hits`, `load_bomb_hits`, `upsert_battle_hits`, `upsert_bomb_hits`). Decrypts `api_key` on read. | Imports `bot/repository.py` (the ABC), `bot/db/session.py`, `bot/db/models.py`, `cryptography.fernet`. Imported by `bot/guilds.py` only (the composition root). |
| `bot/guilds.py` (MODIFIED) | The composition root. The `repo = JsonClusterRepository()` singleton (line 7) becomes env-driven: `SCRAPCODE_REPO_BACKEND=json\|sqlite` (default `sqlite` post-cutover, `json` is the rollback path). | Imports one of the two impls based on env. |
| `bot/tracker.py` (MODIFIED in Slice 04) | `process_api_response(api_data, season, discord_server_id, guild_id)` — the `data_dir` parameter is removed; reads/writes go through the repo's `upsert_battle_hits` / `upsert_bomb_hits`. `load_json`, `save_json`, `try_insert`, and the `BATTLE_SIMPLE_FILE` write (D4) are removed. `get_tier_key`, `get_roster_key` remain (pure parsers). | Imports `bot/repository.py` ABC only (via the `repo` singleton). No longer imports `pathlib.Path` for writes. |
| `bot/embeds.py` (MODIFIED in Slice 04) | `load_leaderboard_file` is removed; `build_battle_messages` / `build_bomb_messages` consume dicts from the repo's `load_battle_hits` / `load_bomb_hits` instead. | Imports `bot.guilds` (for the read wrappers), not `pathlib.Path`. |
| `bot/cogs/replay_cog.py` (MODIFIED in Slice 04) | Reads/writes `replay_entries` / `replay_threads` via the repo. `REPLAY_INDEX_FILE`, `load_replay_index`, `save_replay_index` removed. Thread IDs looked up from `replay_threads` (D5); `FORUM_CHANNELS` / `MAP_THREADS` constants removed. | Imports `bot.guilds` only. |

Genuinely new components are `bot/db/models.py`, `bot/db/session.py`,
`bot/db/alembic/`, `bot/db/migrations_json_to_sqlite.py`, and
`bot/repository_sqlalchemy.py`. Each is justified: SQLAlchemy requires
declarative models and a session factory (no existing equivalent in the
codebase); Alembic requires its own env; the JSON → SQLite migration is a
one-shot data movement the codebase has no analog for (the existing
`bot/migrations/` scripts are runtime-shape migrators, not backend-swap
migrators); and the SQLite adapter is the second implementation behind the
existing ABC (the "extend existing" seam). No additional component is
introduced beyond these.

### D4 — `battle_hits_simple` is DROPPED (resolved deferred decision #2)

A codebase audit (grep for `highest_hits_simple|BATTLE_SIMPLE|hits_simple`
across `bot/`) confirms `highest_hits_simple_season_{n}.json` is written by
`bot/tracker.py` lines 91, 95, 113, 126, 135, 153 and read by **no other
module**. `bot/embeds.py::load_leaderboard_file` is invoked only against
`highest_hits_season_{n}.json` (battle detailed) and
`highest_bombs_season_{n}.json` (bombs), never the simple file. The
data-dictionary §2.8 flag is confirmed: the file is dead. **No
`battle_hits_simple` table is created.** US-008 removes the
`save_json(BATTLE_SIMPLE_FILE, ...)` line and the in-process `battle_simple`
mutations from `bot/tracker.py`.

### D5 — `capped_state` folded as `is_capped bool` column on `player_registrations` (resolved deferred decision #1)

`capped_state` is 1:1 with `player_registrations.discord_id`, is fully
reconstructable from Tacticus, and is edge-detect scratch (data-dictionary
§2.4). Folding it as a column avoids a join and matches those semantics. No
`capped_state` table is created. The `save_capped_state` / `load_capped_state`
ABC methods continue to take/return the existing dict shape
(`{discord_id_str: bool}`) so cogs are unchanged; the SQLite impl
reads/writes the column.

### D6 — `file_lock` is RETIRED (resolved deferred decision #4)

`main.py:45`'s process-wide `asyncio.Lock` is removed. Rationale: SQLite WAL
transactions are the atomicity boundary that `file_lock` was a stand-in for.
Concurrency model (verified brief §2.3): `cap_detect` and `auto_update` fire
concurrently at the top of each hour with no offset. They touch different
tables (`cap_detect`: `player_registrations.is_capped`; `auto_update`:
`battle_hits`, `bomb_hits`, `players`, `live_leaderboards`, and
`player_registrations` for `_register_unknown_players`). The only shared row
set is `player_registrations`; WAL-mode snapshot isolation + SQLAlchemy's
per-transaction session handle this without a process-global lock. The
hourly `auto_update` write path is wrapped in **one transaction per guild**
(so a crash mid-cycle leaves that guild's pre-cycle state intact, matching
US-010's crash-safety scenario). `file_lock` is not kept as belt-and-
suspenders — it would serialize writes across all tenants and reintroduce
the cross-tenant coupling ADR-004 §6 flagged.

### D7 — `api_key` secrets: Fernet-encrypted column with `SCRAPCODE_DB_KEY` from `.env` (resolved api_key decision)

Both `guilds.api_key` and `player_registrations.api_key` are stored as
Fernet ciphertext. The Fernet key is read from `.env` as `SCRAPCODE_DB_KEY`
(matching DISCUSS D4 / US-003). The key MUST NOT be logged; the
`bot/db/session.py` startup probe (D8) round-trips a known plaintext to
verify the key is valid before the bot starts. Decrypt-on-read happens in
`SqlAlchemyClusterRepository`, so cogs see plaintext and are unchanged.
Rejected alternative: `.env`-per-key (one secret per guild + per registered
player) — too many secrets to manage, no audit trail, no improvement on the
current plaintext JSON. Rejected alternative: Supabase Vault / HashiCorp
Vault — adds a service for a single-process single-VM deployment; violates
simplest-solution-first.

### D8 — Startup probe (Earned Trust)

`bot/db/session.py` exposes a `probe()` method that runs at composition time
and MUST succeed before the bot starts. The probe:
1. Opens a connection and runs `PRAGMA journal_mode` — asserts `wal`.
2. Runs `SELECT version_num FROM alembic_version` — asserts it matches the
   Alembic head revision compiled into the binary (mismatch = the DB is
   behind the code; refuse to start).
3. Round-trips a known plaintext through Fernet with `SCRAPCODE_DB_KEY` —
   asserts the ciphertext decrypts back (catches a wrong/rotated key before
   any real `api_key` is touched).
4. Inserts + rolls back a throwaway row in `clusters` — asserts the
   write/rollback path works (catches a read-only filesystem, a full disk,
   or a corrupted DB that opens but cannot transact).

On probe failure, the composition root raises a structured
`health.startup.refused` event (logged at ERROR, with the failing probe step
named) and the bot refuses to start. If `SCRAPCODE_REPO_BACKEND=json` is set
(rollback path), the probe is skipped and the JSON impl is used (US-010's
one-cycle fallback).

This satisfies principle 12 (Earned Trust): the adapter demonstrates
empirically that it can honor its contract in the real environment before
the system depends on it. The probe is enforced at three layers: (a) mypy
Protocol check at the composition root boundary (every adapter passed to
`bot/guilds.py` must expose `probe`); (b) an AST pre-commit hook asserting
`SqlAlchemyClusterRepository` defines a `probe` method; (c) a CI gold-test
runner that injects a corrupted DB, a wrong Fernet key, and a stale alembic
version, asserting each surfaces as a `health.startup.refused` event.

### D9 — Singleton flip is env-driven (rollback = restart)

`bot/guilds.py:7` reads `SCRAPCODE_REPO_BACKEND` from env (`json|sqlite`,
default `sqlite` post-cutover). `=json` is the rollback path; a successful
cutover cycle leaves the JSON tree untouched (read-only fallback). This is
the safety mechanism for Slice 04.

### D10 — `FORUM_CHANNELS` / `MAP_THREADS` seeded into `replay_threads` (resolved deferred decision #5)

The data migration (Slice 03) seeds the `replay_threads` table from the
hardcoded `FORUM_CHANNELS` / `MAP_THREADS` constants in `replay_cog.py`,
keyed by `(discord_server_id=1458181638453203099, boss, map_name)` with the
thread ID and (if present in the existing `replay_index.json`) the
`index_message_id`. `replay_cog.py` (US-009) then looks up thread IDs from
`replay_threads` instead of the hardcoded constants, and the constants are
removed. This closes the ADR-004 §3 leak ("Replay forum/thread IDs are
hardcoded to one server") in the same slice that routes the cog through the
repo — no extra slice, no deferral. **This expands US-009's scope by one
helper** (a `replay_threads` lookup in `replay_cog.py`) versus DISCUSS D6's
deferral; the expansion is small and the leak-closure is worth it. The
expansion is noted in `docs/feature/sqlite-backend/design/wave-decisions.md`.

### D11 — `replay_index` tenancy: single-server assignment, per-tenant URL uniqueness

`replay_entries` gains a `discord_server_id` column (data-dictionary §2.10
notes it is new). The data migration assigns ALL existing `replay_index.json`
entries to the one production server (`1458181638453203099`) — the data has
no `server_id`, so this is the only defensible assignment. URL uniqueness is
scoped per `(discord_server_id, boss, map_name)`, not global (fixes the
cross-tenant collision). True multi-tenant replay partitioning waits for a
second server; recorded in `wave-decisions.md`.

### D12 — `update_channel_id` dropped; `PlayerListMigrator` v1→v2 runs once

`update_channel_id` is NOT created in the SQL schema (ADR-002, data-dictionary
§4). The `PlayerListMigrator._migrate_v1_to_v2` inversion runs once in the
data migration (Slice 03 / US-005), not on every read; `players.last_validated`
gets the `1970-01-01T00:00:00Z` epoch sentinel for migrated v1 rows. The
`__meta__.version` per-file scheme is retired (Alembic versions the schema);
`load_player_list` keeps returning a `{"__meta__": {"version": 2}, "players":
{...}}` dict for cog compatibility (US-004 technical note) — marked as a shim.

### D13 — Development paradigm: OOP

The codebase is OOP (ABCs, dataclasses, repository pattern). The new
components follow the same paradigm (declarative ORM models, a repository
class, a factory function). This routes DELIVER to `@nw-software-crafter`
(not `@nw-functional-software-crafter`). Recorded for the orchestrator in
`wave-decisions.md`.

## Consequences

- **Positive:** the non-atomic-write + silent-empty-read trap (ADR-002 /
  brief §4.8) is retired: hourly writes are transactional; a corrupted/missing
  DB raises (probe + `load` raise) instead of silently returning empty. The
  `replay_index` tenancy leak is partially closed (D10 + D11) — the
  `replay_index.json` global file is gone, `discord_server_id` is in the
  schema, and thread IDs are no longer hardcoded in runtime code. `api_key`
  is encrypted at rest (D7).
- **Positive:** rollback is a restart with `SCRAPCODE_REPO_BACKEND=json`
  (D9); the JSON tree is the read-only fallback for one cycle.
- **Negative:** the `ClusterRepository` ABC grows 4 new methods (ADR-007) —
  an interface change the DISCUSS wave did not scope. `JsonClusterRepository`
  must implement them too (returning the existing `{"boss_hits": ...}` dict
  shape from the JSON files) so the contract tests stay parametrized.
- **Negative:** the `SCRAPCODE_DB_KEY` is a new operational secret; loss of
  the key renders all `api_key` columns unrecoverable. Mitigation: the
  operator stores it in the same `.env` backup as `DISCORD_TOKEN`. The probe
  (D8 step 3) catches a wrong/rotated key at startup.
- **Negative:** the Fernet-encrypted column prevents `api_key` equality
  queries in SQL (two equal keys encrypt to different ciphertexts). The
  1:1 `api_key` uniqueness constraint (data-dictionary §2.3) must be
  enforced on a HMAC-SHA256 of the key (deterministic) stored in a separate
  `api_key_hmac` column, NOT on the ciphertext. The HMAC key is a separate
  fixed value derived from `SCRAPCODE_DB_KEY` (HKDF). This is an
  implementation note for the software-crafter; it does not change the
  architecture.
- **Trade-off:** WAL mode trades a slightly larger disk footprint (the
  `-wal` and `-shm` sidecar files) for concurrent-reader / single-writer
  throughput. At this scale the trade-off is one-sided in favor of WAL.
- **Trade-off:** retiring `file_lock` (D6) removes a cross-tenant coupling
  (ADR-004 §6) at the cost of relying on SQLite's own concurrency model. If a
  future slice adds a second process, the WAL single-writer model must be
  revisited (likely moving to a server-side Postgres — ADR-002's
  Postgres/Supabase alternative).

## Architecture enforcement

Style: modular monolith with dependency-inversion (ports-and-adapters).
Language: Python. Tool: **import-linter** (configured in `pyproject.toml`,
run in CI as a fast first-stage check) + **pytest-archon** for the
composition-root Protocol check.

Rules to enforce:
- `bot/cogs/*` MUST NOT import `sqlalchemy`, `aiosqlite`, `bot.db.*`, or
  `bot.repository_sqlalchemy`. Cogs go through `bot.guilds` only.
- `bot/tracker.py` MUST NOT import `pathlib.Path` (write helpers retired).
- `bot/cogs/replay_cog.py` MUST NOT reference `replay_index.json`.
- `bot.embeds` MUST NOT import `pathlib.Path` after Slice 04 (read path
  routed through repo).
- The composition root (`bot/guilds.py`) MUST pass the `probe()` Protocol
  check — any adapter wired in must expose `probe` (mypy + pytest-archon
  Protocol test).

import-linter is used for the import-graph rules above (it is fit for
module-boundary enforcement). For the adapter `probe()` contract, the
three-layer enforcement from principle 12 applies: mypy Protocol at the
composition root, an AST pre-commit hook walking `bot/repository_sqlalchemy.py`
to assert `probe` is defined, and a CI gold-test runner that injects the
catalogued substrate lies (corrupted DB, wrong Fernet key, stale alembic
version, read-only filesystem). import-linter was investigated for the
probe contract and rejected — its contracts are import-graph only, with no
API for method-presence enforcement on classes.

## Alternatives considered

- **Bare `sqlite3` (no ORM).** Rejected: the codebase is OOP and already
  uses a repository pattern; an ORM preserves the shape and gives Alembic
  migrations for free. The SQLAlchemy 2.0 async core is the smallest
  impedance match.
- **SQLModel (Pydantic + SQLAlchemy wrapper).** Rejected: adds a dependency
  for thin sugar; SQLAlchemy 2.0 declarative is sufficient and more
  portable. No Pydantic models exist in the codebase to leverage.
- **Postgres / Supabase.** Rejected for THIS feature: the deployment is one
  process on one VM serving one server (ADR-004); a network hop adds latency
  and a service to operate. Remains the upgrade path if a second process or
  a second server arrives (ADR-002 / D6 consequence).
- **Keep `file_lock` as belt-and-suspenders.** Rejected: it serializes
  writes across all tenants (reintroduces the ADR-004 §6 coupling) and is
  redundant with WAL transactions. The probe (D8) covers the "is the DB
  usable" question the lock would have hid.
- **Plaintext `api_key` in `.env`-per-key.** Rejected: too many secrets
  (one per guild + per registered player), no audit trail, no improvement
  over the current plaintext JSON.
- **Mirror `battle_hits_simple` table (don't drop).** Rejected: the read-
  path audit (D4) confirms no render path consumes it. Mirroring dead data
  is architectural cruft. Drop is the simplest-solution-first choice.
- **Separate `capped_state` table.** Rejected: 1:1 with
  `player_registrations`, derived, edge-detect scratch (data-dictionary
  §2.4). A column avoids a join and matches the semantics.
- **Defer `FORUM_CHANNELS` / `MAP_THREADS` → `replay_threads` (DISCUSS D6).
  ** Overridden: seeding the table in the same data migration that
  populates `replay_entries` is a small extra step that closes the ADR-004
  §3 leak instead of carrying it forward. Recorded as a scope expansion in
  `wave-decisions.md`.