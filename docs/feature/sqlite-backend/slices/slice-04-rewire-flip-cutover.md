# Slice 04 — Rewire bypasses, flip the singleton, end-to-end cutover

**Feature:** sqlite-backend
**Slice size:** ≤1 day
**Type:** MIXED — 3 @infrastructure stories + 1 user-visible story (US-011)
**Trace to job:** `preserve-data-integrity-through-backend-swap`

## Goal

Cut over. Route the two storage bypasses (`bot/tracker.py` season files and
`bot/cogs/replay_cog.py` replay index) through the new repository layer, flip
the singleton in `bot/guilds.py:7` to `SqlAlchemyClusterRepository`, wrap the
hourly multi-file write in a single transaction (retiring the non-atomic-write
trap), keep the JSON tree read-only as a one-cycle fallback, and verify
end-to-end that every existing user-visible command and background task still
produces the same observable output.

## Learning hypothesis

> If this slice fails, it disproves "cutover breaks a live command" — which
> is the only thing a user (bot operator or Discord end-user) cares about
> in this whole feature. If any of `/view_leaderboard`, `/view_bombs`,
> `/get_replay`, `/register`, `/unregister`, `/move`, admin config, or the
> hourly auto-update/cap-detect tasks regresses, the swap is rolled back.

## In scope

- `bot/tracker.py::process_api_response` rewritten to read/write
  `battle_hits` / `battle_hits_simple` / `bomb_hits` via the repository
  (or via a `BattleHitsRepository` sibling seam — IMPLEMENTATION decision for
  DESIGN wave). The `try_insert` dedup logic moves into the SQL upsert path
  from Slice 03; `tracker.py` no longer holds top-N lists in memory.
- `bot/cogs/replay_cog.py` rewritten to read/write `replay_entries` /
  `replay_threads` via the repository. `REPLAY_INDEX_FILE`,
  `load_replay_index`, `save_replay_index` are removed. The hardcoded
  `FORUM_CHANNELS` / `MAP_THREADS` remain in this slice (the data dict
  recommends moving them into `replay_threads`, but that is a separate
  refactor — flag in `wave-decisions.md` as DEFERRED).
- `bot/guilds.py:7` — `repo = JsonClusterRepository()` flips to
  `repo = SqlAlchemyClusterRepository(...)` (or a factory reading from
  `.env` so the impl is swappable for rollback).
- The hourly `auto_update` multi-file write (currently scattered
  `save_player_list` / `save_guilds` / `save_capped_state` calls outside
  `file_lock`) is wrapped in a single SQLAlchemy transaction (or
  `async with file_lock:` equivalent using `aiosqlite`). This retires the
  non-atomic-write + silent-empty-read trap documented in ADR-002 / brief
  §4.8.
- JSON tree kept read-only as a one-cycle fallback: if the SQLite file is
  missing or fails to open on startup, log loudly and (optionally) fall back
  to `JsonClusterRepository` for one cycle. After one successful cycle, the
  JSON write path is retired (read-only fallback remains available for
  manual rollback).
- End-to-end acceptance pass: every existing slash command and both hourly
  tasks verified against pre-cutover baseline snapshots. US-011's UAT
  scenarios are the gate.

## Out of scope

- Moving `FORUM_CHANNELS` / `MAP_THREADS` into the `replay_threads` table
  (deferred — noted in `wave-decisions.md`).
- True multi-tenant replay partitioning (deferred from Slice 03).
- New user-facing features.

## Taste tests

- **Thin end-to-end?** YES — the cutover is one commit (or a short sequence)
  and the acceptance pass is one session.
- **User-visible?** YES — US-011 is the user-visible story; its Elevator
  Pitch and UAT scenarios are the gate.
- **Production data?** YES — the cutover runs against the live migrated
  SQLite file from Slice 03 on the production VM. Rollback path: flip the
  singleton env var back to JSON.
- **Reversible?** YES — the singleton flip is config-driven; rollback is a
  restart with the JSON impl.
- **Single learning hypothesis?** YES (cutover breaks nothing).

## Exit criteria

- `repo = SqlAlchemyClusterRepository(...)` is the live singleton in
  `bot/guilds.py`.
- `bot/tracker.py` and `bot/cogs/replay_cog.py` no longer contain any
  `path.write_text` / `path.read_text` calls for season files or
  `replay_index.json` (grep-verified).
- The hourly `auto_update` write path is wrapped in a single transaction
  (no partial writes on crash mid-cycle).
- A grep for `path.write_text(json.dumps` in `bot/` returns only the
  read-only-fallback path in the JSON impl (which is no longer the
  singleton).
- US-011's UAT scenarios all pass against the production cutover.
- JSON tree is untouched (read-only fallback intact).

## Stories delivered

- US-008 — Route tracker.py season-file path through the repository
- US-009 — Route replay_cog.py through the repository
- US-010 — Flip the singleton + wrap hourly write in a transaction + JSON
  read-only fallback
- US-011 (user-visible) — Existing command behavior preserved through the
  SQLite cutover