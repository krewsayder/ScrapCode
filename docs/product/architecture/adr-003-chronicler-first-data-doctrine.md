# ADR-003: Chronicler-first data doctrine

- **Status:** Accepted — as-built, retroactive
- **Date:** 2026-07-18 (recorded; decision predates this baseline)
- **Closes:** Gap 8 (seed ADR); Gap 2 (external calls) in decision form
- **Related:** [brief.md §2.5](brief.md#25-external-calls--tacticus-direct-vs-chronicler-_closes-gap-2--feeds-adr-003)

## Context

ScrapCode talks to two external systems: **Tacticus** (`api.tacticusgame.com`,
the game's own API) and **Chronicler** (`www.chronicl3r.com`, a third-party service
that aggregates Tacticus player profiles and rosters). Several data classes can be
sourced from either. Without a doctrine, an agent might fetch from Tacticus what
Chronicler already serves, duplicating work, diverging on stale data, and burning
the user's Tacticus API quota.

## Decision

**Chronicler-first:** never fetch from Tacticus what Chronicler already serves.
Today Chronicler is the source of **player identity / display name** (profile
lookup + registration + roster-aware player-list refresh). Tacticus-direct is
used only for data Chronicler does **not** serve — real-time raid state — plus the
guild roster, which the bot currently pulls directly to drive roster sync.

### Enumerated direct-Tacticus calls allowed today

These are the *only* direct-Tacticus calls found in source, with the reason each is
allowed (i.e. not served by Chronicler today):

| # | Endpoint | Caller(s) | Why direct (not Chronicler) |
|---|----------|-----------|------------------------------|
| 1 | `GET /api/v1/player` | `tasks_cog.cap_detect`, `token_cog`, `bomb_cog`, `registration_cog.register` (validation) | Real-time raid-token / bomb-token progress and live API-key validation (401). Chronicler does not serve real-time token state. |
| 2 | `GET /api/v1/guild` | `PlayerService._fetch_roster` | Current guild member `userId` set, used to mark `is_former` and seed new players. Currently direct; roster may move to Chronicler later. |
| 3 | `GET /api/v1/guildRaid` (current) | `tasks_cog.auto_update`, `admin_cog.set_live_leaderboard`, `admin_cog.set_live_cluster_leaderboard` | Current season number discovery. |
| 4 | `GET /api/v1/guildRaid/{season}` | `tasks_cog.auto_update`, `update_cog.update_leaderboard`, `update_cog.update_all` | Per-season raid hit/bomb entries merged by `tracker.process_api_response`. The Chronicler namespace is `tacticus-guild-raid`, so raid data *may* migrate to Chronicler; until then it is fetched direct. |

Anything not in this table must **not** be added as a direct Tacticus call. If a
future feature needs data Chronicler already exposes, it goes through
`services/chronicl3r`.

### Where Chronicler is used today

`chronicl3rClient` (`bot/services/chronicl3r/client.py`): `api/auth/token/`
(auth), `player-profile/register/` (register), and
`player-profiles/{id}/api-key/` (GET profile, PATCH API key).
`PlayerService` wraps these: `get_or_register`, `refresh_guild`,
`validate_if_stale` (refreshes if `last_validated` older than
`STALE_AFTER_HOURS = 1`), `ensure_player_in_list`, `get_display_name`.

## Consequences

- **API-key validation stays Tacticus-direct.** `registration register` validates
  a submitted key by hitting Tacticus `/api/v1/player` (401 = bad key), even though
  Chronicler also stores keys via `set_player_api_key`. The bot keeps its own copy
  in `player_registrations.json`. This is allowed (call #1) but means key validity
  is checked against the game, not against Chronicler.
- **Chronicler is sync `requests` wrapped in `asyncio.to_thread`.** All Chronicler
  calls block a worker thread; do not add long-running Chronicler calls on the
  event loop without `to_thread`.
- **The raid-data calls (#3, #4) are the likely migration surface.** When
  Chronicler serves raid data, calls #3/#4 move behind `services/chronicl3r` and
  this ADR's table shrinks. Until then they remain allow-listed direct calls.

## Alternatives considered

- **Tacticus-first everywhere.** Rejected: it duplicates Chronicler's aggregation,
  risks stale identity, and wastes the Tacticus key budget on data Chronicler
  already normalizes.
- **Chronicler-only (ban all direct Tacticus).** Not possible today: Chronicler
  does not serve real-time token/bomb state or per-season raid entries, which are
  core to the bot's purpose.