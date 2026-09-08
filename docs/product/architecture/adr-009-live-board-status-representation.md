# ADR-009: Represent a live board's on/off state as an enum-valued status carried by a frozen config dataclass

- **Status:** Accepted — DESIGN wave, feature `cluster-board-control`
- **Date:** 2026-09-08
- **Depends on:** [ADR-006](adr-006-sqlite-storage-backend.md) (SQLite backend,
  D9 JSON rollback path), [ADR-007](adr-007-repo-read-methods-get-guild-data-path-deprecation.md)
  (the precedent for changing the `ClusterRepository` port),
  [ADR-008](adr-008-guild-key-identity-binding.md) (the `key_status` /
  `GuildBinding` pattern this ADR conforms to)
- **Related:** [brief.md §4.6](brief.md#46-live_leaderboardsjson-schema),
  [feature-delta.md](../../feature/cluster-board-control/feature-delta.md) D3

## Context

Feature `cluster-board-control` adds an operator switch that stops a live
leaderboard's hourly refresh. That switch needs a persisted state.

**The implementation shipped before this decision was made.** It represents
the state as `enabled: bool`, **omitted from the config dict when true**, read
per-site as `cfg.get("enabled", True)`. That shape was not chosen on
architectural grounds. It was chosen to keep one exact-equality assertion
passing:

```python
# tests/acceptance/sqlite-backend/test_repository_contract.py:98-100
lbs = {"cluster": {"channel_id": 888, "messages": {...}, "season": 94}}
repo.save_live_leaderboards(server, lbs)
assert repo.load_live_leaderboards(server) == lbs
```

A status field that is always emitted makes the loaded dict differ from the
saved literal, so the field was made conditional instead of the test being
amended. That is a test-shaped reason for a production representation.

This repository already solves the identical problem, one feature earlier.
`key_status` (ADR-008) is a guild key's on/off state, and it is modelled as:

- an explicit `TEXT` column holding the string values of a named enum
  (`KeyStatus.ACTIVE` / `.QUARANTINED`);
- carried across the port by a **frozen dataclass** (`GuildBinding`), so no
  cog handles a raw row or a raw dict;
- with **absence modelled as a default value** — `load_guild_binding` returns
  `GuildBinding()` rather than `None`, which is what "keeps the None-check off
  seven call sites" (that dataclass's own docstring);
- read through a single predicate, never by scattered `== "quarantined"`
  comparisons.

Two representations of "this thing is switched off", in one codebase, differing
only because one of them was written in a hurry.

## Decision

### 1. `board_status` — an enum-valued TEXT column, always present

`live_leaderboards` carries `board_status TEXT NOT NULL DEFAULT 'active'`,
holding the string values of a new `BoardStatus` enum:

```
BoardStatus.ACTIVE   = "active"
BoardStatus.DISABLED = "disabled"
```

Mirrors `KeyStatus` exactly, including the detail that the repository layer
**duplicates the literal rather than importing the enum** — policy depends on
storage and never the reverse (ADR-008 D3), and that constraint applies here
for the same reason.

`DISABLED`, not `PAUSED` or `QUARANTINED`. `QUARANTINED` would be a lie: a
quarantine is a system-set consequence of detected drift, whereas this state is
only ever set by a human. Reusing the word would make the two indistinguishable
in a log grep, which is precisely the confusion that motivated this feature.

A `TEXT` status rather than a `BOOLEAN`: primarily because it is the pattern
this codebase already uses for the same concept and the operator asked for
conformance. Secondarily, a status admits a third value without a migration
where a boolean never can.

### 2. `LiveBoardConfig` — a frozen dataclass at the port

`ClusterRepository.load_live_leaderboards` changes signature:

```
dict[str, dict]  ->  dict[str, LiveBoardConfig]
```

`save_live_leaderboards` takes the same mapping.

The codebase carries **two** port shapes, and the choice between them is
principled rather than stylistic:

| Repo method | Returns | Why |
|---|---|---|
| `load_battle_hits` | raw `dict` | genuinely dict-shaped — `boss_id → encounter → tier → entries`, variable keys at every level |
| `load_guild_binding` | frozen `GuildBinding` | genuinely record-shaped — a fixed set of named fields |

A live-board config is `channel_id`, `guild_id`, `messages`, `season`,
`board_status`: a fixed set of named fields. **Record-shaped, therefore
`GuildBinding` is the governing precedent.**

`LiveBoardConfig` is frozen, and the predicate lives on the type as an
`is_enabled` property — so there is no free function anyone can forget to
call, and no second place the status comparison can be written.

**Consequence — the refresh loop stops mutating.** `_refresh_live_leaderboards`
currently assigns into the config dict (`config["season"] = season`,
`config["messages"] = new_message_ids`). Against a frozen dataclass those
become `dataclasses.replace(...)` and a rebuilt mapping. The loop already
tracks a `dirty` flag and saves the whole mapping wholesale, so the change is
contained — but it IS a change to that loop's shape, and it is the largest
single piece of work this ADR creates.

### 3. Both adapters normalise the default on load

A stored config with no status — which is **every board that exists today** —
materialises `BoardStatus.ACTIVE` on the way out of *both* adapters. No reader
ever infers it.

This is the same move `load_guild_binding` makes by returning `GuildBinding()`
for a guild with no row, and it is what makes the parity contract hold without
a conditional emit: both adapters return the field, always, so a config
round-trips identically through either.

### 4. Amend Alembic `0005` in place rather than chaining `0006`

`0005` is committed but **unpushed and undeployed** — no database anywhere is
at that revision. It is rewritten to add `board_status` instead of `enabled`.

Amending a published revision would be wrong. Amending one that has never run
is not, and chaining a `0006` to drop a column that existed for zero
deployments would leave a permanent scar in the schema history explaining
nothing. The commit trail preserves what happened.

The native `DROP COLUMN` reasoning in `0005`'s docstring is **retained** —
`batch_alter_table` rebuilds the table and re-emits `CREATE TABLE` with the
name quoted and constraints reordered, which `AC-006.2`'s byte-for-byte schema
comparison rejects. That finding stands regardless of the column's type.

### 5. The JSON adapter is NOT degraded here

Recorded explicitly because the asymmetry with `key_status` is surprising and
would otherwise look like an oversight.

`key_status` is **inert** under `JsonClusterRepository` (ADR-006 D9 / ADR-008
DDD-4): that adapter has no binding store, `load_guild_binding` returns the
unbound default, writes are dropped, and a loud `health.startup.json_rollback`
warning fires. Live-board configs, by contrast, **are** stored natively by the
JSON adapter. `board_status` therefore round-trips normally on the rollback
path and needs no degradation, no no-op write, and no warning.

### 6. The contract test literal is amended

`test_repository_contract.py:98-100`'s literal gains `board_status`. The
contract genuinely changed; amending a test to match an intended contract
change is correct, and is a categorically different act from bending the
production representation to avoid amending it — which is what produced the
shipped shape.

## Consequences

- **Positive:** one representation of "switched off" in the codebase instead of
  two. A reader who has understood `key_status` has understood `board_status`.
- **Positive:** the hidden `absent-means-true` invariant is gone. It was
  undocumented in the type system, unenforceable by any tool, and would have
  been "fixed" into a parity break by the next person who found it surprising.
- **Positive:** the predicate cannot be bypassed — it is a property on a frozen
  type, not a free function beside the data.
- **Negative:** the port change touches **5 `load_live_leaderboards` call sites
  and 5 `save_live_leaderboards` call sites**, both adapters, and
  `bot/db/migrations_json_to_sqlite.py`. This is real DELIVER work created by a
  DESIGN decision, and it exists only because the code was written before the
  design.

  **Corrected 2026-09-08, after the Final Wave Review Gate.** This read "4
  config-read sites and 2 write sites". That number was measured wrong: it
  counted config-FIELD reads (`cfg.get("channel_id")` and friends) and reported
  them as CALL sites — two different things. Verified inventory: loads at
  `admin_cog` 437/589/713/776 and `tasks_cog` 556; saves at `admin_cog`
  596/723/799, `tasks_cog` 723, and `migrations_json_to_sqlite` 511.

  The undercount hid a whole driving port. `/set_live_leaderboard` — the
  GUILD-scoped setup command at `admin_cog:589-596` — writes a raw dict literal
  into the mapping and saves it, so DDD-2 breaks it, and it appeared in no
  component table and no scenario. DISTILL has since added a regression
  scenario driving it against the real port.
- **Negative:** `_refresh_live_leaderboards` must stop mutating configs in
  place. Contained, but it is the loop this feature is most afraid of breaking.
- **Trade-off:** a sibling ABC method (`load_live_board_configs` alongside the
  dict-returning original) would have been a smaller diff. Rejected — see
  Alternatives.

## Alternatives considered

- **Keep the shipped `enabled: bool`, omitted-when-true.** Rejected. The
  omission is a hidden invariant with no type-level expression, it contradicts
  the pattern the same codebase uses for the same concept one feature earlier,
  and its only justification was avoiding a one-line test amendment.

- **Keep `enabled: bool` but always emit it.** Rejected as a half-measure. It
  fixes the invariant and the parity story but still leaves two shapes for one
  concept — a boolean here, an enum-valued status there — which is the drift
  this ADR exists to remove.

- **Add a sibling ABC method returning dataclasses, leave the dict method.**
  Rejected on ADR-007's own reasoning. That ADR removed `get_guild_data_path`
  rather than leaving it as a deprecated JSON-only method precisely because
  "keeping it would leave a footgun in the port". Two ways to read the same
  table is the same footgun: the dict path would keep carrying the invariant,
  and nothing would stop a new call site choosing it.

- **Reuse `KeyStatus` for boards.** Rejected. Its `QUARANTINED` member names a
  system-detected fault; a board is switched off by a person. Collapsing the
  two makes an operator action and a detected drift indistinguishable in logs
  and in `/view_config` — the exact ambiguity the feature was written to end.

- **Chain a `0006` instead of amending `0005`.** Rejected while `0005` remains
  undeployed. Revisit immediately if this branch is pushed and anyone migrates
  before the amend lands — at that point amending becomes wrong and `0006`
  becomes the only correct option.

- **Model the state as a separate `live_board_status` table, 1:1 with
  `live_leaderboards`.** Rejected. ADR-008 D4 put `key_status` in a separate
  table for a specific reason: adding binding fields to the `Guild` dataclass
  would have let `save_guilds` write `None` defaults over live state on any
  unrelated admin command. No such hazard exists here — `live_leaderboards`
  rows are written only by the live-board code paths, and the config is already
  rewritten wholesale on every save. A second table would add a join and a
  CASCADE for no protection.
