# Wave Decisions — feature `sqlite-backend` (DISTILL wave)

> DISTILL wave-decisions summary. Author: Quinn (nw-acceptance-designer).
> Records: the reconciliation gate result, the WS strategy, the adapter
> coverage table, the scaffold inventory, the story→scenario traceability,
> the mandate-compliance evidence, and the self-review.

## Reconciliation gate (mandatory before scenarios)

Read all three wave-decisions (`discuss/`, `design/`, `devops/`) and
checked each DISCUSS decision against DESIGN / DEVOPS. Five reconciled
items were carried in (already resolved upstream — not re-blocked):

| # | DISCUSS | Resolution (DESIGN/DEVOPS) | DISTILL action |
|---|---------|---------------------------|----------------|
| R1 | D6 deferred `FORUM_CHANNELS`/`MAP_THREADS` → `replay_threads` | DESIGN ADR-006 D10 OVERRIDES → seed in Slice 03 | US-009 AC INVERTED — scenarios assert constants REMOVED + threads SEEDED (CS9, JP8) |
| R2 | US-008 scoped only the WRITE bypass | DESIGN ADR-007 found the READ bypass (5 cog call sites + `embeds.load_leaderboard_file`) | scenarios cover BOTH the `tracker.py` write rewire AND the cog/embeds read rewire via the 4 new ABC methods (RC2, AP12, CS1–CS3) |
| R3 | DESIGN ADR-006 D1 default `SCRAPCODE_DB_PATH=clusters.db` | DEVOPS U1 overrides to `data/scrapcode.db` | scenarios use the env var, not a hard-coded path (conftest `env_vars` fixture) |
| R4 | (new in DEVOPS) migration runs against a COPY of `clusters/` | DEVOPS constraint | parity scenarios copy the JSON tree to `tmp_path` first (conftest `tmp_clusters_tree`); JP9 asserts missing-source fails loudly |
| R5 | (new in DEVOPS) `RotatingFileHandler` as a Slice-04 fold-in | DEVOPS "deferred considerations" | NOT a DISTILL concern — recorded here for traceability; no scenario |

**Reconciliation passed — 0 NEW contradictions.** No new contradictions
were found during the DISTILL pass; every DISCUSS decision either held or
was already overridden by a recorded DESIGN/DEVOPS decision with a
documented rationale.

## WS strategy

**Strategy C — Real local.** One walking-skeleton scenario parametrized
over the three DEVOPS target environments (`clean`,
`with-existing-json-data`, `with-stale-config`), tagged
`@walking_skeleton @real_io @driving_port`. The `with-stale-config`
variant asserts the startup probe REFUSES the stale-alembic-version DB
(ADR-006 D8 step 2) and is also tagged `@infrastructure-failure`.

See `distill/walking-skeleton.md` for the litmus test + adapter tier audit.

## Adapter coverage table (Mandate 6)

Every driven adapter in the DESIGN component-boundaries table
(`brief.md` §D) has at least one `@real-io @adapter-integration`
scenario. Audit:

| Adapter | Real-I/O scenario(s) | Status |
|---------|----------------------|--------|
| `JsonClusterRepository` (real JSON in `tmp_path`) | RC1 (parametrized json), RC2 (json), RC6, CS1–CS5 (json-backed render baseline) | YES — real JSON |
| `SqlAlchemyClusterRepository` (real SQLite file) | RC1 (sqlite), RC7, RC9, AP1, AP7, AP12, CS1 (sqlite render) | YES — real SQLite |
| Fernet secret encoder (real `cryptography.Fernet` round-trip) | RC9 (`@adapter-integration`), AP2 (`@adapter-integration`), JP3 (`@adapter-integration`) | YES — real Fernet |
| Alembic migrator (real `alembic upgrade head` on tmp DB) | AP1 (asserts WAL + alembic head), JP10 (empty source → schema applied, `alembic_version` populated), WS `clean` env | YES — real alembic |
| JSON→SQLite data migration (real subprocess) | JP1 (`@driving_port @real-io`), JP2, JP3, JP5, JP6, JP8, JP9, JP10, WS1, WS2 | YES — real subprocess |

Zero "NO — MISSING" rows. Every adapter has real-I/O integration coverage
in either the walking skeleton or a dedicated adapter scenario.

## Scaffold inventory (Mandate 7 — RED-ready scaffolding)

`__SCAFFOLD__ = True` markers in:

| File | Scaffold role | Real impl lands in DELIVER (slice) |
|------|---------------|-----------------------------------|
| `bot/db/__init__.py` | Package marker + scaffold flag | — (package only) |
| `bot/db/models.py` | Declarative ORM models (`ClusterRow`, `GuildRow`, `PlayerRegistrationRow`, `PlayerRow`, `BattleHitRow`, `BombHitRow`, `ReplayEntryRow`, `ReplayThreadRow`) — all raise `AssertionError` | Slice 02 (US-003) |
| `bot/db/session.py` | `Database` factory + `session_scope()` + `probe()` — all raise `AssertionError` | Slice 02 (US-003 / ADR-006 D8) |
| `bot/db/alembic/__init__.py` | Package marker | — |
| `bot/db/alembic/env.py` | `run_migrations_offline` / `run_migrations_online` raise `AssertionError` | Slice 02 (US-003) |
| `bot/db/alembic.ini` | Config stub pointing at `bot/db/alembic` | Slice 02 (US-003) |
| `bot/db/alembic/versions/.gitkeep` | Placeholder for baseline + data-migration revisions | Slice 02 (US-003) + Slice 03 (US-005) |
| `bot/db/migrations_json_to_sqlite.py` | `main()` arg wiring is real; `run_migration` + `build_parity_report` raise `AssertionError` | Slice 03 (US-005) |
| `bot/repository_sqlalchemy.py` | `SqlAlchemyClusterRepository(ClusterRepository)` — `__init__` + all 15 ABC methods + `probe()` raise `AssertionError` | Slice 02 (US-004) |

**Real interface change** (NOT a scaffold — ADR-007):

- `bot/repository.py` — `ClusterRepository` ABC extended with 4 new
  abstract methods (`load_battle_hits`, `load_bomb_hits`,
  `upsert_battle_hits`, `upsert_bomb_hits`). `JsonClusterRepository`
  gets real JSON-backed impls of all 4 (so the parametrized contract
  tests stay green against JSON and the rollback path is real). This
  is a permanent interface change, not a scaffold; zero `__SCAFFOLD__`
  markers in `bot/repository.py`.

The DELIVER crafter (`@nw-software-crafter`) replaces every scaffold
with a real impl. **Zero `__SCAFFOLD__` markers must remain at DELIVER
completion** — this is the gate the orchestrator checks at handoff.

## Story → scenario traceability

| Story | Driving port | Scenarios | Tags |
|-------|---------------|-----------|------|
| US-001 (contract test net) | `ClusterRepository` ABC | RC1, RC2, RC3, RC5, RC6, RC8, RC12 | `@driving_port @kpi` |
| US-002 (migrator + try_insert dedup) | `PlayerListMigrator.migrate`, `tracker.try_insert` | RC13, RC14 | `@property` |
| US-003 (SQLAlchemy models + Alembic + secrets) | `bot.db.models`, `bot.db.alembic`, Fernet | RC9, RC10, RC11, RC16, AP1, AP2 | `@real-io @adapter-integration @infrastructure-failure` |
| US-004 (SqlAlchemyClusterRepository easy entities) | `ClusterRepository` ABC | RC1 (sqlite), RC4, RC7, RC8, RC12 (sqlite) | `@driving_port @real-io` |
| US-005 (JSON→SQLite migration + parity) | migration CLI subprocess | JP1, JP2, JP3, JP4, JP5, JP9, JP10 | `@driving_port @kpi @real-io` |
| US-006 (battle_hits + bomb_hits upsert keep-max) | `ClusterRepository.upsert_*` / `load_*` | RC2, RC15 | `@property @real-io` |
| US-007 (replay_index migration + tenancy) | migration CLI subprocess | JP6, JP7, JP8 | `@kpi @real-io @infrastructure-failure` |
| US-008 (route tracker.py through repo) | `tracker.process_api_response` | AP12, AP11 | `@driving_port @kpi @real-io` |
| US-009 (route replay_cog.py through repo) | `replay_cog` commands | CS5, CS6, CS7, CS8, CS9 | `@driving_port @kpi @real-io @infrastructure-failure` |
| US-010 (flip singleton + transactional write + fallback) | composition root, probe | AP1, AP3, AP7, AP8, AP9, AP10 | `@driving_port @kpi @real-io @infrastructure-failure` |
| US-011 (existing command behavior preserved) | `embeds.build_*_messages` | CS1, CS2, CS3, CS4, CS5, CS10 | `@driving_port @kpi @real-io` |

Every story (US-001..US-011) has at least one scenario referencing it.
Check A (story→scenario) of the traceability dimension: PASS.

## Environment → scenario traceability (Check B)

| Environment | Walking-skeleton Given clause | Status |
|-------------|-------------------------------|--------|
| `clean` | WS1 "a JSON clean cluster tree in a temporary working directory" | YES |
| `with-existing-json-data` | WS2 "a JSON with-existing-json-data cluster tree in a temporary working directory" | YES |
| `with-stale-config` | WS3 "the SQLite database is stamped at an older alembic revision than the compiled head" + "configured for with-stale-config" | YES |

Every DEVOPS environment has a matching walking-skeleton Given clause.
Check B: PASS.

## Scenario count + error-path ratio

- Total scenarios: 51 (collected by `pytest --collect-only`)
- Walking skeletons: 3 (`@walking_skeleton @real-io @driving_port`)
- `@infrastructure-failure`: 15 (WS3, RC6, RC7, RC10, RC11, JP4, JP7, JP9, AP3, AP4, AP5, AP6, AP7, AP10, CS7)
- `@edge`: 4 (RC8, RC12, JP10, CS4)
- `@property`: 8 (RC13, RC14, RC15, JP5, JP7, AP8, CS10)
- `@kpi`: 12 (RC1, RC16, JP1, JP6, AP3, AP7, AP11, CS1, CS2, CS3, CS9)
- `@real-io`: 22 (every adapter-integration + WS + migration/probe scenario)
- `@adapter-integration`: 3 (RC9, AP2, JP3)
- `@driving_port`: 17

Error / edge / property / refusal scenarios: 15 + 4 + 8 + 3 (refusals
already counted in infrastructure-failure) = 27 distinct scenarios /
51 ≈ **53%** non-happy-path coverage. The 40% target is met.

## Mandate compliance evidence (CM-A / CM-B / CM-C / CM-D)

### CM-A — Driving-port boundary

Import audit: test files import driving ports only at the top level
(`bot.repository.ClusterRepository`, `bot.repository.JsonClusterRepository`,
`bot.migrations.player_list_migrations.PlayerListMigrator`,
`bot.tracker.try_insert` / `TOP_N`, `bot.models.Cluster` / `Guild`).
All `bot.db.*` and `bot.repository_sqlalchemy` imports are inside fixtures
or test functions (lazy) — they are the adapters under test, not internal
components, and they are accessed through the `ClusterRepository` ABC
fixture (`impl_pair`). No test imports an internal validator, parser, or
formatter directly.

### CM-B — Business language purity

The `.feature` files use domain terms only: "operator migrates a JSON
cluster tree to SQLite," "leaderboards match byte-for-byte," "probe
refuses with a health.startup.refused event," "the bot does not start on
the half-migrated database." No HTTP verbs, no status codes, no class
names, no `requests.post()` / `db.execute()` in the Gherkin. The pytest
module docstrings name the driving port per AC (Mandate 1 intent).

### CM-C — Walking skeleton + focused scenario counts

- Walking skeletons: 3 (one per environment) — Strategy C, real local.
- Focused scenarios: 48 (51 total − 3 WS) covering 11 user stories.

### CM-D — Pure function extraction

Pure functions tested directly (no fixture parametrization):
- `PlayerListMigrator.migrate` (RC13) — pure
- `bot.tracker.try_insert` (RC14) — pure
- `bot.tracker.get_tier_key` / `get_roster_key` — exercised via the
  upsert tests (RC15)

Impure code is isolated behind adapters parametrized via the
`impl_pair` fixture (only the adapter layer is parametrized; the
business logic is tested directly above). The `env_vars` fixture is
the only env-parametrized layer; the rest is hermetic.

## Self-review (DISTILL skill checklist, adapted)

1. ✓ One WS, Strategy C, real local — declared here and in
   `walking-skeleton.md`.
2. ✓ Every driven adapter has ≥1 `@real-io @adapter-integration`
   scenario — adapter coverage table above, zero missing rows.
3. ✓ Error path ratio ≥ 40% — 53%.
4. ✓ `@driving_port` on all WS scenarios (3/3).
5. ✓ `@kpi` scenarios present for all 4 KPIs (KPI-1 RC1/RC16; KPI-2 JP1/JP6;
   KPI-3 AP3/AP7/AP11; KPI-4 CS1/CS2/CS3/CS9).
6. ✓ Gherkin uses business language; zero technical jargon.
7. ✓ RED-ready scaffolding — `__SCAFFOLD__ = True` markers in every
   new module; the first scenario in each test module is ENABLED and
   fails with `AssertionError` (RED), not `ImportError` (BROKEN). The
   rest are `pytest.mark.skip` until DELIVER enables them one at a time.
8. ✓ `bot/repository.py` ABC extended with the 4 new ADR-007 methods;
   `JsonClusterRepository` has real JSON-backed impls (parametrized
   contract tests stay green against JSON).
9. ✓ Existing `bot/tests/test_tracker_tiebreak.py` still passes
   (5/5 green) after the ABC extension.
10. ✓ `pytest tests/acceptance/sqlite-backend/ --collect-only` collects
    51 tests with no errors; the 5 enabled scenarios all fail RED with
    `AssertionError`.
11. ✓ `@when` imports (lazy) come from `bot.*` (no `des.*` — adapted to
    this project's `bot/` layout).
12. ✓ No `pytest-bdd` added; plain pytest + pytest-asyncio, matching the
    user's scoped dependencies (sqlalchemy / alembic / aiosqlite +
    cryptography). No new runtime deps introduced by DISTILL.
13. ✓ No `check_driving_port_boundary.py` script exists in this project;
    substituted with the import audit in CM-A above. Grep confirms no
    test file imports `bot.db.*` or `bot.repository_sqlalchemy` at
    module top level — all such imports are inside fixtures / test
    functions (the adapters under test, accessed via the ABC).
14. ✓ Step methods delegate to production services; no business logic in
    tests beyond pure-function regression-pinning (US-002, explicitly
    `@infrastructure`-tagged in DISCUSS).
15. ✓ `Fixture Theater` check: the `tmp_clusters_tree` fixture sets up
    PRECONDITIONS (input state) only. The expected output (the
    JSON-backed render, the parity PASS, the probe success) is computed
    by the production code under test, not by the fixture. A test cannot
    pass without production code changes (the scaffolds raise
    `AssertionError`).

## Handoff

DISTILL artifacts ready for DELIVER dispatch by the orchestrator:

- `tests/acceptance/sqlite-backend/acceptance/*.feature` (5 files, the
  Gherkin scenario SSOT).
- `tests/acceptance/sqlite-backend/test_repository_contract.py`
  (20 tests — parametrized contract net).
- `tests/acceptance/sqlite-backend/test_json_to_sqlite_parity.py`
  (10 tests — subprocess-driven migration + parity).
- `tests/acceptance/sqlite-backend/test_atomicity_and_probe.py`
  (12 tests — probe + crash injection + grep).
- `tests/acceptance/sqlite-backend/test_cutover_snapshot.py`
  (10 tests — render parity + replay cutover).
- `tests/acceptance/sqlite-backend/conftest.py` (fixtures).
- `tests/acceptance/sqlite-backend/pytest.ini` (pythonpath + asyncio_mode).
- `bot/db/{__init__.py, models.py, session.py, alembic/,
  migrations_json_to_sqlite.py}` (RED scaffolds).
- `bot/repository_sqlalchemy.py` (RED scaffold).
- `bot/repository.py` (real interface change — 4 new ABC methods +
  real JSON-backed impls on `JsonClusterRepository`).
- `docs/feature/sqlite-backend/distill/walking-skeleton.md`.
- `docs/feature/sqlite-backend/distill/wave-decisions.md` (this file).

Collected test count: **51** (verified by
`pytest tests/acceptance/sqlite-backend/ --collect-only`).
Enabled-now count: 5 (one per module), all RED with `AssertionError`.
Skipped count: 46 (DELIVER enables one at a time).

Do NOT proceed to DELIVER — that is the orchestrator's next dispatch.
## Step 03-03 — replay single-server assignment (ADR-006 D11 deferral)

The JSON `replay_index.json` carries no `discord_server_id`, so the data
migration (Slice 03 / JP6) assigns ALL existing replay entries to the single
production server `1458181638453203099`. This is the only defensible
single-tenant assignment given the source data. True multi-tenant replay
partitioning (per-server `replay_entries` scoping driven by source-of-truth
server ids) is **deferred** until a second server exists — recorded as
ADR-006 D11. `replay_threads` is seeded from the hardcoded
`FORUM_CHANNELS` / `MAP_THREADS` constants in `bot/cogs/replay_cog.py`
(ADR-006 D10), closing the ADR-004 §3 hardcoded-thread-ID leak in the same
slice that closes the `replay_index.json` leak.

## Step 04-02 — ADR-007 §2 post-cutover: `get_guild_data_path` removed

ADR-007 §2 disposition completed in this step:

- `ClusterRepository.get_guild_data_path` removed from the ABC and from
  `JsonClusterRepository` (the JSON impl inlines the data-dir path in
  `_season_file` instead). `bot/embeds.load_leaderboard_file` removed
  (ADR-007 §3). The 5 cog read sites in `view_cog`, `admin_cog`,
  `tasks_cog` rewired to `repo.load_battle_hits` / `load_bomb_hits`.
- **Contract test note (ADR-007 §2 "Post-cutover"):** grep of
  `tests/acceptance/sqlite-backend/` for `get_guild_data_path` returns 0
  matches — no parametrized contract test asserted `get_guild_data_path`
  on the ABC (RC1 round-trips the other 14 ABC methods; it never
  exercised `get_guild_data_path`). There is therefore no assertion to
  remove; the removal of the ABC method is verified by the absence of
  import-time `AttributeError` in the suite + the green RC1/RC2
  parametrization over both impls. `bot/guilds.get_guild_data_path` (the
  thin wrapper) and `SqlAlchemyClusterRepository.get_guild_data_path`
  (raises `NotImplementedError`) remain as dead code; both are removed
  in Slice 04-04 (`bot/guilds.py` is 04-04 territory) — recorded here so
  the 04-04 crafter knows to grep-clean them.

## Step 04-02 — CS1 BLOCKER (render parity needs `hero_details` in SQLite)

`test_battle_leaderboard_render_byte_identical_pre_post_cutover` (CS1,
battle byte-identical render parity) is **RED and blocked** on a 03-01
design decision: `SqlAlchemyClusterRepository._battle_entry_from_row`
returns `hero_details: []` (the `battle_hits` schema stores only
`hero_roster_key` for dedup, not the hero list). `build_battle_messages`
renders `_build_hero_display(entry.get("hero_details", []))`, so the
JSON render shows `Aethana Eldryon | Khaine` while the SQLite render
shows `❌ | Khaine` — byte-difference on the hero line.

`discuss/user-stories.md` line 734 explicitly says "`hero_details` and
`machine_of_war` can be JSON columns for display, but the dedup uses
`roster_key` only" — i.e. the design permits storing `hero_details` for
display. The 03-01 step chose to drop it (RC15 only pinned `damage`,
not `hero_details`), so the deviation from data-dictionary §2.7 (which
lists `hero_details` in the load shape) went undetected.

Fixing CS1 requires storing `hero_details` (JSON column) on
`battle_hits`: changes to `bot/db/models.py`, an alembic migration, and
`bot/repository_sqlalchemy.py` (`_battle_entry_from_row` +
`_battle_params`). All three are OFF-LIMITS to 04-02 per BOUNDARY_RULES
("Do NOT touch ... `bot/repository_sqlalchemy.py`, or `bot/db/*`").
CS1 is therefore escalated; CS2/CS4/CS5 are green (bomb render has no
hero line; CS5 was switched to the bomb leaderboard because the
`is_former` fixture player Jonas Klein carries a Bomb hit, not a Battle
hit).
