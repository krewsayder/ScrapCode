# Outcome KPIs — feature `sqlite-backend`

> Brownfield backend swap. KPIs measure *integrity preservation*, not new
> user outcomes (there are none). The single user-facing KPI is
> zero-regression parity; the rest are infra KPIs that gate the cutover.

## Feature-level KPIs (gate the cutover)

### KPI-1: Repository contract test coverage

- **Who:** Krewsayder (bot operator/dev)
- **Does what:** runs the contract test suite as the regression gate
  before every subsequent slice
- **By how much:** 100% of `ClusterRepository` ABC methods covered by at
  least one round-trip test (target: ≥ 11 tests covering all 11 methods
  + the silent-empty-on-corruption pinned trap + the 4 `try_insert`
  dedup branches + the 2 `PlayerListMigrator` paths)
- **Measured by:** `pytest --collect-only bot/tests/test_repository_contract.py
  bot/tests/test_player_list_migrator.py bot/tests/test_tracker_dedup.py`
  test count ≥ 17
- **Baseline:** 0 contract tests today (only `test_tracker_tiebreak.py`
  and `test_permissions.py` exist)
- **Target:** ≥ 17 tests, all green against BOTH `JsonClusterRepository`
  and `SqlAlchemyClusterRepository` (parametrized)
- **Source story:** US-001, US-002

### KPI-2: JSON→SQLite row-count parity

- **Who:** Krewsayder
- **Does what:** runs the data migration + parity report as the cutover
  gate
- **By how much:** 100% row-count parity across all easy-entity tables +
  `battle_hits` + `bomb_hits` + `replay_entries` + `replay_threads`
- **Measured by:** the parity report (markdown or stdout) produced by
  the Slice-03 data migration; exit code 0 with all tables PASS
- **Baseline:** no migration; JSON is the only source (no parity to
  measure)
- **Target:** every table shows `JSON=N SQL=N PASS`; any `MISMATCH`
  exits non-zero and blocks Slice 04
- **Source story:** US-005, US-006, US-007

### KPI-3: Atomic-write guarantee (data-loss trap retired)

- **Who:** Krewsayder
- **Does what:** confirms the non-atomic-write + silent-empty-read trap
  from ADR-002 / brief §4.8 is retired
- **By how much:** 100% of hourly `auto_update` writes transactional;
  0 partial writes observable after a crash; 0 silent-empty reads (a
  corrupted/missing DB raises, not returns empty)
- **Measured by:**
  - Crash-injection test: `kill -9` mid-cycle, then row-count check
    shows no partial commits
  - Corrupted-DB test: point the repo at a non-SQLite file; `load`
    raises (the US-001 pinned-trap test is updated to assert the raise)
  - grep for `path.write_text(json.dumps` in `bot/` returns only the
    read-only-fallback path in the JSON impl (no longer the singleton)
- **Baseline:** scattered non-atomic `save_*` calls outside `file_lock`
  + silent-empty-on-corruption reads (the trap)
- **Target:** all three measurements above pass
- **Source story:** US-004, US-008, US-010

### KPI-4: Zero behavior regression in existing commands

- **Who:** Discord end-users + Krewsayder
- **Does what:** run existing commands and hourly tasks without
  observable regression
- **By how much:** 0 regressions caught by the acceptance pass
- **Measured by:** `pytest bot/tests/test_cutover_acceptance.py` (or
  equivalent) exit 0 with all baseline-snapshot comparisons passing
  byte-for-byte
- **Baseline:** pre-cutover behavior (JSON backend) — captured as
  baseline fixtures during Slice 04 setup
- **Target:** 0 baseline-snapshot diffs across all command groups
  (`/view_leaderboard`, `/view_bombs`, `/get_replay`, `/upload_replay`,
  `/delete_replay`, `/register`, `/unregister`, `/move`, admin config,
  hourly auto-update, hourly cap-detect)
- **Source story:** US-011 (the user-visible gate)

## Per-story KPIs (embedded in each story's `## Outcome KPIs` section)

Every story in `user-stories.md` has its own Outcome KPI block. The
feature-level KPIs above aggregate the per-story KPIs into the cutover
gate. Summary:

| Story | KPI target | Measurement |
|-------|------------|-------------|
| US-001 | 100% ABC method coverage | ≥ 11 contract tests, all green |
| US-002 | 4 `try_insert` branches + 2 migrator paths | ≥ 6 dedup/migrator tests, all green |
| US-003 | 8 easy-entity tables, 0 plaintext `api_key` columns | `alembic upgrade head` exit 0 + grep `update_channel_id` == 0 |
| US-004 | 100% contract tests green against SQLite impl | parametrized `pytest` exit 0 |
| US-005 | 100% row-count parity (easy entities) | parity report exit 0, all PASS |
| US-006 | 100% dedup tests pass against SQL upsert | parametrized `test_tracker_dedup.py` exit 0 |
| US-007 | 100% replay entries assigned to prod server; per-tenant URL uniqueness | row count match + unique-constraint test passes |
| US-008 | 0 direct file-write calls in `bot/tracker.py` | grep `path.write_text` in `bot/tracker.py` == 0 |
| US-009 | 0 references to `replay_index.json` in `bot/cogs/` | grep in `bot/cogs/` == 0 |
| US-010 | 100% hourly writes transactional; 0 partial writes post-crash | crash-injection test + row-count check |
| US-011 | 0 regressions | cutover acceptance pass exit 0, all baselines match |

## Measurement cadence

- **Slice 01 (Release 1):** KPI-1 measured at slice exit (test count +
  green).
- **Slice 02 (Release 2):** KPI-1 re-measured (parametrized against
  SQLite impl); KPI-3's corrupted-DB test first measured here.
- **Slice 03 (Release 3):** KPI-2 measured at slice exit (parity
  report); KPI-3's grep measurement first applies (no JSON writes from
  migrated paths).
- **Slice 04 (Release 4):** KPI-3 fully measured (crash injection +
  grep); KPI-4 measured at slice exit (acceptance pass). KPI-4 is the
  feature's final gate.

## Out of scope for KPIs

- **Performance / latency:** the swap is not a performance project;
  SQLite is in-process and matches the single-process deployment. No
  latency KPI is set. (If a future slice shows a regression, add one
  then.)
- **Storage size:** the SQLite file will be smaller than the JSON tree
  (no key duplication, normalized), but size is not a goal.
- **New user outcomes:** there are none by design (Decision 4 = No
  JTBD). KPIs measure preservation, not adoption.