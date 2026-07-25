# KPI Instrumentation — feature `sqlite-backend` (DEVOPS wave)

> Maps each of the 4 outcome KPIs from
> `docs/feature/sqlite-backend/discuss/outcome-kpis.md` to a concrete
> measurement: the command, the artifact, the pass/fail criterion, and
> where the artifact is stored. There is no metrics stack (DEVOPS D5 =
> structured JSON logs + test artifacts); the KPIs are measured by
> `pytest` runs, migration reports, and grep — not by a dashboard.

## KPI-1 — Repository contract test coverage

- **Target:** ≥17 contract tests, all green against BOTH `JsonClusterRepository`
  and `SqlAlchemyClusterRepository` (parametrized).
- **Measurement:** `pytest --collect-only` count on the contract test files ≥ 17,
  then `pytest` run exit 0.
- **Command:**
  ```
  .venv/bin/pytest --collect-only \
      bot/tests/test_repository_contract.py \
      bot/tests/test_player_list_migrator.py \
      bot/tests/test_tracker_dedup.py \
    | tail -1   # expect "N tests collected"
  .venv/bin/pytest bot/tests/test_repository_contract.py \
                  bot/tests/test_player_list_migrator.py \
                  bot/tests/test_tracker_dedup.py
  ```
- **Parametrization:** the contract tests are parametrized over the two
  impls via a fixture (`@pytest.fixture(params=[json_impl, sqlite_impl])`).
  The count check (≥17) is on the un-parametrized test definitions; the
  actual run is 2× that count after parametrization.
- **Artifact:** the pytest run output. Stored locally; the operator
  pastes the final summary line into the slice exit report. No new file
  written by the test run itself beyond `pytest`'s normal output.
- **Pass criterion:** `pytest` exit 0 AND collected-test count ≥ 17.
- **Cadence:** Slice 01 (JSON only), re-measured Slice 02 (parametrized
  against SQLite). Gate at both slice exits.
- **Failure handling:** a failing contract test blocks the slice. Do not
  proceed to the next slice until green. If a contract test fails only
  on the SQLite impl, the SQLite impl is wrong (the contract is defined
  by the JSON impl's behavior — ADR-002 / ADR-006 D2).

## KPI-2 — JSON→SQLite row-count parity

- **Target:** 100% row-count parity across all easy-entity tables +
  `battle_hits` + `bomb_hits` + `replay_entries` + `replay_threads`.
  Every table shows `JSON=N SQL=N PASS`; any `MISMATCH` exits non-zero
  and blocks Slice 04.
- **Measurement:** the parity report emitted by the Slice-03 data
  migration (`bot/db/migrations_json_to_sqlite.py`).
- **Command:**
  ```
  .venv/bin/python -m bot.db.migrations_json_to_sqlite \
      --source clusters/ \
      --db data/scrapcode.db \
      --report data/backups/parity-cutover-$(date +%Y%m%dT%H%M%S).json
  echo $?   # 0 = PASS, 1 = FAIL
  ```
- **Artifact:** a JSON file at the `--report` path, format:
  ```json
  {
    "generated_at": "...",
    "source": "clusters/",
    "db": "data/scrapcode.db",
    "tables": {
      "guilds":              {"json": 1,   "sql": 1,   "status": "PASS"},
      "player_registrations": {"json": 7,   "sql": 7,   "status": "PASS"},
      "players":              {"json": 42,  "sql": 42,  "status": "PASS"},
      "battle_hits":          {"json": 311, "sql": 311, "status": "PASS"},
      "bomb_hits":            {"json": 89,  "sql": 89,  "status": "PASS"},
      "replay_entries":       {"json": 17,  "sql": 17,  "status": "PASS"},
      "replay_threads":       {"json": 9,   "sql": 9,   "status": "PASS"}
    },
    "overall": "PASS"
  }
  ```
  Stored under `data/backups/` on the VM (gitignored). The operator
  keeps the cutover-day parity report indefinitely as the audit trail.
- **Pass criterion:** `overall: "PASS"` AND process exit 0.
- **Cadence:** measured at Slice 03 exit (dry-run, against the copied
  tree) and at cutover (step B1 of the deploy runbook).
- **Failure handling:** any `MISMATCH` blocks Slice 04 / the cutover.
  Inspect the per-table counts; the most likely causes are (a) the
  `PlayerListMigrator` v1→v2 inversion not run on a v1 file (US-005),
  (b) the `try_insert` dedup not applied (US-006), (c) the
  `replay_index.json` tenancy assignment wrong (US-007 / ADR-006 D11).
  Fix the migration, re-run (idempotent).

## KPI-3 — Atomic-write guarantee (data-loss trap retired)

- **Target:** 100% of hourly `auto_update` writes transactional; 0
  partial writes observable after a crash; 0 silent-empty reads; 0 JSON
  writes post-cutover from migrated paths.
- **Measurement:** three sub-measurements, each with its own artifact.

### KPI-3a — Crash-injection test
- **Command:**
  ```
  # 1. Start the bot on the SQLite backend with a populated DB.
  # 2. Trigger (or wait for) an hourly auto_update cycle to BEGIN.
  # 3. CAPTURE THE PRE-CRASH ROW-COUNT BASELINE before injecting the crash
  #    (this is the comparison point for the post-restart check):
  .venv/bin/python -c "
  import sqlite3, json, time
  c = sqlite3.connect('data/scrapcode.db')
  counts = {t: c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
            for t in ['battle_hits','bomb_hits','players','player_registrations']}
  snap = {'captured_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
          'counts': counts}
  open('/tmp/scrapcode-pre-crash-counts.json','w').write(json.dumps(snap, indent=2))
  print(json.dumps(snap, indent=2))
  "
  # 4. Mid-cycle, kill the process hard:
  sudo kill -9 $(pgrep -f 'python main.py')
  # 5. Restart; let it finish a clean cycle.
  sudo systemctl start discord-bot
  # 6. Row-count check: compare pre-crash (step 3) and post-restart counts.
  .venv/bin/python -c "
  import sqlite3, json
  pre = json.load(open('/tmp/scrapcode-pre-crash-counts.json'))['counts']
  c = sqlite3.connect('data/scrapcode.db')
  for t in ['battle_hits','bomb_hits','players','player_registrations']:
      n = c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
      print(t, 'pre=', pre[t], 'post=', n, 'delta=', n - pre[t])
  "
  ```
- **Artifact:** the pre-crash baseline JSON file at
  `/tmp/scrapcode-pre-crash-counts.json` (kept for the slice exit report)
  + the post-restart row-count stdout. Both pasted into the slice exit
  report.
- **Pass criterion:** no table shows partial-row artifacts; the crashed
  guild's count matches its pre-cycle state, other guilds' counts match
  their completed-cycle state. Repeatable. The pre-crash baseline file
  is the evidence the comparison is against a real captured state, not a
  remembered one.

### KPI-3b — Corrupted-DB test (silent-empty trap retired)
- **Command:**
  ```
  # Point the repo at a non-SQLite file (e.g. a text file):
  echo "not a database" > /tmp/notadb.db
  SCRAPCODE_DB_PATH=/tmp/notadb.db .venv/bin/python -c "
  from bot.db.session import Database
  from bot.guilds import repo   # composition root runs probe()
  # If this line is reached, the probe FAILED to refuse — bug.
  print('UNEXPECTED: probe did not refuse a corrupted DB')
  "
  # Expect: probe raises health.startup.refused; the script exits non-zero
  #         with a structured log record; the print line is never reached.
  ```
- **Artifact:** the script's exit code + the structured
  `health.startup.refused` log record in `discord.log`.
- **Pass criterion:** probe refuses (non-zero exit); `load` raises rather
  than returning empty. The US-001 pinned-trap test is updated to assert
  the raise.

### KPI-3c — Grep measurement (no JSON writes from migrated paths)
- **Command:**
  ```
  # Post-cutover, these greps MUST return 0 matches in the migrated
  # modules (the only allowed match is the read-only fallback in the
  # JSON impl, which is not the singleton post-cutover):
  grep -rn 'path.write_text' bot/tracker.py bot/embeds.py bot/cogs/replay_cog.py \
      && echo "FAIL: JSON write found in migrated module" \
      || echo "PASS"
  grep -rn 'replay_index.json' bot/cogs/ \
      && echo "FAIL: replay_index.json still referenced in cogs" \
      || echo "PASS"
  grep -rn 'load_json\|save_json\|try_insert\|BATTLE_SIMPLE_FILE' bot/tracker.py \
      && echo "FAIL: retired helpers still in tracker.py" \
      || echo "PASS"
  ```
- **Artifact:** the grep stdout (paste into the slice exit report).
- **Pass criterion:** all three greps return 0 matches in the migrated
  modules.
- **Cadence:** KPI-3a first measured Slice 04 (crash injection requires
  the full cutover). KPI-3b first measured Slice 02 (probe exists once
  the SQLite impl + probe exist). KPI-3c first applies Slice 03 (no JSON
  writes from migrated paths after the migration exists).

## KPI-4 — Zero behavior regression in existing commands

- **Target:** 0 baseline-snapshot diffs across all command groups
  (`/view_leaderboard`, `/view_bombs`, `/get_replay`, `/upload_replay`,
  `/delete_replay`, `/register`, `/unregister`, `/move`, admin config,
  hourly auto_update, hourly cap_detect).
- **Measurement:** a snapshot diff of command output pre-cutover (JSON
  backend) vs post-cutover (SQLite backend), byte-for-byte.

### Pre-cutover baseline capture
- **Command:**
  ```
  # On the VM, with SCRAPCODE_REPO_BACKEND=json (the pre-cutover state),
  # capture baseline snapshots of every command's output against a
  # fixed test guild + season. Run via a pytest fixture that drives the
  # bot in a test guild with the copied prod data (US-011).
  .venv/bin/pytest bot/tests/test_cutover_acceptance.py::test_capture_baseline \
      --snapshot-dir data/backups/cutover-baseline/
  ```
  This writes one JSON snapshot file per command group:
  `view_leaderboard.json`, `view_bombs.json`, `get_replay.json`, etc.

### Post-cutover comparison
- **Command:**
  ```
  # After cutover (SCRAPCODE_REPO_BACKEND=sqlite), re-run the same
  # commands against the same data and diff:
  .venv/bin/pytest bot/tests/test_cutover_acceptance.py::test_compare_to_baseline \
      --snapshot-dir data/backups/cutover-baseline/
  ```
- **Artifact:** the diff output. Empty diff = PASS. Any diff is written
  to `data/backups/cutover-diff-<timestamp>.patch` for inspection.
- **Pass criterion:** `pytest` exit 0, 0 diffs across all command groups.
- **Cadence:** baseline captured during Slice 04 setup; comparison run
  at Slice 04 exit (the feature's final gate).
- **Failure handling:** a non-empty diff blocks the cutover. Inspect the
  diff — the most common cause is a sort-order difference (the SQL
  `ORDER BY` must match the JSON `(-damage, completed_on asc)` tiebreak —
  brief §4.5, ADR-006 D12). If the diff is a presentation-only string,
  the snapshot is too tight; loosen it. If it is a real behavioral
  difference, fix the SQLite impl to match (the JSON impl defines the
  behavior — ADR-002).

## Instrumentation summary table

| KPI | Measurement | Command | Artifact | Pass | Cadence |
|-----|-------------|---------|----------|------|---------|
| KPI-1 | Contract test count + green | `pytest --collect-only` + `pytest` | pytest output | ≥17 tests, exit 0 | Slice 01, Slice 02 |
| KPI-2 | Row-count parity | migration `--report` | parity JSON file | `overall:"PASS"`, exit 0 | Slice 03, cutover |
| KPI-3a | Crash injection | `kill -9` + row-count | row-count stdout | no partial commits | Slice 04 |
| KPI-3b | Corrupted-DB refusal | probe against non-DB file | exit code + log | probe refuses | Slice 02 |
| KPI-3c | No JSON writes post-cutover | `grep` in migrated modules | grep stdout | 0 matches | Slice 03, Slice 04 |
| KPI-4 | Snapshot diff | `pytest test_cutover_acceptance.py` | diff (empty=pass) | 0 diffs | Slice 04 |

## Where artifacts live

All artifacts are local files on the VM, under `data/backups/`
(gitignored). The operator pastes the relevant summary lines into the
slice exit report (a short markdown file per slice). No metrics
dashboard is built (DEVOPS D7 = No continuous learning; this is a
one-shot cutover, not an ongoing feature with a north-star metric).

The structured JSON log records (probe results, migration progress,
hourly write transaction outcomes, parity report) are emitted to
`discord.log` per `observability-design.md`. Those log records are the
runtime audit trail; the artifacts above are the cutover-gate evidence.