# Definition of Ready Validation — feature `sqlite-backend`

> 9-item hard gate per the nw-product-owner DoR checklist. Every item
> MUST pass with evidence. Failed items get specific remediation. This
> file is the gate for handoff to the DESIGN wave (solution-architect).

### DoR-1: Problem statement clear, domain language

**Status: PASS**

**Evidence:** Every story's `## Problem` block states the pain in the
domain's language (bot operator, Discord end-users, slash commands,
hourly tasks, repository ABC, JSON season files, non-atomic-write +
silent-empty-read trap). The feature-level problem is restated in the
bootstrapped job `preserve-data-integrity-through-backend-swap`
(`docs/product/jobs.yaml`) and traces to ADR-002's accepted decision
that SQLite is the successor for the JSON layer.

- US-001: "no executable contract pinning what
  `JsonClusterRepository` actually does"
- US-006: "the `try_insert(check_roster=True)` dedup is in-memory ...
  four branches ... silent"
- US-011: "every user-visible command and both hourly background tasks
  must produce the same observable output for the same input as before
  the swap"

No story prescribes a technical solution in its Problem statement.

### DoR-2: User/persona with specific characteristics

**Status: PASS**

**Evidence:** Each story's `## Who` block names the persona and
context. Two personas are used, both grounded in reality:

- **Krewsayder (bot operator/dev)** — appears in 10 of 11 stories (all
  infra stories + US-011). Specifically characterized as the operator
  who needs integrity preservation, the ABC seam, the parity report,
  and the rollback path.
- **Discord end-users (guild members, officers, admins)** — appears in
  US-011, characterized by the commands they run
  (`/view_leaderboard`, `/register`, etc.) and the observable output
  they expect.

No generic "user123" / "test@test.com" personas. Real Discord user IDs
from the codebase (`1458181638453203099`) and realistic player names
(`Maria Santos`, `Jonas Klein`, `Aiko Tanaka`) are used throughout.

### DoR-3: 3+ domain examples with real data

**Status: PASS**

**Evidence:** Every story has a `## Domain Examples` section with 3
examples using realistic data. Examples use:

- Realistic player names: Maria Santos, Jonas Klein, Aiko Tanaka
- Realistic Tacticus uids: `tacticus-uid-001`, `tacticus-uid-002`
- Realistic hero names from the codebase: Aethana, Eldryon, Tan Gida,
  Khaine
- Realistic Discord server ID: `1458181638453203099` (the
  `DEV_GUILD_ID` from `main.py`)
- Realistic guild slugs: `neuro`, `mech` (lowercased, no spaces, per
  `register_guild` normalization)
- Realistic season numbers: 94
- Realistic damages: 12000, 15000, 9000 (Tacticus-scale)
- Realistic replay URLs: `https://replay.example/abc`

No generic / abstract examples. Each example includes a real persona,
real data, an action, and an outcome (the LeanUX 3-example format).

### DoR-4: UAT in Given/When/Then (3-7 scenarios)

**Status: PASS**

**Evidence:** Every story has a `## UAT Scenarios (BDD)` section with
3-5 Given/When/Then scenarios (within the 3-7 range). Scenario titles
describe business outcomes, not implementation mechanics:

- US-001: "Every ClusterRepository ABC method round-trips through JSON"
  (good) — not "JsonClusterRepository._read_json returns dict" (bad)
- US-006: "Same player + same roster + higher damage replaces the
  existing row" (good) — not "ON CONFLICT DO UPDATE fires" (bad)
- US-011: "/view_leaderboard output is byte-identical pre- and
  post-cutover" (good) — not "SqlAlchemyClusterRepository returns same
  rows" (bad)

Counts: US-001 (4), US-002 (5), US-003 (4), US-004 (4), US-005 (4),
US-006 (5), US-007 (4), US-008 (4), US-009 (5), US-010 (5), US-011 (6).
All within 3-7.

### DoR-5: AC derived from UAT

**Status: PASS**

**Evidence:** Every story's `## Acceptance Criteria` checkboxes trace
directly to its UAT scenarios. Spot check:

- US-006 AC "Insert path is `ON CONFLICT DO UPDATE SET damage =
  MAX(excluded.damage, battle_hits.damage)`" derives from UAT
  scenarios "Same player + same roster + higher damage replaces" and
  "Same player + same roster + lower damage does not replace".
- US-010 AC "A crash mid-cycle leaves the DB in the pre-cycle state"
  derives from UAT scenario "Hourly auto_update write is wrapped in a
  single transaction".
- US-011 AC "Post-cutover, every command's embed/reply matches its
  baseline byte-for-byte" derives from the per-command-group UAT
  scenarios.

No AC exists that does not map to a UAT scenario; no UAT scenario lacks
a corresponding AC.

### DoR-6: Right-sized (1-3 days, 3-7 scenarios)

**Status: PASS**

**Evidence:** Every story is sized within 1-3 days and has 3-7
scenarios:

| Story | Scenarios | Est. effort | Within limits? |
|-------|-----------|-------------|----------------|
| US-001 | 4 | 0.5 day (test module) | YES |
| US-002 | 5 | 0.5 day (test module) | YES |
| US-003 | 4 | 1 day (schema + Alembic + Fernet) | YES |
| US-004 | 4 | 1 day (repo impl + parametrized tests) | YES |
| US-005 | 4 | 1 day (migration + parity report) | YES |
| US-006 | 5 | 1 day (hard entities + upsert) | YES |
| US-007 | 4 | 0.5 day (replay migration + tenancy decision) | YES |
| US-008 | 4 | 0.5 day (tracker rewrite) | YES |
| US-009 | 5 | 0.5 day (replay_cog rewrite) | YES |
| US-010 | 5 | 0.5 day (singleton flip + transaction) | YES |
| US-011 | 6 | 1 day (acceptance pass + baselines) | YES |

Total: ~7 days across 11 stories, organized into 4 carpaccio slices of
≤1 day each. No story exceeds 3 days or 7 scenarios.

### DoR-7: Technical notes: constraints/dependencies

**Status: PASS**

**Evidence:** Every story has a `## Technical Notes (Optional)` block
recording constraints and dependencies. Key constraints captured:

- US-001: tests use `tmp_path`, not the real `clusters/` tree.
- US-003: Fernet key MUST NOT be logged; decrypt-on-read in repo layer.
- US-004: `__meta__.version` is a cog-compat shim; `get_guild_data_path`
  may return a sentinel / raise in the SQLite impl.
- US-005: migration runs against a COPY of the production tree (not
  `/opt/discord-bot/clusters/` directly); must be reversible.
- US-006: `roster_key` stored as text, not the heroes as JSON; dedup
  uses `roster_key` only.
- US-007: `submitted_by` has no FK; `damage` is free-text (not int).
- US-008: `process_api_response` signature changes from
  `(api_data, season, data_dir)` to `(api_data, season,
  discord_server_id, guild_id)`.
- US-010: `aiosqlite` keeps the event loop unblocked; `file_lock` may
  retire or stay as belt-and-suspenders (DESIGN decision).

Cross-cutting constraints are also recorded in `wave-decisions.md`
(`## System Constraints`).

### DoR-8: Dependencies resolved or tracked

**Status: PASS**

**Evidence:** Inter-slice and inter-story dependencies are explicit in
`story-map.md` (`## Priority Rationale`) and in each slice brief:

- US-004 depends on US-003 (schema must exist before impl).
- US-005 depends on US-003, US-004 (schema + impl must exist before
  data migration).
- US-006, US-007 depend on US-005 (the migration framework is shared).
- US-008, US-009 depend on US-006, US-007 (the SQL upsert / replay
  tables must exist before the bypasses route through them).
- US-010 depends on US-008, US-009 (the singleton flip is meaningless
  while the bypasses still write JSON).
- US-011 depends on US-010 (the acceptance pass runs against the
  flipped singleton).

External dependencies tracked:

- New Python packages (`sqlalchemy>=2.0`, `alembic`, `aiosqlite`,
  `cryptography`) via `requirements.txt` into the existing `.venv`
  (constraint in `wave-decisions.md`).
- Production data copy for Slice 03 (operator must `scp` the
  `clusters/` tree off the VM — operational dependency, not code).
- The Chronicler API (Gap 4, deferred) is NOT touched by this feature.

No unresolved blockers. All dependencies are either resolved (within
the feature) or tracked (operational step for the operator).

### DoR-9: Outcome KPIs defined with measurable targets

**Status: PASS**

**Evidence:** Every story has an `## Outcome KPIs` block with Who /
Does what / By how much / Measured by / Baseline. The feature-level
KPIs are aggregated in `outcome-kpis.md`:

- KPI-1: contract test coverage (target ≥ 17 tests, all green against
  both impls).
- KPI-2: row-count parity (target 100%).
- KPI-3: atomic-write guarantee (target 0 partial writes post-crash,
  0 silent-empty reads, 0 JSON writes from migrated paths).
- KPI-4: zero behavior regression (target 0 baseline-snapshot diffs).

All targets are numeric and measurable. Baselines are stated (e.g.
"0 contract tests today", "scattered non-atomic `save_*` calls outside
`file_lock`").

---

## Peer review

**Status: PENDING — not yet run.**

Per the nw-product-owner workflow, peer review is required before
`*handoff-design`. The orchestrator's instructions stop at "produce
DISCUSS artifacts; do not proceed to DESIGN." Peer review via the
`nw-product-owner-reviewer` agent is the next step the orchestrator
would dispatch; this DoR validation is the self-assessment that
prepares for it.

Self-identified risks to flag to the reviewer:

1. **JTBD skipped (Decision 4).** The feature's "job" is bootstrapped
   retroactively in `docs/product/jobs.yaml`, not discovered. This is
   honest for a backend swap but the reviewer should confirm the
   skip is justified per the orchestrator's framing.
2. **No DIVERGE artifacts.** DISCOVER was skipped (degenerate for a
   backend swap). `wave-decisions.md` records this as a risk.
3. **US-004's `get_guild_data_path` ambiguity.** The ABC method is
   JSON-specific (returns a filesystem dir for `tracker.py`). Slice
   04 retires it; for now the SQLite impl may return a sentinel or
   raise. This is flagged in `wave-decisions.md` as an IMPLEMENTATION
   decision for DESIGN.
4. **`battle_hits_simple` disposition.** Data-dictionary §2.8 flags it
   as "written but not read." US-006 defers the drop/mirror decision to
   DESIGN; `wave-decisions.md` records the deferral.

## Handoff package for DESIGN wave

The handoff to `solution-architect` (DESIGN) consists of:

- `docs/feature/sqlite-backend/discuss/user-stories.md` (11 LeanUX
  stories with embedded AC)
- `docs/feature/sqlite-backend/discuss/story-map.md` (backbone +
  walking skeleton + 4 slices + scope assessment PASS)
- `docs/feature/sqlite-backend/discuss/dor-validation.md` (this file)
- `docs/feature/sqlite-backend/discuss/outcome-kpis.md` (4 feature
  KPIs + per-story KPI summary)
- `docs/feature/sqlite-backend/discuss/wave-decisions.md` (decisions,
  constraints, deferred items)
- `docs/feature/sqlite-backend/slices/slice-{01,02,03,04}-*.md`
  (carpaccio slice briefs with learning hypotheses + taste tests)
- `docs/product/jobs.yaml` (minimal SSOT bootstrap)
- Source spec (unchanged): `docs/product/architecture/data-dictionary.md`,
  `adr-002-storage-backend-json-legacy.md`, `brief.md`

DESIGN wave should resolve the IMPLEMENTATION-flagged items in
`wave-decisions.md` (Fernet vs. alternative secrets store,
`get_guild_data_path` disposition, `battle_hits_simple` drop/mirror,
`file_lock` retirement, `capped_state` column vs. table) and produce
the architecture + component design for the four slices.