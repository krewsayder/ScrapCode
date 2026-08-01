# Slice 02 — `/update_guild_key`

**Feature:** `guild-key-integrity` · **Stories:** US-003
**Estimate:** ~0.5 day (≤6 h crafter dispatch) · **Order:** 2nd

## Goal

An admin replaces a guild's Tacticus API key from Discord in one command,
with the resolved guild identity shown before the key is trusted, and
without touching a single dependent row.

## Learning hypothesis

**Disproves** "a guild key can be replaced through the existing
`save_guilds` seam without touching dependent rows" **if** the write
turns out to require more than the guild row — for example if the
`api_key_hmac` UNIQUE constraint collides, or if `_upsert_guilds`'
delete-absent behaviour reaches rows it should not.

**Confirms** the recovery path exists, which D3 makes a hard
pre-condition for Slice 03. Low uncertainty by design: the path was
proven by hand on the production VM on 2026-07-31 (`before: a4016f75` →
`after: 864e6e18`, `match: True`). This slice turns that manual proof
into a command.

## IN scope

- `/update_guild_key guild_id:<id> api_key:<key> [force:bool]`, admin tier
- Probe the **submitted** key before storing; refuse on mismatch, dead
  key, or unreachable
- `force:true` installs a mismatching key **and** re-binds the identity
- Atomic `api_key` + `api_key_hmac` write through `save_guilds`
- Clears `key_status` / `quarantine_reason` on a successful matching
  install (so quarantine is never a trap once Slice 03 lands)
- Ephemeral response; key value never echoed, logged, or printed

## OUT of scope

- Quarantine enforcement (Slice 03) — this slice only *clears* the state
- Registration keys (`/registration register` already covers those)
- Any data purge or re-ingestion
- Bulk / multi-guild key update

## Acceptance criteria

AC-003.1 – AC-003.10. See `../feature-delta.md`.

The load-bearing one is **AC-003.2**: `players`, `battle_hits` and
`bomb_hits` counts identical before and after. This is the property that
makes the command safe where the current workaround
(`/deregister_guild` + `/register_guild`) is not — those CASCADE-delete
every dependent row via the `ondelete="CASCADE"` foreign keys in
`bot/db/models.py`.

## Production-data criterion

Not synthetic. The dogfood run re-installs the **current, correct**
`word_bearers` key through the command — a semantic no-op that must
nonetheless exercise the full write path — and asserts the three row
counts are unchanged.

## Dogfood moment (same day)

The operator replaces a real guild key through Discord and confirms the
next hourly `auto_update` succeeds for that guild. This retires the
manual procedure (SSH → `systemctl stop` → DB backup → throwaway script
→ restart) on the day it ships, independently of the rest of the feature.

## Dependencies

- Slice 01's identity probe (reused to resolve the submitted key)
- Slice 01's binding columns (read for the mismatch comparison, written
  on `force:true`)

## Reference class

`/registration validate_keys` (shipped 2026-07-30, commit `ba6d3e8`) —
same shape: an officer-facing command that probes Tacticus with a stored
key, classifies the result, and reports without printing the key.
`_probe_api_keys` and `_format_key_validation` in
`bot/cogs/registration_cog.py` are the pattern to follow.

## Pre-slice SPIKE

**Not required.** The write path was executed successfully against the
production database on 2026-07-31 and verified by reading the key back
and comparing to the submitted plaintext.

## Risk

The command accepts a secret as a slash-command parameter, which Discord
transmits to its own servers and which may appear in a client-side
command history. This is inherent to the surface and is the same exposure
`/registration register` already carries for player keys. Mitigated by
`ephemeral=True` and by never echoing the value — not eliminated. Worth
stating plainly to the operator rather than implying the command is
leak-proof.
