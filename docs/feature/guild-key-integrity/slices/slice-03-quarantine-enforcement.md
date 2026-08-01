# Slice 03 — Enforce quarantine on identity mismatch

**Feature:** `guild-key-integrity` · **Stories:** US-004, US-005
**Estimate:** ~1 day (≤6 h crafter dispatch) · **Order:** 3rd (last)

## Goal

A guild whose key has drifted to another Tacticus guild stops ingesting
entirely — roster and hits — and says so, while every other guild in the
cluster keeps running normally.

## Learning hypothesis

**Disproves** "one chokepoint can gate all seven key-consumption sites"
**if** any site bypasses it, and **disproves** "quarantining one guild is
survivable" **if** the season-detection SPOF cannot be cleanly fixed.

**Confirms** that prevention is real rather than advisory. Slice 01 only
reports; until this slice lands, a drifted key still contaminates.

## IN scope

- Single accessor (D6) that returns a guild's usable key only when
  `key_status = 'active'`; all seven call sites routed through it
- Quarantine on mismatch: set `key_status`, `quarantine_reason`,
  `quarantined_at`
- Block **both** `process_api_response` and
  `validate_if_stale` / `refresh_guild` (D2)
- Alert to update channel **and** the guild's `notification_channel_id`,
  rate-limited to once per 24 h per guild
- **Season-detection SPOF fix** — `auto_update` skips quarantined guilds
  when picking a key for season discovery, falls through to the next
  usable one, and reports an explicit reason when none remain
- `/view_config config:guilds` renders quarantine state (US-005)

## OUT of scope

- Automatic recovery / retry-until-it-works — a human installs a key
- Data purge or re-ingestion of anything contaminated before quarantine
- Quarantine for `player_registrations` keys
- Alerting outside Discord

## Acceptance criteria

AC-004.1 – AC-004.10, AC-005.1 – AC-005.4. See `../feature-delta.md`.

Two carry the slice:

- **AC-004.6** — parametrised across all seven D6 call sites. A guard on
  six of seven is the exact failure mode this AC exists to catch, and a
  non-parametrised test would not catch it.
- **AC-004.7** — a quarantined guild that is *first* in the dict must not
  take down the server. `auto_update` currently derives the season from
  `next(iter(guilds.values()))` ([tasks_cog.py:173](../../../../bot/cogs/tasks_cog.py#L173))
  and skips the whole server when that fails
  ([tasks_cog.py:187-189](../../../../bot/cogs/tasks_cog.py#L187-L189)).
  Without this fix, quarantine turns a one-guild problem into a
  cluster-wide outage — strictly worse than the bug being fixed.

## Production-data criterion

Not synthetic. The negative case uses the **real** Dark Mechanicum key
(tag `PXGQW`, uuid prefix `d71d583f`) installed against a scratch guild
bound to `word_bearers`' identity, and asserts zero rows written to
`battle_hits`, `bomb_hits`, and `players`.

## Dogfood moment (same day)

The operator installs the known Dark Mechanicum key against a scratch
guild via `/update_guild_key … force:false`, watches it refused, then
forces it and watches the next `auto_update` quarantine the guild and
alert — while `word_bearers` and every other guild update normally in the
same cycle.

## Dependencies

- **Slice 01** — the binding must exist and be trusted before it can gate
- **Slice 02 (hard, per D3)** — `/update_guild_key` is the only exit from
  quarantine. Shipping this slice first makes the first quarantine event
  unrecoverable without SSH, which is worse than the status quo.

## Reference class

`sqlite-backend` Slice 04 (rewire + flip + cutover) — same shape:
touching many call sites at once behind one seam, where the risk is a
missed site rather than a hard problem at any single one.

## Pre-slice SPIKE

**Not required**, provided Slice 01 confirmed its hypothesis. If Slice 01
finds the identity discriminator unreliable, **stop and re-plan** — do
not build enforcement on an unstable signal.

## Risk

Highest-risk slice in the feature, and deliberately last. Two failure
modes:

1. **Over-blocking.** A bug that quarantines a healthy guild stops its
   data entirely. Mitigated by D4 (transport failures never quarantine)
   and by Slice 01 having run in report-only mode long enough to show the
   mismatch rate is zero in steady state — check the report output before
   flipping enforcement on.
2. **The season SPOF.** Fixing it touches the loop that drives every
   guild's update. AC-004.9 (a healthy guild still updates when a
   sibling is quarantined) is the regression guard.
