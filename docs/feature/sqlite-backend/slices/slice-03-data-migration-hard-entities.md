# Slice 03 — JSON→SQLite data migration + the hard entities

**Feature:** sqlite-backend
**Slice size:** ≤1 day (the dry run + verification is the time sink, not the code)
**Type:** @infrastructure (no user-visible behavior change)
**Trace to job:** `preserve-data-integrity-through-backend-swap`

## Goal

Migrate the live JSON data into SQLite, add the hard entities (`battle_hits`,
`bomb_hits`, `replay_index`), and verify row-count parity against the real
production JSON tree. This is the slice that *uses real production data* —
the dry run pulls the actual `clusters/{discord_server_id}/` tree from the
single production server.

## Learning hypothesis

> If this slice fails, it disproves "the JSON→SQL data migration loses or
> duplicates rows" — which is the single highest-risk claim of the whole
> feature. If we cannot show 100% row-count parity on real data, the
> cutover in Slice 04 is unjustified.

## In scope

- Alembic data migration (separate revision from the schema baseline in
  Slice 02) that reads the live `clusters/{id}/...` tree and populates the
  SQLite tables.
- The `PlayerListMigrator` v1→v2 inversion runs once as part of the data
  migration (not on every read, as it does today). Any v1 `player_list.json`
  files in the production tree are converted to v2 rows in `players` with
  `last_validated="1970-01-01T00:00:00Z"`.
- `battle_hits` table with the unique constraint on `(server, guild, season,
  boss, encounter, tier, roster_key, user_id)` and an **upsert-keep-max(damage)**
  path that replaces the in-memory `try_insert(check_roster=True)` logic.
  The `(-damage, completed_on asc)` tiebreak is preserved by the read query's
  `ORDER BY`.
- `bomb_hits` table (same pattern minus roster columns).
- `battle_hits_simple` table — IMPLEMENTATION note: confirm whether the
  simple file is read by any render path (data-dictionary §2.8 flags it as
  "written but not read"). If unused, DO NOT migrate it (drop the table from
  the schema and document the decision in `wave-decisions.md`). If used,
  mirror `battle_hits` minus roster columns.
- `replay_index.json` migration with the tenancy decision: add a
  `discord_server_id` column to `replay_entries` and `replay_threads`.
  Decision for the single-server dry run: **treat all existing
  `replay_index.json` data as belonging to the one production Discord server
  (`1458181638453203099`)** — the data has no `server_id` field, so we must
  assign one. Document this in `wave-decisions.md` as a deferred decision
  (true multi-tenant partitioning waits for a second server).
- URL uniqueness in `replay_entries` is scoped per
  `(discord_server_id, boss, map_name)`, NOT global (fixes the cross-tenant
  collision that exists today).
- Row-count parity verification: a script that counts rows per table in
  SQLite and compares to counts derived from the JSON tree. Targets in
  `outcome-kpis.md` (100% parity).

## Out of scope

- Routing `tracker.py` / `replay_cog.py` through the new layer — Slice 04.
- Flipping the singleton — Slice 04.
- True multi-tenant replay partitioning (only one server has data; the
  second-server case is deferred).

## Taste tests

- **Thin end-to-end?** YES — the migration runs, the parity check passes,
  the hard entities exist.
- **User-visible?** NO (infrastructure). The migrated DB is not yet the live
  singleton.
- **Production data?** YES — this is the slice that *requires* real JSON
  data pulled from the production server. The dry run runs against a copy of
  the `clusters/` tree (the operator copies it off the VM). The parity check
  is the proof.
- **Reversible?** YES — the migration is a one-shot Alembic revision against
  a fresh SQLite file; the JSON tree is untouched.
- **Single learning hypothesis?** YES (row-count parity / no loss or
  duplication).

## Exit criteria

- A fresh SQLite file, post-migration, contains rows for all 8 easy entities
  + `battle_hits` + `bomb_hits` (+ `replay_entries`/`replay_threads`).
- Row-count parity report shows 100% for every table vs the source JSON
  counts.
- `battle_hits` unique constraint demonstrably enforces per-roster dedup:
  inserting the same `(server, guild, season, boss, encounter, tier,
  roster_key, user_id)` with a lower damage does NOT replace the row; with a
  higher damage DOES replace (keep-max).
- `replay_entries` rows all carry the production server's
  `discord_server_id`; URL uniqueness is per-tenant.
- A dry-run report is produced (markdown or stdout) listing per-table
  before/after counts. This is the artifact Slice 04's cutover depends on.

## Stories delivered

- US-005 — JSON→SQLite data migration with row-count parity (easy entities +
  PlayerListMigrator one-time)
- US-006 — battle_hits + bomb_hits persistence with upsert-keep-max(damage)
- US-007 — replay_index migration + tenancy decision