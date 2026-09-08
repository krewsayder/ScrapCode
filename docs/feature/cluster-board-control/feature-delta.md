# Feature Delta — `cluster-board-control`

> Single narrative file per the nWave lean-wave-documentation contract.
> Density: `lean` + `ask-intelligent` (DISCUSS hard default, Decision 4
> 2026-04-28). Tier-1 `[REF]` sections only; Tier-2 expansions are listed
> in the wave-end menu and rendered on request.

---

## Wave: DISCUSS / [REF] Retrofit Origin

**This wave ran AFTER the code shipped.** That is the first fact about this
feature and it shapes every section below.

On 2026-09-08 the operator asked a diagnostic question — "we added
functionality to flag or turn off leaderboards, I think we did this by
guild, but we didn't do anything for the cluster leaderboards" — and the
agent answered it by implementing a solution rather than by returning a
finding. Two slash commands, a schema migration, a repository change and
13 tests were merged before any wave artifact existed.

DISCUSS is therefore being retrofitted onto a shipped implementation. The
consequence is recorded honestly rather than smoothed over: **the shipped
storage representation does not match the pattern this project already
uses for the same problem at guild scope** (D3 below). The retrofit's job
is to state the intended design, and DELIVER's job is to bring the code
to it — not to ratify what happened to get written.

What the diagnostic question correctly established, and what survives:

| Finding | Verdict |
|---|---|
| A guild's board stops when its key is quarantined; the cluster board has no equivalent state | confirmed, and is this feature |
| `/set_live_cluster_leaderboard` merges quarantined guilds' stale rows unflagged | confirmed, **out of scope** — operator ruled the cluster board should cover the whole cluster |
| The cluster board freezes silently when no key can answer the season | confirmed, **out of scope this feature** — see Out of Scope, and the upstream note against `guild-key-integrity` |

---

## Wave: DISCUSS / [REF] Persona ID

**`guild-officer`** (primary for this feature) — holds the officer tier,
owns the live boards day to day, and is the tier that runs both new
commands. Persona SSOT created by this feature at
`docs/product/personas/guild-officer.yaml`; it was referenced as a
secondary persona by `jobs.yaml` and the `guild-key-integrity` journey
since 2026-07-31 but had no file.

**`cluster-admin`** (secondary) — the operator-developer who owns the
deployment. Not the primary here: the cluster board is an officer-tier
artifact both to create and to pause (D4).

---

## Wave: DISCUSS / [REF] JTBD One-Liner

- **`control-what-the-cluster-board-publishes`** — When the cluster
  leaderboard is showing something I do not want published, I want to
  stop it updating without tearing it down, so I can control the
  server's most-read artifact without losing the board or its history.

Opportunity score (ODI: `importance + max(importance − satisfaction, 0)`,
1–10 scale, scored by the operator):

| Job | Importance | Satisfaction | Opportunity |
|---|---|---|---|
| `control-what-the-cluster-board-publishes` | 7 | 0 | **14** |

Satisfaction is 0, not low: there is no mechanism of any kind. The only
lever that exists — re-running `/set_live_cluster_leaderboard` — is the
wrong one, because it overwrites the board with a fresh set of messages.

Importance is 7 rather than the 9 carried by
`trust-guild-data-provenance`: this is a control gap, not a
data-corruption incident. It clears the >10 build bar on the strength of
the satisfaction term.

Full dimensions and four-forces live in `docs/product/jobs.yaml`; the
Tier-2 `jtbd-narrative` expansion renders them inline on request.

---

## Wave: DISCUSS / [REF] Locked Decisions

### D1 — The switch is an operator action, never derived

**Verdict: LOCKED — manual only (operator selection, 2026-09-08).**

The cluster board already has an *implicit* off state: when no guild in
the cluster has a usable key, `_current_season` returns `None` and
[tasks_cog.py:289-302](../../../bot/cogs/tasks_cog.py#L289-L302) returns
before `_refresh_live_leaderboards` is reached. The board stops and says
nothing.

This feature does **not** flag that automatically. It adds the
deliberate, human-driven state and leaves the derived one alone.

Rationale is the persona's own stated anti-goal
([cluster-admin.yaml:68-72](../../product/personas/cluster-admin.yaml#L68-L72)):
"Does not want automatic key recovery or retry-until-it-works. A key
problem should stop and wait for a human, not resolve itself in a way
that might be wrong." An auto-pause is that same shape — the system
deciding, on the operator's behalf, that a board should stop publishing.

The silent freeze remains a real defect. It is named in Out of Scope
with a follow-up, not quietly absorbed.

### D2 — Off leaves the posted messages exactly as they are

**Verdict: LOCKED — no edit, no re-send, no delete (operator selection,
2026-09-08).**

Three options were put to the operator: leave untouched, edit once to add
a frozen banner, or delete. Untouched won.

The posted messages are the season's public record in that channel. A
pause that re-sends them destroys their position in channel history; a
pause that edits them rewrites content the channel has already reacted
to and linked. "Turned off" therefore means precisely: the hourly refresh
does not touch this board.

The cost is accepted and stated: **a paused board is visually
indistinguishable from a running one.** That is what makes AC-003 (the
`/view_config` status line) load-bearing rather than cosmetic — it is the
only surface where the difference is observable.

### D3 — Match the guild-level status representation

**Verdict: LOCKED — conform to `GuildBinding` / `key_status` (operator
selection, 2026-09-08). This OVERRIDES what shipped.**

The operator's instruction was "match whatever we had for the guild level
leaderboard behaviors". The guild-level off switch is `key_status`, and
its pattern is:

| Axis | Guild-level pattern (`GuildBinding`) | What shipped for the cluster board |
|---|---|---|
| Port shape | frozen dataclass, cogs never see the ORM row or a raw dict | raw `dict` |
| State field | explicit `key_status: str = "active"`, values from the `KeyStatus` enum | `enabled: bool` |
| Absence | a default **value** — `GuildBinding()` IS the unbound state, so no `None` check at seven call sites | a default **inferred at read** — `cfg.get("enabled", True)` |
| Written form | the field is always present | **omitted when true** |
| Predicate | one function reads the status | one function reads the status ✅ |
| JSON adapter | explicit no-op + loud startup warning (ADR-006 D9 / ADR-008 DDD-4) | silently full-fidelity |

The shipped `omitted-when-true` convention was invented for one reason:
to keep the exact-equality assertion at
[test_repository_contract.py:98-100](../../../tests/acceptance/sqlite-backend/test_repository_contract.py#L98-L100)
passing without amending it. That is a test-shaped reason, not a design
one, and it produced a hidden invariant no other part of this codebase
carries.

`GuildBinding`'s answer to the identical problem is better and is already
here: absence is modelled as a **default value**, normalised on load by
both adapters, so the field is always present to every reader and the two
backends still agree. The contract test literal is then amended because
the contract genuinely changed — which is legitimate, and is not the same
act as bending the production representation to avoid amending it.

**Consequence:** the shipped code is non-conforming and must be
refactored. Recorded as the Slice 01 precursor, not as a separate slice
(slice-composition gate).

### D4 — Officer tier, and the persona SSOT is corrected to match

**Verdict: LOCKED — officer (operator selection, 2026-09-08).**

Whoever can create the cluster board can pause it. Both new commands
match `/set_live_cluster_leaderboard`, which has been officer-tier since
before this feature.

[cluster-admin.yaml:62-66](../../product/personas/cluster-admin.yaml#L62-L66)
lists "Sets permission tiers, ping channels, and live leaderboards" under
`cluster-admin` decision rights. That is **already wrong** about the
existing command and predates this feature. The persona file is
corrected; the code is not.

### D5 — Scope-agnostic storage, cluster-only commands

**Verdict: LOCKED.**

The status field lives on `live_leaderboards` and therefore serves
`guild:{id}` rows as readily as `cluster`. Only the cluster board gets
commands, because only the cluster board was asked for.

A column that already covers both scopes costs nothing today and spares
a second migration when the guild-scoped commands arrive. The refresh
loop honours the status for every scope, which is asserted rather than
assumed (AC-004).

---

## Wave: DISCUSS / [REF] Pre-requisites

| Dependency | Why | Status |
|---|---|---|
| `sqlite-backend` | `live_leaderboards` + `live_lb_messages` tables, `ClusterRepository` ABC | shipped |
| `guild-key-integrity` | supplies the `GuildBinding` / `key_status` pattern D3 conforms to | shipped |
| Alembic head `0004` | the new revision chains from it | shipped |

---

## Wave: DISCUSS / [REF] Driving Ports

| Surface | Type | Tier | Status |
|---|---|---|---|
| `/disable_cluster_leaderboard` | Discord slash command | `officer` | **new** |
| `/enable_cluster_leaderboard` | Discord slash command | `officer` | **new** |
| `/view_config config:leaderboards` | Discord slash command | `officer` | extended — status line |
| `/set_live_cluster_leaderboard` | Discord slash command | `officer` | extended — re-enables |
| `auto_update` hourly loop | background task | — | extended — honours the status |
| `/scrapcode_help tier:officer` | Discord slash command | `officer` | extended — lists both commands |

Both new commands take **no parameters**. There is exactly one cluster
board per Discord server (`scope_key = "cluster"`, unique per
`discord_server_id`), so there is nothing to disambiguate.

---

## Wave: DISCUSS / [REF] WS Strategy

**Strategy C — no walking skeleton.**

Brownfield against a live production database with an established
end-to-end path (Discord → cog → `ClusterRepository` → SQLite) that this
feature only extends. Every seam is already exercised by the
`sqlite-backend` acceptance suite.

Mandate 5 note: the walking-skeleton obligation is discharged by
`docs/feature/sqlite-backend/distill/walking-skeleton.md`, which
established this exact path. Same discharge as `guild-key-integrity`.

---

## Wave: DISCUSS / [REF] Scope Assessment

**PASS — right-sized.** Elephant Carpaccio early gate:

| Oversized signal | Threshold | Actual | Fires? |
|---|---|---|---|
| User stories | >10 | 4 | no |
| Bounded contexts / modules | >3 | 2 (`cogs`, `db`+`repository`) | no |
| WS integration points | >5 | n/a (no WS) | no |
| Estimated effort | >2 weeks | ~1 day incl. the D3 refactor | no |
| Independent shippable outcomes | multiple | 1 | no |

Zero signals fire; two are required to trigger a split. **One slice**,
with a precursor commit.

---

## Wave: DISCUSS / [REF] Journey — Happy Path

Journey `cluster-board-control`, persona `guild-officer`. Schema at
`docs/product/journeys/cluster-board-control.yaml`.

| # | Step | Output the user sees |
|---|---|---|
| 1 | Officer decides the cluster board should stop publishing | — |
| 2 | Runs `/disable_cluster_leaderboard` | ephemeral: `⛔ The live Cluster leaderboard in #leaderboards has been turned off. The posted messages are left exactly as they are and will stop updating. Run /enable_cluster_leaderboard to resume.` |
| 3 | The next hourly cycle reaches the board and skips it | update channel: `🔄 Auto-update complete — Season 106` (unchanged); the board's messages are untouched |
| 4 | Officer confirms the state later | `/view_config config:leaderboards` → `Cluster · Status: ⛔ Turned off — messages frozen at the last update` |
| 5 | Officer resumes | ephemeral: `✅ The live Cluster leaderboard in #leaderboards will update again on the next hourly cycle.` |
| 6 | Next cycle refreshes it in place | the existing messages update; no new messages posted |

Emotional arc (lightweight — one label per step, monotonically
non-decreasing): `decisive → in control → unremarkable → reassured →
in control → unremarkable`.

Step 4 is the pivot. Because D2 leaves the messages untouched, the
channel gives the officer no signal at all — `/view_config` is the only
place the pause is observable, which is why it is a story and not a nice
-to-have.

---

## Wave: DISCUSS / [REF] Journey — Error Paths

| Failure | Detection | Recovery |
|---|---|---|
| No cluster board configured | `live` has no `cluster` key | refuse, naming `/set_live_cluster_leaderboard` as the way to create one. A status stored against a non-existent board would be silently discarded by the next setup, which rebuilds the config from scratch |
| Already in the requested state | stored status equals the request | report it, write **nothing**. `save_live_leaderboards` rewrites every board on the server, so a no-op that saved anyway is a whole-table rewrite triggered by a command that changed nothing |
| Season rolls over while paused | `stored_season != season` on resume | the board posts a **fresh** message set on resume; the paused season's messages stay as a frozen archive. `season` is deliberately not advanced while paused |
| `/set_live_cluster_leaderboard` run on a paused board | config is rebuilt from scratch | board comes back **on**. Setting one up is an unambiguous "I want this board"; carrying a stale pause across it would hand the officer a freshly-posted set of messages that then never update |
| Backend rolled back to JSON (`SCRAPCODE_REPO_BACKEND=json`) | startup probe | the status still round-trips — unlike `key_status`, the JSON adapter stores live-board configs natively, so this path is **not** degraded. Stated explicitly because the guild-level analogue *is* degraded (ADR-006 D9) and the asymmetry is otherwise surprising |
| Alembic upgrade runs against boards that exist | migration | every existing board is `active`. A schema change that pauses production boards is the outage the feature exists to prevent |

---

## Wave: DISCUSS / [REF] Shared Artifacts

| Artifact | Single source of truth | Consumed by |
|---|---|---|
| `${board_status}` | `live_leaderboards.board_status` column (`active` \| `disabled`) | the refresh loop's skip decision, `/view_config`, both new commands |
| `${channel_id}` | `live_leaderboards.channel_id` | refresh loop, command confirmations |
| `${messages}` | `live_lb_messages` rows | refresh loop — never rewritten by a pause (D2) |
| `${season}` | `live_leaderboards.season` | rollover detection — frozen while paused |

Column and enum names are a DISCUSS **recommendation**; DESIGN owns the
final schema. The constraints that matter: one source per artifact, and
the status is an explicit enum-valued field (D3), never an inferred
default.

---

## Wave: DISCUSS / [REF] User Stories

All stories trace to a `job_id` in `docs/product/jobs.yaml`. Every
non-`@infrastructure` story carries an Elevator Pitch.

### US-001 — Turn the cluster leaderboard off

`job_id: control-what-the-cluster-board-publishes` · Slice 01

As a **guild-officer**, I want to stop the cluster leaderboard updating
without deleting it, so that I can control what the server sees without
losing the board or the season's posted history.

**Elevator Pitch**
Before: The cluster board either refreshes every hour or freezes silently because no key could answer the season; the only lever an officer has is `/set_live_cluster_leaderboard`, which overwrites the board with a whole new set of messages.
After: run `/disable_cluster_leaderboard` → sees `⛔ The live Cluster leaderboard in #leaderboards has been turned off. The posted messages are left exactly as they are and will stop updating. Run /enable_cluster_leaderboard to resume.`
Decision enabled: The officer decides when the cluster stops seeing new numbers, and knows from the reply that the existing board is intact rather than destroyed.

**Acceptance criteria**
- AC-001.1 — Given a configured cluster board, when `/disable_cluster_leaderboard` is run by an officer, then the board's status is persisted as `disabled` and the reply names the channel and `/enable_cluster_leaderboard`.
- AC-001.2 — Given a disabled board, when the hourly cycle runs, then **zero** Discord calls are made against it: no message is edited, no message is sent (D2).
- AC-001.3 — Given a disabled board, when the hourly cycle runs, then the config is **not** removed — a pause is not a teardown. *(The loop already deletes configs whose channel has vanished; the skip must not land in that branch.)*
- AC-001.4 — Given a disabled board, when `channel_id`, `messages` and `season` are compared before and after the pause, then all three are unchanged.
- AC-001.5 — Given a disabled board, when the bot process restarts, then the board is still disabled. *(A status held only in the loaded dict is the same as not having the feature.)*
- AC-001.6 — Given no cluster board is configured, when the command is run, then it refuses, names `/set_live_cluster_leaderboard`, and writes nothing.
- AC-001.7 — Given an already-disabled board, when the command is run again, then it reports the existing state and `save_live_leaderboards` is **not** called.
- AC-001.8 — Given a caller below the officer tier, when the command is invoked, then it is refused by the standing tier check (D4).

---

### US-002 — Resume the cluster leaderboard

`job_id: control-what-the-cluster-board-publishes` · Slice 01

As a **guild-officer**, I want to restart the cluster board's hourly
updates, so that pausing it is a reversible decision rather than a
one-way door.

**Elevator Pitch**
Before: Nothing can pause the board, so nothing can resume it either; the only way back to a live board is to re-run setup and post a duplicate set of messages beneath the old ones.
After: run `/enable_cluster_leaderboard` → sees `✅ The live Cluster leaderboard in #leaderboards will update again on the next hourly cycle.`
Decision enabled: The officer knows updates are coming back at the next cycle and that the existing messages — not new ones — are what will move.

**Acceptance criteria**
- AC-002.1 — Given a disabled board, when `/enable_cluster_leaderboard` is run, then the status is persisted as `active` and the reply names the channel.
- AC-002.2 — Given a re-enabled board on the **same** season, when the next cycle runs, then the existing messages are edited in place and no new message is sent.
- AC-002.3 — Given a re-enabled board whose season rolled over while it was paused, when the next cycle runs, then a fresh message set is posted and the paused season's messages are left as a frozen archive.
- AC-002.4 — Given an already-running board, when the command is run, then it reports the existing state and writes nothing.
- AC-002.5 — Given a disabled board, when `/set_live_cluster_leaderboard` is run, then the new board is **active** — setup re-enables.

---

### US-003 — See which boards are turned off

`job_id: control-what-the-cluster-board-publishes` · Slice 01

As a **guild-officer**, I want `/view_config` to tell me which live
boards are paused, so that a board frozen weeks ago is not mistaken for
one that is simply quiet.

**Elevator Pitch**
Before: A paused board and a running board look identical in the channel — the messages sit there either way (D2), so there is no way to tell whether the numbers are live.
After: run `/view_config config:leaderboards` → sees `Cluster` · `Status: ⛔ Turned off — messages frozen at the last update` alongside the channel and tier count.
Decision enabled: The officer can tell at a glance whether the board they are reading is live, and decide whether to resume it before drawing conclusions from the numbers on it.

**Acceptance criteria**
- AC-003.1 — Given a disabled board, when `/view_config config:leaderboards` is run, then its field carries a status line stating it is turned off and that the messages are frozen.
- AC-003.2 — Given a running board, when the same command is run, then its field states it is updating hourly.
- AC-003.3 — Given any board, when the field renders, then the status line is present — it is never omitted for either state, because absence of a warning must not be the only signal that a board is healthy.

---

### US-004 — `@infrastructure` — Conform the status to the guild-level pattern

`job_id: control-what-the-cluster-board-publishes` · **Slice 01
precursor — not a separately-shipped slice**

As the **codebase**, I want the live board's status to use the same
representation as `key_status`, so that the project has one way of
modelling "this thing is switched off" rather than two.

No Elevator Pitch: this story produces **no** user-visible change. Per
the slice-composition gate it cannot be a slice of its own, and it is
scheduled as a precursor commit that lands before Slice 01 is considered
done (see the slice brief).

**Acceptance criteria**
- AC-004.1 — Given the port boundary, when a cog reads a live board config, then it receives a frozen dataclass, not a raw `dict`. *(Matches `GuildBinding`; cogs never see the ORM row.)*
- AC-004.2 — Given the status field, when it is written by any path, then its value comes from a named enum with `active` / `disabled` members. *(Matches `KeyStatus`.)*
- AC-004.3 — Given a stored config with no status recorded — every board that exists today — when it is loaded through **either** adapter, then the default `active` value is materialised on the way out, so no reader infers it. *(Matches `load_guild_binding` returning `GuildBinding()` rather than `None`.)*
- AC-004.4 — Given the JSON and SQLite adapters, when the same config is saved and reloaded through each, then the two results are equal. *(The parity contract holds through the normalised default rather than through a conditionally-omitted key.)*
- AC-004.5 — Given the refresh loop, when it decides whether to skip a board, then the decision is read through exactly one predicate. *(Already true of the shipped code; pinned so the refactor cannot lose it.)*
- AC-004.6 — Given the status is scope-agnostic, when a `guild:{id}` board carries `disabled`, then the refresh loop skips it identically (D5).

---

## Wave: DISCUSS / [REF] Story Map

**Backbone:** Configure a live board → **Control whether it publishes** →
Read it.

This feature owns the middle activity only.

| Slice | Stories | Ships | Learning hypothesis |
|---|---|---|---|
| *precursor* | US-004 `@infrastructure` | commit, not a release | — (structural; carries no hypothesis by design) |
| 01 | US-001, US-002, US-003 | end-to-end, ≤1 day | Disproved if an officer cannot tell a paused board from a live one — which would mean D2's "leave the messages alone" is unworkable without the banner that was rejected |

**Carpaccio taste tests:**

| Test | Result |
|---|---|
| Slice ships 4+ new components? | No — 2 commands + 1 status line, on an existing table |
| Every slice depends on a new abstraction? | Yes — and the abstraction ships FIRST, as the precursor, exactly as the test prescribes |
| Does any slice disprove a pre-commitment? | Yes — Slice 01 tests D2 directly. If the pause turns out to be undetectable in practice, D2 is wrong |
| Synthetic data only? | No — the AC set runs against a configured board in the operator's live cluster |
| 2+ slices identical except for scale? | No — one slice |

All taste tests pass.

**Prioritisation:** precursor first, on learning-leverage grounds — it is
the highest-uncertainty work (it touches the repository parity contract),
so discovering a problem there before the commands are built on top of it
costs the least. Dogfood moment: the operator pauses the real cluster
board the same day and checks `/view_config`.

---

## Wave: DISCUSS / [REF] Outcome KPIs

| # | KPI | Target | Gate | Measured by |
|---|---|---|---|---|
| KPI-1 | Discord calls made against a paused board per cycle | **0** edits, **0** sends | HARD | `test_a_board_that_is_off_is_left_completely_alone` |
| KPI-2 | Paused boards that survive a process restart | **100%** | HARD | `test_the_switch_survives_the_sqlite_round_trip` |
| KPI-3 | Paused boards distinguishable from running ones without reading logs | **100%** — status line present for both states | HARD | AC-003.3 |
| KPI-4 | Boards paused as a side effect of the schema migration | **0** | HARD | migration asserts `active` for every pre-existing row |
| KPI-5 | Adapter pairs disagreeing on a round-tripped config | **0** | HARD | `test_every_abc_method_round_trips_through_both_impls` |

Every HARD KPI names an executable assertion, per the `gate_classes`
convention in `docs/product/kpi-contracts.yaml` — a hard gate with no
assertion behind it is a soft gate with a firm adjective.

---

## Wave: DISCUSS / [REF] Definition of Ready

| # | Item | Evidence |
|---|---|---|
| 1 | Business value articulated | JTBD one-liner + opportunity 14 |
| 2 | User stories written | US-001…US-004 |
| 3 | Acceptance criteria testable | 22 ACs, each naming a state and an observable |
| 4 | Dependencies identified | Pre-requisites table — all shipped |
| 5 | Job traceability | all 4 stories → `control-what-the-cluster-board-publishes` |
| 6 | Elevator pitches | US-001/002/003; US-004 is `@infrastructure` and labelled |
| 7 | Scope assessed | PASS — 0 of 5 oversized signals |
| 8 | Outcome KPIs measurable | 5 KPIs, all HARD, each naming its assertion |
| 9 | Slice composition valid | 1 slice, contains 3 value stories; the `@infrastructure` story is a **precursor commit**, not a slice |

**DoR: PASS (9/9).**

Requirements completeness: **0.96** — the one open item is the
`board_status` enum member naming, which DESIGN owns (Shared Artifacts
notes it as a recommendation).

---

## Wave: DISCUSS / [REF] Out of Scope

| Excluded | Why | Follow-up |
|---|---|---|
| Auto-flagging the silent freeze when no key can answer the season | D1 — the operator's anti-goal is systems that decide on their own | Named below as an upstream gap; deserves its own feature |
| Guild-scoped `/disable_guild_leaderboard` | Not asked for. Storage covers it (D5); only commands are absent | Trivial once wanted — no migration needed |
| Flagging quarantined guilds' stale rows inside the cluster board | Operator ruled the cluster board should cover the whole cluster | Closed, not deferred |
| A banner on the paused board | D2 — explicitly rejected in favour of leaving messages untouched | Revisit only if Slice 01's hypothesis is disproved |
| Deleting / tearing down a board | Different job. `/set_live_cluster_leaderboard` already replaces one | — |

---

## Wave: DISCUSS / [REF] Upstream Changes

### Changed assumption — `guild-key-integrity` journey, `all-guilds-quarantined`

**Original** (`docs/product/journeys/guild-key-integrity.yaml:129-133`,
locked 2026-07-31):

> ```
> - id: all-guilds-quarantined
>   trigger: no usable key remains for season detection
>   detection: every guild quarantined
>   recovery: skip server with an explicit reason, not a silent continue
> ```

**New assumption:** that recovery is honoured by the *ingestion cycle*
and **not** by the *live cluster board*. When the season cannot be
resolved, [tasks_cog.py:289-302](../../../bot/cogs/tasks_cog.py#L289-L302)
returns before `_refresh_live_leaderboards` is called, so the board keeps
displaying its last refresh with no signal — the silent-continue shape
the decision forbids, one altitude up.

**Rationale for recording rather than fixing:** D1 scopes this feature to
the deliberate switch only. The gap is real and pre-existing; absorbing it
here would double the feature and mix a manual control with an automatic
one. The prior journey file is **not** modified — this note is the
back-propagation record, and the fix needs its own DISCUSS.

### Corrected — `cluster-admin` decision rights

`docs/product/personas/cluster-admin.yaml` claimed live leaderboards as a
`cluster-admin` decision right. That already misdescribed
`/set_live_cluster_leaderboard` (officer-tier since before this feature).
Corrected under D4.

### Created — `guild-officer` persona

Referenced as a secondary persona by `jobs.yaml` and the
`guild-key-integrity` journey since 2026-07-31 with no backing file.
Created by this feature, which makes it the primary persona.

Fields that would require interview evidence are marked `unvalidated`
rather than invented, and carry `open_questions`. An unvalidated field is
a question for a future DISCOVER wave; a fabricated one is a wrong answer
that looks like a right one.

### Fixed — `cluster-admin.yaml` was not valid YAML

Found by this wave's Prior Wave Consultation, which parses every SSOT
file rather than only reading it.

```
  - "Registering a guild is a one-time setup action." Was true until a key moved.
```

A double-quoted scalar with trailing text. **The file has been
unparseable since it was created on 2026-07-31**, which means no tool has
ever read the persona SSOT as data — every consumer to date has been a
human or an LLM reading it as prose, and neither notices. Rewritten as a
block scalar with the original text preserved verbatim.

This is worth more than the one-line fix: it says the SSOT has no parse
gate. Recommended follow-up for DEVOPS — a `yaml.safe_load` over
`docs/product/**/*.yaml` costs nothing and would have caught this on the
day it landed.

### Aliased — `bot-operator-dev` → `cluster-admin`

`jobs.yaml`'s `preserve-data-integrity-through-backend-swap` names a
primary persona `bot-operator-dev`, coined by the `sqlite-backend`
bootstrap before any persona file existed. It refers to the same person
as `cluster-admin`.

Recorded as an `aliases` entry on `cluster-admin.yaml` rather than
rewritten in place: editing another feature's traceability record to fix
a naming drift loses the fact that the drift happened. With the alias,
every persona reference across the SSOT now resolves.

---

## Wave: DISCUSS / [REF] Handoff

**To:** `nw-solution-architect` (DESIGN — full artifact set) and
`nw-platform-architect` (DEVOPS — Outcome KPIs only).

**DESIGN owns:**
1. The `board_status` column and enum shape — DISCUSS recommends, DESIGN decides.
2. The `LiveBoardConfig` dataclass boundary: which methods on `ClusterRepository` change signature, and whether `load_live_leaderboards` returns `dict[str, LiveBoardConfig]` or gains a sibling method.
3. Whether the `0005` migration already in the tree is amended in place or superseded — it ships `enabled BOOLEAN`, and D3 calls for an enum-valued status.

**Open question carried forward:** the shipped migration `0005` is in the
working tree but **not yet deployed**. If it is still undeployed when
DESIGN runs, amending it in place is cleaner than chaining a `0006` that
rewrites a column nobody has.

---
