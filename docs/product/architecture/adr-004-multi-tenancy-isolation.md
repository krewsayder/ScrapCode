# ADR-004: Multi-tenancy isolation rules

- **Status:** Accepted — as-built, retroactive
- **Date:** 2026-07-18 (recorded; decision predates this baseline)
- **Closes:** Gap 8 (seed ADR); Gap 3 (multi-tenancy) in decision form
- **Related:** [brief.md §3](brief.md#3-multi-tenancy-_closes-gap-3--decision-form-in-adr-004),
  [ADR-002](adr-002-storage-backend-json-legacy.md)

## Context

One ScrapCode process serves multiple Discord servers. State for each server lives
under `clusters/{discord_server_id}/`. Most code threads `discord_server_id`
(correctly from `interaction.guild_id`) through every data call. But a handful of
paths are *not* tenant-isolated today (see brief §3.2), and an agent could
silently extend the leak. This ADR pins the rules so future work does not regress
isolation.

## Decision — rules an agent must never violate

1. **Thread `discord_server_id` into every data call.** Get it from
   `interaction.guild_id` in command handlers; iterate `repo.list_server_ids()`
   in task loops. Never read or write another tenant's path. The `bot/guilds.py`
   wrappers take `discord_server_id` as the first argument for a reason — use them
   rather than `repo` directly when a wrapper exists.
2. **Never join data across Discord servers.** Cluster-wide leaderboards
   (`/view_cluster_leaderboard`, `set_live_cluster_leaderboard`,
   `_refresh_live_leaderboards` with key `cluster`) merge *guilds within one
   server*, never guilds across servers. Keep it that way.
3. **Never hardcode a Discord server/guild/channel ID in runtime code.** The
   hardcoded `DEV_GUILD_IDS` (command sync, `main.py`), the replay
   `FORUM_CHANNELS`/`MAP_THREADS` (`replay_cog.py`), and the `migrations/`
   scripts' `SERVER_ID` constants are existing exceptions — flagged leaks or
   run-once scripts, not patterns to copy.
4. **Per-server config is per-server.** `role_tiers`, `member_role_ids`,
   notification channels, and live-leaderboard configs are stored in each
   server's own `guilds.json` / `live_leaderboards.json`. Do not share or cache
   them across tenants.
5. **Task loops operate on one server at a time.** `cap_detect` and `auto_update`
   iterate servers and must keep each iteration's reads/writes confined to that
   server. Do not accumulate cross-server state between iterations.
6. **External service identity is universal, not per-tenant — by design.**
   Chronicler and Tacticus keys are keyed by `tacticus_user_id` / guild, which are
   game-level identifiers shared across Discord servers. Sharing the
   `chronicl3rClient` (one credential set) is correct; it is not a tenancy leak.

## Known isolation leaks (flagged, not fixed)

These are documented in [brief.md §3.2](brief.md#32-tenancy-leaks-flagged-not-fixed)
and are **out of scope** to fix in this documentation baseline. An agent must not
add new code that depends on or extends them:

- **`replay_index.json` is global** (project root; `replay_cog.py` ignores
  `interaction.guild_id`). Replay submissions from all servers share one index.
- **Replay forum/thread IDs are hardcoded** to one server's channels/threads.
- **`auto_update` posts to one global `UPDATE_CHANNEL_ID`** regardless of which
  server was updated.
- **Command sync is dev-guild-scoped** (`DEV_GUILD_IDS`), not per-tenant.
- **`REPLAY_INDEX_CHANNEL_ID` is dead config** — defined in `config.py`, never used.
- **`file_lock` is process-global** — serializes writes across all tenants (shared
  resource, not a data leak).

## Consequences

- The leaks mean replays and the auto-update summary are effectively single-tenant
  in implementation; any multi-tenant replay feature must first relocate
  `replay_index.json` under `clusters/{id}/` and make forum routing configurable.
- Because isolation is enforced by convention (threading `discord_server_id`)
  rather than by a hard boundary, new code is only as isolated as the author is
  careful. Reviewers must check rule #1 on every data-touching change.

## Alternatives considered

- **A hard tenant boundary (separate process / separate bot per server).** Rejected
  at current scale; one process with per-server partitioning is the as-built model
  and matches a small operator footprint.
- **A tenant context object threaded explicitly instead of passing
  `discord_server_id` everywhere.** Reasonable future refactor; not warranted
  for this baseline, which changes no behavior.