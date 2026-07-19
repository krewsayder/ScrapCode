# ScrapCode — Architecture Overview

> **Start here.** This is the one-page entry point to ScrapCode's architecture
> documentation: what the system is, where to read deeper, and which libraries
> power it. Everything linked here is **as-built** (captured by a read-only pass,
> zero behavior changes). For the project README, see [`/README.md`](../../../README.md).

## What ScrapCode is

ScrapCode is a single-process **multi-tenant Discord bot** for Warhammer Tacticus
guild clusters. One running process serves many Discord servers; each server
("cluster") manages several in-game guilds. The bot handles guild/cluster
registry, per-server permission tiers, raid leaderboards (on-demand + live
auto-updating), token-cap notifications, token/bomb availability, and a raid
replay index.

**Stack:** Python · [`discord.py`](https://docs.pycord.dev/en/stable/) (slash
commands + gateway) · [`httpx`](https://www.python-httpx.org/async/) (async
Tacticus calls) · [`requests`](https://requests.readthedocs.io/) (sync Chronicler
calls, wrapped with [`asyncio.to_thread`](https://docs.python.org/3/library/asyncio-task.html#asyncio.to_thread))
· [`python-dotenv`](https://pypi.org/project/python-dotenv/) (config) · flat JSON
files on local disk for storage. Logged to a local `discord.log`.

## Architecture at a glance

| Aspect | What's true today | Deep dive |
|--------|-------------------|----------|
| Runtime | Single process, 9 cogs, 2 hourly background loops (`cap_detect`, `auto_update`), systemd-supervised | [brief §2](brief.md#2-runtime-model-_closes-gap-2) · [§2.6 deployment](brief.md#26-deployment--runtime-target) |
| Storage | Per-server JSON under `clusters/{discord_server_id}/`; SQLite is the accepted successor | [brief §4](brief.md#4-data-layout--storage-_closes-gap-1--decision-form-in-adr-002) · [data-dictionary.md](data-dictionary.md) · [ADR-002](adr-002-storage-backend-json-legacy.md) |
| Multi-tenancy | All data keyed by `discord_server_id`; a few global leaks (replay index, update channel) flagged | [brief §3](brief.md#3-multi-tenancy-_closes-gap-3--decision-form-in-adr-004) · [ADR-004](adr-004-multi-tenancy-isolation.md) |
| Permissions | Three tiers (admin/officer/guild-member) + Discord-admin bypass; checks live only in `permissions.py` | [brief §5.3](brief.md#53-how-permission-checks-are-invoked) · [ADR-001](adr-001-permission-checks-single-source.md) · [ADR-005](adr-005-permission-model-tiers-bypass.md) |
| External systems | Tacticus (raid/tokens/roster/season, direct) + Chronicler (player profiles); Chronicler-first doctrine | [brief §2.5](brief.md#25-external-calls--tacticus-direct-vs-chronicler-_closes-gap-2--feeds-adr-003) · [ADR-003](adr-003-chronicler-first-data-doctrine.md) |

## Deep-dive index

- **[brief.md](brief.md)** — full architecture (SSOT). Sections:
  [§1 what it is](brief.md#1-what-scrapcode-is), [§2 runtime model](brief.md#2-runtime-model-_closes-gap-2)
  (cogs, task loops, [§2.5 external calls](brief.md#25-external-calls--tacticus-direct-vs-chronicler-_closes-gap-2--feeds-adr-003),
  [§2.6 deployment](brief.md#26-deployment--runtime-target)), [§3 multi-tenancy](brief.md#3-multi-tenancy-_closes-gap-3--decision-form-in-adr-004),
  [§4 data layout](brief.md#4-data-layout--storage-_closes-gap-1--decision-form-in-adr-002),
  [§5 conventions](brief.md#5-conventions-_closes-gap-5),
  [§6 README/HELP_DATA drift](brief.md#6-readme--helpdata-drift).
- **[data-dictionary.md](data-dictionary.md)** — per-entity field tables, readers/writers,
  `erDiagram`, and migration mapping to SQLite/Postgres/Supabase.
- **[c4-diagrams.md](c4-diagrams.md)** — system context, container, and TasksCog
  diagrams (plain Mermaid flowcharts).
- **ADRs** (all as-built, retroactive):
  - [ADR-001](adr-001-permission-checks-single-source.md) — permission checks live only in `permissions.py`.
  - [ADR-002](adr-002-storage-backend-json-legacy.md) — storage = per-server JSON today; SQLite successor; JSON is legacy/migration source.
  - [ADR-003](adr-003-chronicler-first-data-doctrine.md) — Chronicler-first; enumerated allow-list of direct-Tacticus calls.
  - [ADR-004](adr-004-multi-tenancy-isolation.md) — per-server isolation rules + flagged leaks.
  - [ADR-005](adr-005-permission-model-tiers-bypass.md) — tier model + Discord-admin bypass.

## Library reference index

The bot is built on these libraries. Each concept links to its official docs; the
"where used" column points to the local section that depends on it. An agent
working on ScrapCode should understand these constructs before changing code that
uses them.

| Concept | Library / docs | Where used in ScrapCode |
|---------|----------------|--------------------------|
| `Bot`, `Intents` | [discord.py — Bot](https://docs.pycord.dev/en/stable/api/api.html#discord.Bot) · [Intents](https://docs.pycord.dev/en/stable/api/intents.html) | [brief §2.1](brief.md#21-process-and-entry-point) — bot construction, default intents, `on_ready` |
| Cogs (`commands.Cog`, `add_cog`, `cog_unload`) | [discord.py — Cogs](https://docs.pycord.dev/en/stable/ext/commands/cogs.html) | [brief §2.2](brief.md#22-cogs) — the 9 cogs and their `setup_*` functions |
| `tasks.loop`, `before_loop`, `start`/`cancel` | [discord.py — ext.tasks](https://docs.pycord.dev/en/stable/ext/tasks/loop.html) | [brief §2.3](brief.md#23-background-task-loops-tasks_cogpy) — `cap_detect` + `auto_update` hourly loops |
| `app_commands.command` / `Group` | [discord.py — app_commands](https://docs.pycord.dev/en/stable/api/app_commands.html) | [brief §5.2](brief.md#52-naming) — slash commands and the `registration` group |
| `app_commands.check`, `CheckFailure` | [discord.py — app_commands checks](https://docs.pycord.dev/en/stable/api/app_commands.html#discord.app_commands.CheckFailure) | [brief §5.3](brief.md#53-how-permission-checks-are-invoked) — `@require_tier`/`@require_guild_member` + the `on_app_command_error` denial path |
| `httpx.AsyncClient` | [httpx — async](https://www.python-httpx.org/async/) | [brief §2.5](brief.md#25-external-calls--tacticus-direct-vs-chronicler-_closes-gap-2--feeds-adr-003) — all Tacticus calls |
| `requests` + `asyncio.to_thread` | [requests](https://requests.readthedocs.io/) · [asyncio.to_thread](https://docs.python.org/3/library/asyncio-task.html#asyncio.to_thread) | [brief §2.5](brief.md#25-external-calls--tacticus-direct-vs-chronicler-_closes-gap-2--feeds-adr-003) — Chronicler sync calls off the event loop |
| `asyncio.gather` | [asyncio — gather](https://docs.python.org/3/library/asyncio-task.html#asyncio.gather) | [brief §2.3](brief.md#23-background-task-loops-tasks_cogpy) — parallel per-player Tacticus fetches |
| `python-dotenv` | [python-dotenv](https://pypi.org/project/python-dotenv/) | [brief §2.6](brief.md#26-deployment--runtime-target) — `.env` secrets/config |
| `dataclass` | [dataclasses](https://docs.python.org/3/library/dataclasses.html) | `bot/models.py` — `Cluster`, `Guild` |
| `pytest` / `pytest-asyncio` | [pytest](https://docs.pytest.org/) · [pytest-asyncio](https://pytest-asyncio.readthedocs.io/) | `bot/tests/` — `test_permissions.py`, `test_tracker_tiebreak.py` |

> discord.py docs are served at `docs.pycord.dev` (the Pycord fork is the
> actively-maintained line; the API surface ScrapCode uses — `commands.Cog`,
> `tasks.loop`, `app_commands` — is the same in both). Pin the installed version
> (`requirements.txt`) when behavior matters.

## Gap closure (architecture-review baseline)

| Gap | Status | Closed by |
|-----|--------|-----------|
| 1 — data layout | ✅ Closed | [brief §4](brief.md#4-data-layout--storage-_closes-gap-1--decision-form-in-adr-002), [data-dictionary.md](data-dictionary.md), [ADR-002](adr-002-storage-backend-json-legacy.md) |
| 2 — runtime model | ✅ Closed | [brief §2](brief.md#2-runtime-model-_closes-gap-2) (incl. [§2.5](brief.md#25-external-calls--tacticus-direct-vs-chronicler-_closes-gap-2--feeds-adr-003), [§2.6](brief.md#26-deployment--runtime-target)) |
| 3 — multi-tenancy | ✅ Closed | [brief §3](brief.md#3-multi-tenancy-_closes-gap-3--decision-form-in-adr-004), [ADR-004](adr-004-multi-tenancy-isolation.md) |
| 4 — Chronicler API contract | ⏸ Deferred | gates integration roadmap; own kickoff brief |
| 5 — conventions | ✅ Closed | [brief §5](brief.md#5-conventions-_closes-gap-5) |
| 6, 7, 9 — versioning/feature-log + remaining | ⏸ Deferred | fold into first delivery features |
| 8 — seed ADRs | ✅ Closed | [ADR-001](adr-001-permission-checks-single-source.md)…[ADR-005](adr-005-permission-model-tiers-bypass.md) |

DESIGN wave decision summary: [wave-decisions.md](../../feature/scrapcode-architecture-baseline/design/wave-decisions.md).