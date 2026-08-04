# Slice 04 — Survive hostile vendor output

**Feature:** `guild-key-integrity` · **Remediates:** US-001, KPI-4, DISCUSS D3
**Estimate:** ~1 day (≤6 h crafter dispatch) · **Order:** 1st of the remediation set

## Goal

The classifier is **total** over anything Tacticus can return: no vendor
response can crash the hourly loop, and no response that names the bound guild
can be read as drift.

## Learning hypothesis

**Disproves** "`parse_guild_snapshot` classifies vendor output correctly"
**if** any well-formed response naming the bound guild still quarantines, or
any malformed response still escapes as an exception.

**Confirms** that the five-outcome classification is a real partition of the
input space rather than a partition of the *fixtures*. Every other slice
assumes the classification is trustworthy; if it is not, enforcement built on
it amplifies the error instead of preventing it.

## IN scope

- **Loop-death guard.** `response.json()` and the payload walk move inside a
  guard. A 200 whose body is not JSON, or is JSON of the wrong shape,
  classifies as **UNVERIFIABLE** — not UNREACHABLE (the key worked; the
  *check* did not) and never as an escaping exception.
  [`guild_client.py:183`](../../../../bot/services/tacticus/guild_client.py#L183)
- **Total payload walk.** `payload.get("guild")` on a non-dict, a truthy
  non-dict `guild`, and the eager `frozenset(m["userId"] …)` on a malformed
  roster entry all classify instead of raising.
  [`guild_client.py:109,110,130`](../../../../bot/services/tacticus/guild_client.py#L109)
- **Canonicalisation before `GuildIdentity` is constructed:** reject non-`str`,
  strip whitespace and BOM, validate against the existing `UUID_PATTERN`
  ([`guilds.py:63-66`](../../../../bot/guilds.py#L63)), and classify a value
  that fails as UNVERIFIABLE. Comparison casefolds.
  [`guild_client.py:112`](../../../../bot/services/tacticus/guild_client.py#L112)
- **`matches()` compares canonically**, not raw `==`.
  [`guild_client.py:71-72`](../../../../bot/services/tacticus/guild_client.py#L71)
- **Same canonicalisation in `install_guild_key`** — otherwise a poisoned
  binding still refuses the operator's correct key.
  [`guild_keys.py:253-261`](../../../../bot/guild_keys.py#L253)
- **Cycle-level containment:** a per-server `try` in `auto_update` plus an
  `@auto_update.error` handler, so no future unhandled exception can silently
  end the loop for every server.
  [`tasks_cog.py:191-200`](../../../../bot/cogs/tasks_cog.py#L191)
- Data migration for already-adopted non-canonical `tacticus_guild_id` values
  **iff** the SPIKE finds any.

## OUT of scope

- Any change to what the five outcomes *mean* — this slice makes the existing
  partition total, it does not redesign it
- Retry/backoff on a malformed response (UNVERIFIABLE already means "retry
  next cycle, change nothing")
- Vendor schema versioning or a contract-test expansion (owned by the
  designer; see the escalation in `../remediation-plan.md`)
- The `guildId`-absent path — already correct, do not touch

## Required behaviour (proposed AC-007.x — tests owned by `@nw-acceptance-designer`)

| # | Given a 200 whose `guildId` is | Outcome |
|---|---|---|
| 1 | `"B64BDBA4-…"` (case differs only) | **MATCH** |
| 2 | `" b64bdba4-… "` / `"﻿b64…"` / `"b64…\n"` | **MATCH** |
| 3 | `"   "` (whitespace only) | UNVERIFIABLE |
| 4 | `12345` (JSON number) | UNVERIFIABLE |
| 5 | body is HTML / empty / truncated / `null` | UNVERIFIABLE, **no exception** |
| 6 | payload is a list / string / bool | UNVERIFIABLE, **no exception** |
| 7 | `guildId` matches but one member entry lacks `userId` | MATCH, roster degrades — loop survives |
| 8 | genuinely different, canonical uuid | MISMATCH (control — must still quarantine) |

Plus: after any of 1–7, `key_status` is byte-identical, and the hourly loop is
still running on the next cycle.

## Production-data criterion

Not synthetic. Replay the **recorded** Tacticus response
(`fixtures/guild_response_recorded.json`) with its `guildId` re-cased and
BOM-prefixed, and assert MATCH against the real stored binding. The fixture's
own header notes `guildId` is *undocumented* by the vendor — the whole payload
is unversioned output, which is the argument for totality.

## Dogfood moment (same day)

Point the bot at a local stub returning `200 text/html` (an nginx 502 page),
watch the cycle classify UNVERIFIABLE and **keep running**, then restore the
real endpoint and watch the same guild ingest normally in the next cycle.
Today that stub ends ingestion for every server until restart.

## Dependencies

None. Deliberately first: every later slice trusts the classification.

## Pre-slice SPIKE — **required**, ~30 min

Query the live DB for stored bindings that are not canonical:

```sql
SELECT discord_server_id, guild_id, tacticus_guild_id
FROM guild_key_bindings
WHERE tacticus_guild_id <> lower(trim(tacticus_guild_id));
```

Any row means canonicalising the *comparison* alone silently changes which
guilds match, so the slice must also migrate stored values — and any guild
already quarantined by this defect needs releasing, not just fixing.

## Reference class

`sqlite-backend` Slice 02 (probe + atomicity) — hardening one function's
failure modes behind a stable signature, where the risk is an unenumerated
input rather than a hard problem in any single branch.

## Risk

**Under-blocking.** Canonicalisation makes the comparison *more* permissive;
a sloppy implementation (e.g. normalising away hyphens, or `casefold()` on a
non-uuid string that then passes `UUID_PATTERN`) could make two genuinely
different guilds compare equal — which is the original incident. AC-007.8 is
the regression guard and must fail before the slice is accepted.
