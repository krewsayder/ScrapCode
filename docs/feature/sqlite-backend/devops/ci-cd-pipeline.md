# CI/CD Pipeline — feature `sqlite-backend` (DEVOPS wave)

> **There is no CI.** DEVOPS D3 (confirmed by the user: "No CI — local
> pytest only."). This document records that fact and the local
> command that is the gate. One page.

## 1. CI platform: none

The ScrapCode bot has no CI today. No GitHub Actions, no GitLab CI, no
Jenkins, no Azure DevOps. Tests run locally via `pytest` on the
operator's dev machine (WSL2 / macOS) and on the VM before deploy. The
architecture baseline (brief §2.6) records this as the as-built state;
this wave does NOT introduce a CI system. Introducing one is a separate
project (recorded as a deferred consideration in `wave-decisions.md`).

## 2. The gate: local `pytest`

Every slice's exit gate is a local `pytest` invocation. The full local
gate for this feature:

```
cd /opt/discord-bot   # or the dev checkout
.venv/bin/pip install -r requirements.txt   # after requirements.txt changes
.venv/bin/pytest                                # full suite, exit 0
```

The full suite includes (per `outcome-kpis.md`):
- `bot/tests/test_repository_contract.py` — the 11 ABC methods + the
  pinned silent-empty trap, parametrized over both impls (KPI-1).
- `bot/tests/test_player_list_migrator.py` — the 2 migrator paths (KPI-1).
- `bot/tests/test_tracker_dedup.py` — the 4 `try_insert` dedup branches
  against the SQL upsert (KPI-1).
- `bot/tests/test_cutover_acceptance.py` — the snapshot-diff acceptance
  pass (KPI-4).
- The existing `test_permissions.py`, `test_tracker_tiebreak.py`.

Plus the architecture-enforcement tools (ADR-006 §"Architecture
enforcement"):
- `import-linter` (module-boundary rules — cogs MUST NOT import
  `sqlalchemy` / `aiosqlite` / `bot.db.*` / `bot.repository_sqlalchemy`):
  ```
  .venv/bin/lint-imports
  ```
- `pytest-archon` (composition-root `probe()` Protocol check):
  runs as part of the `pytest` suite (a `test_probe_protocol.py`).

## 3. Branch strategy: GitHub Flow (DEVOPS D8)

- Short-lived feature branches off `main`.
- PR → review → merge to `main`.
- `main` is always deployable.
- No `develop` branch, no release branches, no hotfix branches.
- Trunk-based was rejected (no CI gates on every commit — D3 means
  there is no fast commit-stage to enforce trunk-based's "integrate
  continuously" requirement). GitFlow was rejected (overkill for a
  single-operator bot with no release trains).

This matches the current workflow on `docs/architecture-baseline` →
feature branches → PR → `main` (the baseline branch itself is a docs
branch, not a release branch).

## 4. Deploy mechanism: manual `git pull` + `systemctl restart`

There is no automated CD. Deploy is a manual SSH + `git pull` +
`systemctl restart` per `platform-architecture.md` §8. The probe is the
auto-stop safety: a bad deploy refuses to start. There is no automated
rollback; rollback is the operator running the runbook
(`platform-architecture.md` §7).

## 5. Mutation testing: pre-release (DEVOPS D9)

Pre-release mutation testing is already persisted to the project
`CLAUDE.md`. Runs on the entire solution before each release. Does not
block delivery. Not a CI step (there is no CI); the operator runs it
locally before tagging a release.

## 6. What this wave does NOT introduce

- No GitHub Actions workflow file (no `.github/workflows/sqlite-backend.yml`).
  The DEVOPS deliverables for this wave deliberately omit a workflow
  skeleton — there is nothing to skeleton.
- No commit-stage pipeline (lint, test, security scan) — the operator
  runs `pytest`, `lint-imports`, and (pre-release) mutation testing
  locally.
- No deployment pipeline — manual SSH + `git pull` + `systemctl restart`.
- No PR status checks (no CI to report status). PR review is human-only.
- No secrets scanning (SCA) — `SCRAPCODE_DB_KEY` is in `.env`
  (gitignored). The operator ensures `.env` is not committed. A
  pre-commit `detect-secrets` hook is a deferred consideration, not
  this wave.

## 7. When CI is added later (deferred, not this wave)

The cheapest future CI for this repo is GitHub Actions on
`ubuntu-latest` running:
- `pip install -r requirements.txt`
- `pytest` (the full suite)
- `lint-imports`

That is a ~30-line workflow file. It is not built this wave because the
user explicitly confirmed "No CI — local pytest only." and introducing
it would expand scope beyond the storage-swap feature. The
architecture-enforcement rules (import-linter, pytest-archon) are
designed to drop into that future CI unchanged.