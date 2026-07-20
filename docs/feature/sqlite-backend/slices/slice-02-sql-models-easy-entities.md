# Slice 02 — SQLAlchemy models + Alembic baseline + easy-entity repo

**Feature:** sqlite-backend
**Slice size:** ≤1 day
**Type:** @infrastructure (no user-visible behavior change)
**Trace to job:** `preserve-data-integrity-through-backend-swap`

## Goal

Stand up the SQLite successor backend for the "easy" entities — clusters,
role_tiers, guilds, guild_member_roles, player_registrations, capped_state,
players, live_leaderboards — as a second `ClusterRepository` implementation
behind the existing ABC, plus an Alembic baseline migration that creates the
schema. The Slice-01 contract tests pass against the new implementation with
no code changes to the tests.

## Learning hypothesis

> If this slice fails, it disproves "the ClusterRepository ABC is too leaky
> to swap implementations" — i.e. if the SQLite impl cannot satisfy the JSON
> contract tests unchanged, the ABC is implicitly coupled to JSON's shape
> (dict returns, `__meta__.version`, etc.) and must be refactored before the
> hard entities are attempted.

This slice is the go/no-go gate for the abstraction seam.

## In scope

- `requirements.txt`: add `sqlalchemy>=2.0`, `alembic`, `aiosqlite` (installed
  into the existing `.venv`).
- SQLAlchemy 2.0 declarative models for the 8 easy entities per
  `docs/product/architecture/data-dictionary.md` §4 (migration mapping):
  - `clusters` (PK `discord_server_id`), `role_tiers` (composite
    `(server_id, tier, role_id)`, check `tier ∈ {admin, officer}`),
    `guilds` (PK `(server_id, guild_id)` with `guild_id` natural key, no
    surrogate required), `guild_member_roles` (composite), `player_registrations`
    (PK `discord_id`, composite FK `(server_id, guild_id) → guilds`, unique
    `api_key`), `capped_state` folded as `is_capped bool` column on
    `player_registrations` OR a separate table (IMPLEMENTATION note: pick the
    column option — fewer joins, matches "edge-detect scratch" semantics),
    `players` (PK `tacticus_user_id`, `last_validated` as `TIMESTAMPTZ`
    equivalent), `live_leaderboards` (+ `live_lb_messages` child for the
    per-tier message map).
- `update_channel_id` is NOT created in the SQL schema (dropped per ADR-002).
- Alembic env + baseline revision creating the 8 tables.
- `SqlAlchemyClusterRepository` implementing the same 11 ABC methods, returning
  the same dict shapes the JSON impl returns (so cogs are untouched).
- Slice-01 contract tests parameterized to run against both
  `JsonClusterRepository` and `SqlAlchemyClusterRepository` — both green.
- `api_key` (guild + registration) plaintext → secrets store. RECOMMENDATION:
  app-level encrypted column via `cryptography.Fernet` with a key from `.env`
  (`SCRAPCODE_DB_KEY`). Rationale: single-server, single-process, no extra
  service; simpler than `.env`-per-key for many keys; preserves the existing
  "load registrations → pass api_key to Tacticus" path with decrypt-on-read.

## Out of scope

- The hard entities (`battle_hits`, `bomb_hits`, `replay_index`) — Slice 03.
- JSON→SQLite data migration — Slice 03.
- Routing `tracker.py` / `replay_cog.py` through the new layer — Slice 04.
- Flipping the singleton in `bot/guilds.py:7` — Slice 04.

## Taste tests

- **Thin end-to-end?** YES — schema + impl + contract tests green is a
  demonstrable slice.
- **User-visible?** NO (infrastructure). The new repo is constructed in a
  test harness only; no production path uses it yet.
- **Production data?** NO — synthetic fixtures from Slice 01. Real data
  enters in Slice 03.
- **Reversible?** YES — adding a second repo impl + tests does not change
  the live singleton.
- **Single learning hypothesis?** YES (ABC swap-ability).

## Exit criteria

- `pip install -r requirements.txt` succeeds in the project `.venv`.
- `alembic upgrade head` creates the 8 tables in a fresh temp SQLite file.
- Slice-01 contract tests pass against BOTH repo implementations (pytest
  parametrization).
- `api_key` columns are encrypted at rest; decrypt-on-read returns the
  plaintext the cogs expect.
- No production code path imports `SqlAlchemyClusterRepository` yet (the
  singleton in `bot/guilds.py:7` still points at JSON).

## Stories delivered

- US-003 — SQLAlchemy models + Alembic baseline + secrets store for api_key
- US-004 — SqlAlchemyClusterRepository for easy entities behind the ABC