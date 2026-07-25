# ScrapCode

A Discord bot (Python, asyncio, `discord.py`) that tracks Tacticus battle/bomb
leaderboards, token-cap detection, and a replay index for one or more Discord
servers. Persistence is migrating from flat per-server JSON files to an embedded
SQLite database (SQLAlchemy 2.0 + Alembic + aiosqlite) — see
`docs/product/architecture/` and `docs/feature/sqlite-backend/`.

## Development Paradigm

This project follows the **object-oriented** paradigm (ABCs, dataclasses, the
`ClusterRepository` repository pattern). Use `@nw-software-crafter` for
implementation.

## Mutation Testing Strategy

**pre-release** — mutation testing is handled at the release boundary, not
per-feature. Rationale: no mutation-testing tool (cosmic-ray / mutmut) is in the
stack today; for the SQLite-backend migration the primary quality gates are the
repository contract/parity test suites and the adversarial review. Per-feature
mutation can be introduced later when a tool is added to `requirements.txt`.