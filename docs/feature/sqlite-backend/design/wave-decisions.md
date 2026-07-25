# Wave Decisions — feature `sqlite-backend` (DESIGN wave)

> DESIGN wave-decisions summary per the nw-design format. Records the
> decisions made during this wave, the architecture summary, the Reuse
> Analysis table (mandatory, RCA F-1), the technology stack, the constraints
> established, and the upstream changes (DISCUSS-unscooped expansions).
> Author: Morgan (nw-solution-architect), propose mode.

## Key decisions

### D1 — Architecture pattern: modular monolith with dependency-inversion

Confirmed (not overridden). The `ClusterRepository` ABC is the port;
`JsonClusterRepository` (existing) and `SqlAlchemyClusterRepository` (new)
are the two driven adapters. The domain model is unchanged. Matches the
as-built pattern (ADR-002 §4) and the team size (single operator-dev, single
process). No simpler alternative fits: the ABC already exists and the swap
is a second impl behind it (principle: extend existing, not create new).
[ADR-006 D2](../../../product/architecture/adr-006-sqlite-storage-backend.md#d2--architecture-pattern).

### D2 — Storage stack: SQLite (WAL) + SQLAlchemy 2.0 + Alembic + aiosqlite + cryptography.Fernet

All OSS (MIT / Apache 2.0). WAL mode for concurrent reader/writer across the
two hourly task loops. aiosqlite keeps the event loop unblocked. Fernet for
`api_key` at rest. [ADR-006 D1](../../../product/architecture/adr-006-sqlite-storage-backend.md#d1--storage-stack).

### D3 — Deferred decision #1 `capped_state`: folded as `is_capped bool` column on `player_registrations` (ACCEPT)

`capped_state` is 1:1 with `discord_id`, derived from Tacticus, edge-detect
scratch (data-dictionary §2.4). A column avoids a join and matches the
semantics. The `save_capped_state` / `load_capped_state` ABC methods keep
their existing dict shape so cogs are unchanged; the SQLite impl reads/writes
the column. [ADR-006 D5](../../../product/architecture/adr-006-sqlite-storage-backend.md#d5--capped_state-folded-as-is_capped-bool-column-on-player_registrations-resolved-deferred-decision-1).

### D4 — Deferred decision #2 `battle_hits_simple`: DROPPED (audit-confirmed dead)

A codebase audit (grep `highest_hits_simple|BATTLE_SIMPLE|hits_simple` across
`bot/`) confirms `highest_hits_simple_season_{n}.json` is written by
`bot/tracker.py` (lines 91, 95, 113, 126, 135, 153) and read by **no other
module**. `bot/embeds.py::load_leaderboard_file` is invoked only against
`highest_hits_season_{n}.json` and `highest_bombs_season_{n}.json`, never the
simple file. The data-dictionary §2.8 flag is confirmed: the file is dead.
**No `battle_hits_simple` table is created.** US-008 removes the write +
the in-process `battle_simple` mutations from `tracker.py`.
[ADR-006 D4](../../../product/architecture/adr-006-sqlite-storage-backend.md#d4--battle_hits_simple-is-dropped-resolved-deferred-decision-2).

### D5 — Deferred decision #3 `get_guild_data_path`: DEPRECATE then REMOVE; ABC grows 4 new methods (OVERRIDE + scope expansion)

The orchestrator's framing of this decision ("used only by `tracker.py`'s
direct JSON I/O") inherits an undercount from brief §4. The DESIGN-wave
codebase audit (grep `get_guild_data_path|load_leaderboard_file|data_dir`
across `bot/`) shows `get_guild_data_path` is called by **4 cogs**
(`view_cog.py` lines 54/104/153, `admin_cog.py` lines 330/408,
`tasks_cog.py` lines 292/312, `update_cog.py` lines 60/116) for **READS**
via `embeds.load_leaderboard_file` — not only by `tracker.py` for writes.
Disposition (ADR-007):

1. The ABC grows 4 new methods: `load_battle_hits`, `load_bomb_hits`,
   `upsert_battle_hits`, `upsert_bomb_hits` — all storage-medium-agnostic,
   returning the existing `{"boss_hits": ...}` dict shape so cogs and
   `embeds.build_*_messages` are unchanged.
2. `JsonClusterRepository` implements them (read/write the existing JSON
   files) so the parametrized contract tests stay green on both impls.
3. `SqlAlchemyClusterRepository.get_guild_data_path` raises
   `NotImplementedError` in Slice 02 (no caller reaches it through the
   SQLite impl yet).
4. Slice 04 rewires the 4 cog read sites + `embeds.load_leaderboard_file`
   to the new read methods, removes `data_dir` from
   `tracker.process_api_response`, then removes `get_guild_data_path`
   from the ABC and `embeds.load_leaderboard_file`.

This is an interface change the DISCUSS wave did not scope. It expands
US-008 (write-side only in DISCUSS) to cover the read-side bypass too. See
"Upstream changes" below. A dedicated ADR captures the contradiction:
[ADR-007](../../../product/architecture/adr-007-repo-read-methods-get-guild-data-path-deprecation.md).

### D6 — Deferred decision #4 `file_lock`: RETIRED

`main.py:45`'s process-wide `asyncio.Lock` is removed. SQLite WAL
transactions replace it. Concurrency model (verified brief §2.3):
`cap_detect` and `auto_update` fire concurrently at the top of each hour
with no offset. They touch different tables; the only shared row set is
`player_registrations`, which WAL snapshot isolation handles without a
process-global lock. The hourly `auto_update` write becomes one
transaction per guild (US-010). `file_lock` is NOT kept as belt-and-
suspenders — it would serialize writes across all tenants and
reintroduce the ADR-004 §6 cross-tenant coupling.
[ADR-006 D6](../../../product/architecture/adr-006-sqlite-storage-backend.md#d6--file_lock-is-retired-resolved-deferred-decision-4).

### D7 — Deferred decision #5 `FORUM_CHANNELS` / `MAP_THREADS`: SEEDED into `replay_threads` (OVERRIDE of DISCUSS D6)

DISCUSS D6 deferred moving the hardcoded constants into `replay_threads`.
This wave overrides that deferral: the Slice-03 data migration seeds
`replay_threads` from the hardcoded `FORUM_CHANNELS` / `MAP_THREADS`
constants, keyed by `(discord_server_id=1458181638453203099, boss,
map_name)` with the thread ID and (if present in `replay_index.json`) the
`index_message_id`. `replay_cog.py` (US-009) then looks up thread IDs from
`replay_threads` and the hardcoded constants are removed. This closes
ADR-004 §3 leak ("Replay forum/thread IDs are hardcoded to one server") in
the same slice that routes the cog through the repo — no extra slice. The
expansion is small (one helper in `replay_cog.py` + a few extra inserts in
the data migration) and the leak-closure is worth it.
[ADR-006 D10](../../../product/architecture/adr-006-sqlite-storage-backend.md#d10--forum_channels--map_threads-seeded-into-replay_threads-resolved-deferred-decision-5).

### D8 — `api_key` secrets store: Fernet-encrypted column with `SCRAPCODE_DB_KEY`

Both `guilds.api_key` and `player_registrations.api_key` are Fernet
ciphertext at rest; the Fernet key is `SCRAPCODE_DB_KEY` from `.env`
(matching DISCUSS D4 / US-003), never logged. Decrypt-on-read in
`SqlAlchemyClusterRepository` keeps cogs unchanged. The 1:1 `api_key`
uniqueness constraint is enforced on a deterministic `api_key_hmac` column
(HMAC-SHA256, key derived from `SCRAPCODE_DB_KEY` via HKDF), not on the
ciphertext (Fernet is non-deterministic). The orchestrator suggested
`FERNET_KEY` as an alias; DISCUSS D4 already named `SCRAPCODE_DB_KEY`, so
this wave uses `SCRAPCODE_DB_KEY` to avoid introducing two names for one
secret.
[ADR-006 D7](../../../product/architecture/adr-006-sqlite-storage-backend.md#d7--api_key-secrets-fernet-encrypted-column-with-scrapcode_db_key-from-env-resolved-api_key-decision).

### D9 — Development paradigm: OOP

The codebase is OOP (ABCs, dataclasses, repository pattern). New
components follow the same paradigm (declarative ORM models, a repository
class, a factory function). This routes DELIVER to
`@nw-software-crafter` (not `@nw-functional-software-crafter`). The
orchestrator will write the paradigm to a project `CLAUDE.md`.

### D10 — Startup probe (Earned Trust)

`bot/db/session.py::probe()` runs at composition time and MUST succeed
before the bot starts: (1) asserts WAL mode; (2) asserts
`alembic_version.version_num` matches the compiled head; (3) round-trips a
known plaintext through Fernet with `SCRAPCODE_DB_KEY`; (4) inserts + rolls
back a throwaway row in `clusters`. Failure raises a structured
`health.startup.refused` event and the bot refuses to start. Probe is
skipped when `SCRAPCODE_REPO_BACKEND=json` (rollback path). Enforced at
three layers (principle 12): mypy Protocol at the composition root, an AST
pre-commit hook asserting `probe` is defined on the adapter, and a CI
gold-test runner injecting a corrupted DB / wrong Fernet key / stale
alembic version / read-only filesystem.
[ADR-006 D8](../../../product/architecture/adr-006-sqlite-storage-backend.md#d8--startup-probe-earned-trust).

### D11 — `replay_index` tenancy: single-server assignment, per-tenant URL uniqueness

`replay_entries` gains a `discord_server_id` column. The data migration
assigns ALL existing `replay_index.json` entries to the one production
server (`1458181638453203099`). URL uniqueness scoped per
`(discord_server_id, boss, map_name)`, not global. True multi-tenant replay
partitioning waits for a second server.
[ADR-006 D11](../../../product/architecture/adr-006-sqlite-storage-backend.md#d11--replay_index-tenancy-single-server-assignment-per-tenant-url-uniqueness).

### D12 — `update_channel_id` dropped; `PlayerListMigrator` v1→v2 runs once

`update_channel_id` is NOT created in the SQL schema (ADR-002 /
data-dictionary §4). `PlayerListMigrator._migrate_v1_to_v2` runs once in the
Slice-03 data migration, not on every read; `players.last_validated` gets
the `1970-01-01T00:00:00Z` epoch sentinel for migrated v1 rows. The
`__meta__.version` per-file scheme is retired (Alembic versions the
schema); `load_player_list` keeps returning the `{"__meta__": {"version":
2}, "players": {...}}` dict for cog compatibility (US-004 technical note —
marked as a shim with a code comment).
[ADR-006 D12](../../../product/architecture/adr-006-sqlite-storage-backend.md#d12--update_channel_id-dropped-playerlistmigrator-v1v2-runs-once).

### D13 — Singleton flip is env-driven (rollback = restart)

`bot/guilds.py:7` reads `SCRAPCODE_REPO_BACKEND` from env
(`json|sqlite`, default `sqlite` post-cutover). `=json` is the rollback
path; a successful cutover cycle leaves the JSON tree untouched (read-only
fallback). This is the safety mechanism for Slice 04.
[ADR-006 D9](../../../product/architecture/adr-006-sqlite-storage-backend.md#d9--singleton-flip-is-env-driven-rollback--restart).

### D14 — SPIKE skipped (well-trodden stack)

Per the orchestrator's note, SPIKE was skipped because the stack
(SQLAlchemy 2.0 + Alembic + aiosqlite + Fernet) is well-trodden with mature
docs and no risky assumption. Recorded here for traceability. The one
novel check the DESIGN wave performed is the `battle_hits_simple` read-path
audit (D4) and the `get_guild_data_path` read-side audit (D5) — both grep
audits, not SPIKEs.

## Architecture summary

A storage-layer swap behind an existing ABC. The `ClusterRepository`
port is unchanged in spirit (it gains 4 storage-agnostic read/write methods
and loses 1 JSON-specific method — ADR-007). The domain model, cogs, and
`bot/guilds.py` wrappers are unchanged in shape; only `tracker.py`,
`embeds.py`, `replay_cog.py`, and 4 cog read sites are rewired in Slice 04.
The new infrastructure is `bot/db/{models,session,alembic/,
migrations_json_to_sqlite.py}` + `bot/repository_sqlalchemy.py`. WAL mode
+ one-transaction-per-guild retire the non-atomic-write + silent-empty-read
trap from ADR-002 / brief §4.8. Fernet + `SCRAPCODE_DB_KEY` retire the
plaintext-`api_key` hazard. The `file_lock` is retired. The
`replay_index.json` global tenancy leak is closed (single-server assignment
+ `replay_threads` seed). C4 diagrams updated: Container (post-cutover)
+ Component (data layer) in `c4-diagrams.md` §§4–5. Full text appended to
`brief.md` under `## Application Architecture — sqlite-backend`.

## Reuse analysis (RCA F-1, mandatory)

| Existing component | Reuse decision | Justification |
|--------------------|----------------|----------------|
| `bot/repository.py::ClusterRepository` ABC | **REUSE / EXTEND** (port) | The dependency-inversion seam. Gains 4 new methods (ADR-007) and loses 1 JSON-specific method; the port shape is preserved. This is "extend existing," not "create new." |
| `bot/repository.py::JsonClusterRepository` | **REUSE** (rollback adapter) | Kept as the env-driven rollback impl (`SCRAPCODE_REPO_BACKEND=json`). Implements the 4 new methods against the existing JSON files so contract tests stay parametrized. No new code beyond the new methods. |
| `bot/guilds.py` wrappers | **REUSE** | The API cogs call today. Unchanged in shape; only the composition root (line 7 singleton) flips to env-driven. |
| `bot/models.py` (`Cluster`, `Guild` dataclasses) | **REUSE** (unchanged) | Domain model is unchanged by this feature. |
| `bot/migrations/player_list_migrations.py::PlayerListMigrator` | **REUSE** (invoked once) | The v1→v2 inversion logic is reused verbatim by the JSON→SQLite data migration. It runs once in Slice 03 instead of on every read; the logic itself is unchanged. |
| `bot/tracker.py::get_tier_key`, `get_roster_key` | **REUSE** | Pure parsers used to build the natural key for the SQL upsert. US-008 keeps them. `try_insert`, `load_json`, `save_json` are removed (their behavior is enforced by the SQL upsert + unique constraint). |
| `bot/embeds.py::build_battle_messages`, `build_bomb_messages`, `guild_autocomplete`, `resolve_members` | **REUSE** | Render functions unchanged; only their data source changes (`load_leaderboard_file` → repo read methods). `load_leaderboard_file` itself is removed. |
| `bot/services/chronicl3r/*` | **REUSE** (untouched) | No external-integration change in this feature. |
| `bot/migrations/to_cluster_layout.py`, `seed_roles.py` | NOT REUSED | Run-once historical scripts; the new migration is a different kind (backend swap, not shape migration). Listed for the `bot/migrations/` lineage context only. |

### Genuinely NEW components (each justified — "no existing alternative")

| New component | Justification |
|---------------|----------------|
| `bot/db/models.py` | SQLAlchemy requires declarative models. No existing equivalent in the codebase. |
| `bot/db/session.py` | Engine + session factory + WAL pragmas + `probe()`. No existing equivalent. |
| `bot/db/alembic/` | Alembic requires its own env. No existing equivalent (`bot/migrations/` is runtime-shape migrators, not schema migrations). |
| `bot/db/migrations_json_to_sqlite.py` | One-shot backend-swap data movement. No existing equivalent in the codebase. |
| `bot/repository_sqlalchemy.py` | The second impl behind the ABC. By definition "extend existing," but the file itself is new because the impl is new. |

No additional new component is introduced. Every other modification is a
re-wire of an existing module (cogs, `tracker.py`, `embeds.py`,
`replay_cog.py`, `main.py`, `bot/guilds.py`, `bot/repository.py` ABC).

## Technology stack

| Component | Version | License | Notes |
|-----------|---------|---------|-------|
| SQLite | bundled (Python stdlib `sqlite3`) | public domain | WAL mode; in-process; no network. |
| SQLAlchemy | `>=2.0` | MIT | ORM, declarative models, async core. |
| Alembic | latest | MIT | schema baseline + data migration + `replay_threads` seed. |
| aiosqlite | latest | MIT | async driver so event loop is unblocked. |
| cryptography (Fernet) | latest | Apache 2.0 / BSD | `api_key` at rest; `SCRAPCODE_DB_KEY` from `.env`. |
| import-linter | latest | BSD | module-boundary enforcement (cogs → ABC only). |
| pytest-archon | latest | MIT | composition-root Protocol (`probe`) check. |

All OSS; no proprietary component. Added to `requirements.txt` (US-003).

## Constraints established

### Inherited (from orchestrator + ADR-002 + ADR-004 + brief)

- New Python packages go through `requirements.txt` and the existing `.venv`.
- `ClusterRepository` ABC is the dependency-inversion seam.
- Do NOT re-introduce the non-atomic-write + silent-empty-read trap.
- `update_channel_id` (unused) is dropped.
- JSON tree kept read-only as a one-cycle fallback after cutover.
- Thread `discord_server_id` into every data call (ADR-004 rule #1).

### Introduced (this wave)

- `SCRAPCODE_DB_PATH` (env, default `clusters.db`) — SQLite file location.
- `SCRAPCODE_DB_KEY` (env) — Fernet key for `api_key` columns; MUST NOT be
  logged; loss renders `api_key` columns unrecoverable.
- `SCRAPCODE_REPO_BACKEND` (env, `json|sqlite`, default `sqlite`
  post-cutover) — singleton flip; `=json` is rollback.
- WAL mode is set on every connection; `synchronous=NORMAL`;
  `foreign_keys=ON`.
- The hourly `auto_update` write is **one transaction per guild**.
- The startup `probe()` MUST succeed before the bot starts; failure raises
  `health.startup.refused`.
- Architecture enforcement via import-linter (cogs MUST NOT import
  `sqlalchemy` / `aiosqlite` / `bot.db.*` / `bot.repository_sqlalchemy`;
  `bot.tracker` MUST NOT import `pathlib.Path` after Slice 04;
  `bot.cogs.replay_cog` MUST NOT reference `replay_index.json`;
  `bot.embeds` MUST NOT import `pathlib.Path` after Slice 04) + pytest-archon
  (composition-root `probe()` Protocol check).
- `api_key` uniqueness enforced on a deterministic `api_key_hmac` column
  (HMAC-SHA256), not on Fernet ciphertext.
- `battle_hits_simple` is NOT migrated (D4); the `save_json(BATTLE_SIMPLE_FILE,
  ...)` line is removed from `tracker.py` (US-008).
- `replay_threads` is seeded from `FORUM_CHANNELS` / `MAP_THREADS` in the
  Slice-03 data migration (D7); the constants are removed from
  `replay_cog.py` (US-009).
- Slice 03 runs against a COPY of the production `clusters/` tree (operator
  `scp`s it off the VM); the migration never reads
  `/opt/discord-bot/clusters/` directly.

## Upstream changes (DISCUSS-unscooped expansions this wave surfaced)

The DESIGN-wave codebase audit surfaced two gaps in the DISCUSS artifacts.
Both are recorded here so the orchestrator can update the affected user
stories' AC if desired.

### U1 — `get_guild_data_path` read-side bypass (expands US-008 + adds ABC work)

DISCUSS US-008 scopes only the `tracker.py` WRITE side. The audit shows the
season files are ALSO read by `view_cog.py`, `admin_cog.py`, `tasks_cog.py`
via `get_guild_data_path` + `embeds.load_leaderboard_file` (5 call sites).
Resolution: ADR-007. Consequences:

- The `ClusterRepository` ABC grows 4 new methods (`load_battle_hits`,
  `load_bomb_hits`, `upsert_battle_hits`, `upsert_bomb_hits`).
  `JsonClusterRepository` must implement them (real Slice-02 work in
  addition to `SqlAlchemyClusterRepository`).
- US-008's scope expands to rewire the 4 cog read sites +
  `embeds.load_leaderboard_file` to the new read methods, then remove
  `get_guild_data_path` + `load_leaderboard_file`.
- US-001's contract test set expands: the 4 new ABC methods each need a
  round-trip test; the `get_guild_data_path` round-trip test becomes a
  "this method is removed" assertion post-cutover.
- The `wave-decisions.md` (DISCUSS) D8 deferral is RESOLVED: not "deprecate
  vs keep," but "deprecate in Slice 02, remove in Slice 04, and replace with
  storage-agnostic read methods."

### U2 — `FORUM_CHANNELS` / `MAP_THREADS` not deferred (expands US-009 + adds data-migration work)

DISCUSS D6 deferred moving the hardcoded constants into `replay_threads`.
This wave overrides the deferral (D7 above): the constants are seeded into
`replay_threads` in the Slice-03 data migration, and `replay_cog.py`
(US-009) looks them up from there. Consequences:

- US-007 (Slice 03 data migration) gains one extra step: read
  `FORUM_CHANNELS` / `MAP_THREADS` and insert `replay_threads` rows.
- US-009's scope expands by one helper (a `replay_threads` lookup in
  `replay_cog.py`) and the constants are removed (vs. DISCUSS's "remain
  deferred"). US-009 AC "FORUM_CHANNELS / MAP_THREADS remain (deferred)" is
  INVERTED.
- Closes ADR-004 §3 leak in the same slice that closes the
  `replay_index.json` leak.

### U3 — `api_key` HMAC column (implementation note, not a scope expansion)

The `api_key` 1:1 uniqueness constraint cannot be enforced on Fernet
ciphertext (non-deterministic). The design adds a deterministic
`api_key_hmac` column (HMAC-SHA256, key derived from `SCRAPCODE_DB_KEY` via
HKDF). This is an implementation note for the software-crafter; it does not
expand any story's AC (US-003's "player_registrations.api_key has a unique
constraint" AC is satisfied by the HMAC column). Recorded for transparency.

## External integration annotation (for platform-architect handoff)

> No new external integrations are introduced by this feature. The
> existing Tacticus API and Chronicler integrations (brief §2.5) are
> unchanged. Contract-test recommendations for Tacticus + Chronicler (per
> principle 10) remain as previously annotated; this feature does not add
> to that surface and does not require new contract tests.

## Quality gates (self-check)

- [x] Requirements traced to components (brief §L table).
- [x] Component boundaries with clear responsibilities (brief §D table).
- [x] Technology choices in ADRs with alternatives (ADR-006, ADR-007).
- [x] Quality attributes addressed: atomicity (WAL + txn/guild + probe),
  parity (KPI-2 row-count), security (Fernet + HMAC), reliability (probe +
  rollback path), maintainability (port + 2 impls + import-linter).
- [x] Dependency-inversion compliance (cogs → ABC only; all arrows inward).
- [x] C4 diagrams L1 unchanged + L2 (target Container) + L3 (data layer
  Component) in `c4-diagrams.md` §§4–5.
- [x] Integration patterns specified (async via aiosqlite; one txn/guild;
  no new external integration).
- [x] OSS preference validated (all components OSS; licenses recorded).
- [x] AC behavioral, not implementation-coupled (stories unchanged; this
  wave writes no code).
- [x] External integrations annotated (no new ones; existing unchanged).
- [x] Architectural enforcement tooling recommended (import-linter +
  pytest-archon; ADR-006 §"Architecture enforcement").
- [ ] Peer review completed — pending `nw-solution-architect-reviewer`
  invocation by the orchestrator (this wave hands off; the orchestrator
  decides whether to invoke the reviewer before DEVOPS).

## Handoff

DESIGN artifacts ready for DEVOPS / DISTILL dispatch by the orchestrator:

- `docs/product/architecture/brief.md` — `## Application Architecture —
  sqlite-backend` section appended.
- `docs/product/architecture/c4-diagrams.md` — §§4–5 added (target
  Container + data-layer Component).
- `docs/product/architecture/adr-006-sqlite-storage-backend.md` — the main
  ADR (supersedes ADR-002's "accepted successor" note).
- `docs/product/architecture/adr-007-repo-read-methods-get-guild-data-path-deprecation.md`
  — the ABC read-side extension + `get_guild_data_path` deprecation.
- `docs/feature/sqlite-backend/design/wave-decisions.md` — this file.

Not produced by this wave (out of scope, belonging to other waves / the
orchestrator):
- Roadmap / DELIVER schedule (`docs/product/roadmap.json`) — DELIVER wave.
- Acceptance tests — DISTILL wave (`nw-acceptance-designer`).
- Code, migrations, tests — DELIVER wave (`@nw-software-crafter`).
- Project `CLAUDE.md` paradigm line — orchestrator writes per D9.