# DESIGN Decisions — scrapcode-architecture-baseline

> **Brownfield, documentation-only baseline.** No DISCUSS or SPIKE artifacts exist
> by design; this feature enters the wave sequence at DESIGN. "Missing upstream
> wave" warnings are expected graceful degradation, not defects.
> **Branch:** `docs/architecture-baseline`. No commits to `main`.

## Key Decisions

- [D1] **Scope = application/components.** ScrapCode is a single-process discord.py
  bot, not a distributed system; system- and domain-architect scopes intentionally
  left empty. (see: `docs/product/architecture/brief.md` §0)
- [D2] **Read-only pass; zero behavior changes.** Every gap is documented as-built.
  Known bugs (non-atomic JSON writes, silent-empty-on-corruption reads, multi-tenancy
  leaks) are recorded and explicitly left unfixed. (see: brief §4.8, §3.2)
- [D3] **JSON storage is legacy / migration source; SQLite is the accepted
  successor** — pinned as ADR-002. (see: `adr-002-storage-backend-json-legacy.md`)
- [D4] **Chronicler-first data doctrine** with an enumerated allow-list of
  direct-Tacticus calls — pinned as ADR-003. (see: `adr-003-...md`)
- [D5] **Permission checks live only in `permissions.py`;** tier model + Discord-admin
  bypass pinned as ADR-001 + ADR-005. (see: `adr-001-...md`, `adr-005-...md`)
- [D6] **Multi-tenancy isolation rules + flagged leaks** pinned as ADR-004.
  (see: `adr-004-...md`, brief §3.2)
- [D7] **README / `HELP_DATA` drift is flagged, not resolved.** Code is the source
  of truth for this baseline; README and in-bot help are hand-maintained in parallel.
  (see: brief §6)

## Architecture Summary

- **Pattern:** modular monolith (single process, no ports-and-adapters formalization;
  `ClusterRepository` ABC is the one seam). One `asyncio.Lock` for write
  serialization around `process_api_response` only.
- **Paradigm:** OOP (dataclasses + cogs + a repository/service split). Not written
  to project CLAUDE.md — out of scope for a docs-only baseline.
- **Key components:** 9 cogs (`bot/cogs/*`), `tasks_cog` background loops
  (`cap_detect`, `auto_update` + live-LB refresh), `bot/permissions.py`,
  `bot/guilds.py` + `bot/repository.py` (JSON, per-server), `bot/tracker.py`
  (top-N merge), `bot/embeds.py` + `bot/getNameAndEmoji.py` (rendering),
  `bot/services/chronicl3r/` (identity).
- **External systems:** Tacticus (raid/tokens/roster/season, httpx async),
  Chronicler (profiles, requests sync via `to_thread`), local filesystem.

## Reuse Analysis

No new components were created — this is a documentation baseline. The table is
recorded for the future SQLite migration that this baseline enables:

| Existing Component | File | Overlap with future work | Decision | Justification |
|--------------------|------|---------------------------|----------|---------------|
| `ClusterRepository` (ABC) | `bot/repository.py` | SQLite backend lands here | EXTEND | ABC already defines the full interface; a `SqliteClusterRepository` is a second impl behind the same seam. |
| `PlayerListMigrator` | `bot/migrations/player_list_migrations.py` | JSON→SQLite data migration | EXTEND | Chained-version migrator pattern is the place to add a v2→SQLite step. |
| `to_cluster_layout.py` | `bot/migrations/to_cluster_layout.py` | Structural data migration lineage | EXTEND | The SQLite migration joins this lineage rather than introducing a new migration framework. |
| `file_lock` | `main.py` (asyncio.Lock) | Write serialization under SQLite | RETIRE (at migration) | SQLite's own transactional writes replace the process-global lock. |

## Technology Stack

- **Python + discord.py**: as-built; entrypoint `main.py`. No change proposed.
- **httpx** (async) for Tacticus; **requests** (sync, `to_thread`) for Chronicler.
- **Storage: JSON files today** → **SQLite** accepted successor (ADR-002).

## Constraints Established

- No new top-level JSON files; no new silent-empty read helpers (ADR-002).
- No new direct-Tacticus calls outside the ADR-003 allow-list.
- All permission checks via `permissions.py` only (ADR-001/005).
- All data access threads `discord_server_id`; no cross-server joins (ADR-004).
- README/`HELP_DATA` are not sources of truth; flag contradictions, do not fix them.

## Upstream Changes

None. No DISCUSS/SPIKE artifacts exist to change; this baseline *establishes* the
assumptions future waves inherit.

## Gap Closure

| Gap | Status | Where |
|-----|--------|-------|
| 1 — data layout | **Closed** | brief §4; data-dictionary.md; ADR-002 |
| 2 — runtime model | **Closed** | brief §2 (incl. §2.5 external calls, §2.6 deployment) |
| 3 — multi-tenancy | **Closed** | brief §3; ADR-004 |
| 4 — Chronicler API contract | **Deferred** | brief §7 (gates integration roadmap; own kickoff brief) |
| 5 — conventions | **Closed** | brief §5 |
| 6, 7, 9 — versioning/feature-log + remaining | **Deferred** | brief §7 (fold into first delivery features) |
| 8 — seed ADRs | **Closed** | ADR-001..005 |

## Artifacts

```
docs/product/architecture/
  overview.md                                # summary + doc index + library reference + gap status
  brief.md                                   # SSOT — Gaps 1,2,3,5 + deployment + drift
  data-dictionary.md                         # per-entity fields, erDiagram, migration mapping
  c4-diagrams.md                             # context / container / TasksCog (plain Mermaid flowcharts)
  adr-001-permission-checks-single-source.md
  adr-002-storage-backend-json-legacy.md
  adr-003-chronicler-first-data-doctrine.md
  adr-004-multi-tenancy-isolation.md
  adr-005-permission-model-tiers-bypass.md
docs/feature/scrapcode-architecture-baseline/design/
  wave-decisions.md                          # this file
README.md                                    # repo-root pointer to the architecture docs
```