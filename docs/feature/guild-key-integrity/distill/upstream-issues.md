# Upstream issues — DISTILL, feature `guild-key-integrity`

> Gaps and stale text the DISTILL wave found in prior waves. Per the
> back-propagation contract none of them are silently edited into the prior
> wave's text; they are raised here and reflected in the scenarios.
>
> Dated 2026-07-31. Author: Sentinel (nw-acceptance-designer), DISTILL wave.

Reconciliation gate: **passed, 0 unresolved contradictions.** DISCUSS D1–D6
map onto DDD-1/3/5/6/8 and the DEVOPS slice ordering with no conflict. The
three supersessions in play (DESIGN's "Correction to DISCUSS", DEVOPS U2,
DEVOPS U3) are all documented and authorized, so they are resolved history
rather than open ambiguity.

---

## UI-1 — AC-006.1 and AC-006.2 describe a schema DDD-4 replaced

**Severity:** low — the intent survives intact; only the wording is stale.
**Action needed:** reword when US-006 is next touched. No decision changes.

### What DISCUSS says

> AC-006.1 — Given the Alembic revision is applied to a copy of the production
> database, when it completes, then **every existing guild row gains the new
> columns** with `key_status = 'active'` and null identity fields, and no
> existing row is otherwise altered.

US-006's title and body likewise say the migration "adds `tacticus_guild_id`
… to `guilds`".

### Why it no longer holds

DDD-4 moved binding state into its own `guild_key_bindings` table precisely
so `save_guilds` cannot clobber it. Under DDD-4 the migration adds **no**
column to `guilds` and there is **no** backfill (trust-on-first-use populates
the table). The AC as literally written is now unsatisfiable.

DESIGN's "Correction to DISCUSS" section addressed AC-001.7 and AC-006.3 and
confirmed both still run. It did not revisit AC-006.1 or AC-006.2.

### How DISTILL resolved it

The scenarios are written against DDD-4 — the LOCKED decision — and assert
the AC's actual intent, which is stronger than the original text:

```
tests/.../test_slice_01_bind_and_report.py
  ::test_upgrade_creates_the_binding_store_and_touches_no_guild_record
```

asserts that after `alembic upgrade head` the `guilds` rows are byte-identical
AND its column list is unchanged. The original AC would have been satisfied by
a migration that added columns; the scenario now fails if one does.

**Suggested AC-006.1 wording:**

> Given the revision is applied to a copy of the production database, when it
> completes, then a `guild_key_bindings` table exists and is empty, and every
> existing `guilds` row is byte-identical including its column list.

---

## UI-2 — `environments.yaml` cross-references two ACs to the wrong environments

**Severity:** cosmetic — traceability labels only, no scenario affected.
**Action needed:** correct the two `exercises:` lists.

The DEVOPS artifact lists `AC-001.3` (the first-bind announcement) under
`bound-matching`, and `AC-001.2`/`AC-001.6` under `clean`. The mapping should
be:

| AC | Environment it actually belongs to | Why |
|---|---|---|
| AC-001.3 (announce once on first bind) | `clean` | there is no binding yet — that is the definition of `clean` |
| AC-001.4 (do not re-announce) | `bound-matching` | requires an existing binding |
| AC-001.2 (`/view_config` shows the binding) | `bound-matching` | needs something to show |

The suite maps them correctly regardless; this is a documentation label, not
a coverage hole.

---

## UI-3 — `hypothesis` is required and is not in `requirements.txt`

**Severity:** medium — one test layer cannot run without it.
**Action needed:** pin it, alongside the two tools DEVOPS D10 already pins.

DISTILL emits a Tier B state-machine layer
(`tier_b/test_key_status_state_machine.py`) because the key-status model *is*
a state machine — three states, six commands — which is the Hebert ch.11
trigger. Two of its properties are claims about the whole state space that no
enumerated example establishes:

* `quarantine_is_never_a_trap` — from every reachable quarantined state there
  exists a path back to active. DISCUSS D3 is exactly this claim, and a single
  example only shows that one path works.
* `quarantined_guilds_never_write` — zero rows under every interleaving of
  probes and key updates, not just the orderings someone wrote down.

`requirements.txt` has `pytest` and `pytest-asyncio` and no property-testing
library. The layer currently `importorskip`s with a clear reason, so the suite
is honest about it rather than silently missing coverage — but it is missing
coverage until the pin lands.

Combined with DEVOPS D10, three lines are needed:

```
hypothesis
import-linter
pytest-archon
```

`test_architecture_chokepoint.py::test_both_enforcement_tools_are_pinned`
already fails until the last two arrive.

---

## UI-4 — `bot/services/tacticus/guild_client.py` now owns three shared types

**Severity:** low — a clarification to the DESIGN component table, not a change.
**Action needed:** none, recorded so DELIVER does not re-derive them.

DESIGN describes the module as `fetch_guild_snapshot(api_key) -> GuildSnapshot`,
~40 LOC. DISTILL additionally placed `ProbeOutcome`, `KeyStatus` and
`GuildIdentity` there, because the acceptance suite must compare against the
*same* enum objects production uses — a test-side copy of an enum compares
unequal under `is`, and the copy that silently drifts is always the one nobody
runs in production.

`tests/.../domain_types.py` re-exports them rather than re-declaring them
(Mandate-12 criterion 2). The module stays import-light: `dataclasses` and
`enum` at module scope, `httpx` only inside `fetch_guild_snapshot`.

---

## Not an issue, recorded because it looks like one

**Three architecture tests pass today.** `test_the_guilds_wrapper_layer_stays_free_of_policy_and_http`,
`test_storage_never_imports_policy` and `test_import_linter_contracts_all_pass`
are green before a line of the feature is written. They are not vacuous — they
are regression guards on properties the repo currently has and this feature
could easily break, and one of them (`lint-imports`, 4 contracts kept)
confirms that adding `bot/guild_keys.py`, `bot/obs.py` and
`bot/services/tacticus/` broke none of the existing contracts.

---

# Remediation wave — UI-5 … UI-12 (2026-08-02)

> Raised by the DISTILL remediation pass that followed the adversarial
> re-review (`../remediation-plan.md`). UI-5 … UI-8 are the escalated
> test-integrity defects and are **fixed in this wave**. UI-9 … UI-12 are
> corrections owed to the slice briefs and to prior-wave records; they are
> raised here, not silently edited into the briefs.

## UI-5 — `KeyConsumptionSite` named the wrong sites (FIXED this wave)

**Severity:** blocking — it invalidated AC-004.6, the criterion that
certifies "every key-consumption site refuses a quarantined guild".

The enum listed seven sites. Two of them — `player_service.refresh_guild` and
`player_service.validate_if_stale` — are not key-consumption sites at all:
DDD-2 moved the fetch out and both methods are HANDED a `GuildSnapshot`
(`player_service.py:132,160`, parameter named `snapshot`). The
`_exercise_site` branches that claimed to drive them called `active_key` in a
vacuum and raised, so `assert call_count == 0` was vacuously true — no
production entry point ran, so of course nothing was fetched.

It also omitted `admin_cog.register_guild`, `admin_cog.set_live_leaderboard`
and `admin_cog.set_live_cluster_leaderboard`, which is precisely where the
confirmed slice-04/05 defects live.

**Fixed** by deriving the inventory from production and, more importantly, by
making the derivation an executed assertion:
`test_architecture_chokepoint.py::test_the_key_consumption_inventory_matches_production`
AST-scans `bot/` for calls to `active_key` / `verify_and_resolve` /
`install_guild_key` and requires set equality against the enum's declared
coordinates. Replayed against the old enum it reports 3 unaccounted and 2
stale, so it catches both failure directions.

**Owed upstream:** ADR-008 D3 says "seven call sites across three cogs plus a
service". That number was never true of this repository, and it is repeated in
`guild_keys.py`'s module docstring, in the chokepoint test's docstring and in
`test_slice_03`'s. The real shape is **six reader functions behind eight
driving ports**, plus one recovery entry point that must NOT refuse. The enum
no longer states a count, deliberately — a docstring that pins a number
invites the next reader to make the set fit it, which is what happened.

## UI-6 — the Tier B property executed zero assertions (FIXED this wave)

**Severity:** blocking — `kpi-contracts.yaml:159-168` cited it as KPI-2's only
property-based evidence.

`quarantine_is_never_a_trap` was an `@invariant()` that called `update_key`,
which RELEASES the quarantine. Hypothesis runs invariants in name order, so it
sorted ahead of `quarantined_guilds_never_write`, which consequently found the
guild ACTIVE at every step and short-circuited before its assertion.

Measured at 200 examples × 25 steps: `quarantined_guilds_never_write` executed
its assertion **0 times**; after the fix, **1037**.

**A correction to the escalation's wording.** The escalation reads "0
assertions". That is exactly right for `quarantined_guilds_never_write`, but
`quarantine_is_never_a_trap` executed its own assertion 988 times — it was not
idle, it was mutating. The distinction determined the fix: the reachability
check now runs against a `deepcopy` (so it stays a universally-quantified,
non-mutating claim about every reachable quarantined state), and the real
rescue moved to a `@rule` with a `@precondition`, so hypothesis chooses when to
take the exit and the model spends genuine stretches quarantined.

`test_both_properties_actually_assert_something` is the new structural guard:
it counts assertion-body executions and fails at zero. Restoring the original
mutation makes it fail with `assert 0 > 0`.

**Owed upstream:** UD-13 in the DELIVER upstream-issues list describes this as
a potential *flake*. The measured defect is *vacuity* — a different class with
a different fix. UD-13 should be reclassified.

## UI-7 — the fake could not emit hostile payloads (FIXED this wave)

**Severity:** blocking, and the root cause of the whole slice-04 defect class.

`GuildServiceResponse.payload()` could render exactly one shape: a well-formed
`{"guild": {...}}` whose `guildId` was one of two hand-picked canonical
constants, or the same with a field dropped. It could not express a non-JSON
body, a non-dict payload, a case/whitespace/BOM variant, a non-string
`guildId`, or a malformed roster entry.

That is not a coverage gap. It is an EXPRESSIVENESS gap: no scenario
expressible against that double could reach any slice-04 defect, so no amount
of diligence writing scenarios would have found them. **A double that can only
emit well-formed input certifies a parser that only handles well-formed
input.**

**Fixed** by `domain_types.VendorBody` (11 body shapes) +
`domain_types.GuildIdVariant` (14 identifier values) + a
`GuildServiceResponse.as_httpx_response()` renderer producing a real
`httpx.Response` — including one whose `.json()` raises. Defaults are
byte-compatible with the old behaviour, so no existing scenario changed.

## UI-8 — the transport double is duplicated across four test modules

**Severity:** low, but it is why UI-7 was invisible.
**Action needed:** consider consolidating when the suite is next touched.

`_tacticus_answered_by` / `_RecordedTacticus` are copied verbatim into
`test_slice_01`, `test_slice_02`, `test_slice_03` and now `test_slice_05` /
`test_slice_06`, each building `httpx.Response(status, json=...)`. That call
shape is *structurally incapable* of putting a non-JSON body on the wire, so
the limitation was replicated four times and read as a convention rather than
a constraint.

Slice 04 uses a renderer that delegates to the programmed answer
(`as_httpx_response`), which is the shape the others should converge on. Not
done here: UD-10 records that cross-importing these modules collides same-name
`conftest` constants, so consolidation needs its own change with its own test
run, and this wave's job was to make the defects reachable rather than to
refactor the harness.

## UI-9 — AC-008.5 as proposed is not a defect

**Severity:** medium — the slice brief asks for work that has nothing to fix.
**Action needed:** amend `slices/slice-05-close-the-write-holes.md`.

The brief groups `/set_live_leaderboard` with the cluster-leaderboard SPOF:
"`set_live_leaderboard` (`:404`) has the same shape." It does not.
`set_live_cluster_leaderboard` reads `next(iter(guilds))` — an arbitrary guild
unrelated to what the officer asked for — and aborts the whole cluster on it.
`set_live_leaderboard` reads `active_key(server_id, guild_id)` for the guild
the officer NAMED. No arbitrary pick, no cross-guild blast radius, no
fall-through to apply.

Verified: the scenario asserting the proposed behaviour passes against
production as it stands. It is kept as a regression guard, because the
AC-008.4 fall-through touches shared leaderboard code.

**Substituted AC-008.5b**, the defect the command does have: a quarantined
guild is refused with "❌ Guild `x` has no API key set." That is false — it has
a key — and it routes the officer to `/register_guild`, the command AC-008.1
shows overwrites the roster. Same defect shape as the "narrow the swallow"
item already in the slice.

**Open product question, deliberately not decided here:** should a QUARANTINED
guild be able to set up a live leaderboard over its own frozen historical
data? Read literally, the proposed AC implies yes. That means publishing a
live board over data the bot has stopped updating — a product call, not a
defect.

## UI-10 — AC-008.1 bundles two defects at two different depths

**Severity:** medium — the AC as written cannot be satisfied by one scenario.
**Action needed:** split it in the brief. Both halves are now covered.

*(Revised 2026-08-03 after an operator question. The first version of this
entry said the `is_former` clause was simply unreachable and folded it into
AC-008.3. That was half right and stopped one step too early — see below.)*

> **Superseded in part by UI-13 (same day, later).** The split described here
> is still correct, but the zero-rows clause is now carried by **AC-008.1c**
> (`test_registering_over_an_orphaned_quarantined_binding_writes_nothing`),
> not by the scenario named below, and AC-008.1 was re-scoped onto the
> registered-and-quarantined state. Read UI-13 before acting on this entry.

AC-008.1 asks that `/register_guild` against a quarantined guild write zero
player rows **and** leave "`is_former` untouched on every existing member".
Those two clauses need mutually exclusive preconditions.

**The zero-rows clause** needs the slash command's own `Given`: no guild row,
because `admin_cog.py:83` refuses an already-registered guild_id before any
probe. Reaching "quarantined binding, no guild row" means `_rollback_data`,
which also deletes `players` — the table CASCADEs from `guilds` exactly as
the binding does. So in that state there is no roster to flip, and the clause
is unobservable. Covered by `test_registering_over_an_orphaned_quarantined_
binding_writes_nothing` (**AC-008.1c** — renamed and re-based from
`test_registering_over_a_quarantined_binding_writes_nothing` on 2026-08-03,
because the `_rollback_data` route named in this paragraph is the one UI-13
had to remove).

**The `is_former` clause** needs a roster, therefore a guild row, therefore
NOT the slash command. And that is what the reproduction in
`remediation-plan.md` actually describes: "a scratch guild bound to
`word_bearers`' identity, quarantined, with the real Dark Mechanicum key
installed" is a REGISTERED guild. It would have been refused at
`admin_cog.py:83`. The measured "five real Word Bearers flipped to
`is_former = True`" cannot have come through `/register_guild`; it came from
the two calls the command makes at `admin_cog.py:121-124` —
`verify_and_resolve(enforce=False)` then `refresh_guild(snapshot)`.

That sequence is now driven directly by
`test_the_registration_sequence_does_not_flip_real_members_to_departed`
(AC-008.1b), on a registered + quarantined guild with a seeded roster. It
reds today with `['tacticus-uid-001', 'tacticus-uid-002']` marked departed.

The `enforce=False` detail is the sharp one: a gate that only fires under
`enforce=True` leaves this path fully open. That is the original defect's
shape — enforcement that depends on the caller asking for it — which is why
AC-008.3 moves the gate inside `verify_and_resolve` rather than adding a
second `enforce` flag.

**Owed upstream:** `remediation-plan.md`'s reproduction should say which
entry point it was driven through. Read literally it implies the slash
command, and the slash command cannot reach it.

## UI-11 — AC-009.5 does not say where the quarantine history is retained

**Severity:** low — a genuine open design question, not an omission.
**Action needed:** DELIVER decides; record the choice. **Resolved 2026-08-03.**

"Warn on re-registering a slug whose binding was quarantined" presumes the
history survives. The CASCADE that makes `/deregister_guild` destructive is
what drops the binding, so by the time the re-registration happens there is
nothing left to read. Two candidates, neither picked here: the
`guild.key.mismatch` records survive and carry `observed_id` (noted in
`remediation-plan.md`), or a tombstone row. The scenario asserts only that the
history is surfaced, not where it is kept.

### Resolution — DELIVER chose the tombstone, and the scenarios are compatible

DELIVER reported (2026-08-03) that it is taking a tombstone table
(`guild_key_quarantine_history`, no FK to `guilds`, written at deregistration)
over the surviving log records, on the grounds that those records live in a
`RotatingFileHandler` with `backupCount=5` — so the history expires silently
and reading it needs a log parser in a cog. That reasoning retires the
log-record candidate properly rather than by preference: a warning that stops
working after five rotations is worse than no warning, because it looks like
"no quarantine on record".

Checked against every scenario this wave authored. Nothing is made dishonest:

* AC-009.5 asserts only that the reply mentions the quarantine. A tombstone
  read satisfies it; the scenario never names a source.
* AC-009.6 counts rows in `guild_key_bindings` only. A tombstone deliberately
  has no FK and therefore survives `_rollback_data` — which is its purpose,
  not a repeat of the orphan defect. The two are different hazards: an
  orphaned BINDING is compared against a fresh key and silently adopted, a
  surviving TOMBSTONE is only ever read to warn. Fail-safe versus fail-open.
* AC-008.1c gates on `load_guild_binding(...).key_status`, not on history.
  It and AC-009.5 are independent requirements and a tombstone satisfies
  neither on the other's behalf — worth stating, because a tombstone read
  bolted onto `/register_guild` looks like it covers both and does not.

One consequence to record rather than act on: a parity rollback now erases
quarantine history entirely (bindings by AC-009.6, tombstones because a
re-migration starts from a JSON tree that has never held either). That is a
coherent reset — the whole cluster returns to unbound and trust-on-first-use
re-announces every adoption — not a laundering, and no scenario claims
otherwise.

## UI-12 — `bot/guilds.py:95` is a second unsanctioned key read

**Severity:** low — an addition to slice 07's scope, found by its own scenario.
**Action needed:** fold into slice 07's exemption list.

Slice 07's brief names `bot/guilds.py:79` (`load_guilds` putting the plaintext
into the dict handed to every cog). Running the widened scan finds `:95` as
well — `save_guilds` reading `data.get("api_key", "")` — plus
`bot/db/migrations_json_to_sqlite.py:444` and
`bot/migrations/to_cluster_layout.py:52,74`.

The two migration modules are legitimate: they read `api_key` in order to
migrate it, the same class of read the two repository adapters are already
sanctioned for. They belong in the explicit exemption list slice 07 already
calls for, with a reason each, per the pattern `EXEMPT_PLAYER_KEY_FUNCTIONS`
sets. Recorded so DELIVER does not meet them mid-slice and treat them as a
surprise.

---

# Escalation from DELIVER — UI-13 … UI-15 (2026-08-03)

> Raised by DELIVER mid-implementation of slices 05/06 and resolved by this
> designer, per the standing constraint that DELIVER does not author or edit
> acceptance assets. UI-13 is the escalated conflict. UI-14 and UI-15 are
> findings that fell out of resolving it.

## UI-13 — AC-008.1 and AC-009.6 could not both hold (RESOLVED this wave)

**Severity:** blocking — two ACs in the same remediation wave specified
mutually exclusive states, and no amount of correct production code could
satisfy both.

### What DELIVER found

AC-009.6 (slice 06) requires `_rollback_data` to leave zero rows in
`guild_key_bindings`; the fix is to add the table to
`_DATA_TABLES_DELETE_ORDER`. AC-008.1 (slice 05) built its `Given` by calling
that same `_rollback_data` and depended on it leaving the binding behind — an
orphaned quarantined binding with no guild row was the precondition. Once
AC-009.6 is satisfied the helper produces an **UNBOUND** guild,
`/register_guild` correctly adopts it under trust-on-first-use (DDD-8), and
AC-008.1 reds forever.

Verified by simulation before re-authoring: with `guild_key_bindings` prepended
to the delete order, the guild list is empty AND `load_guild_binding` returns
`is_unbound=True, key_status=active`. DELIVER's report is exact.

The helper's own docstring anticipated half of this ("if AC-009.5 is fixed by
adding the table to the delete order … this scenario reds again for a NEW
reason"). What it missed is that the new red is not fixable: a correct gate
MUST distinguish QUARANTINED from UNBOUND, and refusing the UNBOUND case is
forbidden by AC-008.2.

### The design error, stated plainly

The `Given` was assembled by calling a production function that a *different
AC in the same wave* was about to change. That was chosen for honesty — a
state reached through a real production path rather than by editing the
database behind its back — and the instinct was right. The failure mode it
missed is that such a `Given` does not break loudly when the other slice
lands; it silently becomes a **different** `Given`, and the scenario then reds
while reporting something that is no longer true. Coupling a precondition to
code under concurrent change is the same class of mistake as UI-5 (an enum of
call sites that drifted from production) and UI-6 (an invariant that mutated
the model it was quantifying over): a test asset that describes the system by
restating it rather than by pinning it.

### How it was resolved

**AC-009.6 stands unchanged.** The orphan leak is a confirmed defect with a
real re-adoption path.

AC-008.1 is **split along the two states a quarantined binding can be in**,
and neither `Given` now depends on `_rollback_data`:

| AC | State | Entry point | Today |
|---|---|---|---|
| AC-008.1 | REGISTERED + quarantined | `/register_guild` refused at `admin_cog.py:83` | RED on the reply |
| AC-008.1c | orphaned quarantined binding | `/register_guild` walks the full contamination path | RED on the roster |

**AC-008.1 (re-scoped)** — `test_registering_over_a_quarantined_guild_names_
the_way_out`. Post-AC-009.6 this is the ONLY reachable state a quarantined
binding can be in, and it is where every officer whose key drifted actually
is. The command replies "❌ A guild with ID `word_bearers` is already
registered. Choose a different ID or contact an admin to remove the existing
entry." Zero rows are written — so the write-hole half is already closed here,
and the two write assertions are kept as GUARDS. **The defect is the routing:**
"remove the existing entry" is `/deregister_guild`, which per AC-009.4
destroys the guild's entire raid history and per AC-009.5 launders the
quarantine on re-registration. An officer one command away from
`/update_guild_key` is handed a route through the two most destructive
commands in the cog. Same defect shape as AC-008.5b — a refusal that reaches
the operator as the wrong KIND of refusal — which is why it stays in slice 05.

**AC-008.1c (new)** — `test_registering_over_an_orphaned_quarantined_binding_
writes_nothing`. The original scenario's assertions carried over **verbatim**;
only the `Given`'s construction changed, from calling `_rollback_data` to
reproducing what the pre-fix `_rollback_data` did to these tables directly
(`PRAGMA foreign_keys=OFF`, delete `players`, delete `guilds`). Still reds
today with `['dm1','dm2','dm3']`, and — verified by simulation — still reds
identically with AC-009.6 applied. Justification for the state's honesty is
UI-14 below.

`_leave_an_orphaned_quarantined_binding` now **asserts its own
postcondition** — guild row gone, binding still QUARANTINED — with a message
naming the exact collapse that caused this escalation. A `Given` that can
silently degrade into the state whose opposite the scenario is about has to
check itself.

### On the regression guard

`test_registering_a_never_bound_guild_still_adopts_normally` is now explicitly
paired with **AC-008.1c, not AC-008.1**. Both enter `/register_guild` on a
guild with no row, so both take the same branch and are separated only by what
`load_guild_binding(...).key_status` says. That is the whole discrimination
the gate has to make, and neither forces it alone: the guard alone is
satisfied by an ungated command, AC-008.1c alone by one that refuses
everything. AC-008.1 lives in the already-registered branch and cannot
substitute for either. Recorded in both docstrings so the pairing survives the
next reader.

**Owed upstream — DONE 2026-08-03.** `slices/slice-05-close-the-write-holes.md`
and `slices/slice-06-admin-command-safety.md` described AC-008.1 and AC-009.6
as independent items. They are one state seen from two ends, and sequencing 06
before 05 without that note reds slice 05 for what looks like a slice-05
defect. Both briefs now carry a banner at the top saying so, with the split
table on the 05 side and the forward-only + do-not-revert warnings on the 06
side.

**One stale reference left deliberately unedited:**
`deliver/roadmap.json:451` still names
`test_registering_over_a_quarantined_binding_writes_nothing` in its
`scenario_name` field for the slice-05 step. That name no longer resolves.
It is DELIVER's artifact, so it is flagged rather than edited here — the
replacement is the two names in the table above.

## UI-14 — AC-009.6 is forward-only and does not clean up existing orphans

**Severity:** medium — it decides whether AC-008.1c's `Given` is residue or
fiction, and it is a real operational gap.
**Action needed:** DELIVER decides whether to add a one-time cleanup; either
way AC-008.1c stands. **Answered 2026-08-03 — see below.**

Adding `guild_key_bindings` to `_DATA_TABLES_DELETE_ORDER` stops
`_rollback_data` from CREATING orphaned bindings. It does not delete the rows
an earlier rollback already left. Any database that went through a parity
rollback during the SQLite cutover is carrying those rows now, and
`/register_guild` on such a slug walks the contamination path AC-008.1c
reproduces. So "orphaned quarantined binding" is **residue, not an
unreachable state**, and a scenario built on it is testing something an
operator can actually hit.

This is also why AC-008.1c is worth keeping rather than withdrawing once the
leak is closed. The two mechanisms guard the same hazard independently: the
delete order stops new orphans, the `/register_guild` gate makes the ones
already there harmless. One of them is a tuple literal in a migration module
that was already wrong once.

Not proposed as a new AC: a cleanup is a data migration with its own parity
question, and the gate makes the residue safe without one.

### DELIVER's answer (2026-08-03) — no cleanup migration

**Decided: no one-time cleanup for pre-existing orphaned bindings.** Two
reasons, both recorded so a later reader does not re-open it as an oversight:

1. The operator's standing instruction for this session is that live data is
   assumed to be in a good state. A cleanup migration written against an
   assumption of residue that may not exist is unbacked work carrying a real
   parity question.
2. The `/register_guild` gate (AC-008.1c) makes residue harmless wherever it
   does exist. The hazard is closed at the point of use rather than at rest,
   which is the cheaper and more durable of the two.

**This does not change AC-008.1c.** If anything it is what makes the scenario
load-bearing rather than belt-and-braces: with no cleanup, the gate is the
ONLY thing standing between an orphaned quarantined binding and a silent
re-adoption. Withdrawing it on the reasoning that AC-009.6 closed the leak
would now leave the residue case genuinely unguarded — the leak is closed
going forward, and nothing sweeps up what is already there.

## UI-15 — `_confirm_if_awaiting` named a seam production cannot implement (FIXED this wave)

**Severity:** blocking for AC-009.5 — and it would have planted, inside this
feature's own test suite, the exact defect class the feature exists to remove.

DELIVER asked whether `interaction.pending_confirmation` was the intended
contract, since the helper's docstring said only "DELIVER wires it here" —
which is both ambiguous and an instruction DELIVER is not permitted to follow.

**It was not implementable.** `discord.Interaction` declares `__slots__` and
no `__dict__` (verified against discord.py 2.7.1, the pinned version), so
`interaction.pending_confirmation = callback` raises `AttributeError` against
a real interaction. Production could have satisfied the double and crashed on
the first real click — code that is tested, wired, and broken.

**Fixed** with two seams that production can really use, both exercised by a
mutation check before this was recorded:

1. a `discord.ui.View` passed as `view=` to `response.send_message` /
   `followup.send`, carrying a button whose label or `custom_id` reads as a
   confirmation. This is the real widget and is the expected choice.
2. a zero- or one-argument callable (sync or async) in
   `interaction.extras["pending_confirmation"]`. `extras` is a real
   `Interaction` slot discord.py provides for this hand-off.

`_FakeInteraction` now captures `view=` — it used to swallow it with the rest
of `**kwargs`, so the double could not tell "the command paused" from "the
command did not pause".

**And AC-009.5 no longer assumes the deregistration happened.** It asserts it,
between the two commands. Without that guard the scenario had a live path to
passing for the wrong reason: with a confirmation in place and no way to take
it, the guild stays registered, `/register_guild` is refused as
already-registered, and — once the re-scoped AC-008.1 lands — that refusal
names the quarantine, so `assert "quarantin" in reply` would go GREEN while
asserting AC-008.1's behaviour. AC-009.4 and AC-009.5 land in the same slice,
so this was not hypothetical.

AC-009.4 still does not pin the mechanism. It asserts "nothing has been
deleted yet" BEFORE any confirmation, which any widget satisfies.
