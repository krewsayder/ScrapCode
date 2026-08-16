# Slice 01 — Capture every tier, and report what was skipped

**Feature:** `dynamic-tier-registry` · **Stories:** US-001, US-002, US-007
**Estimate:** ~0.5 day (≤6 h crafter dispatch) · **Order:** 1 of 4

## Goal

Stop discarding hits for tiers the bot has never seen, and make every
discarded entry visible in the hourly post.

## Why this is first

Every cycle that runs before this ships permanently destroys that hour's
Mythic 3 hits — the Tacticus endpoint serves a rolling window, so they cannot
be backfilled. This overrides the usual highest-uncertainty-first ordering:
here, delay *is* the expensive failure. It is also the lowest-uncertainty
slice, so it is the least likely to stall.

## IN scope

- Generalise `get_tier_key` ([bot/tracker.py:5-25](../../../../bot/tracker.py#L5)):
  any `set >= 0` within a tracked rarity, for both Legendary and Mythic.
- Per-reason entry-skip counters: `untracked_rarity`, `malformed_set`,
  `unparseable`. Never collapsed.
- Skip line in the update-channel post, naming count **and** reason, with the
  observed rarity string verbatim.
- `📥 Captured but not displayable: {key} — {n} hits` line — a standing
  condition re-evaluated each cycle, not a one-time announcement
  (ADR-009 D5; supersedes AC-002.4/AC-002.5).
- Counters surfaced on the structured cycle event alongside `_CycleReport`.
- **One literal choice** in `config.py`
  (`Choice(name="Mythic 3", value="Mythic_2")`) so the board is readable on day
  one. Deleted in Slice 02 when the registry-derived list lands.
  *(Operator decision 2026-08-15 — see `design/upstream-changes.md` §2.)*

## OUT of scope

- The tier registry module (Slice 02) — this slice keeps the parse rule in
  `tracker.py` and generalises it in place, and adds the Mythic 3 choice as a
  hand-written literal rather than a derived one.
- Derived labels, ordering, and overrides. A *future* tier (Mythic 4) captured
  by this slice is still unreachable from any picker until Slice 02 — which is
  exactly what the `📥` line reports.
- Live leaderboards (Slice 03).
- Backfilling the already-lost window. Not possible.

## Learning hypothesis

**If Mythic 3 rows still do not appear in `battle_hits` after this ships, it
disproves the claim that `get_tier_key` is the only thing gating ingest** —
meaning something downstream (damage-type routing, the `UNIQUE` constraint on
`(boss_id, encounter_index, tier_key, …)`, or the `String(32)` key column) is
also rejecting them, and the feature is bigger than a parser change.

**If it succeeds**, it confirms the write path is tier-agnostic below the
filter, which is the assumption Slices 02–04 all rest on.

The hypothesis is real: the write path was rebuilt during the SQLite cutover
and has only ever run against the seven enumerated tier keys.

## Acceptance criteria

AC-001.1 – AC-001.7, AC-002.1 – AC-002.7, AC-007.1 – AC-007.4. See
[feature-delta.md](../feature-delta.md).

The load-bearing ones for this slice:

- **AC-001.4** — every pre-existing tier key parses byte-identically. This is
  the regression pin; a subtle change here orphans historical rows.
- **AC-001.5** — a real `set=2` entry produces a real `battle_hits` row,
  verified by SQL, not by a mock.
- **AC-002.3** — a clean cycle posts no skip line. Silence must mean clean, or
  the operator learns to scroll past the warning.

## Production data requirement

Acceptance is against **real cluster data**: a season containing genuine
Mythic 3 hits from a registered guild, ingested through `auto_update` or
`/update_leaderboard`. A synthetic `set=2` fixture proves the parser and
nothing else. The `make_tacticus_entry` fixture
([conftest.py:306](../../../../tests/acceptance/sqlite-backend/conftest.py#L306))
already parametrises `rarity` and `set_`, so unit coverage is cheap — but it
does not substitute for the production run.

## Dogfood moment (same day)

Operator runs `/update_leaderboard guild_id:<id> season:<n>`, confirms:

```sql
SELECT tier_key, COUNT(*) FROM battle_hits WHERE season = 107 GROUP BY tier_key;
```

then runs `/view_leaderboard season:107 tier:"Mythic 3"` and reads a real board.
A non-zero `Mythic_2` count **and** a rendered board is the slice's definition of
working — the choice literal means both land on day one.

## Dependencies

None. This slice depends on nothing and nothing depends on it — Slices 02–04
consume the registry, not this change. It can ship, and be reverted,
independently of everything else.

## Reference class

Comparable to `guild-key-integrity` Slice 01 (probe + classify + report,
shipped in ~1 day). This is smaller: one pure function generalised, plus
counters on an existing report surface. No new table, no new column, no
migration, no new external call.

## Risks

- **Over-generalising into rarity.** Removing the `set` bound is safe;
  removing the rarity allow-list is not, and would start ingesting
  high-volume low-rarity content. D1 keeps it closed; AC-001.7 pins it.
- **Alert fatigue.** `untracked_rarity` will be routinely non-zero if Epic
  hits are returned. AC-007.3 rate-limits to once per cycle per rarity so
  routine noise cannot bury a novel rarity.

## Pre-slice SPIKE

**Not required.** The operator confirmed the payload shape
(`rarity=Mythic, set=2`), which is what a SPIKE would have established.
