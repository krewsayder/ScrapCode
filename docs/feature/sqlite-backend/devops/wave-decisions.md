# DEVOPS Decisions — feature `sqlite-backend`

> DEVOPS wave-decisions summary per the nw-devops format. Records the
> 9 decisions made by the orchestrator with the user, the
> infrastructure summary, the constraints established, and the upstream
> changes (one: the `SCRAPCODE_DB_PATH` default override).
> Author: Apex (nw-platform-architect), DEVOPS wave.

## Key Decisions

- **[D1] Deployment target: On-premise.** Self-hosted single Linux VM
  at `/opt/discord-bot`, systemd-managed. No cloud, no multi-region.
  (see: brief §2.6, `platform-architecture.md` §1)
- **[D2] Container orchestration: None.** Bare VM, systemd service. No
  Docker, no Kubernetes, no Compose. (see: `platform-architecture.md` §1)
- **[D3] CI/CD platform: None.** No CI today; the gate is local
  `pytest` (user-confirmed: "No CI — local pytest only."). No workflow
  skeleton is produced. (see: `ci-cd-pipeline.md`)
- **[D4] Existing infrastructure: reused.** The VM + systemd unit +
  `.venv/` are reused. CI is greenfield-but-skipped (D3). No existing
  CI/CD to integrate with. (see: `platform-architecture.md` §1, §4)
- **[D5] Observability: structured JSON logs + probe + KPI artifacts.**
  Extend the existing `discord.log` with structured JSON records; the
  ADR-006 `probe()` is the health gate; KPI instrumentation emitted as
  log records + test artifacts under `data/backups/`. No new metrics
  stack. (see: `observability-design.md`)
- **[D6] Deployment strategy: Recreate.** `systemctl restart
  discord-bot` (stop-and-replace). The probe refuses startup on a bad
  DB, so a failed deploy auto-stops the service (systemd `failed`
  state) rather than running a half-broken bot. No blue-green, no
  canary, no rolling — one process, one VM. (see:
  `platform-architecture.md` §5, §8)
- **[D7] Continuous learning: No.** No existing monitoring/alerting to
  build on; this is a one-shot cutover feature, not an ongoing service
  with steady-state traffic. Foundational only. (see:
  `observability-design.md` §5, §8)
- **[D8] Git branching: GitHub Flow.** Short-lived feature branches →
  PR → merge to `main`. Matches the current docs/architecture-baseline
  workflow. Trunk-based rejected (no fast CI commit-stage to enforce
  it); GitFlow rejected (overkill for single-operator bot). (see:
  `ci-cd-pipeline.md` §3)
- **[D9] Mutation testing: pre-release.** Already persisted to the
  project `CLAUDE.md` by the orchestrator. Runs on the entire solution
  before each release; does not block delivery. Not re-asked or
  rewritten. (see: `ci-cd-pipeline.md` §5)

## Infrastructure Summary

- **Deployment:** On-premise single Linux VM, systemd `discord-bot`
  unit, Recreate strategy (`systemctl restart`). Probe =
  auto-stop-on-failure.
- **CI/CD:** None (local `pytest` is the gate); GitHub Flow branching;
  manual `git pull` + `systemctl restart` deploy; pre-release mutation
  testing.
- **Observability:** structured JSON logs to existing `discord.log` +
  `journalctl`; ADR-006 `probe()` startup health gate; KPI artifacts
  (parity report, snapshot diffs, grep outputs) stored under
  `data/backups/`. No metrics stack, no automated alerting.
- **Data:** SQLite (WAL) at `/opt/discord-bot/data/scrapcode.db`
  (override via `SCRAPCODE_DB_PATH`, default `data/scrapcode.db`).
  Hourly online backup via systemd timer (`VACUUM INTO`, keep 24).
  Fernet-encrypted `api_key` columns (`SCRAPCODE_DB_KEY`).
- **Rollback:** env flip — `SCRAPCODE_REPO_BACKEND=json` + restart. JSON
  tree kept read-only one cycle post-cutover (ADR-006 D9). Plus DB-file
  restore from backup if the SQLite data itself is corrupted.
- **Mutation testing:** pre-release (per project `CLAUDE.md`).

## Constraints Established

- The systemd `discord-bot` unit is reused unchanged; if `Restart=always`
  is set on the host, add an `on-failure` drop-in so probe-failure
  loops are visible, not masked (`platform-architecture.md` §5).
- The SQLite DB file lives under `data/` (already gitignored). WAL
  sidecars (`-wal`, `-shm`) are backed up and restored together with
  the main file.
- `SCRAPCODE_DB_KEY` is a new operational secret; backed up with
  `DISCORD_TOKEN` in the operator's `.env` backup; never logged; loss
  renders `api_key` columns unrecoverable.
- The JSON→SQLite migration ALWAYS runs against a COPY of the
  production `clusters/` tree, never against `/opt/discord-bot/clusters/`
  directly (ADR-006 / DISCUSS constraint). The migration is idempotent
  (upsert-based).
- The one-cycle read-only JSON fallback: `clusters/` is NOT deleted
  during cutover. Deletion is a separate explicit step after one clean
  hourly cycle on SQLite (US-010).
- The probe is skipped when `SCRAPCODE_REPO_BACKEND=json` so a
  missing/invalid `SCRAPCODE_DB_KEY` does not block a JSON-backend
  rollback.
- No CI workflow file is produced this wave (DEVOPS D3). The
  architecture-enforcement tools (import-linter, pytest-archon) are
  designed to drop into a future CI unchanged.
- No metrics stack, no automated alerting (DEVOPS D7). The operator's
  "dashboard" is three `journalctl`/`systemctl` commands
  (`observability-design.md` §4).
- New deps go into `requirements.txt` and the existing `.venv/`:
  `sqlalchemy>=2.0`, `alembic`, `aiosqlite`, `cryptography`,
  `import-linter`, `pytest-archon`.

## Upstream Changes

### U1 — `SCRAPCODE_DB_PATH` default overridden from DESIGN

ADR-006 D1 and DESIGN `wave-decisions.md` record the SQLite default
path as `clusters.db` beside the existing `clusters/` tree. This
DEVOPS wave overrides that default to `data/scrapcode.db` (relative to
`WorkingDirectory=/opt/discord-bot`, absolute
`/opt/discord-bot/data/scrapcode.db`).

**Rationale:** the `data/` path is already gitignored (`.gitignore`
line 9); placing the DB there keeps the JSON source tree
(`clusters/`) and the new DB artifact (`data/`) visually separate.
This matters during the one-cycle read-only fallback when both exist
on disk — confusing the two would be a footgun. The env var override
still lets the operator pick any path, so no operational flexibility
is lost.

**Impact on DESIGN artifacts:** ADR-006 D1's text is NOT rewritten
(per the baseline's "contradictions are flagged, not rewritten" rule);
the override is recorded here and in `platform-architecture.md` §2 / §
10 (Changed Assumptions). The software-crafter reads
`platform-architecture.md` for the operational default; ADR-006
remains the architectural source of truth for the env-var-driven
mechanism.

No other DESIGN assumption is changed.

### No other upstream changes

The rest of the DEVOPS wave aligns with DESIGN:
- The probe (ADR-006 D8) is the deployment auto-stop mechanism (D6).
- The env-driven singleton flip (ADR-006 D9) is the rollback mechanism.
- The WAL + one-transaction-per-guild design (ADR-006 D6) is the
  crash-safety KPI-3a foundation.
- The Fernet `SCRAPCODE_DB_KEY` (ADR-006 D7) is the secrets
  mechanism; the operator runbook documents key generation + backup.
- The data-migration against-a-COPY constraint (ADR-006) is
  preserved in both the development/dry-run runbook and the cutover
  runbook (the snapshot in step A1 protects the production tree before
  step B1 runs against it).

## Deferred considerations (recorded, not implemented this wave)

- **CI.** The cheapest future CI is a ~30-line GitHub Actions workflow
  on `ubuntu-latest` running `pip install` + `pytest` + `lint-imports`.
  Out of scope (D3). The enforcement tools are designed to drop in.
- **Log rotation (HIGH-PRIORITY follow-up, folded into DELIVER Slice 04).**
  `discord.log` grows unbounded (`FileHandler` append, no rotation per
  brief §2.6). Switching to `logging.handlers.RotatingFileHandler`
  (10 MB × 5 files) is NOT implemented in the DEVOPS wave (no code
  changes this wave), but is tracked as a high-priority follow-up to be
  folded into DELIVER Slice 04 — Slice 04 already touches `main.py` to
  retire `file_lock` (ADR-006 D6) and extends the logging setup with
  the structured-JSON formatter (`observability-design.md` §1). Adding
  `RotatingFileHandler` at the same site is a one-line change in the
  same diff, so it costs no extra slice. The software-crafter should
  pick this up when implementing Slice 04's `main.py` modification; it
  is recorded here so it is not lost.
- **Automated alerting.** Cheapest path: systemd `OnFailure=` + a
  small webhook-to-Discord shim. Out of scope (D7).
- **Pre-commit `detect-secrets` hook.** `SCRAPCODE_DB_KEY` is in
  `.env` (gitignored). A pre-commit secrets hook would be defense in
  depth. Out of scope this wave.

## Quality gates (self-check)

- [x] Environment inventory produced (`environments.yaml` with 3 target
  environments, coexistence matrix, platform coverage, deployment
  assumptions).
- [x] CI/CD pipeline design documented (D3 = none; local `pytest` is
  the gate; `ci-cd-pipeline.md`).
- [x] Logging infrastructure design complete (extended `discord.log`
  with structured JSON records; `observability-design.md`).
- [x] Monitoring and alerting design complete (probe = health gate;
  manual `journalctl`/`systemctl` checks; no automated alerting — D7).
- [x] Observability design complete (probe at startup; no distributed
  tracing — single process).
- [x] Infrastructure integration assessed (reused VM + systemd + venv;
  no new infra except DB file + Fernet key + backup timer).
- [x] Continuous learning: N/A (D7 = No).
- [x] Git branching strategy selected and aligned (GitHub Flow; D8).
- [x] Mutation testing strategy selected and persisted to project
  `CLAUDE.md` (D9 pre-release; already done by orchestrator).
- [x] Outcome KPIs instrumentation designed (KPI-1..KPI-4 mapped to
  commands + artifacts in `kpi-instrumentation.md`).
- [x] Data collection per KPI documented (`kpi-instrumentation.md`
  table).
- [x] "Dashboard" = the operator's three `journalctl`/`systemctl`
  commands (`observability-design.md` §4); no Grafana.
- [ ] Peer review by `@nw-platform-architect-reviewer` — pending
  orchestrator invocation (this wave hands off; the orchestrator
  decides whether to invoke the reviewer before DISTILL).
- [ ] Handoff accepted by `nw-acceptance-designer` (DISTILL wave) —
  orchestrator's next dispatch.

## Handoff

DEVOPS artifacts ready for DISTILL dispatch by the orchestrator:

- `docs/feature/sqlite-backend/devops/platform-architecture.md` —
  the operational runbook (deploy, rollback, migration, backup).
- `docs/feature/sqlite-backend/devops/environments.yaml` — the
  environment inventory (MANDATORY for DISTILL Mandate 4).
- `docs/feature/sqlite-backend/devops/kpi-instrumentation.md` —
  KPI-1..KPI-4 → measurement mapping.
- `docs/feature/sqlite-backend/devops/observability-design.md` —
  structured JSON log design + probe as health gate.
- `docs/feature/sqlite-backend/devops/ci-cd-pipeline.md` — "no CI"
  documented; local `pytest` is the gate.
- `docs/feature/sqlite-backend/devops/wave-decisions.md` — this file.

Not produced by this wave (out of scope):
- Code, migrations, tests — DELIVER wave (`@nw-software-crafter`).
- Acceptance tests — DISTILL wave (`@nw-acceptance-designer`).
- CI workflow file — deferred (DEVOPS D3).
- Mutation testing execution — pre-release, run by the operator
  before tagging a release (D9).