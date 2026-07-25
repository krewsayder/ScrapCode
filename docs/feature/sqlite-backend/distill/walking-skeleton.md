# Walking Skeleton — feature `sqlite-backend` (DISTILL wave)

> Author: Quinn (nw-acceptance-designer), DISTILL wave.

## Strategy: C — Real local

The thinnest end-to-end slice that proves the swap is feasible is one
operator journey: seed a JSON `clusters/` tree → run the JSON→SQLite
migration as a real subprocess → construct the SQLite-backed
`ClusterRepository` against the resulting DB → load the cluster's
guilds, player list, and battle hits through the ABC → render the
leaderboards via the existing `embeds.build_battle_messages` /
`build_bomb_messages` → assert the render matches the JSON-backed
render byte-for-byte.

This is **Strategy C (Real local)**: every adapter in the slice uses real
I/O — real JSON in `tmp_path`, a real `python -m bot.db.migrations_json_to_sqlite`
subprocess, a real SQLite file at `tmp_path/data/scrapcode.db`, a real
Fernet round-trip via `cryptography`, and (for the `clean` env) a real
`alembic upgrade head` against a fresh DB.

The walking skeleton does NOT touch production data (it uses a synthetic
cluster tree stand-in) and does NOT flip the production singleton — it is
the test-harness proof ADR-006 D2 depends on.

## Scenarios (3 — one per environment)

1. `clean` — fresh `.venv`, empty SQLite DB (schema created by
   `alembic upgrade head`, no rows). Migration runs against an empty
   source; the render matches (empty no-entries message).
2. `with-existing-json-data` — a synthetic `clusters/` tree (two guilds,
   a v1 player list for `mech`, a v2 player list for `neuro`, season 94
   battle + bomb hits, a `replay_index.json`) is copied into `tmp_path`.
   Migration runs against the copy; the render matches byte-for-byte.
3. `with-stale-config` — the SQLite DB is stamped at an older alembic
   revision than the compiled head. The startup `probe()` REFUSES with a
   `health.startup.refused` event; the bot does NOT start on the
   half-migrated DB. This is the ADR-006 D8 step-2 refusal path.

See `tests/acceptance/sqlite-backend/acceptance/walking-skeleton.feature`.

## Litmus test

- **Title describes user goal?** YES — "Operator migrates a JSON cluster
  tree to SQLite and renders the same leaderboards."
- **Given/When describe user actions?** YES — operator runs the
  migration, constructs the repo, loads data, renders.
- **Then describes user observations?** YES — non-empty render,
  byte-identical to the JSON-backed render, probe succeeds.
- **Non-technical stakeholder confirms?** YES — the operator can see the
  same leaderboards before and after the migration; the bot refuses to
  start on a half-migrated DB.

## Driving ports

- `ClusterRepository` ABC (the port) — `bot/repository.py`
- JSON→SQLite migration CLI — `python -m bot.db.migrations_json_to_sqlite`
  (real subprocess adapter)
- `bot.embeds.build_battle_messages` / `build_bomb_messages` (the read-side
  proxy for Discord commands — KPI-4)
- `bot.db.session.Database.probe()` — the startup health gate

## Adapter tier audit (Mandate 9d — "if I deleted the real adapter,
would this WS still pass?")

| Adapter | Tier in WS | Delete-test |
|---------|------------|-------------|
| `JsonClusterRepository` | real JSON in `tmp_path` | deleting the JSON files → `load_*` returns empty → render empty → assertion fails. PASS. |
| `SqlAlchemyClusterRepository` | real SQLite file at `tmp_path/data/scrapcode.db` | deleting the DB file → `load_*` raises → assertion fails. PASS. |
| Fernet secret encoder | real `cryptography.Fernet` round-trip via `SCRAPCODE_DB_KEY` | deleting the key → probe refuses → WS fails. PASS. |
| Alembic migrator (`clean` env) | real `alembic upgrade head` on `tmp_path` DB | deleting alembic env → schema not created → load raises → WS fails. PASS. |
| JSON→SQLite data migration | real subprocess | deleting the migration module → subprocess exits non-zero → WS fails. PASS. |

No `@in-memory` tags on the walking skeleton. Strategy C confirmed.