# Evolution — guild-key-integrity

> **Status:** Feature complete. Slice 01 shipped under `enforce=False`; the
> operator-owned 2026-08-02 decision is that 05-01 flips the caller to
> `enforce=True` and inverts the slice-01 intermediate-state tests (UD-11).
> **Dates:** 2026-07-29 → 2026-08-02.

## What happened

A Tacticus API key belongs to a player, not a guild. When a key-holder
changes guild the key keeps working and returns the *other* guild's data.
On ~2026-07-28 this produced ~72 hours of one guild's data written under
another's identity: season 106 saw 30/30 battle + 20/20 bomb rows written
off-roster, and 60 of 67 `players` rows were corrupted. The bot had no
provenance guard on guild keys — any key was trusted to belong to whichever
guild it was configured against.

This feature adds that guard. A probe folded into the roster fetch records
the identity each key actually returns; a binding store makes that identity
the source of truth; a single chokepoint (`bot/guild_keys.py`) becomes the
only sanctioned reader of a guild `api_key`; and on mismatch the bot
quarantines the guild (blocks roster *and* hits) without stopping the server.

## What shipped (all three slices, one guardrail)

- **Slice 01 — bind + report.** Identity probe folded into the roster fetch
  (`bot/services/tacticus/guild_client.py`). Binding store
  (`guild_key_bindings` table, 1:1 with `guilds`). The `bot/guild_keys.py`
  chokepoint — the only sanctioned reader of a guild `api_key`. All seven
  key-consumption sites routed through it. `/view_config` shows the bound
  identity. Reports drift, blocks nothing.
- **Slice 02 — `/update_guild_key`.** The only exit from quarantine. Admin
  tier, probe-before-store, `force` parameter (not a confirmation View —
  KPI-6 leak surface), ephemeral, no key value echoed. `replace_guild_key`
  writes `api_key` + `api_key_hmac` in one transaction without CASCADE
  (AC-003.2).
- **Slice 03 — enforce.** Quarantine on mismatch (DDD-6 — only a
  well-formed 200 with a different `guildId` quarantines;
  UNREACHABLE/UNVERIFIABLE/DEAD leave the binding untouched). Block roster
  AND hits (DDD-5). 24h alert suppression (`last_alerted_at`; suppressed
  alerts recorded, not dropped). Season-SPOF fall-through — one guild's
  quarantine does not stop the server (KPI-5). `/view_config` quarantine
  rendering. The Tier B `KeyStatusJourney` hypothesis state machine
  (`quarantine_is_never_a_trap`, `quarantined_guilds_never_write`).

Slice 01 alone detects drift but still writes the contaminated roster — it
does not meet the requirement. The 2026-08-02 operator decision was to ship
all three slices as one guardrail.

## The components

- `bot/services/tacticus/guild_client.py` — the roster fetch now carries an
  identity probe; `guildId` from the response is the provenance signal.
- `bot/guild_keys.py` — the chokepoint. The only sanctioned reader of a
  guild `api_key`. Seven key-consumption sites routed through it. Owns
  quarantine state and the `/view_config` identity label (the duplicated
  rendering's right home — open item).
- `guild_key_bindings` table — 1:1 with `guilds`. `bound_guild_id` is the
  trusted identity; `last_probe_ok_at` drives KPI-1 detection latency;
  `last_alerted_at` drives 24h suppression.
- `bot/repository.py` / `bot/repository_sqlalchemy.py` — three new
  `ClusterRepository` methods for the binding store. No CASCADE on key
  replace (AC-003.2).
- `bot/cogs/admin_cog.py` — `/update_guild_key` (the quarantine exit) and
  `/view_config` quarantine rendering.
- No new external integration. The Chronicler package no longer makes any
  Tacticus call.

## Decisions (ADR-008)

See `docs/product/architecture/adr-008-guild-key-integrity.md`. Headlines:
uuid-only binding, not the human key (D1); probe folded into the roster
fetch, no separate call (D2); single chokepoint for all key reads (D3);
separate binding table, not a column on `guilds` (D4); block roster AND
hits on mismatch (D5); transport failure ≠ mismatch — only a
well-formed 200 with a different `guildId` quarantines (D6); season
fall-through so one quarantine does not stop the server (D7);
trust-on-first-use for the first binding (D8); `force` parameter, not a
confirmation View (D9).

Two operator decisions are load-bearing and not in the ADR:

1. **Ship all three slices as one guardrail** (2026-08-02). Slice 01 alone
   detects drift but still writes the contaminated roster; it does not meet
   the requirement that a key-bound-to-another-guild be unable to write.
2. **05-01 flips the caller to `enforce=True` and inverts the slice-01
   intermediate-state tests** (UD-11, 2026-08-02). Slice 01 shipped under
   `enforce=False` so the probe-and-report path could be observed in
   production before enforcement began.

## Verification at close

- 17 roadmap steps, all COMMIT/PASS, on the 3-phase TDD canon (ADR-025).
- DES integrity exit 0. Final bar: 246 passed / 0 failed.
- 6 import-linter contracts kept.
- Adversarial review APPROVED — all 10 load-bearing claims CLEAN.
- Mutation testing: `pre-release` per `CLAUDE.md` (no mutation tool in the
  stack today).

## KPIs

From `docs/product/kpi-contracts.yaml`:

- **KPI-1 — detection latency:** `alerted_at − last_probe_ok_at`. The
  probe runs on every roster fetch, so latency is bounded by the fetch
  cadence, not a separate sweep.
- **KPI-2 — contaminated rows:** target 0. Baseline from the incident:
  50 hits + 60 players corrupted. Quarantine (Slice 03) is what drives
  this to 0 — Slice 01's report alone does not.
- **KPI-3 — wall-clock to replace a key:** `/update_guild_key` with
  `force`, one-transaction write, ephemeral response. No key value
  echoed.
- **KPI-5 — blast-radius:** % of guilds surviving a sibling's quarantine.
  Baseline 0% (a single bad key could corrupt the server's data); target
  100% (season fall-through — one quarantine does not stop the server).
- **KPI-6 — key-value leaks:** 0, by construction. `force` parameter, not a
  confirmation View; `key_ref` correlation only, never the key value.

## Known follow-ups

1. **UD-6 — empty-roster shape in unit tests only.** The empty-roster
   contract is asserted in unit tests; no integration path drives it.
   Promote to an integration scenario or document the gap.
2. **UD-10 — conftest name collision.** A shared conftest fixture name
   collides across the guild-key suite and neighbours. Rename or scope.
3. **UD-13 — Tier B mutating invariant.** The
   `KeyStatusJourney` mutating-invariant property is owned by the
   acceptance-designer; not yet wired into a property test harness.
4. **UD-14 — L1 dead code from the enforce flip.** When 05-01 flips
   `enforce=True`, the `enforce=False` branch in the caller becomes dead.
   Remove it as part of the 05-01 change.
5. **`SCRAPCODE_TACTICUS_CONTRACT_KEY` + re-recorded fixture.**
   Operator-owned. The contract fixture is re-recorded against a stable
   key; the env var names the key for re-record.
6. **By-value `repo` imports in admin_cog / tasks_cog.** The cogs import
   `repo` by value where the chokepoint wants the binding store by
   reference. Refactor to the chokepoint's expected seam.
7. **Duplicated identity-label rendering.** The `/view_config` identity
   label is rendered in two places. The right home is `bot/guild_keys.py`;
   deduplicate there.

## Commits (this feature)

On `feature/guild-key-integrity-slice-01`, branched from the
`sqlite-backend` baseline. Per-step TDD with DES-traced phases; the
`deliver/roadmap.json` + `deliver/execution-log.json` are gitignored by the
DES tooling and live on disk as the trace record.