# Wave Decisions — feature `sqlite-backend` (DISCUSS wave)

> DISCUSS wave-decisions summary per the nw-discuss format. Records the
> decisions made during this wave, the requirements summary, the
> constraints inherited + introduced, and the upstream changes
> (none — this is a brownfield entry).

## Key decisions

### D1 — JTBD skipped; job bootstrapped retroactively

Per the orchestrator's Decision 4, JTBD is skipped because motivations
are clear from ADR-002 (SQLite is the accepted successor for the JSON
layer; the value is data integrity preservation, not new user
outcomes). A single job `preserve-data-integrity-through-backend-swap`
is bootstrapped in `docs/product/jobs.yaml` as a one-line restatement
for traceability. Every story in `user-stories.md` traces to this job
(N:1). This is NOT a validated JTBD artifact; it is a placeholder so
future waves have one place to find the feature's purpose.

### D2 — DISCOVER skipped; no DIVERGE artifacts

DISCOVER was degenerate for a backend swap (no user journey to
discover). No DIVERGE artifacts exist at
`docs/feature/sqlite-backend/diverge/`. This is recorded as a RISK
(not a blocker): the feature's value is integrity preservation, which
is fully specified by ADR-002 + the data dictionary; no divergence /
convergence cycle is needed.

### D3 — Four carpaccio slices, each ≤1 day, each with a learning hypothesis

The orchestrator's suggested 4 slices are adopted with refinement:

- Slice 01 (Pin current behavior) — disproves "we can't capture current
  behavior as a regression net."
- Slice 02 (SQL models + Alembic + easy-entity repo) — disproves "the
  ClusterRepository ABC is too leaky to swap implementations."
- Slice 03 (Data migration + hard entities) — disproves "the JSON→SQL
  data migration loses or duplicates rows." Uses real production data
  (operator copies the `clusters/` tree off the VM); the parity report
  is the gate.
- Slice 04 (Rewire + flip + cutover) — disproves "cutover breaks a
  live command."

### D4 — API key secrets store: app-level Fernet encrypted column (RECOMMENDATION)

For the single-server, single-process deployment, the simplest secrets
store that fits is an app-level encrypted column via
`cryptography.Fernet` with key `SCRAPCODE_DB_KEY` from `.env`,
decrypt-on-read in the repo layer. This avoids adding a service (no
Supabase Vault, no HashiCorp Vault) and avoids `.env`-per-key sprawl
(guild + registration `api_key` columns are many). This is a
RECOMMENDATION to DESIGN, not a final decision; DESIGN may pick an
alternative if it surfaces a reason to.

### D5 — `capped_state` folded as `is_capped bool` column on `player_registrations` (RECOMMENDATION)

Data-dictionary §2.4 notes `capped_state` is "derived state, fully
reconstructable from Tacticus, edge-detect scratch." Folding it as a
column on `player_registrations` matches those semantics (1:1 with
`discord_id`, no separate table, no join) and is the simpler choice.
A separate `capped_state` table is acceptable if DESIGN prefers
symmetry with the JSON layout. RECOMMENDATION, not final.

### D6 — `replay_index` tenancy: single-server assignment for the dry run

`replay_index.json` has no `discord_server_id` field (the tenancy leak
in brief §3.2). The data migration (US-007) assigns ALL existing
entries to the one production server (`1458181638453203099`) — the
data has no `server_id`, so this is the only defensible assignment.
True multi-tenant replay partitioning (and moving `FORUM_CHANNELS` /
`MAP_THREADS` into the `replay_threads` table) is DEFERRED until a
second Discord server joins. The deferral is recorded here and in
`story-map.md` (Slice 04 out-of-scope).

### D7 — `battle_hits_simple` disposition DEFERRED to DESIGN

Data-dictionary §2.8 flags `highest_hits_simple_season_{n}.json` as
"written but not read by any render path." US-006 leaves the
drop/mirror decision to DESIGN: if confirmed unused, drop the table
from the schema and remove the `save_json(BATTLE_SIMPLE_FILE, ...)`
line from `tracker.py` (US-008). If used, mirror `battle_hits` minus
roster columns. The decision requires a render-path audit the DISCUSS
wave did not perform.

### D8 — `get_guild_data_path` ABC method is JSON-specific; DEFERRED to DESIGN

The `ClusterRepository.get_guild_data_path` method returns a filesystem
dir for `tracker.py`'s direct file I/O. Slice 04 retires the tracker's
direct file I/O (US-008), making the method obsolete. For Slice 02,
the SQLite impl may return a sentinel or raise `NotImplementedError` if
no caller reaches it through the new path. DESIGN should decide
whether to remove the method from the ABC (breaking change to the
interface) or keep it as a deprecated JSON-only method.

### D9 — Singleton flip is env-driven for rollback

US-010 makes `bot/guilds.py:7` read `SCRAPCODE_REPO_BACKEND` from env
(`json|sqlite`, default `sqlite` post-cutover). The JSON impl is kept as
a read-only fallback for one cycle post-cutover, then retired. Rollback
is a restart with `SCRAPCODE_REPO_BACKEND=json`. This decision is what
makes the 4-slice sequence safe.

## Requirements summary

The feature delivers ONE user-visible outcome: every existing Discord
command and both hourly background tasks produce the same observable
output post-cutover as before, with the non-atomic-write +
silent-empty-read data-loss trap retired. Everything else is
infrastructure that enables that outcome. The 11 stories decompose the
work into 4 carpaccio slices, each ≤1 day, each with a single learning
hypothesis that gates the next slice. The cutover (Slice 04) is the
only slice with user-visible impact; its acceptance pass (US-011) is
the feature's final gate. Outcome KPIs (4 feature-level + 11 per-story)
measure integrity preservation, not adoption.

## Constraints

### Inherited (from orchestrator + ADR-002 + brief)

- New Python packages (`sqlalchemy>=2.0`, `alembic`, `aiosqlite`) go
  through `requirements.txt` and are installed into the existing `.venv`.
  (`cryptography` added by D4.)
- The `ClusterRepository` ABC is the dependency-inversion seam — the
  SQLite impl slots in behind it.
- Do NOT re-introduce the non-atomic-write / silent-empty-read trap
  (ADR-002 / brief §4.8).
- `update_channel_id` (unused) is dropped in the SQL schema.
- Keep JSON files read-only as a one-cycle fallback after cutover, then
  retire.

### Introduced (this wave)

- `api_key` (guild + registration) plaintext → Fernet encrypted column
  with `SCRAPCODE_DB_KEY` from `.env` (D4 — RECOMMENDATION to DESIGN).
- `replay_index` gains a `discord_server_id` column; for the dry run,
  all existing entries assigned to the production server (D6).
- URL uniqueness in `replay_entries` scoped per
  `(discord_server_id, boss, map_name)`, NOT global (fixes the
  cross-tenant collision).
- `battle_hits` unique constraint on `(server, guild, season, boss,
  encounter, tier, roster_key, user_id)` with upsert-keep-max(damage);
  `(-damage, completed_on asc)` tiebreak preserved by read `ORDER BY`.
- `PlayerListMigrator` v1→v2 inversion runs once as a data migration
  (US-005), not on every read.
- Singleton flip via `SCRAPCODE_REPO_BACKEND` env var (D9); rollback is
  a restart.
- Slice 03 runs against a COPY of the production `clusters/` tree
  (operator `scp`s it off the VM); the migration never reads
  `/opt/discord-bot/clusters/` directly.

## Upstream changes

None. This is a brownfield entry with no prior DISCUSS or SPIKE
artifacts to contradict. The architecture baseline
(`docs/product/architecture/`) is the upstream and is unchanged by
this wave.

## Risks (flagged, not blocking)

1. **JTBD skipped (D1).** The "job" is a retroactive bootstrap, not a
   discovery. Honest for a backend swap; the reviewer should confirm.
2. **No DIVERGE artifacts (D2).** No alternative storage backends were
   explored in this wave; ADR-002 already accepted SQLite. If a
   later wave wants to revisit (e.g. Postgres for horizontal scale),
   it would run DIVERGE then.
3. **`battle_hits_simple` unused-read-path claim (D7).** The
   data-dictionary §2.8 flag ("written but not read") is from a
   code-read pass; a render-path audit in DESIGN should confirm
   before dropping the table.
4. **`get_guild_data_path` ABC coupling (D8).** The ABC has a
   JSON-specific method. The Slice-02 SQLite impl may not satisfy it
   cleanly; DESIGN decides deprecation vs. removal.
5. **Slice 03 production-data copy (constraint).** The operator must
   `scp` the `clusters/` tree off the VM before Slice 03 can run. This
   is an operational dependency, not a code dependency; flagged so
   DEVOPS / the operator has it on the radar.
6. **`file_lock` retirement (US-010 technical note).** The
   process-wide `asyncio.Lock` may become redundant once the hourly
   write is transactional. DESIGN decides keep-as-belt-and-suspenders
   vs. retire; either is acceptable.

## SSOT bootstrap note

DISCOVER was degenerate (no `docs/product/vision.md`, `jobs.yaml`,
`journeys/`, `project-brief.md`, or `stakeholders.yaml` existed). The
DISCUSS wave bootstrapped the SSOT minimally by creating
`docs/product/jobs.yaml` with one job entry
(`preserve-data-integrity-through-backend-swap`). No vision,
stakeholder, or journey files were created — fabricating them would
not reflect reality (single-server backend swap, single operator-dev).
Future waves that need a fuller SSOT should run DISCOVER properly.