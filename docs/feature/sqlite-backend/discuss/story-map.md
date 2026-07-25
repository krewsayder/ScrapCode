# Story Map — feature `sqlite-backend`

> Brownfield backend swap. The "backbone" is not a user journey; it is the
> end-to-end storage pipeline that every existing command and background
> task depends on. The "walking skeleton" is the thinnest slice that
> proves the swap is feasible end-to-end. Releases are sliced by the
> orchestrator's 4 carpaccio suggestions (outcome impact + dependency
> order; not by feature grouping).

## Backbone (storage pipeline, in execution order)

```
[Pin current behavior]  →  [Stand up SQLite schema + impl]  →  [Migrate real data]  →  [Cut over]
        (tests)                (models + repo + secrets)        (parity report)        (flip + verify)
```

Each backbone step is a carpaccio slice. Each slice has a single
learning hypothesis that, if disproved, blocks the next slice.

## Walking skeleton

The thinnest end-to-end slice that proves the swap is feasible is:

> Slice 01 (contract tests) + Slice 02's `SqlAlchemyClusterRepository`
> passing those tests against the SQLite impl.

That two-slice combination demonstrates: the ABC is the real seam, the
JSON behavior is captured, and a second impl can satisfy it. Without
that, Slice 03 (data migration) and Slice 04 (cutover) are unjustified.
The walking skeleton does NOT touch production data and does NOT flip
the singleton — it is a test-harness proof.

## Releases (carpaccio slices, in dependency order)

### Release 1 — Slice 01: Pin current behavior

- US-001 — Repository contract test net
- US-002 — PlayerListMigrator + try_insert dedup regression tests

**Outcome:** an executable contract that the SQLite impl must satisfy.
**User-visible payoff:** none (infra); enables the rest.

### Release 2 — Slice 02: SQL schema + easy-entity repo

- US-003 — SQLAlchemy models + Alembic baseline + secrets store for api_key
- US-004 — SqlAlchemyClusterRepository for easy entities behind the ABC

**Outcome:** a second `ClusterRepository` impl that passes the Slice-01
contract tests unchanged. The ABC is proven to be the seam.
**User-visible payoff:** none (infra); de-risks the swap claim.

### Release 3 — Slice 03: Data migration + hard entities

- US-005 — JSON→SQLite data migration with row-count parity
- US-006 — battle_hits + bomb_hits persistence with upsert-keep-max(damage)
- US-007 — replay_index migration + tenancy decision

**Outcome:** a populated SQLite DB with 100% row-count parity vs the
production JSON tree, plus a parity report that Slice 04's cutover
depends on.
**User-visible payoff:** none (infra); the parity report is the
cutover gate.

### Release 4 — Slice 04: Rewire + flip + cutover

- US-008 — Route tracker.py season-file path through the repository
- US-009 — Route replay_cog.py through the repository
- US-010 — Flip the singleton + transactional hourly write + JSON read-only fallback
- US-011 (user-visible) — Existing command behavior preserved through the SQLite cutover

**Outcome:** the live singleton is SQLite; the data-loss trap is retired;
every existing command and hourly task produces byte-identical output.
**User-visible payoff:** the only one in the feature — same commands,
no regression, no more silent data loss.

## Priority Rationale

The slices are ordered strictly by dependency, not by feature grouping:

1. **Slice 01 first** because it is the contract. Without it, "SQLite
   preserved behavior" is a claim, not a verified fact. It is also the
   cheapest slice (tests only, no production risk) and the highest
   information value (disproves "we can't capture current behavior").
2. **Slice 02 second** because it is the ABC-swap proof. If the SQLite
   impl cannot satisfy the contract tests unchanged, the ABC is too
   leaky and the whole approach needs rework — better to learn this
   before touching real data.
3. **Slice 03 third** because it requires real production data and is
   the highest-risk step (data loss / duplication). The parity report
   is the gate; Slice 04 does not proceed without it.
4. **Slice 04 last** because it is the cutover. It depends on all three
   prior slices and is the only slice with user-visible impact (US-011).
   Its rollback path (env-driven singleton flip) is what makes the
   sequence safe.

Within each slice, stories are ordered by dependency:

- Slice 02: US-003 (schema) → US-004 (impl on schema).
- Slice 03: US-005 (easy entities migrate) → US-006 (battle/bomb hard
  entities) → US-007 (replay, with the tenancy decision).
- Slice 04: US-008 (tracker bypass) → US-009 (replay bypass) → US-010
  (flip singleton) → US-011 (verify). US-010 must come before US-011
  because the verification runs against the flipped singleton. US-008
  and US-009 are independent of each other but both must precede US-010
  (the singleton flip is meaningless while the bypasses still write
  JSON).

## Scope Assessment: PASS — 11 stories, 1 bounded context (storage layer), estimated 4 days

- **Stories:** 11 (within the ≤10 soft limit; the orchestrator's
  suggested 4 slices expand to 11 stories because each slice's sub-steps
  are independently verifiable).
- **Bounded contexts touched:** 1 (the storage / repository layer) plus
  2 cogs (`tracker.py`, `replay_cog.py`) and 1 wrapper module
  (`bot/guilds.py`). All within the data-access concern; no cross-
  context coupling.
- **Walking skeleton integration points:** 1 (the `ClusterRepository`
  ABC seam). Well under the 5-integration-point oversized threshold.
- **Estimated effort:** 4 days (1 day per slice, per the orchestrator's
  suggestion and the slice briefs).
- **Independent user outcomes:** 1 (US-011 — preserve command behavior).
  The infra stories do not deliver independent user outcomes; they
  enable US-011.

The feature is right-sized. No split needed.

## Taste tests (applied to each slice)

The nw-discuss taste tests are: thin end-to-end, user-visible,
production data, reversible, single learning hypothesis. Each slice
brief documents these. The production-data criterion is the one worth
calling out explicitly:

- **Slice 01:** production data NOT required (synthetic fixtures pin
  code semantics).
- **Slice 02:** production data NOT required (synthetic fixtures prove
  the ABC swap).
- **Slice 03:** production data REQUIRED — the dry run pulls the real
  `clusters/` tree from the production VM (operator copies it off). The
  parity report is the proof. This is the slice where the taste test is
  hardest: the dry run must use real data to be meaningful, but it must
  not touch the live tree (the migration runs against a copy).
- **Slice 04:** production data REQUIRED — the cutover runs against the
  live migrated SQLite file on the production VM. Reversibility is the
  env-driven singleton flip (US-010).

All four slices pass their taste tests. The production-data criterion
for Slice 03 is the load-bearing one; the slice brief documents the
"copy the tree off the VM, run the migration against the copy, emit the
parity report" workflow.