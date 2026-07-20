<!-- markdownlint-disable MD024 -->

# User Stories — feature `sqlite-backend`

> **Traceability:** JTBD skipped per Decision 4 (motivations are clear from
> ADR-002). Every story traces to the single bootstrapped job
> `preserve-data-integrity-through-backend-swap` in
> `docs/product/jobs.yaml`. Stories marked `@infrastructure` are honest
> infra stories (no user-visible output) and so have no Elevator Pitch per
> the orchestrator's framing. The one user-visible story (US-011) carries
> the full `### Elevator Pitch`.
>
> **Scope:** 11 stories across 4 carpaccio slices (Slice 01: US-001, US-002;
> Slice 02: US-003, US-004; Slice 03: US-005, US-006, US-007; Slice 04:
> US-008, US-009, US-010, US-011). Each slice is ≤1 day. See
> `docs/feature/sqlite-backend/slices/` and `story-map.md`.

---

## US-001: Repository contract test net @infrastructure

### Problem

Krewsayder is the bot operator migrating ScrapCode's flat-JSON backend to
SQLite. He finds it unsafe to swap the storage layer because there is no
executable contract pinning what `JsonClusterRepository` actually does —
only prose in `data-dictionary.md`. Without a regression net he cannot make
an objective "SQLite preserved behavior" claim later; any divergence would
surface as a silent data-loss regression, which is exactly the trap ADR-002
is trying to retire.

### Who

- Krewsayder (bot operator/dev) | before Slice 02 | needs an executable
  contract so the SQLite impl can be objectively verified against current
  behavior.

### Solution

A pytest module that round-trip-tests every `ClusterRepository` ABC method
against `JsonClusterRepository` using synthetic JSON fixtures, plus a test
that pins the silent-empty-on-corruption read behavior (documenting it as
the trap Slice 02/04 retires). Tests are written to be repo-implementation-
agnostic so Slice 02 can parametrize them against
`SqlAlchemyClusterRepository` unchanged.

### Domain Examples

1. **Round-trip — cluster with two guilds.** Krewsayder saves a `Cluster`
   with `discord_server_id=1458181638453203099`, `role_tiers={"admin":
   [111], "officer":[222]}`, and guilds `"neuro"` (api_key="key-neuro",
   role_id=999, member_role_ids=[555]) and `"mech"` (api_key="",
   role_id=888). Loading returns an equivalent `Cluster` (same guild ids,
   same `role_tiers`, same `member_role_ids`).
2. **Round-trip — player_registrations 1:1 api_key.** Saving
   `{"123456789": {"api_key":"tacticus-abc","guild_id":"neuro"}}` and
   loading returns the same dict with the same string key.
3. **Silent-empty-on-corruption — pinned trap.** A `guilds.json` file
   containing the bytes `{broken` (truncated) is read back as an empty
   `Cluster(discord_server_id=...)` with no exception raised. The test
   asserts this *current* behavior and includes a comment: "RETIRE in
   Slice 02 — SqlAlchemyClusterRepository must raise, not return empty."

### UAT Scenarios (BDD)

#### Scenario: Every ClusterRepository ABC method round-trips through JSON

Given Krewsayder has a temp `clusters/` dir with synthetic fixtures for
  server `1458181638453203099` (cluster config, two guilds, registrations,
  capped state, player list v2, live leaderboards)
When Krewsayder calls each of `load`, `save`, `load_player_registrations`,
  `save_player_registrations`, `load_capped_state`, `save_capped_state`,
  `load_player_list`, `save_player_list`, `load_live_leaderboards`,
  `save_live_leaderboards`, `get_guild_data_path`, `list_server_ids`
Then each method's return value equals the input that produced it (round-
  trip equality on the dict / dataclass shapes the cogs consume)

#### Scenario: Player list v2 round-trips without invoking the migrator

Given a `player_list.json` already at `__meta__.version == 2` with one
  player `tacticus-uid-001` (display_name="Maria Santos",
  last_validated="2026-07-18T10:00:00Z", is_former=false)
When Krewsayder calls `load_player_list(server, "neuro")`
Then the returned dict equals the file content exactly and no file rewrite
  occurs (`was_migrated=False`)

#### Scenario: Silent-empty-on-corruption behavior is pinned as the trap to retire

Given a `guilds.json` file containing the truncated bytes `{broken`
When Krewsayder calls `load(server)` against `JsonClusterRepository`
Then the call returns `Cluster(discord_server_id=server)` with no guilds
  and no exception, and the test is annotated as "RETIRE in Slice 02"

#### Scenario: list_server_ids enumerates only numeric dirs

Given a `clusters/` dir containing `1458181638453203099/`,
  `9876543210/`, and a stray `clusters/.gitkeep` file
When Krewsayder calls `list_server_ids()`
Then the return is `[1458181638453203099, 9876543210]` (only numeric
  directory names, as ints)

### Acceptance Criteria

- [ ] A new `bot/tests/test_repository_contract.py` exists.
- [ ] Every one of the 11 `ClusterRepository` ABC methods has at least one
      round-trip assertion.
- [ ] One test pins the silent-empty-on-corruption read and is annotated as
      the behavior to retire in Slice 02.
- [ ] `pytest bot/tests/test_repository_contract.py` is green.
- [ ] Tests import only `JsonClusterRepository` and the ABC, so Slice 02
      can parametrize the impl.

### Outcome KPIs

- **Who:** Krewsayder (bot operator)
- **Does what:** runs the contract test suite as the regression gate before
  every subsequent slice
- **By how much:** 100% of `ClusterRepository` ABC methods covered by at
  least one round-trip test
- **Measured by:** `pytest --collect-only` test count for
  `test_repository_contract.py` ≥ 11 (one per ABC method, plus the
  pinned-trap test)
- **Baseline:** 0 contract tests today

### Technical Notes (Optional)

- Pin the silent-empty-on-corruption read with `pytest.mark.xfail` against
  the *target* behavior (`SqlAlchemyClusterRepository` raises) so the same
  test flips to green when the new impl lands.
- Tests must not write to the real `clusters/` tree; use `tmp_path`.

---

## US-002: PlayerListMigrator v1→v2 + try_insert dedup regression tests @infrastructure

### Problem

Krewsayder is about to move `PlayerListMigrator` v1→v2 from a per-read
runtime concern into a one-time data migration (Slice 03), and to replace
`tracker.try_insert`'s in-memory top-N dedup with a SQL upsert-keep-max
path. Both behaviors are subtle (the migrator flips an inverted map; the
dedup has four branches: same-roster-equal, same-roster-higher,
different-roster, top-N-truncate) and only `try_insert`'s tiebreak is
pinned today (`test_tracker_tiebreak.py`). Without pinning the rest, a
migration that "loses" a v1 player or "drops" a roster variant would be
silent.

### Who

- Krewsayder (bot operator/dev) | before Slice 03 | needs the migrator
  and dedup behavior pinned so the SQL upsert path can be verified
  equivalent.

### Solution

Extend `bot/tests/` with (a) `test_player_list_migrator.py` covering v1→v2
inversion, v2-noop, and the `last_validated` epoch sentinel; (b)
`test_tracker_dedup.py` covering the four `try_insert` branches that
`test_tracker_tiebreak.py` does not (same-roster-equal keeps first,
same-roster-higher replaces, different-roster inserts separately, top-N
truncation drops the lowest).

### Domain Examples

1. **v1→v2 inversion — three players.** A v1 `player_list.json` shaped
   `{"Maria Santos": "tacticus-uid-001", "Jonas Klein":
   "tacticus-uid-002", "Aiko Tanaka": "tacticus-uid-003"}` migrates to v2
   with three players keyed by tacticus uid, each `is_former=false` and
   `last_validated="1970-01-01T00:00:00Z"`, `was_migrated=True`.
2. **v2 input is a noop.** An already-v2 file (with `__meta__.version==2`)
   passes through `PlayerListMigrator.migrate` unchanged and
   `was_migrated=False`.
3. **Same-roster-higher replaces; different-roster inserts.** Player
   "Maria Santos" (uid-001) already has a Battle hit at damage=12000 with
   heroes `[Aethana, Eldryon]` and MoW `Khaine`. A new hit arrives at
   damage=15000 with the same heroes+MoW → the existing row is replaced
   (keep-max). A second new hit arrives at damage=9000 with heroes
   `[Aethana, Tan Gida]` (different roster) → inserted as a separate
   entry, not deduped against the first.

### UAT Scenarios (BDD)

#### Scenario: v1 player list inverts to v2 with the epoch sentinel

Given a v1 `player_list.json` mapping `{"Maria Santos":
  "tacticus-uid-001"}`
When `PlayerListMigrator.migrate(raw)` is called
Then the result is `{"__meta__":{"version":2}, "players":
  {"tacticus-uid-001":{"display_name":"Maria Santos",
  "last_validated":"1970-01-01T00:00:00Z","is_former":false}}}` and
  `was_migrated=True`

#### Scenario: v2 player list is a noop

Given a v2 `player_list.json` with `__meta__.version == 2` and one player
When `PlayerListMigrator.migrate(raw)` is called
Then the returned dict is unchanged and `was_migrated=False`

#### Scenario: Same player + same roster + higher damage replaces the existing entry

Given a Battle hits list containing one entry for player "Maria Santos"
  (uid-001) at damage=12000 with heroes `[Aethana, Eldryon]` and MoW
  `Khaine`
When `try_insert(entries, new_entry, check_roster=True)` is called with a
  new entry for the same player at damage=15000 and the same roster
Then the list still has one entry for that player+roster, now at
  damage=15000, and the function returned `True`

#### Scenario: Same player + different roster inserts as a separate entry

Given a Battle hits list containing one entry for "Maria Santos" with
  heroes `[Aethana, Eldryon]` and MoW `Khaine` at damage=12000
When `try_insert` is called with a new entry for the same player at
  damage=9000 with heroes `[Aethana, Tan Gida]` (different roster) and the
  same MoW
Then the list has two entries for that player (one per roster), and the
  function returned `True`

#### Scenario: Top-N truncation drops the lowest when a higher hit arrives

Given a Battle hits list already at `TOP_N=5` entries at damages
  100, 90, 80, 70, 60
When `try_insert` is called with a new entry at damage=75 with a roster
  not matching any existing entry
Then the list still has 5 entries, the damage=60 entry is gone, and the
  damage=75 entry is present

### Acceptance Criteria

- [ ] `bot/tests/test_player_list_migrator.py` covers v1→v2 inversion,
      v2-noop, and the epoch sentinel.
- [ ] `bot/tests/test_tracker_dedup.py` covers all four `try_insert`
      branches (same-roster-equal, same-roster-higher, different-roster,
      top-N-truncate).
- [ ] All new tests are green; existing `test_tracker_tiebreak.py`
      remains green untouched.
- [ ] Tests use real-style data (player names like "Maria Santos", tacticus
      uids like `tacticus-uid-001`, hero names from the codebase).

### Outcome KPIs

- **Who:** Krewsayder
- **Does what:** runs the migrator + dedup tests as the equivalence gate
  for Slice 03's SQL upsert path
- **By how much:** 4 `try_insert` branches + 2 migrator paths covered
  (6 named tests minimum)
- **Measured by:** `pytest --collect-only` count for the two new test
  files ≥ 6
- **Baseline:** only `test_tracker_tiebreak.py` (tiebreak-only) today

### Technical Notes (Optional)

- `test_tracker_tiebreak.py` already pins `(-damage, completed_on asc)` —
  do not duplicate; the new dedup tests use the same `_entry` helper.

---

## US-003: SQLAlchemy models + Alembic baseline + secrets store for api_key @infrastructure

### Problem

Krewsayder needs the SQLite successor schema to exist before any data
migration or repo impl can land. The schema must reflect the data
dictionary's §4 mapping, drop the unused `update_channel_id`, and move the
plaintext `api_key` columns (guild + registration) out of plaintext —
otherwise the swap would carry forward two standing security/privacy
hazards (plaintext secrets in a JSON file readable by anyone with shell
access to the VM, and an unused field that implies a config knob that
does not work).

### Who

- Krewsayder (bot operator/dev) | before Slice 02 repo impl | needs the
  schema + migration tooling + secrets story settled so the repo impl has
  something to sit on.

### Solution

Add `sqlalchemy>=2.0`, `alembic`, `aiosqlite` to `requirements.txt`; create
SQLAlchemy 2.0 declarative models for the 8 easy entities per data-
dictionary §4; create an Alembic baseline revision that builds the tables
in a fresh SQLite file; move `api_key` (guild + registration) to an
app-level encrypted column via `cryptography.Fernet` with key
`SCRAPCODE_DB_KEY` from `.env` (decrypt-on-read so cogs are unchanged).

### Domain Examples

1. **Cluster with role tiers + two guilds.** Server
   `1458181638453203099` has `role_tiers` admin=[111], officer=[222] and
   guilds `neuro` (role_id=999, member_role_ids=[555,556]) and `mech`
   (role_id=888). `alembic upgrade head` against a fresh SQLite file
   creates `clusters`, `role_tiers`, `guilds`, `guild_member_roles` rows
   that match; `update_channel_id` column does NOT exist.
2. **api_key encrypted at rest.** Guild `neuro` is saved with
   `api_key="tacticus-neuro-key"`. A direct `sqlite3` shell query of the
   `guilds.api_key` column returns ciphertext (NOT the plaintext). Loading
   via the repo returns `"tacticus-neuro-key"` (decrypt-on-read).
3. **player_registrations 1:1 api_key constraint.** Two Discord users
   (`123456789`, `987654321`) register with the same Tacticus key
   `"shared-key"`. The second insert raises a unique-constraint violation
   on `player_registrations.api_key`, matching the current JSON-side
   rejection in `register`.

### UAT Scenarios (BDD)

#### Scenario: Alembic baseline builds the easy-entity schema in a fresh SQLite file

Given a fresh temp SQLite file and `alembic upgrade head`
When Krewsayder inspects the resulting schema
Then the tables `clusters`, `role_tiers`, `guilds`, `guild_member_roles`,
  `player_registrations`, `capped_state` (or `is_capped` column on
  `player_registrations`), `players`, `live_leaderboards`,
  `live_lb_messages` all exist, and there is NO `update_channel_id`
  column anywhere

#### Scenario: api_key is encrypted at rest and decrypted on read

Given a `.env` with `SCRAPCODE_DB_KEY=<fernet-key>` and a `guilds` row for
  `neuro` saved via the repo with `api_key="tacticus-neuro-key"`
When Krewsayder queries `SELECT api_key FROM guilds WHERE guild_id='neuro'`
  directly via sqlite3
Then the returned value is Fernet ciphertext, not `"tacticus-neuro-key"`
When Krewsayder loads the guild via the repo
Then the returned `api_key` is `"tacticus-neuro-key"` (plaintext, decrypted)

#### Scenario: player_registrations api_key uniqueness is enforced

Given `player_registrations` already has a row for Discord user
  `123456789` with `api_key="shared-key"`
When a second row for Discord user `987654321` with the same
  `api_key="shared-key"` is inserted
Then the insert fails with a unique-constraint violation on `api_key`

#### Scenario: role_tiers check constraint rejects an invalid tier

Given the `role_tiers` table created by the baseline migration
When Krewsayder inserts a row with `tier='superuser'`
Then the insert fails with a check-constraint violation (only
  `admin`, `officer` are valid)

### Acceptance Criteria

- [ ] `requirements.txt` contains `sqlalchemy>=2.0`, `alembic`,
      `aiosqlite`, `cryptography`.
- [ ] `pip install -r requirements.txt` succeeds in the project `.venv`.
- [ ] `alembic upgrade head` builds the 8 easy-entity tables in a fresh
      SQLite file.
- [ ] No `update_channel_id` column exists in any table.
- [ ] `api_key` columns (guild + registration) are Fernet-encrypted at
      rest; a direct sqlite3 query returns ciphertext; the repo returns
      plaintext on read.
- [ ] `player_registrations.api_key` has a unique constraint.
- [ ] `role_tiers.tier` has a check constraint `tier IN ('admin',
      'officer')`.

### Outcome KPIs

- **Who:** Krewsayder
- **Does what:** runs `alembic upgrade head` to stand up a fresh schema
- **By how much:** 8 easy-entity tables created, 0 plaintext `api_key`
  columns at rest
- **Measured by:** `alembic upgrade head` exit code 0 + grep for
  `update_channel_id` in models returns 0 matches
- **Baseline:** no SQLAlchemy models, no Alembic, plaintext `api_key` in
  JSON

### Technical Notes (Optional)

- `capped_state` RECOMMENDATION: implement as `is_capped bool` column on
  `player_registrations` (matches the "edge-detect scratch" semantics in
  data-dictionary §2.4 and avoids a join). A separate `capped_state` table
  is acceptable if DESIGN prefers symmetry with the JSON layout.
- The Fernet key MUST NOT be logged. Decrypt-on-read happens in the repo
  layer, not in cogs.

---

## US-004: SqlAlchemyClusterRepository for easy entities behind the ABC @infrastructure

### Problem

Krewsayder has the schema (US-003) but no implementation. The swap is only
safe if the SQLite repo satisfies the same `ClusterRepository` ABC the
JSON repo does, returning the same dict/dataclass shapes the cogs consume —
otherwise the cutover (Slice 04) would require touching every cog. The
risk is that the ABC is implicitly coupled to JSON's shape (e.g.
`__meta__.version`, dict returns) and cannot be satisfied by a relational
impl without an ABC refactor.

### Who

- Krewsayder (bot operator/dev) | during Slice 02 | needs the SQLite impl
  to satisfy the contract tests (US-001) unchanged to prove the ABC is
  the real seam.

### Solution

Implement `SqlAlchemyClusterRepository(ClusterRepository)` covering the
same 11 ABC methods, returning the same dict shapes the JSON impl returns
(`load` returns a `Cluster` dataclass; `load_player_list` returns the v2
dict with `__meta__.version` so the cogs' `.get("players", {})` calls work
unchanged). Parametrize the US-001 contract tests against both
implementations and require both green.

### Domain Examples

1. **Round-trip parity — cluster.** Save a `Cluster` for server
   `1458181638453203099` with guild `neuro`; load it back. The returned
   `Cluster` equals the one returned by `JsonClusterRepository` for the
   same input (same guild ids, same `role_tiers`, same `member_role_ids`).
2. **Round-trip parity — player_registrations with api_key.** Save
   `{"123456789": {"api_key":"tacticus-abc","guild_id":"neuro"}}`; load
   back. Returns the same dict with `api_key` decrypted to plaintext.
3. **Silent-empty-on-corruption is RETIRED.** Pointing the SQLite impl at
   a missing/empty DB returns an empty `Cluster` (no error) the first
   time, BUT a corrupted DB (e.g. a non-SQLite file at the DB path) raises
   on construction, NOT silently returns empty. The US-001 pinned-trap
   test is updated to reflect the retired behavior.

### UAT Scenarios (BDD)

#### Scenario: US-001 contract tests pass against SqlAlchemyClusterRepository

Given the US-001 contract test module
When Krewsayder runs `pytest bot/tests/test_repository_contract.py` with
  the impl parametrized to `SqlAlchemyClusterRepository`
Then all round-trip tests pass with no test code changes (only the
  fixture that constructs the repo differs)

#### Scenario: load_player_list returns the v2 dict shape cogs expect

Given a `players` table with one row for `tacticus-uid-001` (display_name
  "Maria Santos", last_validated="2026-07-18T10:00:00Z", is_former=false)
  in guild `neuro` on server `1458181638453203099`
When Krewsayder calls `load_player_list(server, "neuro")` on
  `SqlAlchemyClusterRepository`
Then the returned dict has shape `{"__meta__":{"version":2},
  "players":{"tacticus-uid-001":{"display_name":"Maria Santos",
  "last_validated":"2026-07-18T10:00:00Z","is_former":false}}}` so cog
  code calling `.get("players", {})` works unchanged

#### Scenario: api_key is decrypted on read for cog consumption

Given a guild `neuro` row with `api_key` stored as Fernet ciphertext
When Krewsayder calls `load(server)` and inspects the returned
  `Cluster.guilds["neuro"].api_key`
Then the value is the plaintext `"tacticus-neuro-key"`, not ciphertext

#### Scenario: Corrupted DB raises instead of silently returning empty

Given a path that points at a non-SQLite file (e.g. a text file with
  bytes "not a database")
When Krewsayder constructs `SqlAlchemyClusterRepository(path)` and calls
  `load(server)`
Then the call raises (sqlite3 / SQLAlchemy error), it does NOT silently
  return `Cluster(discord_server_id=server)`

### Acceptance Criteria

- [ ] `SqlAlchemyClusterRepository` implements all 11 `ClusterRepository`
      ABC methods.
- [ ] `load_player_list` returns the v2 dict shape (with `__meta__.version`)
      so cogs are unchanged.
- [ ] `api_key` is decrypted on read (cogs see plaintext).
- [ ] US-001 contract tests pass against the SQLite impl with no test code
      changes (parametrization only).
- [ ] A corrupted/missing DB raises, not silently returns empty (the
      trap is retired).
- [ ] No production code path imports `SqlAlchemyClusterRepository` yet
      (the singleton in `bot/guilds.py:7` still points at JSON — that
      flip is US-010).

### Outcome KPIs

- **Who:** Krewsayder
- **Does what:** runs the contract suite against the SQLite impl as the
  ABC-swap gate
- **By how much:** 100% of US-001 contract tests green against
  `SqlAlchemyClusterRepository` (parametrized, no test edits)
- **Measured by:** `pytest bot/tests/test_repository_contract.py` exit
  code 0 against both impls
- **Baseline:** 0 SQLite impl today

### Technical Notes (Optional)

- The `__meta__.version` field in `load_player_list`'s return is a
  *compatibility shim* for cog code that reads it. Mark it with a code
  comment: "kept for cog compatibility; the SQL schema versions via
  Alembic instead."
- `get_guild_data_path` is JSON-specific (returns a filesystem dir for
  `tracker.py`). Slice 04 retires it; for now the SQLite impl can return
  a sentinel / raise `NotImplementedError` if no caller reaches it
  through the new path. Document this in `wave-decisions.md`.

---

## US-005: JSON→SQLite data migration with row-count parity @infrastructure

### Problem

Krewsayder has the SQLite schema and impl (US-003, US-004) but no data.
The migration from the live JSON tree to SQLite is the single highest-risk
step of the whole feature: a silent row loss or duplication would corrupt
leaderboards, registrations, or the replay index with no obvious signal.
The `PlayerListMigrator` v1→v2 inversion, today a per-read runtime
concern, must run exactly once here, and the row counts in SQLite must
match the JSON-derived counts exactly.

### Who

- Krewsayder (bot operator/dev) | during Slice 03 | needs a one-shot
  Alembic data migration + a parity report before any cutover.

### Solution

An Alembic data migration revision that reads the live
`clusters/{id}/...` tree (operator copies it off the VM into a working
dir), runs `PlayerListMigrator._migrate_v1_to_v2` once for any v1
`player_list.json` files, populates the 8 easy-entity tables, and emits
a per-table row-count parity report (markdown or stdout). The parity
report is the artifact Slice 04's cutover depends on.

### Domain Examples

1. **Easy entities — single server.** The production tree has one
   server dir `clusters/1458181638453203099/` with `guilds.json` (2
   guilds: neuro, mech), `player_registrations.json` (12 entries),
   `capped_state.json` (4 trues), `live_leaderboards.json` (1 cluster
   + 2 per-guild configs with 7 tier-messages each), and per-guild
   `player_list.json` files (neuro: 30 players v2, mech: 18 players v1).
   Post-migration, SQLite has 2 `guilds` rows, 12 `player_registrations`
   rows, 4 `is_capped=true`, 3 `live_leaderboards` rows + 21
   `live_lb_messages` rows, 48 `players` rows (18 v1 inverted to v2 with
   the epoch sentinel).
2. **v1 player_list one-time inversion.** `mech`'s v1
   `player_list.json` is `{"Maria Santos":"tacticus-uid-001", ...}` (18
   entries). Post-migration, the `players` table has 18 rows for `mech`
   with `last_validated="1970-01-01T00:00:00Z"` and `is_former=false`.
3. **Parity report flag.** If a `guilds.json` has 2 guilds but the
   `guilds` table has 1 row post-migration (e.g. a slug-collision
   dropped one), the parity report shows
   `guilds: JSON=2 SQL=1 MISMATCH` and the migration exits non-zero.

### UAT Scenarios (BDD)

#### Scenario: Easy-entity row counts match JSON-derived counts exactly

Given a copy of the production `clusters/` tree in a working dir
When Krewsayder runs the data-migration Alembic revision
Then for each easy-entity table the SQL row count equals the JSON-derived
  count (clusters, role_tiers, guilds, guild_member_roles,
  player_registrations, capped_state, players, live_leaderboards,
  live_lb_messages) and a parity report is printed

#### Scenario: v1 player_list files are inverted to v2 exactly once

Given the production tree has at least one v1 `player_list.json` (e.g.
  `mech`'s, keyed by display_name)
When the data migration runs
Then the `players` table rows for those guilds have
  `last_validated="1970-01-01T00:00:00Z"` and `is_former=false`, keyed by
  `tacticus_user_id` (not display_name), and re-running the migration
  is a noop (idempotent)

#### Scenario: api_key values are encrypted on insert, not stored plaintext

Given the production `guilds.json` has `api_key="tacticus-neuro-key"`
  for guild `neuro`
When the data migration runs
Then the `guilds.api_key` column for `neuro` contains Fernet ciphertext,
  not `"tacticus-neuro-key"`

#### Scenario: Parity mismatch fails the migration loudly

Given a synthetic `guilds.json` with 2 guilds whose `guild_id` slugs
  collide after normalization (both normalize to `"neuro"`)
When the data migration runs
Then the migration exits non-zero, prints a parity report showing
  `guilds: JSON=2 SQL=1 MISMATCH`, and the SQLite file is left in a
  rolled-back / uncommitted state

### Acceptance Criteria

- [ ] An Alembic data-migration revision exists and is idempotent.
- [ ] `PlayerListMigrator._migrate_v1_to_v2` runs once per v1 file; v2
      files pass through.
- [ ] A parity report (markdown or stdout) lists per-table JSON-count vs
      SQL-count and a MISMATCH/PASS flag.
- [ ] Any mismatch exits non-zero and leaves the DB uncommitted.
- [ ] `api_key` values are Fernet-encrypted on insert.
- [ ] Migration runs against a copy of the production `clusters/` tree
      (operator's responsibility to copy it off the VM).

### Outcome KPIs

- **Who:** Krewsayder
- **Does what:** runs the migration + parity report as the cutover gate
- **By how much:** 100% row-count parity across all easy-entity tables
- **Measured by:** parity report exit code 0 + all tables PASS
- **Baseline:** no migration; JSON is the only source

### Technical Notes (Optional)

- The migration MUST be reversible: `alembic downgrade` clears the
  tables it populated without touching the JSON tree.
- Real production data enters here; the operator must `scp` the
  `clusters/` tree off the VM before running. The migration does NOT
  read from `/opt/discord-bot/clusters/` directly.

---

## US-006: battle_hits + bomb_hits persistence with upsert-keep-max(damage) @infrastructure

### Problem

Krewsayder has the easy entities migrated (US-005) but the season files —
`highest_hits_season_{n}.json`, `highest_hits_simple_season_{n}.json`,
`highest_bombs_season_{n}.json` — are not yet in SQLite. The
`try_insert(check_roster=True)` dedup is in-memory in `bot/tracker.py`
and has four branches (US-002 pins them). Moving this to SQL is the
hardest data-modeling step: the per-player-per-roster dedup must become a
unique constraint + upsert-keep-max(damage), and the `(-damage,
completed_on asc)` tiebreak must be preserved by the read query's
`ORDER BY`.

### Who

- Krewsayder (bot operator/dev) | during Slice 03 | needs the hard
  entities in SQL with the dedup enforced by the schema, not by
  in-memory Python.

### Solution

Add `battle_hits` and `bomb_hits` tables (data-dictionary §2.7, §2.9) +
a new Alembic revision. The `battle_hits` unique constraint is
`(server, guild, season, boss, encounter, tier, roster_key, user_id)`
with `damage` stored as the best per key; inserts use
`INSERT ... ON CONFLICT DO UPDATE SET damage = MAX(excluded.damage,
battle_hits.damage)`. The read query orders by `(-damage, completed_on
asc)` and limits to `TOP_N=5`. `battle_hits_simple` is dropped from the
schema if US-005's read-path check confirms it is unused (data-dictionary
§2.8 flag); otherwise mirror `battle_hits` minus roster columns.

### Domain Examples

1. **Upsert keep-max — same roster, higher damage.** Maria Santos
   (uid-001) has a `battle_hits` row for season 94, boss `Avatar`,
   encounter 0, tier `Legendary_0`, roster `[Aethana, Eldryon]` + MoW
   `Khaine`, damage=12000. A new Tacticus entry arrives at damage=15000
   with the same roster. The upsert updates the existing row to
   damage=15000. The read query returns the 15000 row first.
2. **Upsert keep-max — same roster, lower damage.** Same row at
   damage=12000. A new entry arrives at damage=9000 with the same roster.
   The upsert leaves the row at damage=12000 (keep-max). The read query
   returns the 12000 row.
3. **Different roster — separate row.** Maria's row at damage=12000
   with roster `[Aethana, Eldryon]`. A new entry at damage=9000 with
   roster `[Aethana, Tan Gida]`. No conflict (roster_key differs); a new
   row is inserted. The read query returns both rows for Maria.

### UAT Scenarios (BDD)

#### Scenario: Same player + same roster + higher damage replaces the existing row

Given `battle_hits` has a row for season 94, `Avatar`, encounter 0,
  tier `Legendary_0`, player `tacticus-uid-001`, roster_key
  `[Aethana, Eldryon]+Khaine`, damage=12000
When a new entry arrives with the same natural key and damage=15000
Then the row's `damage` is updated to 15000 and there is still one row
  for that key

#### Scenario: Same player + same roster + lower damage does not replace

Given the same row at damage=12000
When a new entry arrives with the same natural key and damage=9000
Then the row's `damage` stays 12000 (keep-max) and there is still one
  row for that key

#### Scenario: Different roster inserts a separate row

Given a row for player uid-001 with roster_key
  `[Aethana, Eldryon]+Khaine` at damage=12000
When a new entry arrives for the same player with a different roster_key
  `[Aethana, Tan Gida]+Khaine` at damage=9000
Then a new row is inserted (no conflict) and the read query returns
  both rows for uid-001

#### Scenario: Read query orders by damage desc then completed_on asc, limited to TOP_N

Given 7 rows for the same (season, boss, encounter, tier) at damages
  100, 90, 80, 70, 60, 50, 40 with completed_on timestamps such that
  the damage=70 row has the earliest timestamp
When the read query runs
Then exactly 5 rows are returned, ordered 100, 90, 80, 70, 60, and the
  damage=70 row's earlier timestamp did not promote it above the
  damage=80 row (tiebreak only applies to equal damage)

#### Scenario: bomb_hits has no roster dedup (plain top-N)

Given 6 bomb entries for the same (season, boss, encounter, tier) at
  damages 100, 90, 80, 70, 60, 50 from 6 different players
When the read query runs
Then exactly 5 rows are returned ordered by damage desc (no roster
  dedup)

### Acceptance Criteria

- [ ] `battle_hits` table has the unique constraint on `(server, guild,
      season, boss, encounter, tier, roster_key, user_id)`.
- [ ] Insert path is `ON CONFLICT DO UPDATE SET damage =
      MAX(excluded.damage, battle_hits.damage)`.
- [ ] Read query `ORDER BY damage DESC, completed_on ASC LIMIT 5`
      (per `(season, boss, encounter, tier)` partition).
- [ ] `bomb_hits` table mirrors minus roster columns; plain top-N.
- [ ] US-002 dedup tests pass against the SQL upsert path (parametrized).
- [ ] `battle_hits_simple` is either dropped (with a `wave-decisions.md`
      note) or mirrored; the choice is documented.

### Outcome KPIs

- **Who:** Krewsayder
- **Does what:** verifies the SQL upsert enforces the same dedup as the
  in-memory `try_insert`
- **By how much:** 100% of US-002 dedup tests pass against the SQL path
- **Measured by:** `pytest bot/tests/test_tracker_dedup.py` parametrized
  against the SQL upsert path, exit 0
- **Baseline:** in-memory `try_insert` only

### Technical Notes (Optional)

- `roster_key` is a hashable serialization of `(sorted(hero unitIds),
  mow_unit_id)`. Store it as a text column; do NOT store the heroes as
  JSON in `battle_hits` (the read path does not query by hero).
- `hero_details` and `machine_of_war` can be JSON columns for display,
  but the dedup uses `roster_key` only.
- If `battle_hits_simple` is confirmed unused by render paths, drop it
  and remove the `save_json(BATTLE_SIMPLE_FILE, ...)` line from
  `tracker.py` in US-008.

---

## US-007: replay_index migration + tenancy decision @infrastructure

### Problem

Krewsayder is migrating `replay_index.json` (global, tenancy leak per
brief §3.2) to SQLite. The JSON has no `discord_server_id` field, so the
migration must decide which server owns the existing entries. The
current URL-uniqueness is global (a replay URL submitted in any server
blocks all others); the SQL schema must scope it per
`(discord_server_id, boss, map_name)` to fix the cross-tenant collision
without changing the visible "duplicate URL" rejection for the single
production server.

### Who

- Krewsayder (bot operator/dev) | during Slice 03 | needs the replay
  index in SQLite with a documented tenancy decision before the cutover.

### Solution

Add `replay_threads` and `replay_entries` tables (data-dictionary §2.10)
+ Alembic revision. The data migration assigns ALL existing
`replay_index.json` entries to the one production server
(`1458181638453203099`) — the data has no `server_id`, so this is the
only defensible assignment; document as a deferred decision in
`wave-decisions.md` (true multi-tenant partitioning waits for a second
server). URL uniqueness is enforced per `(discord_server_id, boss,
map_name)`, NOT global. The hardcoded `FORUM_CHANNELS` / `MAP_THREADS`
stay in `replay_cog.py` for now (a `replay_threads` table population is
deferred — flagged in `wave-decisions.md`).

### Domain Examples

1. **All existing entries → production server.** The production
   `replay_index.json` has 47 entries across bosses `Avatar`, `Cawl`,
   `Tervigon`. Post-migration, all 47 `replay_entries` rows have
   `discord_server_id=1458181638453203099`.
2. **Per-tenant URL uniqueness.** Two entries with the same URL
   `https://replay.example/abc` are inserted under the same
   `(server, boss, map_name)`. The second insert fails a unique-
   constraint violation, matching the current "duplicate URL" rejection
   in `upload_replay`.
3. **Cross-tenant same URL is allowed.** Two entries with the same URL
   are inserted under different `discord_server_id` (a hypothetical
   second server). Both succeed — the cross-tenant collision is fixed.

### UAT Scenarios (BDD)

#### Scenario: All existing replay entries are assigned to the production server

Given the production `replay_index.json` with N entries across multiple
  bosses/maps
When the data migration runs
Then every `replay_entries` row has
  `discord_server_id=1458181638453203099` and the row count equals N

#### Scenario: URL uniqueness is scoped per (server, boss, map_name)

Given a `replay_entries` row for server `1458181638453203099`, boss
  `Avatar`, map `GB_Khaine_01`, URL `https://replay.example/abc`
When a second row with the same (server, boss, map_name, url) is
  inserted
Then the insert fails with a unique-constraint violation

#### Scenario: Same URL under a different server is allowed

Given the row above
When a row with the same URL but
  `discord_server_id=999999999999999999` is inserted
Then the insert succeeds (per-tenant scoping, not global)

#### Scenario: replay_threads rows are created per (server, boss, map_name)

Given the production `replay_index.json` with M distinct (boss,
  map_name) keys, each with an `index_message_id`
When the data migration runs
Then the `replay_threads` table has M rows, each with the production
  server id and the original `index_message_id`

### Acceptance Criteria

- [ ] `replay_entries` table has `discord_server_id` column + unique
      constraint on `(discord_server_id, boss, map_name, url)`.
- [ ] `replay_threads` table has `(discord_server_id, boss, map_name)`
      and `index_message_id`.
- [ ] Data migration assigns all existing entries to the production
      server; row count matches.
- [ ] `wave-decisions.md` records the single-server assignment decision
      and defers true multi-tenant partitioning.
- [ ] `wave-decisions.md` records the deferral of `FORUM_CHANNELS` /
      `MAP_THREADS` → `replay_threads` table population.

### Outcome KPIs

- **Who:** Krewsayder
- **Does what:** runs the replay migration with per-tenant URL uniqueness
- **By how much:** 100% of existing entries assigned to the production
  server; 0 cross-tenant URL collisions possible
- **Measured by:** row count of `replay_entries` == JSON entry count;
  unique-constraint test passes
- **Baseline:** global `replay_index.json` with global URL uniqueness

### Technical Notes (Optional)

- The damage column in `replay_entries` is free-text (e.g. "1.33M") per
  data-dictionary §2.10; keep as TEXT, do not parse to int (diverges
  from the numeric `damage` in `battle_hits`).
- `submitted_by` is a Discord ID with no FK (the submitter is not
  necessarily a registered player) — do NOT add a FK.

---

## US-008: Route tracker.py season-file path through the repository @infrastructure

### Problem

`bot/tracker.py::process_api_response` bypasses the repository ABC and
reads/writes three season files directly via `load_json` / `save_json`
(with the same non-atomic-write + silent-empty-read trap as the repo).
Krewsayder needs `tracker.py` to read/write `battle_hits` /
`bomb_hits` via the new layer so the trap is gone and the singleton flip
(US-010) is meaningful — otherwise the season files would remain on JSON
even after the cutover.

### Who

- Krewsayder (bot operator/dev) | during Slice 04 | needs `tracker.py`
  off direct file I/O so the cutover is complete.

### Solution

Rewrite `process_api_response` to read existing `battle_hits` /
`bomb_hits` rows for the (server, guild, season) partition, run the
Tacticus entries through the SQL upsert path from US-006 (no in-memory
`try_insert`), and commit. The `try_insert` function and `load_json` /
`save_json` helpers in `tracker.py` are removed (their behavior is pinned
by US-002 and enforced by the SQL upsert). `get_tier_key` and
`get_roster_key` remain (they are pure parsers used to build the natural
key for the upsert).

### Domain Examples

1. **Hourly auto-update writes to SQLite.** The hourly `auto_update`
   task for server `1458181638453203099`, guild `neuro`, season 94,
   receives 25 Tacticus entries. `process_api_response` upserts them
   into `battle_hits` / `bomb_hits` via the repository; no
   `highest_hits_season_94.json` file is written.
2. **Roster dedup enforced by SQL.** Two of the 25 entries are Maria
   Santos with the same roster at damage=12000 and damage=15000. The
   SQL upsert leaves one row at damage=15000 (US-006).
3. **Crash mid-write does not corrupt.** A crash occurs halfway through
   the upsert loop. Because the writes are in a single transaction, the
   DB is unchanged on restart — no partial season file.

### UAT Scenarios (BDD)

#### Scenario: process_api_response writes to battle_hits/bomb_hits, not JSON files

Given a running bot with the SQLite singleton active
When `process_api_response(api_data, season=94, server, guild="neuro")`
  is called with 25 Tacticus entries
Then `battle_hits` and `bomb_hits` rows are inserted/updated for that
  (server, guild, season) and no `highest_hits_season_*.json` or
  `highest_bombs_season_*.json` file is written to disk

#### Scenario: Roster dedup is enforced by the SQL upsert, not in-memory

Given the 25 entries include two for Maria Santos with the same roster
  at damage=12000 and damage=15000
When `process_api_response` runs
Then exactly one `battle_hits` row exists for that (player, roster) with
  damage=15000

#### Scenario: A crash mid-write leaves the DB in the pre-call state

Given `process_api_response` is mid-transaction (5 of 25 entries
  upserted) and the process crashes
When the bot restarts and reads `battle_hits` for that (server, guild,
  season)
Then the 5 partial upserts are NOT present (the transaction was not
  committed)

#### Scenario: load_json / save_json / try_insert are no longer in tracker.py

Given the cutover commit is applied
When Krewsayder greps `bot/tracker.py` for `path.write_text`,
  `path.read_text`, `load_json`, `save_json`, and `try_insert`
Then there are zero matches (the helpers are removed; `get_tier_key`
  and `get_roster_key` remain)

### Acceptance Criteria

- [ ] `process_api_response` reads/writes only via the repository / SQL
      upsert; no direct file I/O.
- [ ] `load_json`, `save_json`, `try_insert` are removed from
      `tracker.py`.
- [ ] `get_tier_key`, `get_roster_key` remain (pure parsers).
- [ ] The hourly write is wrapped in a single transaction (crash
      safety).
- [ ] US-002 dedup tests pass against the new path.
- [ ] grep for `path.write_text(json.dumps` in `bot/tracker.py` returns
      0 matches.

### Outcome KPIs

- **Who:** Krewsayder
- **Does what:** confirms the tracker no longer writes JSON files
- **By how much:** 0 direct file-write calls in `bot/tracker.py`
- **Measured by:** grep count for `path.write_text` in `bot/tracker.py`
  == 0
- **Baseline:** 3 `save_json` calls per `process_api_response` invocation

### Technical Notes (Optional)

- `process_api_response`'s signature today is `(api_data, season,
  data_dir=Path("."))`. The new signature is `(api_data, season,
  discord_server_id, guild_id)` — the `data_dir` parameter is gone
  (the SQL partition key replaces it). Callers in `tasks_cog` /
  `update_cog` are updated in US-010.

---

## US-009: Route replay_cog.py through the repository @infrastructure

### Problem

`bot/cogs/replay_cog.py` bypasses the repository ABC and reads/writes a
single global `replay_index.json` at the project root (the tenancy leak
in brief §3.2). Krewsayder needs the cog to read/write
`replay_entries` / `replay_threads` via the new layer so the leak is
gone and the per-tenant URL uniqueness from US-007 is enforced.

### Who

- Krewsayder (bot operator/dev) | during Slice 04 | needs `replay_cog.py`
  off the global JSON file so the cutover is complete.

### Solution

Replace `load_replay_index` / `save_replay_index` with repository calls
that read/write `replay_entries` / `replay_threads` rows scoped to
`interaction.guild_id`. The `REPLAY_INDEX_FILE` constant is removed.
`FORUM_CHANNELS` / `MAP_THREADS` remain hardcoded for now (deferred to
a later refactor — `wave-decisions.md`).

### Domain Examples

1. **upload_replay writes to SQLite.** A Discord user in server
   `1458181638453203099` runs `/upload_replay` for boss `Avatar`, map
   `GB_Khaine_01`, URL `https://replay.example/abc`. A `replay_entries`
   row is inserted with `discord_server_id=1458181638453203099`. No
   `replay_index.json` write occurs.
2. **Duplicate URL rejected per-tenant.** The same user runs
   `/upload_replay` again with the same URL. The insert hits the unique
   constraint and the user sees the existing "❌ This replay URL has
   already been submitted under **Avatar / GB_Khaine_01**." reply.
3. **get_replay reads from SQLite.** A user runs `/get_replay` for boss
   `Avatar`, map `GB_Khaine_01`. The cog reads `replay_entries` rows
   for `(1458181638453203099, Avatar, GB_Khaine_01)` and renders the
   same index message as before.

### UAT Scenarios (BDD)

#### Scenario: upload_replay writes a replay_entries row, not replay_index.json

Given a Discord user in server `1458181638453203099` runs
  `/upload_replay boss=Avatar map_name=GB_Khaine_01 url=https://replay.example/abc`
When the command completes
Then a `replay_entries` row exists with
  `discord_server_id=1458181638453203099`, `boss=Avatar`,
  `map_name=GB_Khaine_01`, `url=https://replay.example/abc` and no
  write to `replay_index.json` occurred

#### Scenario: Duplicate URL in the same (server, boss, map) is rejected

Given the row above exists
When the same user runs `/upload_replay` with the same URL
Then the user receives the existing "❌ This replay URL has already been
  submitted" reply and no new row is inserted

#### Scenario: get_replay reads from replay_entries and renders the same index

Given two `replay_entries` rows for `(1458181638453203099, Avatar,
  GB_Khaine_01)` with teams `Neuro` and `Laviscus`
When a user runs `/get_replay boss=Avatar map_name=GB_Khaine_01`
Then the rendered message matches the pre-cutover rendering for the
  same input data (team-grouped, tier-grouped, same format)

#### Scenario: delete_replay removes the row from replay_entries

Given the row from the first scenario exists
When a user runs `/delete_replay boss=Avatar map_name=GB_Khaine_01 url=https://replay.example/abc`
Then the `replay_entries` row is deleted and the index message is
  re-rendered with the remaining entries

#### Scenario: REPLAY_INDEX_FILE / load_replay_index / save_replay_index are removed

Given the cutover commit is applied
When Krewsayder greps `bot/cogs/replay_cog.py` for `REPLAY_INDEX_FILE`,
  `load_replay_index`, `save_replay_index`, and `replay_index.json`
Then there are zero matches

### Acceptance Criteria

- [ ] `upload_replay`, `get_replay`, `delete_replay` read/write via the
      repository, not `replay_index.json`.
- [ ] `REPLAY_INDEX_FILE`, `load_replay_index`, `save_replay_index` are
      removed from `replay_cog.py`.
- [ ] All three commands scope reads/writes by `interaction.guild_id`.
- [ ] The rendered index message is byte-identical to the pre-cutover
      rendering for the same data (regression snapshot).
- [ ] `FORUM_CHANNELS` / `MAP_THREADS` remain (deferred — `wave-
      decisions.md`).

### Outcome KPIs

- **Who:** Krewsayder
- **Does what:** confirms the replay cog no longer touches the global
  JSON file
- **By how much:** 0 references to `replay_index.json` in `bot/cogs/`
- **Measured by:** grep count for `replay_index.json` in `bot/cogs/` ==
  0
- **Baseline:** 4 references to `replay_index.json` in `replay_cog.py`
  today

### Technical Notes (Optional)

- The `team` / `tier` / `position` / `damage` / `comment` /
  `submitted_by` fields are stored as-is (damage is free-text per §2.10).
- `_edit_index_message` continues to edit the Discord message in the
  forum thread; only the *source* of the entries changes (SQL vs JSON).

---

## US-010: Flip the singleton + transactional hourly write + JSON read-only fallback @infrastructure

### Problem

Krewsayder has the SQLite impl (US-004) and the bypasses routed (US-008,
US-009), but the live singleton in `bot/guilds.py:7` still points at
`JsonClusterRepository`. The hourly `auto_update` task still calls
scattered `save_*` helpers outside `file_lock`, each non-atomic. Without
flipping the singleton and wrapping the hourly write in a single
transaction, the data-loss trap from ADR-002 / brief §4.8 is still live
and the whole feature has delivered no value.

### Who

- Krewsayder (bot operator/dev) | during Slice 04 | needs the singleton
  flipped and the hourly write transactional so the trap is actually
  retired.

### Solution

Make the singleton in `bot/guilds.py:7` config-driven (env var
`SCRAPCODE_REPO_BACKEND=json|sqlite`, default `sqlite` post-cutover) so
the flip is one env change and rollback is a restart. Wrap the hourly
`auto_update` write path (the scattered `save_player_list` /
`save_guilds` / `save_capped_state` / `save_live_leaderboards` calls) in
a single SQLAlchemy transaction (via `aiosqlite` so the event loop is
not blocked). Keep the JSON tree read-only as a one-cycle fallback: if
the SQLite file is missing or fails to open on startup, log loudly and
fall back to `JsonClusterRepository` for one cycle (manual rollback
path).

### Domain Examples

1. **Singleton flip via env.** `.env` has
   `SCRAPCODE_REPO_BACKEND=sqlite`. On startup, `bot/guilds.py`
   constructs `SqlAlchemyClusterRepository`. All cog calls go to
   SQLite.
2. **Rollback via env.** A cutover regression is found. Krewsayder sets
   `SCRAPCODE_REPO_BACKEND=json`, restarts the bot (`sudo systemctl
   restart discord-bot`), and the bot runs against the JSON tree
   (read-only fallback kept the JSON intact during the cutover cycle).
3. **Hourly write is atomic.** The hourly `auto_update` for server
   `1458181638453203099` updates 12 `player_registrations` rows, 30
   `players` rows, and 3 `live_leaderboards` rows. A crash occurs
   mid-cycle. On restart, none of the partial writes are present — the
   transaction rolled back.

### UAT Scenarios (BDD)

#### Scenario: SCRAPCODE_REPO_BACKEND=sqlite selects SqlAlchemyClusterRepository

Given `.env` has `SCRAPCODE_REPO_BACKEND=sqlite`
When the bot starts
Then `bot/guilds.py:repo` is an instance of
  `SqlAlchemyClusterRepository` and `load_guilds(server)` reads from
  SQLite

#### Scenario: SCRAPCODE_REPO_BACKEND=json falls back to JsonClusterRepository

Given `.env` has `SCRAPCODE_REPO_BACKEND=json`
When the bot starts
Then `bot/guilds.py:repo` is an instance of `JsonClusterRepository`
  (rollback path) and the JSON tree is used read/write as before

#### Scenario: Hourly auto_update write is wrapped in a single transaction

Given the hourly `auto_update` runs for server
  `1458181638453203099` and is mid-transaction (some saves done, some
  not) when the process crashes
When the bot restarts and reads the affected tables
Then none of the partial saves from that cycle are present (the
  transaction was not committed)

#### Scenario: Missing SQLite file on startup falls back to JSON for one cycle

Given `.env` has `SCRAPCODE_REPO_BACKEND=sqlite` but the SQLite file
  does not exist at the configured path
When the bot starts
Then the bot logs a loud "SQLite DB missing — falling back to JSON for
  one cycle" warning and `bot/guilds.py:repo` is `JsonClusterRepository`
  for that cycle

#### Scenario: JSON tree is not modified after the flip

Given the cutover cycle has run successfully against SQLite
When Krewsayder inspects the `clusters/` tree modification times
Then no JSON file was modified during or after the cutover cycle (the
  JSON tree is read-only fallback, not a write target)

### Acceptance Criteria

- [ ] `bot/guilds.py:7` reads `SCRAPCODE_REPO_BACKEND` from env and
      constructs the matching impl (default `sqlite` post-cutover).
- [ ] The hourly `auto_update` write path is a single transaction.
- [ ] A crash mid-cycle leaves the DB in the pre-cycle state.
- [ ] Missing SQLite file on startup logs loudly and falls back to JSON
      for one cycle.
- [ ] The JSON tree is not written to during or after a successful
      cutover cycle (read-only fallback).
- [ ] grep for `repo = JsonClusterRepository()` in `bot/guilds.py` shows
      the construction is conditional on the env var, not unconditional.

### Outcome KPIs

- **Who:** Krewsayder
- **Does what:** confirms the non-atomic-write + silent-empty-read trap
  is retired
- **By how much:** 100% of hourly writes transactional; 0 partial writes
  observable after a crash
- **Measured by:** a crash-injection test (kill -9 mid-cycle) followed by
  a row-count check showing no partial commits
- **Baseline:** scattered non-atomic `save_*` calls outside `file_lock`

### Technical Notes (Optional)

- Use `aiosqlite` so the transaction commit does not block the event
  loop; the existing `file_lock` can be retired once the transaction
  boundary is in place (or kept as a belt-and-suspenders guard —
  IMPLEMENTATION note for DESIGN).
- The "one-cycle fallback" is intentionally manual: after a successful
  cycle, `SCRAPCODE_REPO_BACKEND=sqlite` is the only supported mode;
  `=json` is the rollback path, not a long-term mode.

---

## US-011: Existing command behavior preserved through the SQLite cutover

### Problem

Krewsayder is the bot operator; Discord end-users (guild members,
officers, admins) run the existing slash commands. After the SQLite
cutover (US-010), every user-visible command and both hourly background
tasks must produce the same observable output for the same input as
before the swap — otherwise the migration has regressed a live command
and must roll back. This is the *only* user-visible story in the feature;
it is the acceptance gate for the whole migration.

### Who

- Discord end-users (guild members, officers, admins) | post-cutover |
  expect `/view_leaderboard`, `/view_bombs`, `/get_replay`,
  `/upload_replay`, `/delete_replay`, `/register`, `/unregister`,
  `/move`, admin config commands to behave exactly as before.
- Krewsayder (bot operator) | post-cutover | needs the hourly
  auto-update and cap-detect tasks to keep working without manual
  intervention.

### Solution

An end-to-end acceptance pass that exercises every existing command and
both hourly tasks against the post-cutover SQLite backend, comparing
observable output (Discord message content, ephemeral replies, channel
pings) to pre-cutover baseline snapshots. Any divergence is a regression
that blocks the cutover (US-010 rollback path is env-driven).

### Elevator Pitch

**Before:** A Discord officer in server `1458181638453203099` runs
`/view_leaderboard` for guild `neuro`, season 94, boss `Avatar`. The bot
reads `clusters/1458181638453203099/neuro/data/highest_hits_season_94.json`
via `bot/tracker.py`'s direct file I/O, renders the embed, and replies.
A crash mid-hourly-update could silently truncate that JSON file, and
the next `/view_leaderboard` would show an empty leaderboard with no
error — the data-loss trap from ADR-002 is live.

**After:** The same officer runs the same command and sees the same
embed (same rows, same order, same formatting). The bot now reads
`battle_hits` rows for `(1458181638453203099, neuro, 94, Avatar)` via
the repository, but the rendered output is byte-identical. A crash
mid-hourly-update leaves the DB in the pre-cycle state (transactional),
so the next `/view_leaderboard` shows the same leaderboard it would
have before the crash. The data-loss trap is retired.

**Decision enabled:** The SQLite migration can ship to production
without regressing any existing command. If any scenario fails, the
cutover rolls back via `SCRAPCODE_REPO_BACKEND=json` and the regression
is fixed before re-attempting.

### Domain Examples

1. **/view_leaderboard parity.** Officer `krewsayder` runs
   `/view_leaderboard guild=neuro season=94 boss=Avatar` before and
   after the cutover. The embed content is byte-identical (same players,
   same damages, same order, same "(former)" suffixes for `is_former`
   players like `Jonas Klein (former)`).
2. **/register parity.** A new Discord user `234567890` runs
   `/register api_key=tacticus-new-key guild=neuro`. The bot validates
   the key against Tacticus, rejects if the key is already bound to a
   different user (1:1), inserts a `player_registrations` row, and
   replies "✅ Registered" — same as before. The `api_key` is encrypted
   at rest (US-003), invisible to the user.
3. **Hourly auto-update parity.** The hourly `auto_update` runs at the
   top of the hour for server `1458181638453203099`, fetches season 94
   raid data from Tacticus, upserts into `battle_hits` / `bomb_hits`,
   refreshes the live leaderboard messages (edits them in place), and
   posts the "Auto-update complete" summary to the `UPDATE_CHANNEL_ID`.
   The summary's row counts and the edited message content match the
   pre-cutover baseline.

### UAT Scenarios (BDD)

#### Scenario: /view_leaderboard output is byte-identical pre- and post-cutover

Given a pre-cutover baseline snapshot of `/view_leaderboard guild=neuro
  season=94 boss=Avatar` (embed content saved as a fixture)
When the same command is run after the cutover (SQLite backend)
Then the embed content matches the baseline snapshot byte-for-byte
  (same players, same damages, same order, same "(former)" suffixes)

#### Scenario: /view_bombs output is byte-identical pre- and post-cutover

Given a pre-cutover baseline snapshot of `/view_bombs guild=neuro
  season=94 boss=Avatar`
When the same command is run after the cutover
Then the embed content matches the baseline snapshot byte-for-byte

#### Scenario: /register, /unregister, /move produce the same replies and persistence

Given a pre-cutover baseline for `/register api_key=tacticus-new-key
  guild=neuro`, `/unregister`, and `/move guild=mech`
When the same commands are run after the cutover with the same inputs
Then the ephemeral replies match the baseline ("✅ Registered", etc.)
  AND the persisted state (`player_registrations` rows) matches the
  JSON-side state after the same sequence (same 1:1 api_key rejection,
  same guild_id move)

#### Scenario: /get_replay, /upload_replay, /delete_replay produce the same replies

Given a pre-cutover baseline for `/get_replay boss=Avatar
  map_name=GB_Khaine_01`, `/upload_replay` (success + duplicate-URL
  rejection), and `/delete_replay`
When the same commands are run after the cutover with the same inputs
Then the replies and the rendered index message match the baseline
  byte-for-byte

#### Scenario: Hourly auto-update and cap-detect tasks produce the same observable output

Given a pre-cutover baseline of the "Auto-update complete" summary and
  the cap-detect ping behavior (which players got pinged on which cycle)
When the hourly tasks run after the cutover with the same Tacticus API
  responses (mocked or recorded)
Then the summary's row counts and the cap-detect pings match the
  baseline (same players pinged, same channels)

#### Scenario: Admin config commands produce the same replies and persistence

Given a pre-cutover baseline for admin config commands
  (`/set_live_leaderboard`, `/set_ping_channel`, `/config_roles`,
  `/config_guilds`, etc.)
When the same commands are run after the cutover
Then the replies match the baseline and the persisted config
  (`live_leaderboards`, `guilds.notification_channel_id`,
  `role_tiers`, `guild_member_roles`) matches the JSON-side state
  after the same sequence

### Acceptance Criteria

- [ ] A pre-cutover baseline snapshot exists for every command group
      above (saved as fixtures).
- [ ] Post-cutover, every command's embed/reply matches its baseline
      byte-for-byte.
- [ ] Post-cutover, the hourly tasks' observable output (summary, pings)
      matches the baseline.
- [ ] Any divergence blocks the cutover and triggers rollback
      (`SCRAPCODE_REPO_BACKEND=json`).
- [ ] The acceptance pass is runnable as a single pytest session
      (`pytest bot/tests/test_cutover_acceptance.py` or equivalent).

### Outcome KPIs

- **Who:** Discord end-users + Krewsayder
- **Does what:** run existing commands and hourly tasks without
  observable regression
- **By how much:** 0 regressions caught by the acceptance pass
- **Measured by:** `pytest bot/tests/test_cutover_acceptance.py` exit 0
  with all baseline-snapshot comparisons passing
- **Baseline:** pre-cutover behavior (JSON backend) — captured as the
  baseline fixtures

### Technical Notes (Optional)

- The acceptance pass can be a mix of (a) mocked-API integration tests
  for the commands and (b) a manual smoke checklist for the hourly tasks
  on the staging VM. The goal is *objective* parity, not test
  architecture purity — a recorded baseline + diff is sufficient.
- This story is the gate for declaring the feature done. If it fails, the
  cutover is rolled back and the failing slice is reworked.