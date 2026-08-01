# ATDD Infrastructure Policy

Per `nw-distill` § Project Infrastructure Policy. One file per project.
Apply-if-exists; write-if-absent; rewrite with `--policy=fresh`. Git history
is the audit trail.

Bootstrapped during DISTILL of `guild-key-integrity` (2026-07-31). The rows
below were **not** negotiated from scratch — they record the mechanisms the
`sqlite-backend` acceptance suite already established and has been running
against since 2026-07-18. Where this feature introduced a port with no
precedent, the row says so.

**Project convention that overrides the generic nWave examples:** this
project does **not** use `pytest-bdd`. The `.feature` files under
`tests/acceptance/{feature}/acceptance/` are the human-readable scenario
SSOT; the `test_*.py` modules beside them are the executable specs, written
in plain pytest + `pytest-asyncio`. Precedent:
`tests/acceptance/sqlite-backend/`.

## Driving

| Port | Mechanism | Note |
|---|---|---|
| Discord slash command | direct invocation of the app-command callback with an interaction double | `discord.py` app-commands cannot be driven over the wire in a test; the double captures `ephemeral` and the reply text, which is what the ACs assert |
| `@tasks.loop` background task | direct `await` of the loop body with the loop decorator bypassed | the schedule is `discord.py`'s concern; the cycle body is ours |
| Repository ABC (`ClusterRepository`) | real adapter, constructed by the test | the port both cogs and services depend on |
| Alembic CLI | real `alembic.command.upgrade` / `downgrade` against a `tmp_path` DB | migration ACs are about the real migration or they are about nothing |
| JSON→SQLite migration CLI | real subprocess | established by `sqlite-backend` |

## Driven internal (real)

| Port | Mechanism | Note |
|---|---|---|
| `SqlAlchemyClusterRepository` (SQLite) | real SQLite file in `tmp_path`, real alembic, real Fernet key | no Testcontainers — SQLite is embedded, so the "real thing" costs nothing |
| `JsonClusterRepository` (files) | real JSON tree in `tmp_path` | the ADR-006 D9 rollback path; must stay exercised |
| `guild_key_bindings` store | reached through the repository port, never by importing the ORM row | keeps the Universe port-exposed; a test that imports `GuildKeyBindingRow` reds on an internal rename |
| Structured log sink | real `logging` via `caplog`, asserting on `record.event` | the KPI queries in `kpi-contracts.yaml` grep these exact event names |

## Driven external / non-deterministic (fake)

| Port | Fake | Note |
|---|---|---|
| Tacticus guild endpoint | `FakeGuildService` (conftest) | programmable per key; records calls so a `Then` can assert a request was **not** made |
| Tacticus raid / season endpoints | same double | one seam, one double |
| Chronicler client | unchanged from `sqlite-backend` | this feature does not touch it |
| Discord channel send | `FakeChannel` (conftest) | captures text so "nothing was posted" is assertable |
| Clock | `monkeypatch` on the time source | manual advance; used by the 24-hour alert-suppression scenario |

**Exception, deliberate:** the Tacticus guild endpoint additionally gets a
small `@requires_external` contract suite
(`acceptance/tacticus-guild-contract.feature`) that hits the live service.
The field this whole feature binds on — `guildId` — is undocumented by the
vendor, so a fake can never tell us it disappeared. Those tests skip unless
`SCRAPCODE_TACTICUS_CONTRACT_KEY` is set. See ADR-006 §H ("highest-risk
boundary") and ADR-008 D1's accepted residual risk.
