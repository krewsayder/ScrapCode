# Slice 01 — Pin current behavior (regression net)

**Feature:** sqlite-backend
**Slice size:** ≤1 day
**Type:** @infrastructure (no user-visible behavior change)
**Trace to job:** `preserve-data-integrity-through-backend-swap` (see `docs/product/jobs.yaml`)

## Goal

Before touching the storage layer, capture the *current* behavior of
`JsonClusterRepository`, the `PlayerListMigrator` v1→v2 runtime migrator, and
`tracker.try_insert` as executable regression tests. These tests become the
contract that the SQLite implementation must satisfy in Slice 02.

## Learning hypothesis

> If this slice fails, it disproves "we can capture current behavior as a
> regression net" — and the whole swap is unsafe to attempt without first
> rewriting the contract by hand.

In other words: if we cannot write tests that pin the JSON repo's read/write
round-trip, the migrator's v1→v2 invocation, and `try_insert`'s dedup +
tiebreak, then we have no objective way to claim "SQLite preserved behavior"
later. This slice de-risks that claim.

## In scope

- Contract tests against `JsonClusterRepository` covering all 11 ABC methods:
  `load`, `save`, `load_player_registrations`, `save_player_registrations`,
  `load_captured_state`, `save_capped_state`, `load_player_list`,
  `save_player_list`, `load_live_leaderboards`, `save_live_leaderboards`,
  `get_guild_data_path`, `list_server_ids`.
- Round-trip tests: save → load returns equivalent data for clusters,
  registrations, capped state, player list (v2), live leaderboards.
- `PlayerListMigrator` tests: v1 input → v2 output, `was_migrated=True`,
  `last_validated="1970-01-01T00:00:00Z"` sentinel; v2 input → unchanged,
  `was_migrated=False`.
- `try_insert` dedup tests: same player + same roster + equal damage keeps
  first; same player + same roster + higher damage replaces; same player +
  different roster → separate entry; top-N truncation at `TOP_N=5`;
  tiebreak `(-damage, completed_on asc)` pinned (already covered by
  `bot/tests/test_tracker_tiebreak.py` — extend, do not duplicate).
- Silent-empty-on-corruption read behavior pinned (corrupt JSON file →
  returns `{}`) so the SQLite impl can deliberately diverge and the test
  documents the trap we are retiring.

## Out of scope

- Any SQLite/SQLAlchemy code.
- Any behavior change to existing commands.
- The `replay_index.json` path (covered by Slice 03, where the tenancy
  decision lives).

## Taste tests

- **Thin end-to-end?** YES — the slice produces one artifact (a test module)
  that runs green against the live codebase and is the regression net for the
  whole feature.
- **User-visible?** NO (infrastructure) — honestly tagged. The user-visible
  payoff is deferred to Slice 04.
- **Production data?** NOT REQUIRED — synthetic JSON fixtures are sufficient;
  pinning behavior is about code semantics, not real data shape.
- **Reversible?** YES — adding tests cannot break production.
- **Single learning hypothesis?** YES (see above).

## Exit criteria

- `pytest bot/tests/` green with the new contract tests.
- Every `ClusterRepository` ABC method has at least one round-trip test.
- `try_insert` dedup branches (same-roster-equal, same-roster-higher,
  different-roster, top-N-truncate) each have a named test.
- The silent-empty-on-corruption behavior is pinned by a named test that
  documents it as "to be retired by Slice 02/04".

## Stories delivered

- US-001 — Repository contract test net
- US-002 — PlayerListMigrator + try_insert regression tests