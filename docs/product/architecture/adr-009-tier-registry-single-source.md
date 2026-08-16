# ADR-009: Tier registry as a single source; stored keys frozen

- **Status:** Accepted
- **Date:** 2026-08-15
- **Feature:** `dynamic-tier-registry`
- **Supersedes:** nothing. **Amends:** [brief.md §4.5](brief.md#45-per-season-hitbomb-files-trackerpy) and [data-dictionary.md](data-dictionary.md) §2.5/§2.9, both of which document the tier key set as closed.
- **Related:** [ADR-001](adr-001-permission-checks-single-source.md) (single-source precedent), [ADR-007](adr-007-repo-read-methods-get-guild-data-path-deprecation.md) (ABC growth pattern), [ADR-008](adr-008-guild-key-identity-binding.md) (chokepoint precedent)

## Context

Tacticus shipped a **Mythic 3** raid tier. `bot/tracker.py::get_tier_key` returns
`None` for `rarity="Mythic", set=2`, and `process_api_response` discards any
entry whose key is `None` ([tracker.py:110-111](../../../bot/tracker.py#L110)).
Every Mythic 3 hit has been silently dropped since the tier went live, and the
operator has confirmed the loss. It is irreversible: the Tacticus guild-raid
endpoint serves a rolling window, not season history.

Tier knowledge is currently duplicated across two files that must agree and
nothing checks that they do:

| Location | Owns | Shape |
|---|---|---|
| [config.py:22-30](../../../config.py#L22) | labels, values, order | hand-written `app_commands.Choice` list |
| [tracker.py:5-25](../../../bot/tracker.py#L5) | payload → key parse rule | hardcoded `if tier == 0 / == 1` |

The visible residue of that duplication is an undocumented off-by-one: the key
`Mythic_1` displays as "Mythic 2", and `Legendary_0` as "Legendary 1".

This is the third occurrence of a pattern this codebase has twice decided
against. ADR-001 made `bot/permissions.py` the only place permission checks
live. ADR-008 made `bot/guild_keys.py` the single key-policy chokepoint. Tier
knowledge is the same class of concept — a rule that several modules need and
that breaks quietly when they disagree.

## Decision

### D1 — `bot/tiers.py` is the single source of tier truth

One module owns four things: the parse rule (payload → stored key), the label
rule (stored key → display label), the ordering rule, and an override table for
tiers the game names irregularly.

`config.TIER_CHOICES` becomes derived. `tracker.get_tier_key` delegates.

**The module is pure.** It imports nothing from `discord`, nothing from
`config`, and nothing from `bot.guilds` / `bot.repository*`. It is a rule table
and four functions, unit-testable with no fixtures, no event loop, and no
database. The three import prohibitions are enforced (see D8) and each has a
specific reason:

| Prohibition | Reason |
|---|---|
| no `discord` | keeps the rules testable without an event loop, and lets `config.py` build `Choice` objects from a pure list |
| no `config` | cycle guard — `config.py` imports `bot.tiers`, so the reverse would close a loop |
| no `bot.guilds` / `bot.repository*` | policy must not depend on storage — the same direction ADR-008 §G pins for `bot/guild_keys.py` |

### D2 — The parse rule generalises the `set` bound, not the rarity allow-list

`set` becomes unbounded (`>= 0`) for both tracked rarities. `rarity` stays a
closed allow-list of `{Legendary, Mythic}`.

The asymmetry is deliberate. Removing the `set` bound fixes a bug. Removing the
rarity allow-list would begin ingesting Common/Uncommon/Rare/Epic raid hits —
far higher volume, never previously tracked, and a change to what the
leaderboard *means*. That is a product decision and must not be made as a side
effect of a parser fix. An unrecognised rarity is instead counted and named
(D5), so adopting one later is a one-line allow-list change.

### D3 — Stored tier keys are frozen; labels are derived

| Stored key (frozen) | Derived label |
|---|---|
| `Legendary_0` … `Legendary_4` | Legendary 1 … Legendary 5 |
| `Mythic` | Mythic 1 |
| `Mythic_1` | Mythic 2 |
| `Mythic_2` | Mythic 3 |

Rule: `Legendary_{n}` → `Legendary {n+1}`; bare `Mythic` → `Mythic 1`;
`Mythic_{n}` → `Mythic {n+1}`. Overrides win over derivation.

Three consequences, and the third is why this is locked rather than preferred:

1. **No Alembic revision.** `battle_hits.tier_key` and `bomb_hits.tier_key` are
   untouched. Both participate in a `UNIQUE` constraint
   ([models.py:192](../../../bot/db/models.py#L192),
   [models.py:222](../../../bot/db/models.py#L222)), so a rewrite would have to
   be ordered to avoid transient collisions — cost with no user-visible benefit.
2. **`live_leaderboards.messages` mappings survive.** That dict is keyed by tier
   *value* ([data-dictionary.md:179](data-dictionary.md#L179)). Renaming values
   would orphan every existing live-board message mapping, and the symptom would
   be boards silently ceasing to update.
3. **Replay rows survive.** `/upload_replay` stores `tier.name` — the display
   *label* ([replay_cog.py:208](../../../bot/cogs/replay_cog.py#L208)) — and
   rendering filters against `[t.name for t in TIER_CHOICES]`
   ([replay_cog.py:54](../../../bot/cogs/replay_cog.py#L54)). Any label change
   drops historical replays out of `/get_replay` while leaving them in the
   database. Because derivation reproduces today's labels byte-for-byte, this
   risk goes to zero rather than being managed.

**Accepted cost:** raw `sqlite3` inspection still shows `Mythic_1` for a row
displayed as "Mythic 2". The skew now lives behind one documented function
instead of being a fact humans must remember across two files.

### D4 — `Tier` is structurally compatible with `app_commands.Choice[str]`

A frozen dataclass exposing exactly `.value` (stored key) and `.name` (display
label) — the two attributes existing code reads.

A DESIGN-wave audit found **26 raid-tier `.name`/`.value` reads across five
modules**, not the three in `embeds.py` that DISCUSS assumed:

| Module | Raid-tier reads |
|---|---|
| `bot/cogs/tasks_cog.py` | 11 |
| `bot/cogs/view_cog.py` | 6 |
| `bot/cogs/admin_cog.py` | 6 (of 8 `tier.` reads — 2 are permission tiers) |
| `bot/embeds.py` | 5 |
| `bot/cogs/replay_cog.py` | 1 |

Structural compatibility is therefore not a convenience, it is the only shape
that makes the Slice 04 autocomplete migration tractable: all 26 sites keep
working unmodified. Naming a domain dataclass's fields `name` and `value` is a
concession to that compatibility and is recorded as such.

### D5 — Divergence reporting is a standing condition, not a first-sighting event

Two report lines, both derived per cycle from that cycle's data, both
self-clearing, neither requiring persisted state:

| Line | Meaning |
|---|---|
| `⚠️ N entries skipped — {reason}: {n} ({detail})` | data **not** stored |
| `📥 Captured but not displayable: {key} — {n} hits` | data stored, unreachable from any picker |

Skip reasons are separable and never collapsed: `untracked_rarity`,
`malformed_set`, `unparseable`. Collapsing `untracked_rarity` into
`malformed_set` would hide a vendor schema change behind a counter that is
routinely non-zero — the same reasoning ADR-008 D6 applies to keeping transport
failure distinct from identity mismatch.

**This supersedes the DISCUSS acceptance criteria AC-002.4/AC-002.5**, which
specified a first-observation announcement fired exactly once. Announce-once
requires persisted per-(server, season, tier) state — a new column or table —
for an event that is self-clearing by construction: after D1/D3 land, a captured
tier is immediately displayable, so "captured but not displayable" becomes
structurally impossible rather than merely resolved. Paying for state to
de-duplicate a condition that cannot persist is the wrong trade. Recorded as an
upstream change for product-owner review.

### D6 — Ingest reporting flows by return value, not by logging from `tracker`

`process_api_response` returns an `IngestReport` (per-reason counts plus the set
of tier keys written). `bot/tracker.py` stays pure — it has no logging import
today and gains none. `tasks_cog._update_one_guild` folds the report into the
existing `results` list and into `_CycleReport`; `update_cog` renders it into
its command response.

The alternatives were a collector parameter (couples tracker to a reporting
type) or module-level counters (global mutable state, untestable per-guild).
The return value is the smallest change and the only one that keeps `tracker`
free of I/O.

Callers that ignore the return value continue to work, so this is not a
breaking signature change.

### D7 — Live-board reconciliation is an additive branch, keyed on tier value

`_refresh_live_leaderboards`'s same-season path currently skips any tier lacking
a stored `message_id` ([tasks_cog.py:663-666](../../../bot/cogs/tasks_cog.py#L663)).
It gains a reconciliation step: any registry tier with no stored id gets a
message sent and persisted, in registry order.

**Additive only.** A tier absent from the registry keeps its message, frozen; no
delete is ever issued. Automatic deletion of a live board driven by a
vendor-shaped input contradicts the operator's recorded anti-goal that a problem
should stop and wait for a human
([cluster-admin.yaml:69-72](../personas/cluster-admin.yaml#L69)).

Idempotence is keyed on tier value. The rollover branch continues to own
full-set creation; reconciliation is a no-op after a rollover in the same cycle.

### D8 — Architecture enforcement

Extends the rule sets in brief §I (`sqlite-backend`) and §G
(`guild-key-integrity`):

- `bot/tiers.py` MUST NOT import `discord`, `config`, `bot.guilds`, or
  `bot.repository*`.
- The literals `"Mythic_"`, `"Legendary_"`, `"Mythic"` as a tier key, and any
  `app_commands.Choice` carrying a tier label MUST appear only in
  `bot/tiers.py` and its tests.
- **Name-collision guard.** `tier` names two unrelated concepts in this
  codebase: raid tiers and permission tiers. `fun_cog.py` (4 sites) and
  `admin_cog.py:734,736` read `tier.value` as a **permission** tier
  (`member`/`officer`/`admin`). The enforcement rule above MUST exempt those
  sites explicitly by path, and any mechanical `tier.value` refactor MUST NOT
  touch them. This is a live footgun: a global rename would break
  `/scrapcode_help` and `/config_role_tier` silently, since both would still
  type-check.

The command option remains named `tier:` in both senses. Renaming the raid-tier
option to `raid_tier:` was considered and rejected — it is user-visible churn
for officers who use these commands daily, to fix a hazard that only affects
agents editing the code. The enforcement exemption is the cheaper guard.

### D9 — `list_tier_keys` joins the ABC in Slice 04 only

Autocomplete needs the distinct tier keys present in stored data. That is a new
read method on `ClusterRepository`, following the ADR-007 pattern exactly (ABC
grows a read method, both adapters implement it).

It is scoped to Slice 04 deliberately. Slices 01–03 require **no** repository
change at all, which is what keeps Slice 01 at half a day and independently
revertible.

## Consequences

- **Slice 01 is confined to a pure function plus counters.** No schema change,
  no ABC change, no migration, no new module. It reverts cleanly and nothing
  above it depends on it.
- **Capture survives every rollback above it.** Reverting Slices 02–04 leaves
  Slice 01's capture running. This falls out of the slice ordering and is the
  main practical argument for it.
- **`bot/tiers.py` becomes the third single-source module**, after
  `bot/permissions.py` (ADR-001) and `bot/guild_keys.py` (ADR-008). The pattern
  is now established enough to be a convention rather than three coincidences,
  and brief §5.1 is updated to say so.
- **brief §4.5 and data-dictionary §2.5/§2.9 become stale on merge.** All three
  document the tier key set as the closed enumeration `Legendary_0..4`,
  `Mythic`, `Mythic_1`. They are amended by this ADR to describe it as open.
- **The off-by-one survives** in stored data. Anyone reading the database
  directly still needs to know that `Mythic_1` means "Mythic 2".

## Outcome collision check

`nwave-ai outcomes check-delta` exits `0` but reports *"1 outcomes checked, 0
collisions found across **0 outcomes**"* — the five `OUT-1..OUT-5` rows in
[registry.yaml](../outcomes/registry.yaml) were not loaded, so the pass is
vacuous. This is the same class of tooling gap that file's own header comment
records for `outcomes register`. Checked manually instead:

| Existing | Candidate | Verdict |
|---|---|---|
| OUT-1 — classify a guild-key reading into 5 `ProbeOutcome`s | classify a raid entry into stored / 3 skip reasons | **Related, not duplicate.** Same classification *shape*, disjoint inputs (guild API response vs raid entry) and outputs. Link `related: [OUT-1]`. |
| OUT-2 — `api_key` read only at the chokepoint | tier literals appear only in `bot/tiers.py` | **Related, not duplicate.** Both are single-source architecture invariants over different concepts. Link `related: [OUT-2]`. |
| OUT-3, OUT-4, OUT-5 | — | No overlap. |

No genuine duplication. Two `related` links to be written when DISTILL registers
this feature's outcomes.

## Alternatives considered

**Normalise stored keys to `Mythic_0/1/2`.** Tidier, and removes the off-by-one
at source. Rejected on the three grounds in D3, decisively on the third: replay
rows are keyed by display label, so relabelling silently drops history from
`/get_replay`. That risk only became visible after reading `replay_cog.py`.

**Host the tier rules in `config.py`.** Zero new modules, and `TIER_CHOICES`
stays where readers expect it. Rejected: `config.py` is a constants module that
already performs `load_dotenv()` and reads env vars at import time; adding a
parse rule consumed by `bot/tracker.py` would make the ingest path depend on a
module with import-time side effects, and would prevent the no-`discord` purity
rule that makes the tier rules cheaply testable.

**Host them in `bot/getNameAndEmoji.py`.** The nearest existing "raw identifier →
display string" module, so the overlap is real and was examined. Rejected on
mechanism and coupling: that module does keyword *substring* matching over
`unitId`s with no parse step, no ordering, and no overrides, and it changes
every time the game adds a unit. Tier rules are exact, ordered, and change
yearly. Merging them would make `config.py` — imported by every cog — depend on
a display-asset module on a completely different change cadence.

**Announce a new tier exactly once (as DISCUSS specified).** Rejected per D5:
requires persisted state for a condition that is self-clearing by construction.

**Ship `bot/tiers.py` as its own slice.** Rejected in DISCUSS by the
slice-composition gate — an infrastructure-only slice has no user-visible value
story. It lands as Slice 02's precursor commit instead.
