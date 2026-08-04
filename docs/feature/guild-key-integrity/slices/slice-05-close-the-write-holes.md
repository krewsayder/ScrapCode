# Slice 05 — Close the write holes

**Feature:** `guild-key-integrity` · **Remediates:** US-003, KPI-2, KPI-5, DDD-3
**Estimate:** ~1 day (≤6 h crafter dispatch) · **Order:** 2nd of the remediation set

> ## ⚠ Read before sequencing against slice 06
>
> **AC-008.1 here and AC-009.6 in slice 06 are one state seen from two ends.**
> Slice 06 stops a parity rollback from leaving orphaned `guild_key_bindings`
> behind; this slice governs what `/register_guild` does when it meets one.
>
> They used to be coupled through code: AC-008.1's `Given` was built by calling
> the very `_rollback_data` that AC-009.6 changes. Landing 06 first therefore
> turned AC-008.1's precondition into a *different* precondition — an UNBOUND
> guild, which registration correctly adopts — and the scenario went red for a
> reason that had nothing to do with slice 05. **A reader who sequenced 06
> before 05 would have read that as a slice-05 defect. It is not.**
>
> Resolved 2026-08-03 by the DISTILL escalation
> (`distill/upstream-issues.md` UI-13). AC-008.1 was split so that neither half
> depends on `_rollback_data`, and the two slices are now order-independent:
>
> | AC | State it governs | Scenario |
> |---|---|---|
> | **AC-008.1** | REGISTERED + quarantined — the state every drifted guild is actually in | `test_registering_over_a_quarantined_guild_names_the_way_out` |
> | **AC-008.1c** | orphaned quarantined binding — the residue a pre-AC-009.6 rollback left | `test_registering_over_an_orphaned_quarantined_binding_writes_nothing` |
>
> Two corrections to this brief's ACs follow from that split, both already
> reflected in the suite:
>
> * **AC-008.1's zero-rows clause is now a guard, not the reproduction.** In
>   the registered state the command already writes nothing (it refuses at
>   `admin_cog.py:83`). The live defect is the *routing*: the refusal sends the
>   officer to `/deregister_guild`, which slice 06 shows destroys the guild's
>   entire history and launders the quarantine on re-registration.
> * **The `is_former` clause never belonged on either.** `players` CASCADEs
>   from `guilds`, so no route to "no guild row" leaves a roster to flip. It is
>   asserted in AC-008.1b via the registration *sequence*. See UI-10.

## Goal

A quarantined guild writes **zero rows at every site**, including the three
that slice 03 never gated — and one quarantined guild never disables a
cluster-wide command.

## Learning hypothesis

**Disproves** "one chokepoint gates all key-consumption sites" **if** any site
still reaches a key without a status check after this slice, or if the
enumeration of sites cannot be made authoritative.

**Confirms** that the chokepoint is a *structural* guarantee rather than a
convention that today's call sites happen to honour. Slice 03 claimed this and
shipped with three sites ungated and an enum that named the wrong seven — so
the claim itself is what is under test here.

## IN scope

- **Make the chokepoint self-enforcing.** `_is_quarantined` currently has
  exactly one caller — inside `active_key`. `verify_and_resolve`, whose own
  docstring says *"this is what an ingestion path calls"*, reads the key at
  [`guild_keys.py:112`](../../../../bot/guild_keys.py#L112) with no status
  check. Every current caller is safe only because it happens to call
  `active_key` first. Move the gate inside so safety does not depend on call
  order.
- **Gate `/register_guild` → `refresh_guild`** (D6 site #7).
  [`admin_cog.py:121-124`](../../../../bot/cogs/admin_cog.py#L121) calls
  `verify_and_resolve(enforce=False)` and hands the snapshot straight to
  `refresh_guild`, never calling `active_key`. Must refuse a **quarantined**
  guild while still allowing an **unbound** one (trust-on-first-use, DDD-8, is
  the whole point of the probe here).
- **Narrow the swallow.** [`admin_cog.py:132`](../../../../bot/cogs/admin_cog.py#L132)
  `except Exception` would catch the refusal added above and report it as
  "player list could not be fetched". The refusal must reach the operator as a
  refusal, naming `/update_guild_key` as the exit.
- **Apply the DDD-7 fall-through to the two leaderboard commands.**
  [`admin_cog.py:485`](../../../../bot/cogs/admin_cog.py#L485)
  `set_live_cluster_leaderboard` uses `next(iter(guilds))` and aborts the whole
  cluster if that one guild's key is unusable — the identical SPOF AC-004.7
  fixed in `_current_season` and never applied to the siblings.
  `set_live_leaderboard` ([`:404`](../../../../bot/cogs/admin_cog.py#L404))
  has the same shape.

## OUT of scope

- Re-ingesting or repairing rosters already corrupted by the `/register_guild`
  hole — a data-repair decision, not a code fix (see Risk)
- The `player_registrations` key exemptions — verified correct, do not touch
- Extending the AST chokepoint scan beyond `bot/cogs` + `bot/services`
  (belongs with slice 07's composition-root work)
- Fixing `KeyConsumptionSite` — **acceptance-designer's**, see Dependencies

## Required behaviour (proposed AC-008.x — tests owned by `@nw-acceptance-designer`)

1. `/register_guild` against a **quarantined** guild writes zero `players`
   rows, leaves `is_former` untouched on every existing member, and replies
   with a refusal naming `/update_guild_key`.
2. `/register_guild` against an **unbound** guild still adopts and populates
   normally (trust-on-first-use unbroken).
3. `verify_and_resolve` called directly on a quarantined guild refuses,
   without the caller having called `active_key` first.
4. `/set_live_cluster_leaderboard` with the quarantined guild **first** in
   iteration order still writes the cluster leaderboard, using a healthy
   sibling's key.
5. Same for `/set_live_leaderboard`.
6. All-quarantined remains a clean skip with a stated reason (regression
   guard on AC-004.8).

## Production-data criterion

Not synthetic. Reproduce the confirmed failure first: a scratch guild bound to
`word_bearers`' identity, quarantined, with the real Dark Mechanicum key
(tag `PXGQW`, uuid prefix `d71d583f`) installed. Today `/register_guild`
writes Dark Mechanicum's members in and flips the five real Word Bearers
members to `is_former = True`. The slice is done when that write count is 0.

## Dogfood moment (same day)

Operator quarantines a scratch guild, runs `/register_guild` against it, and
is refused with a pointer to `/update_guild_key` — then runs
`/set_live_cluster_leaderboard` with that guild sorted first and watches the
cluster leaderboard build anyway from a healthy sibling.

## Dependencies

- **Slice 04** — gating a site is only worth doing once the classification
  driving quarantine is trustworthy.
- **HARD, external: the `KeyConsumptionSite` escalation must land first.**
  [`domain_types.py:48-63`](../../../../tests/acceptance/guild-key-integrity/domain_types.py)
  omits the three sites this slice fixes, and two of its remaining branches
  drive no production code. The AC that is supposed to prove "all seven sites"
  is parametrized over the wrong set, so **this slice cannot be verified until
  the designer corrects it.** Do not dispatch the crafter before then.

## Reference class

Slice 03 itself — same shape (many call sites behind one seam, risk is a
missed site). It is the reference class *and* the cautionary tale: it shipped
believing it had covered seven sites when it had covered four real ones.

## Pre-slice SPIKE

**Not required.** All four defects are confirmed with reproductions; the work
is known, not uncertain.

## Risk

**Already-corrupted rosters are not detectable after the fact.** The
`/register_guild` hole leaves a guild quarantined with a silently overwritten
roster, and the hourly cycle then skips it forever, so nothing re-reads or
corrects it. `/view_config` shows ⛔ with no hint the roster was replaced.
Closing the hole stops new corruption; it does not find old corruption. If
this command has been run against a quarantined guild in production, treat
roster repair as separate work and scope it from the `guild.key.mismatch`
records, which do carry `observed_id`.
