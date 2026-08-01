# Slice 01 — Bind and report Tacticus guild identity

**Feature:** `guild-key-integrity` · **Stories:** US-006 (precursor), US-001, US-002
**Estimate:** ~1 day (≤6 h crafter dispatch) · **Order:** 1st

## Goal

Every registered guild carries the Tacticus identity its key resolves to,
and a drift from that identity is reported within the hour — without
blocking anything yet.

## Learning hypothesis

**Disproves** "`GET /api/v1/guild` returns a `guildId` we can bind on"
**if** the field is absent for some guilds, or drifts between calls for
an unchanged key.

**Confirms** the D1 binding (`guildId` alone; tag and name display only)
is implementable against the live API — the pre-commitment Slice 03 is
built on. `guildId` is tracked and returned but **undocumented**, so this
slice exists to prove it is dependable across every registered guild
before enforcement depends on it.

## IN scope

- Alembic revision adding to `guilds`: `tacticus_guild_id` (the binding),
  `tacticus_guild_tag` + `tacticus_guild_name` (display only),
  `identity_bound_at`, `key_status`, `quarantine_reason`, `quarantined_at`
- `bot/models.py:Guild` extended; `load_guilds` **and** `save_guilds`
  round-trip every new field
- One identity-probe function: `GET /api/v1/guild` → classify as
  `match` / `mismatch` / `dead key` / `unreachable`
- Trust-on-first-use adoption for guilds with no binding, announced once
- Mismatch reported in `auto_update`'s update-channel summary and in
  `/update_leaderboard`'s response
- `/view_config config:guilds` shows the bound tag, uuid prefix, and
  `identity_bound_at`

## OUT of scope

- Any blocking behaviour — ingestion still proceeds on mismatch (D3)
- `/update_guild_key` (Slice 02)
- The D6 single-chokepoint accessor (Slice 03)
- The season-detection SPOF fix (Slice 03)
- Alert rate-limiting (Slice 03; this slice's mismatch is a summary line,
  not a standalone alert)

## Acceptance criteria

AC-006.1 – AC-006.3, AC-001.1 – AC-001.9, AC-002.1 – AC-002.6.
See `../feature-delta.md`.

## Production-data criterion

Not synthetic. The acceptance run probes the **live** `word_bearers` key
and asserts the resolved identity is tag `EUVQZ` / uuid prefix
`b64bdba4`. The known Dark Mechanicum key (tag `PXGQW`, uuid prefix
`d71d583f`) is the negative case for the mismatch path.

## Dogfood moment (same day)

Within one hour of deploy, the operator reads the first-bind
announcements for every registered guild in the update channel and
confirms each guild's adopted tag is the one they expect. This is also
the first time the multi-guild sweep question gets answered as a
by-product: any *other* guild whose key resolves somewhere unexpected
shows up in the same batch of announcements.

## Dependencies

- SQLite backend live (confirmed in production 2026-07-31)
- `SCRAPCODE_DB_KEY` present — the guild row write path goes through
  Fernet + HKDF-HMAC

## Reference class

The `sqlite-backend` slices 02 and 03 (schema + repo change, ~1 day
each). This slice is smaller in schema surface (six columns on one
existing table, no data migration) but adds an external call path.

## Pre-slice SPIKE

**Not required.** The uncertainty this slice carries is precisely what
the slice measures, and it fails loudly rather than silently: AC-001.5
classifies a missing `guildId` as `unverifiable` and alerts, with no
quiet fallback to a weaker check. A separate spike would cost more than
just shipping the slice.

## Risk

Trust-on-first-use (D5) will adopt whatever identity the current key
resolves to. If a guild other than `word_bearers` is *also* currently
holding a drifted key, this slice will silently bind to the **wrong**
identity and report nothing. The first-bind announcement is the only
mitigation — the operator must actually read it. Worth doing the
announcement pass deliberately rather than letting it scroll past.
