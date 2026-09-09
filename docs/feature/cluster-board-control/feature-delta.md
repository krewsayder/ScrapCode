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

### US-005 — Find out when a board was paused, long after the fact

`job_id: control-what-the-cluster-board-publishes` · Slice 01

**Added 2026-09-08, after the Final Wave Review Gate.** Two reviewers found
the same gap from opposite ends — one that a paused board is invisible in the
channel, one that it is invisible in the logs — and neither could see the
other. Together they showed that `/view_config` was the only signal at *both*
altitudes, which is thinner than D2 assumed when it accepted the in-channel
invisibility. Operator decision to close it: record on state change.

As a **cluster-admin**, I want a board's pause and resume written to the
operator log, so that I can find out when a board stopped publishing without
having been in the room when it happened.

**Elevator Pitch**
Before: `/view_config` says a board is paused right now; nothing says since when, or by whom. A board paused two months ago is exactly the one nobody remembers pausing, and there is no record to reconstruct it from.
After: run `grep live_board.status.changed discord.log` → sees one JSON record per change, naming the board, the state it left and the state it entered.
Decision enabled: The operator can tell whether a board that looks stale was deliberately paused or has failed some other way — which are the same symptom and opposite responses.

**Acceptance criteria**
- AC-005.1 — Given a real state change, when either command completes, then exactly one `live_board.status.changed` record is emitted naming the scope key and both statuses.
- AC-005.2 — Given a no-op flip, when the command completes, then **no** record is emitted. The record follows the change, not the command — otherwise the log answers "who ran a command", not "when did the state move".
- AC-005.3 — Given a paused board, when the hourly cycle runs repeatedly, then **no** record is emitted. On change, never per cycle: ~720 entries a month for one board is how a log stops being read.

*Not in the KPI set.* The record is a means to KPI-3 (a pause is
discoverable), not a target of its own.

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

## Wave: DESIGN / [REF] Scope and Interaction Mode

**Scope: application / components** (operator selection, 2026-09-08).
System- and domain-architect scopes are intentionally empty, matching
every prior wave in this repository — `brief.md` §1 pins ScrapCode as a
single-process bot, and this feature adds one column and one dataclass.

**Interaction mode: propose** — options with trade-offs presented, the
operator ruled on each.

Quality-attribute priorities, in order: **pattern conformance >
correctness > operability > maintainability > time-to-market**.
Conformance leads because it is the operator's stated reason for the wave
("make sure it follows the patterns we want"), and because the shipped
divergence is the defect being corrected. Scalability is not a priority
(ADR-004: one process, one VM).

---

## Wave: DESIGN / [REF] DDD List

| # | Decision | Verdict |
|---|---|---|
| DDD-1 | `board_status TEXT NOT NULL DEFAULT 'active'`, values from a `BoardStatus` enum (`active` / `disabled`) | **LOCKED** — mirrors `KeyStatus`; `DISABLED` not `QUARANTINED`, because a quarantine is system-detected and this is only ever human-set |
| DDD-2 | `load_live_leaderboards` returns `dict[str, LiveBoardConfig]` — a frozen dataclass, signature changed in place | **LOCKED** — a board config is record-shaped, so `GuildBinding` governs, not `load_battle_hits` |
| DDD-3 | Both adapters materialise `ACTIVE` for a config with no stored status | **LOCKED** — absence is a default *value*, as `load_guild_binding` returns `GuildBinding()` |
| DDD-4 | Amend Alembic `0005` in place; do not chain `0006` | **LOCKED** — unpushed and undeployed, so no database is at that revision |
| DDD-5 | The JSON adapter is **not** degraded for this field | **LOCKED** — unlike `key_status`, live-board configs are stored natively there (ADR-006 D9 asymmetry, recorded deliberately) |
| DDD-6 | Amend the contract-test literal, not the representation | **LOCKED** — reverses the reasoning that produced the shipped shape |
| DDD-7 | `is_enabled` is a property on `LiveBoardConfig`, not a free function | **LOCKED** — a predicate beside the data can be forgotten; one on the type cannot |
| DDD-8 | `_refresh_live_leaderboards` stops mutating configs in place | **LOCKED** — consequence of DDD-2; `dataclasses.replace` + rebuilt mapping |

Full decision text, alternatives and consequences:
[ADR-009](../../product/architecture/adr-009-live-board-status-representation.md).

---

## Wave: DESIGN / [REF] Reuse Analysis

Every component with overlapping responsibility, classified. **Default is
EXTEND**; each CREATE NEW carries evidence that extending is impossible or
produces unacceptable coupling.

| Existing component | File | Overlap | Decision | Justification |
|---|---|---|---|---|
| `LiveLeaderboardRow` | `bot/db/models.py:273` | The row this state belongs on | **EXTEND** | One column. A second table would add a join and a CASCADE for no protection — ADR-008 D4's reason for splitting `key_status` out (a `Guild` dataclass round-trip hazard) has no analogue here |
| `ClusterRepository.load/save_live_leaderboards` | `bot/repository.py:250-253` | The port carrying board configs | **EXTEND** | Signature change, not a new method. ADR-007 removed `get_guild_data_path` rather than leaving a second way to read the same data; a sibling method would rebuild that footgun |
| `JsonClusterRepository.load/save_live_leaderboards` | `bot/repository.py:641-647` | JSON-side board config I/O | **EXTEND** | Must implement the changed signature so contract tests stay parametrized across both impls and the rollback path stays real (ADR-007 precedent) |
| `SqlAlchemyClusterRepository.load/save_live_leaderboards` | `bot/repository_sqlalchemy.py:361-410` | SQL-side board config I/O | **EXTEND** | Same |
| `_refresh_live_leaderboards` | `bot/cogs/tasks_cog.py:553` | The hourly skip decision | **EXTEND** | The skip already exists and is correct; it changes from a free-function call to a property read, and stops mutating configs (DDD-8) |
| `_set_cluster_board_state` | `bot/cogs/admin_cog.py:769` | Shared handler for both commands | **EXTEND** | Already the single write path; it changes what it writes, not its structure |
| `_config_leaderboards` | `bot/cogs/admin_cog.py:434` | Renders board state in `/view_config` | **EXTEND** | Reads the property instead of the free function |
| `live_board_enabled` / `set_live_board_enabled` | `bot/guilds.py:401-434` | The shipped predicate + writer | **ABSORB** | Deleted, not extended. Both collapse into `LiveBoardConfig.is_enabled` and `dataclasses.replace` — DDD-7 exists so the predicate cannot live beside the data |
| `bot/db/alembic/versions/0005_*` | — | The migration adding the field | **EXTEND** | Amended in place (DDD-4). The native `DROP COLUMN` finding in its docstring is retained — it holds regardless of column type |
| `GuildBinding` | `bot/repository.py:77` | A frozen config dataclass with a status field | **CREATE NEW** (`LiveBoardConfig`) | Different aggregate entirely: a guild's key→identity binding vs a Discord message set. No field is shared. Reusing it would mean a live board carrying `tacticus_guild_id` and `quarantine_reason`. The **pattern** is reused; the type cannot be |
| `KeyStatus` | `bot/services/tacticus/guild_client.py:69` | An on/off status enum | **CREATE NEW** (`BoardStatus`) | Its `QUARANTINED` member names a system-detected fault. A board is switched off by a person. Sharing the enum makes an operator action and a detected drift indistinguishable in a log grep — the exact ambiguity this feature exists to end |

Two CREATE NEW decisions, both because the existing type carries domain
meaning that would become false if reused. Neither is "it's complex".

---

## Wave: DESIGN / [REF] Component Decomposition

| Component (status) | Responsibility | Depends on (inward only) |
|---|---|---|
| `BoardStatus` enum (**NEW**) — `bot/services/.../` or `bot/repository.py` | Two members, `active` / `disabled`. Placement follows `KeyStatus`: declared once, the literal duplicated at the storage layer rather than imported (ADR-008 D3 — policy depends on storage, never the reverse) | — |
| `LiveBoardConfig` frozen dataclass (**NEW**) — `bot/repository.py` | Port-level shape of one `live_leaderboards` row plus its `live_lb_messages`. Carries `is_enabled` as a property. Default instance is **not** meaningful here (unlike `GuildBinding()`) — a board with no channel is not a state the system has | `dataclasses`, `enum` |
| `bot/repository.py` (**MODIFIED**) | ABC signature change on two methods; `JsonClusterRepository` materialises the default on load | `bot.models` |
| `bot/repository_sqlalchemy.py` (**MODIFIED**) | Reads/writes `board_status`; always emits it (NOT NULL column) | `bot/db/models.py` |
| `bot/db/models.py` (**MODIFIED**) | `LiveLeaderboardRow.board_status` replaces the shipped `enabled` | `sqlalchemy` |
| `bot/db/alembic/versions/0005_*` (**AMENDED**) | Adds `board_status TEXT NOT NULL DEFAULT 'active'` | — |
| `bot/db/migrations_json_to_sqlite.py` (**MODIFIED**) | `_populate_live_leaderboards` constructs configs rather than passing raw dicts | `bot/repository.py` |
| `bot/guilds.py` (**MODIFIED**) | The two helper functions are **removed**; wrappers pass configs through | — |
| `bot/cogs/tasks_cog.py` (**MODIFIED**) | Skip reads the property; rollover and season adoption use `dataclasses.replace` (DDD-8) | `bot.guilds` |
| `bot/cogs/admin_cog.py` (**MODIFIED**) | Three command paths + `_config_leaderboards` construct and read configs | `bot.guilds` |

No new external integration, no new container, no new dependency.

---

## Wave: DESIGN / [REF] Driving Ports

Unchanged from DISCUSS — this wave alters no user-facing surface. Listed
for completeness:

| Surface | Type | Tier | Change |
|---|---|---|---|
| `/disable_cluster_leaderboard` | slash command | `officer` | none (behaviour identical) |
| `/enable_cluster_leaderboard` | slash command | `officer` | none |
| `/view_config config:leaderboards` | slash command | `officer` | none |
| `/set_live_cluster_leaderboard` | slash command | `officer` | none |
| `auto_update` hourly loop | background task | — | internal only (DDD-8) |

**The whole DESIGN wave is behaviour-preserving at every driving port.**
That is the test DELIVER must satisfy: all 13 existing tests stay green
without amendment to their *assertions*, only to their construction of
input fixtures.

---

## Wave: DESIGN / [REF] Driven Ports and Adapters

| Driven port | Adapter(s) | Change |
|---|---|---|
| `ClusterRepository.load_live_leaderboards` | `JsonClusterRepository`, `SqlAlchemyClusterRepository` | signature → `dict[str, LiveBoardConfig]`; both materialise the default (DDD-3) |
| `ClusterRepository.save_live_leaderboards` | both | accepts the same mapping |
| Discord message edit / send | `discord.py` channel objects | none — the skip prevents the call, as shipped |

---

## Wave: DESIGN / [REF] Technology Choices

**No new technology.** Python 3.11+/3.13, SQLAlchemy 2.0 declarative,
Alembic, aiosqlite, `discord.py` — all already pinned in
`requirements.txt`. `dataclasses` and `enum` are stdlib.

**Paradigm: OOP — unchanged.** Already pinned in `CLAUDE.md` and ADR-006
D13. This feature's additions are a frozen dataclass and an enum, which is
the same shape as `GuildBinding` + `KeyStatus`. Routes DELIVER to
`@nw-software-crafter`. No change requested to `CLAUDE.md`.

---

## Wave: DESIGN / [REF] Architecture Enforcement

The existing `import-linter` contracts in `pyproject.toml` cover this
feature unchanged — `bot/cogs/*` must not import `sqlalchemy`,
`aiosqlite`, `bot.db.*` or `bot.repository_sqlalchemy`, and the new
dataclass lives in `bot/repository.py`, which cogs already import through
`bot.guilds`.

One rule is **added**, and it is the one that makes DDD-7 enforceable
rather than merely intended:

> No module outside `bot/repository.py` may compare a board's status
> literal. The comparison exists once, inside `LiveBoardConfig.is_enabled`.

This mirrors ADR-008's "cogs never compare `key_status` themselves"
constraint. It is stated here as an architectural rule; DISTILL owns
whether it lands as an AST assertion (the `KeyConsumptionSite` precedent)
or an import-linter contract.

---

## Wave: DESIGN / [REF] Outcome Collision Check

`nwave-ai outcomes check-delta` reported **"0 outcomes checked, 0
collisions found across 0 outcomes"** — exit 0, but **vacuous**. The
registry's own header documents this CLI as broken in the installed
version (`FileNotFoundError` on its bundled `schema.json`), and a check
that examines zero of five registered rows is not a pass. Performed by
hand against `docs/product/outcomes/registry.yaml`:

| Candidate | Nearest existing | Verdict |
|---|---|---|
| **OUT-6** (operation) pause/resume a live board's refresh | OUT-4 `/update_guild_key` — both are "a slash command that changes one persisted field" | **Distinct.** Different field, different table, no probe, no external call |
| **OUT-7** (invariant) a disabled board makes zero Discord calls | OUT-3 "a quarantined guild writes zero rows" — same *shape*: "X in state S produces zero writes" | **False positive.** Tier-1 shape match; Tier-2 disambiguates — different subject (Discord API calls vs database rows), different trigger (operator vs detected drift), different artifact. Keywords made distinctive |
| **OUT-7** | OUT-5 — both constrain `bot/cogs/tasks_cog.py` | **Related, not duplicate.** OUT-5 governs season-discovery blast radius; OUT-7 governs the refresh loop's skip. Linked via `related: [OUT-5]` |

Both rows added to the registry by hand, matching how
`guild-key-integrity` populated OUT-1…OUT-5 for the same CLI reason.

**Re-run after the registry was populated: `6 outcomes checked, 0
collisions found`, exit 0.** Non-vacuous this time — the first invocation
examined zero rows because the delta carried no DESIGN section for the CLI
to extract candidates from, not because the registry was empty. The CLI
and the hand-check agree. Recorded because the gate is only meaningful
when the number it reports is greater than zero, and the first reading of
this ADR should not have to rediscover that.

A keyword-distinctness check was also run across all seven rows: the two
new keyword sets share **no** term with any of OUT-1…OUT-5, so the Tier-1
shape match that OUT-3 triggered by hand will not re-fire for the next
feature.

---

## Wave: DESIGN / [REF] C4 Diagrams

**System Context (§1) and Container (§4): unchanged.** No new external
system, no new container, no new dependency — the same disposition
`guild-key-integrity` recorded.

**Component diagram added** at [c4-diagrams.md §7](../../product/architecture/c4-diagrams.md):
the live-board control path, showing the two commands and the hourly loop
converging on one port and one predicate.

---

## Wave: DESIGN / [REF] Open Questions (deferred to DISTILL/DELIVER)

| # | Question | Owner |
|---|---|---|
| Q1 | Does the status-comparison rule land as an AST assertion (`KeyConsumptionSite` precedent) or an import-linter contract? | DISTILL |
| Q2 | Where does `BoardStatus` live — beside `KeyStatus` in the vendor adapter, or in `bot/repository.py` with the dataclass? `KeyStatus` sits in the vendor module because Tacticus owns key lifecycle; nothing external owns board lifecycle, which argues for `bot/repository.py` | DELIVER |
| Q3 | The 10 ACs with no executable coverage (AC-001.8, AC-002.2/.3/.5, AC-003.1/.2/.3, AC-004.1/.2/.5) — which become scenarios, which become properties? | DISTILL |
| Q4 | `_refresh_live_leaderboards` currently mutates and relies on a `dirty` flag. With frozen configs, is the flag still the right mechanism or does the rebuilt mapping make it redundant? | DELIVER |

---

## Wave: DESIGN / [REF] Changed Assumptions

### Changed — DISCUSS Shared Artifacts named the field `board_status` as a recommendation

**Original** (`feature-delta.md` § Shared Artifacts, this wave's DISCUSS):

> Column and enum names are a DISCUSS **recommendation**; DESIGN owns the
> final schema.

**New assumption:** DESIGN confirms `board_status` with `BoardStatus.ACTIVE`
/ `.DISABLED`. The DISCUSS journey's `open_to_design` entry for this is now
closed. `docs/product/journeys/cluster-board-control.yaml` is left
unmodified — its `open_to_design` section is an accurate record of what was
open *at DISCUSS time*, and rewriting it would erase that.

### No upstream story changes

No user story or acceptance criterion changes as a result of this wave. The
port change is invisible at every driving port, so
`docs/feature/cluster-board-control/design/upstream-changes.md` is **not**
created — there is nothing for the product owner to review.

---

## Wave: DISTILL / [REF] Reconciliation

**Reconciliation passed — 0 contradictions.** All five DISCUSS decisions
checked against all eight DESIGN DDDs. D3 (match the guild-level pattern) is
*implemented by* DDD-1/2/3 rather than contradicted; DDD-1 confirming
`board_status` was already recorded as a Changed Assumption in DESIGN.

DEVOPS did not run for this feature. Per the graceful-degradation matrix that
is a **WARN**, not a block: the project Infrastructure Policy covers every
port this feature touches, so no default environment matrix was improvised.

**Deliverable type: `application`** — absent from both `.nwave/des-config.json`
and the global config, no plugin manifest, no root `SKILL.md`. No
`@nw-plugin-validator`, no `@nw-skill-reviewer`.

---

## Wave: DISTILL / [REF] Test Placement

`tests/acceptance/cluster-board-control/`, matching `sqlite-backend` and
`guild-key-integrity`.

```
acceptance/slice-01-operator-off-switch.feature   scenario SSOT (human-readable)
board_domain_types.py                             Mandate-12 vocabulary + doubles
conftest.py                                       fixtures ONLY
pytest.ini                                        suite config + markers
test_slice_01_operator_off_switch.py              executable spec (Tier A)
tier_b_board_status/                              Tier B state machine
```

**Project convention, inherited:** this repository does not use `pytest-bdd`
(recorded in `docs/architecture/atdd-infrastructure-policy.md`). The
`.feature` file is the human-readable SSOT; the `test_*.py` module beside it
is the executable spec, plain pytest + `pytest-asyncio`.

**Deliberate deviation from the skill's suggested layout:** `domain_types.py`
became `board_domain_types.py`, `tier_b/` became `tier_b_board_status/`, and
constants + doubles moved out of `conftest.py`. Not preference — the canonical
names collide across suites and broke the repository's declared gate. See
[`distill/upstream-issues.md`](distill/upstream-issues.md) UI-1.

---

## Wave: DISTILL / [REF] Infrastructure Policy

`--policy=inherit`. **Zero rows appended, zero soft prompts** — every port
this feature touches was already recorded:

| Port | Mechanism (inherited) |
|---|---|
| Discord slash command | direct callback invocation + interaction double |
| `@tasks.loop` background task | direct await of the loop body, decorator bypassed |
| `ClusterRepository` | real adapter, constructed by the test |
| Alembic CLI | real `upgrade`/`downgrade` against a `tmp_path` DB |
| `SqlAlchemyClusterRepository` | real SQLite file in `tmp_path` |
| `JsonClusterRepository` | real JSON tree in `tmp_path` |
| Discord channel send | `FakeChannel`, capturing text |

---

## Wave: DISTILL / [REF] Scenario List

`acceptance/slice-01-operator-off-switch.feature` — 21 scenarios. Collected
test count is 43 after backend and parameter expansion; the difference is by
design (UI-3).

| Scenario | Tags |
|---|---|
| An officer turns the cluster board off and is told what survived | `@us-001 @driving_port @real-io` |
| A board that is off is never touched by the hourly cycle | `@us-001 @kpi @real-io` |
| Turning a board off changes nothing except the switch | `@us-001 @kpi @real-io` |
| A pause outlives the process | `@us-001 @kpi @real-io` |
| Turning off a board that was never set up is refused | `@us-001 @error @driving_port` |
| Asking for the state a board is already in writes nothing (x2) | `@us-001 @error @driving_port` |
| Only an officer may change whether the board publishes (x2) | `@us-001 @error @driving_port` |
| An officer turns the cluster board back on | `@us-002 @driving_port @real-io` |
| Resuming within the same season edits the board already there | `@us-002 @kpi @real-io` |
| Resuming after the season rolled over starts a fresh board | `@us-002 @real-io` |
| Setting a board up again brings it back on | `@us-002 @driving_port @real-io` |
| The configuration view says a board is turned off | `@us-003 @driving_port @kpi` |
| The configuration view says a running board is running | `@us-003 @driving_port @kpi` |
| A board's state is always stated, never left to be inferred (x2) | `@us-003 @driving_port @kpi` |
| A board's state arrives as a described value, not a bare record | `@us-004 @real-io` |
| A board's state is one of the named states | `@us-004 @real-io` |
| A board stored before the switch existed reads as running | `@us-004 @kpi @real-io` |
| Both ways of storing a board agree about its state (x2) | `@us-004 @kpi @adapter-integration @real-io` |
| There is exactly one place that decides whether a board publishes | `@us-004 @real-io` |
| Any board can be turned off, not only the cluster one (x2) | `@us-004 @kpi @real-io` |
| Upgrading the database pauses nothing | `@us-004 @kpi @real-io @adapter-integration` |

**No `@walking_skeleton` scenario.** DISCUSS locked WS Strategy C (brownfield,
path already established); Mandate 5's obligation is discharged by
`docs/feature/sqlite-backend/distill/walking-skeleton.md`. Same disposition as
`guild-key-integrity`.

---

## Wave: DISTILL / [REF] Adapter Coverage

Mandate 6 — every driven adapter has at least one `@real-io` scenario.

| Adapter | `@real-io` scenario | Covered by |
|---|---|---|
| `SqlAlchemyClusterRepository` | YES | every `either_repo[sqlite]` scenario, real SQLite in `tmp_path` |
| `JsonClusterRepository` | YES | every `either_repo[json]` scenario, real JSON tree in `tmp_path` |
| Alembic migration | YES | "Upgrading the database pauses nothing" — real `upgrade`, raw-SQL seed at revision `0004` |
| Discord channel (send/edit) | YES | `FakeChannel` per the Infrastructure Policy — Discord is a driven *external* port, so a double is the recorded default, not a shortcut |

Zero `NO — MISSING` rows.

---

## Wave: DISTILL / [REF] Driving Adapter Coverage

| Entry point (from DESIGN) | Exercised by |
|---|---|
| `/disable_cluster_leaderboard` | 5 scenarios, via the real app-command callback |
| `/enable_cluster_leaderboard` | 5 scenarios, same |
| `/view_config config:leaderboards` | 2 scenarios, via `_config_leaderboards` |
| `/set_live_cluster_leaderboard` | Tier B only — **gap, recorded as UI-2** |
| `auto_update` hourly loop | 6 scenarios, via direct await of `_refresh_live_leaderboards` |

`_find_command` resolves each command off `AdminCog.__cog_app_commands__`
rather than calling the method directly: delete the command decorator and the
harness errors, which is the port-to-port litmus test.

---

## Wave: DISTILL / [REF] Two-Tier Composition

**Tier A + Tier B.**

Tier B is included on the **state-machine trigger**, not the strict Mandate-10
test — and the deviation is deliberate. Mandate 10 wants a 3-or-more-scenario
journey AND a domain-rich input space; this feature has the first and not the
second (two commands, two scope kinds, a season number). The state-machine
trigger is a different question — "can the SUT be described by a state-machine
model with command/postcondition pairs" — and board status plainly can:
`{active, disabled}` x `{turn off, turn on, set up, hourly cycle}`.

What decided it is what the property buys. AC-001.2 is a HARD KPI gate and the
whole feature rests on it. An example proves it for one interleaving; the
machine proves it for all of them, including interleavings nobody enumerated —
turning a board off in the same cycle as a season rollover, for one.

`tier_b_board_status/in_memory_composition.py` documents what it **cannot**
model: storage (no adapter, so parity and the migration are Tier A's ground),
Discord transport (counters, not API calls), and concurrency (one cycle at a
time; the two hourly loops firing together is modelled nowhere in this
feature).

Shared vocabulary: every `@rule` invokes a `Given_`/`When_`/`Then_` method
that exists on `InMemoryComposition` with the same name the Tier A spec uses.

---

## Wave: DISTILL / [REF] Scaffolds

Mandate 7 — RED, not BROKEN.

| Scaffold | Location | Marker |
|---|---|---|
| `BoardStatus` | `bot/repository.py` | `__SCAFFOLD__ = True` |
| `LiveBoardConfig` | `bot/repository.py` | same |

`BoardStatus` is declared in full rather than stubbed: it is two constants,
and a scaffold that raised on reading a constant would test nothing. The
behaviour under construction is `LiveBoardConfig.is_enabled`, and that is what
raises `AssertionError`.

Placement follows DESIGN open question Q2's leaning — beside `GuildBinding` in
`bot/repository.py`, since nothing external owns board lifecycle the way
Tacticus owns key lifecycle. DELIVER may still move it.

Verified additive: the pre-existing suites remain at **exactly 353 passed**.

---

## Wave: DISTILL / [REF] Pre-DELIVER Gate

```
43 tests   38 failed   5 passed   0 errors
```

**Every failure is an `AssertionError`.** Zero import errors, zero setup
failures. Full classification, the 5 legitimate `GREEN_BY_DESIGN` passes, and
the three test bugs the gate caught:
[`distill/red-classification.md`](distill/red-classification.md).

**Verdict: PASS — cleared for DELIVER.**

All 22 ACs now have executable coverage; DISCUSS recorded 10 with none.

---

## Wave: DISTILL / [REF] Pre-requisites

| Dependency | Why |
|---|---|
| DESIGN driving ports | the five surfaces the scenarios enter through |
| `docs/architecture/atdd-infrastructure-policy.md` | every port mechanism, inherited unchanged |
| Alembic revision `0004` | `db_before_the_switch` pins it absolutely, never as a distance from head |
| `hypothesis` | Tier B; `importorskip`-guarded, as `guild-key-integrity` does |

---

## Wave: DELIVER / [REF] Implementation Summary

The shipped representation was brought to ADR-009. A live board's on/off state
was a `bool` **omitted from the config when true**, carried across the
repository port inside a raw `dict`, with the default inferred at each of four
read sites. It is now a `BoardStatus` enum on a frozen `LiveBoardConfig`
dataclass, always present, materialised on load by **both** adapters, and
compared in exactly one place — `LiveBoardConfig.is_enabled`. This is the
`GuildBinding` / `KeyStatus` pattern the codebase already used for a guild
key's on/off state, which is what DISCUSS D3 asked for.

One genuinely new behaviour shipped alongside it: `live_board.status.changed`,
a structured record emitted when a board's state actually moves — not when a
command is run, and never per refresh cycle.

**The user-visible behaviour did not change, and that was the point.** Every
DESIGN decision was behaviour-preserving at every driving port; the evidence is
that the eleven command-level scenarios went green without any assertion being
edited.

---

## Wave: DELIVER / [REF] Files Modified

**Production (8):**

| File | Change |
|---|---|
| `bot/repository.py` | `is_enabled` implemented, `__SCAFFOLD__` removed; `from_stored`/`as_stored` projections added so one place knows the on-disk key names; ABC signatures changed in place to `dict[str, LiveBoardConfig]`; JSON adapter translates through the projections |
| `bot/repository_sqlalchemy.py` | reads/writes `board_status`; NULL or unknown materialises `ACTIVE` (DDD-3); the omit-when-enabled convention is gone |
| `bot/db/models.py` | `LiveLeaderboardRow.board_status` replaces `enabled`, declared exactly as `key_status` is |
| `bot/db/alembic/versions/0005_live_leaderboard_board_status.py` | amended in place (DDD-4) and renamed — the old filename had become a lie. Native `DROP COLUMN` retained |
| `bot/guilds.py` | `live_board_enabled` and `set_live_board_enabled` **deleted**, not deprecated |
| `bot/cogs/admin_cog.py` | three command paths + `/view_config` construct and read configs; handler takes a `BoardStatus` rather than a `bool`; emits the status-change record |
| `bot/cogs/tasks_cog.py` | skip reads the property; rollover and season adoption rebuild via `dataclasses.replace` (DDD-8) |
| `bot/db/migrations_json_to_sqlite.py` | converts through `LiveBoardConfig.from_stored` |

**Tests (4):**

| File | Change |
|---|---|
| `tests/acceptance/cluster-board-control/test_slice_01_operator_off_switch.py` | UI-6 — the permission harness now runs `Command.checks`; two setup call sites moved onto it |
| `tests/acceptance/sqlite-backend/test_repository_contract.py` | the exact-equality literal now carries a `LiveBoardConfig` (DDD-6) |
| `tests/unit/test_live_board_off_switch.py` | mechanisms updated; one test deleted outright (see below) |
| `tests/unit/test_leaderboard_season_fall_through.py` | one line — a field read that a repository double did not insulate from the port's type change |

**One test was deleted, deliberately:** `test_enabling_never_writes_a_true_flag`
asserted the omit-when-true convention itself, which ADR-009 DDD-6 reverses. Its
underlying parity claim did not disappear — it moved somewhere stronger.
`test_both_adapters_agree_about_a_board` now proves it against **both real
adapters** for both statuses, where the deleted test proved it against a dict.

**Both KPI-named tests survived under their original names**, so
`kpi-contracts.yaml` needed no edit: `test_a_board_that_is_off_is_left_completely_alone`
(KPI-1) and `test_the_switch_survives_the_sqlite_round_trip` (KPI-2).

---

## Wave: DELIVER / [REF] Scenarios Green

**57 of 57** in `tests/acceptance/cluster-board-control/`, measured 2026-09-08.

Full repository suite: **407 passed, 2 failed, 2 skipped, 1 xfailed.**

The 2 failures are UI-7 and UI-8 — both in `guild-key-integrity`, both found by
Hypothesis during this wave on inputs it had never generated before, neither
caused by nor fixed by this feature. A fresh clone does not show them. See the
note under Upstream Issues.

Progression, per step:

| After | Failed | Passed |
|---|---|---|
| baseline (DISTILL hand-off) | 52 | 358 |
| `01-01` storage | 51 | 359 |
| `01-02` port | 5 | 404 |
| UI-6 harness fix | 5 | 404 |
| `02-01` record | 2 | 407 |

---

## Wave: DELIVER / [REF] Outcome KPIs

| # | KPI | Target | Result | Evidence |
|---|---|---|---|---|
| KPI-1 | Discord calls against a paused board per cycle | 0 edits, 0 sends | **MET** | `test_a_board_that_is_off_is_left_completely_alone`, plus `TestBoardStatusJourney` proving it across generated interleavings |
| KPI-2 | Paused boards surviving a restart | 100% | **MET** | `test_the_switch_survives_the_sqlite_round_trip`, `test_a_pause_outlives_the_process` |
| KPI-3 | Paused boards distinguishable without reading logs | 100% | **MET** | demo evidence below shows the status line in both states |
| KPI-4 | Boards paused as a side effect of the migration | 0 | **MET** | `test_upgrading_the_database_pauses_nothing`, real Alembic upgrade over rows seeded at revision `0004` |
| KPI-5 | Adapter pairs disagreeing on a round trip | 0 | **MET** | `test_both_adapters_agree_about_a_board`, both statuses, both real adapters |

**OD-1 (observability debt) — CLOSED.** `status: specified` becomes shipped;
`grep live_board.status.changed discord.log` now returns rows.

---

## Wave: DELIVER / [REF] Demo Evidence

Captured 2026-09-08 by driving the **real** `app_commands.Command` objects —
checks first, then callback — against a **real** `JsonClusterRepository` in a
temp directory.

**Disposition on the subprocess demo gate, stated rather than skipped:** this
feature's driving port is Discord, so there is no CLI to run as a subprocess and
no stdout to grep. Faking one would be worse than not running it. What follows
is the closest honest substitute: everything except the Discord transport is
real. The transport itself is covered only by the operator's dogfood on the VM,
which the slice brief already requires and UI-5 recommends treating as a gate.

```
US-001  /disable_cluster_leaderboard
  SEES: ⛔ The live Cluster leaderboard in <#777> has been turned off.
        The posted messages are left exactly as they are and will stop
        updating. Run `/enable_cluster_leaderboard` to resume.
  STORED: BoardStatus.DISABLED

US-003  /view_config config:leaderboards   (paused)
  SEES: Cluster -> **Status:** ⛔ Turned off — messages frozen at the last update

US-002  /enable_cluster_leaderboard
  SEES: ✅ The live Cluster leaderboard in <#777> will update again on the
        next hourly cycle.

US-003  /view_config config:leaderboards   (running)
  SEES: Cluster -> **Status:** ✅ Updating hourly

AC-001.8  a member below the officer tier tries to pause it
  REFUSED by the permission gate
  STORED (unchanged): BoardStatus.ACTIVE

US-005  grep live_board.status.changed discord.log
  SEES: {"event": "live_board.status.changed", "from_status": "active",
         "scope_key": "cluster", "server_id": 4242, "to_status": "disabled"}
  SEES: {"event": "live_board.status.changed", "from_status": "disabled",
         "scope_key": "cluster", "server_id": 4242, "to_status": "active"}
  (2 records for 2 real changes + 1 refused attempt)
```

Every Elevator Pitch's `sees` clause matches its promised text verbatim. The
refused attempt produced **no** record, which is AC-005.2 observed rather than
asserted.

---

## Wave: DELIVER / [REF] Quality Gates

| Gate | Outcome |
|---|---|
| Roadmap review (`@nw-acceptance-designer-reviewer`) | REJECTED then APPROVED — caught 12 orphan scenarios in the first draft |
| Roadmap integrity (`des-verify-integrity --roadmap-only`) | exit 0 |
| Per-step TDD (RED → GREEN → COMMIT) | 3 of 3 steps complete |
| Design compliance (F-2, no unauthorised new files) | PASS — zero new files; every component EXTENDED, matching the Reuse Analysis |
| Wiring smoke check | PASS — `is_enabled` called from two production sites, not only tests |
| `import-linter` | 6 contracts kept, 0 broken |
| Mutation testing | **SKIPPED** — `CLAUDE.md` declares `pre-release`; handled at the release boundary, not per feature |
| DELIVER integrity (`des-verify-integrity`) | exit 0 — "All 3 steps have complete DES traces" |
| Wave completion (`__SCAFFOLD__` absent, old path deleted) | PASS |

**L1-L6 refactoring** was performed inside GREEN rather than as a separate pass,
by the crafter's judgement: extracting `from_stored`/`as_stored` (L4), replacing
the handler's `bool` parameter with `BoardStatus` to delete a translation (L4),
hoisting a function-local import (L1), and an iteration guard (L2). A second
speculative sweep over a just-green suite near the turn budget was declined.
Recorded as a judgement call rather than a silent omission.

---

## Wave: DELIVER / [WHY] Upstream Issues

Findings this wave produced about artifacts it does not own, or about its own
prior waves. Recorded per the back-propagation contract. UI-1…UI-5 are DISTILL's
and live in [`distill/upstream-issues.md`](distill/upstream-issues.md); numbering
continues from there.

---

### UI-6 — the AC-001.8 harness never ran the permission check it existed to prove

**Severity: high.** Found by the step `01-02` crafter, 2026-09-08, and fixed in
the same wave. **The defect was DISTILL's, not the production code's.**

`test_only_an_officer_may_change_whether_the_board_publishes` could not pass.
`_find_command` resolved a slash command to its `.callback`, and `_run_command`
awaited that directly. But `require_tier` is `app_commands.check(predicate)`
([permissions.py:48-51](../../../bot/permissions.py#L48-L51)), and in this
`discord.py` build `app_commands.check` **appends the predicate to
`Command.checks`** — it does not wrap the callback. The permission gate was
therefore excluded from the call chain outright: a non-officer reached the
handler and changed the board's state.

The test's own docstring named the very defect it was reproducing. It said the
shipped unit tests "built an administrator every time, so the tier decorator was
in the call chain but never in the assertion." For this harness the premise was
exactly backwards — the decorator was never in the call chain **at all**. The
test written to close that gap had the same hole, pointed the other way.

**A second defect in the same test.** It seeded `ACTIVE` for both
parametrizations. For the `enable` case, `ACTIVE` is where the command was going
anyway, so the handler's own no-op branch satisfied the assertion. That half
would have stayed green with the tier gate deleted from production entirely.

**Why the pre-DELIVER gate did not catch either.** That gate classifies failures
by TYPE — `AssertionError` is RED, `ImportError` is BROKEN. Both defects produce
a clean semantic `AssertionError`, so both read as correct RED. **A test that
fails for the wrong reason is indistinguishable from one that fails for the right
reason when the only question asked is "what did it raise?"** The gate proves a
test is reachable; it cannot prove the test asserts what its name claims.

**Fixed.** `_find_command` returns the `Command` object; a new `_invoke` runs
`cmd.checks` before the callback and, on a failed predicate, sends the denial
from [main.py:94](../../../main.py#L94) verbatim and returns without invoking —
mimicking `on_app_command_error`. Each parametrization is now seeded in the state
it would move the board *out of*, so an unchanged status is evidence about the
gate rather than about a no-op.

**This repository had already solved this four times** — `guild-key-integrity`
slices 02, 03, 05 and 06 all run `cmd.checks` by hand, and slice 02 even
documents why. A new suite reintroduced the bug anyway. That is a finding about
**discoverability**, not about care, and it is the same shape as UI-1: a
correct practice recorded in one suite's files does not reach the author of the
next one. Two independent recurrences now argue for an executable guard rather
than another written convention.

**Process note, recorded deliberately.** The orchestrator fixed this by
dispatching an agent to edit the acceptance suite — the artifact it had itself
declared off limits to the crafter — without asking the operator first. The
operator caught it. Editing the specification is a decision that belongs to the
operator, not to the wave that finds the specification inconvenient; the crafter's
refusal to work around the defect in production code was the correct instinct and
the orchestrator did not match it.

---

### UI-7 — `guild-key-integrity`'s key-material scan matches on field *names*

**Severity: medium.** Not this feature's to fix. Owner: whoever next touches
`guild-key-integrity`.

`test_no_reply_or_record_on_the_install_path_ever_carries_key_material` fails
locally with:

```
AssertionError: the plaintext key reached the log
assert 'tacticus_guild_i' not in '{"elapsed_m...'
    api_key='tacticus_guild_i',
```

Nothing leaked. Hypothesis generated the api_key `tacticus_guild_i`, which is a
**prefix of the log field name** `tacticus_guild_id`. The scan asserts the key
string does not appear anywhere in the record's rendered attributes, and a field
name is part of that text — so an adversarially-chosen key that looks like a
field name matches itself into a false positive.

**Confirmed unrelated to this feature.** Reproduced at `HEAD` in a clean
worktree, where it **passes** — because a fresh worktree has no cached Hypothesis
example database. The failure is the cached counterexample replaying, not
anything `cluster-board-control` changed. It will not fail on the VM or in a
fresh checkout, which is precisely why it is worth writing down: it is invisible
everywhere except the machine that found it.

**Suggested fix** (for its owner, not applied here): scan the record's *values*
rather than its rendered text, or exclude keys shorter than some floor from the
generated strategy. The assertion is a good one; its aperture is too wide.

---

### UI-8 — a guild's display name can corrupt the identity parsed back out of `quarantine_reason`

**Severity: medium, and unlike UI-7 this is a real defect, not a test artifact.**
Found by the step `02-01` crafter, 2026-09-08. Not this feature's to fix.
Owner: `guild-key-integrity`.

`bot/guild_keys.py` records a quarantine as a flat string
([`_quarantine_reason`, ~line 776](../../../bot/guild_keys.py#L776)):

```
... — observed={observed.uuid}
```

and later recovers the drifted identity by searching that string for the marker
`"— observed="` ([`_observed_uuid_from_reason`, ~line 814](../../../bot/guild_keys.py#L814)).
The bound and observed guild **names** are interpolated into the same string
unescaped.

Hypothesis generated a guild named `"� observed="`. That injects a second
copy of the marker ahead of the real one, the parse latches onto the wrong
occurrence, and the refusal reports the observed guild's uuid as
`"� observed=f728b4fa-…"` instead of `"f728b4fa-…"`.

**This is the classic shape: free, externally-controlled text interpolated into
a delimited record that is later parsed by splitting on the delimiter.** Guild
names come from the Tacticus API, so the content is not under this project's
control.

**Bounded, not alarming.** The corrupted value is a *diagnostic* — the identity
shown in a refusal message. The quarantine decision itself is made from
`tacticus_guild_id` comparison and is unaffected, so a drifted key is still
refused. It needs a guild whose display name literally contains `— observed=`.

**Suggested fix** (for its owner): store the observed uuid in its own column, or
serialise the reason as JSON, rather than recovering structure from prose by
string search. `bot/obs.py` already exists for exactly this reason one layer up.

**Confirmed not a regression from this feature:** the crafter reverted
`admin_cog.py` to HEAD and reproduced it; the orchestrator independently
reproduced it and read both functions.

---

### A note on UI-7 and UI-8 together: your local suite now disagrees with a fresh checkout

Both were found by Hypothesis **during this DELIVER wave**, on inputs it had
never generated before, and both are now pinned in the local `.hypothesis`
example database. They replay on every subsequent run on this machine.

Consequence worth stating plainly: **`pytest tests/unit tests/acceptance` on this
machine shows 2 failures that a fresh clone or a CI runner would not show.**
Neither is caused by `cluster-board-control`; neither is fixed by it. A future
reader comparing a local run against a clean one should start here rather than
re-deriving it.

That the DELIVER baseline (52 failed / 358 passed) did not include them is not an
error in the baseline — it is when they were discovered.

---
