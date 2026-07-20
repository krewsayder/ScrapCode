# ScrapCode — Architecture Baseline (as-built)

> **Status: AS-BUILT, RETROACTIVE.** This document captures the ScrapCode Discord bot
> *as it exists in code today*. It is a documentation-only baseline produced by a
> read-only pass over the repository. Nothing here is a prescription for future
> behavior; nothing here authorizes a refactor. Where code contradicts the README
> or in-bot help text, the contradiction is **flagged**, not resolved — see
> [README / HELP_DATA drift](#readme--help_data-drift).
>
> **Wave:** DESIGN (brownfield entry, no DISCUSS artifacts by design).
> **Scope:** Application / components. This is a single-process Discord bot, not a
> distributed system, so the system- and domain-architect scopes are intentionally
> empty. Everything below is application architecture.
> **Branch:** all baseline artifacts live on `docs/architecture-baseline`.
> **Gap closure:** 1, 2, 3, 5, 8 are closed by this baseline. Gaps 4, 6, 7, 9 are
> explicitly deferred (see [Deferred gaps](#deferred-gaps)).

---

## 1. What ScrapCode is

ScrapCode is a multi-tenant Discord bot for Warhammer Tacticus guild clusters.
A single running bot process serves multiple Discord servers; each Discord server
("cluster") manages several in-game guilds. The bot's jobs:

- **Cluster/guild registry** — register in-game guilds with their Tacticus API key
  and a Discord "leader" role; configure per-server permission tiers and per-guild
  member roles.
- **Raid leaderboards** — fetch raid hit/bomb data from the Tacticus API, keep a
  per-guild per-season top-N record on local disk, and render Battle / Bomb /
  cluster-wide leaderboards as Discord messages.
- **Live leaderboards** — pin leaderboard messages that the bot edits in place
  each hour, with season-rollover handling that freezes the old season's messages
  and spawns a fresh set.
- **Token-cap notifications** — hourly, poll each registered player's raid-token
  progress and ping them when their tokens are full.
- **Token/bomb availability** — on-demand views of every registered player's token
  or bomb status in a guild.
- **Replay index** — submit raid-replay links to per-boss/per-map forum threads and
  maintain an index message per thread.
- **Fun command** — `/scrapcode_attack`, flavor text.

Tech stack: Python, `discord.py` (app-commands / slash), `httpx` (async Tacticus
calls), `requests` (sync Chronicler calls, wrapped in `asyncio.to_thread`),
`python-dotenv`. Storage is flat JSON files on local disk; logging to a local
`discord.log` file. See [ADR-002](adr-002-storage-backend-json-legacy.md). For a
concise library reference with links to each library's official docs, see the
[library reference index](overview.md#library-reference-index) in the overview.

> **Doc index:** [overview.md](overview.md) is the one-page entry point and
> summary. Detailed data reference: [data-dictionary.md](data-dictionary.md).
> Diagrams: [c4-diagrams.md](c4-diagrams.md).

---

## 2. Runtime model  _(closes Gap 2)_

### 2.1 Process and entry point

`main.py` is the entry point. Startup sequence, in order:

1. `load_dotenv()`; read `DISCORD_TOKEN` from env.
2. Configure logging: a `FileHandler` writing `discord.log` (append, utf-8).
3. `discord.Intents.default()` (no privileged intents — confirmed by README and
   `intents` setup). `commands.Bot(command_prefix="!", intents=..., help_command=None)`
   ([`discord.Bot`](https://docs.pycord.dev/en/stable/api/api.html#discord.Bot) ·
   [`Intents`](https://docs.pycord.dev/en/stable/api/intents.html)).
4. Create a single process-wide `asyncio.Lock` named `file_lock` (see
   [§4 Atomicity](#4-data-layout--storage-_closes-gap-1)).
5. `main()` async context: `async with bot:` → `discord.utils.setup_logging` →
   `load_cogs()` → `bot.start(token)`.
6. `load_cogs()` constructs the one `chronicl3rClient`, calls `authenticate()`, wraps
   it in a `PlayerService`, then registers cogs in a fixed order (see below).
7. `on_ready`: prints version + short git hash, then syncs the command tree to the
   hardcoded `DEV_GUILD_IDS = [1458181638453203099]` via
   `copy_global_to` + `tree.sync(guild=...)`. A code comment notes prod should swap
   to a global `bot.tree.sync()` instead. **This is a flagged leftover: command
   sync is guild-scoped to one dev guild, not per-tenant.**

### 2.2 Cogs

All cogs live in `bot/cogs/{name}_cog.py`. A "cog" is a
[`discord.ext.commands.Cog`](https://docs.pycord.dev/en/stable/ext/commands/cogs.html)
subclass grouping related slash commands; each module exports an async
`setup_{name}(bot, ...)` that calls `bot.add_cog(...)`. `main.py` imports and
invokes them in this exact order:

| # | Cog file | Class | Setup fn | Extra deps injected |
|---|----------|-------|----------|----------------------|
| 1 | `update_cog.py` | `UpdateCog` | `setup_update` | `file_lock`, `player_service` |
| 2 | `view_cog.py` | `ViewCog` | `setup_view` | — |
| 3 | `admin_cog.py` | `AdminCog` | `setup_admin` | `player_service` |
| 4 | `registration_cog.py` | `RegistrationCog` | `setup_registration` | — |
| 5 | `tasks_cog.py` | `TasksCog` | `setup_tasks` | `file_lock`, `player_service` |
| 6 | `fun_cog.py` | `FunCog` | `setup_fun` | — |
| 7 | `bomb_cog.py` | `BombCog` | `setup_bomb` | — |
| 8 | `token_cog.py` | `TokenCog` | `setup_token` | — |
| 9 | `replay_cog.py` | `ReplayCog` | `setup_replay` | — |

Only `file_lock` and `player_service` are dependency-injected; everything else is
constructed inside the cog from the shared module-level singletons (`repo` in
`bot/guilds.py`, `chronicl3rClient`).

### 2.3 Background task loops (`tasks_cog.py`)

`TasksCog.__init__` starts two `discord.ext.tasks` loops and cancels them in
`cog_unload`:

- **`cap_detect`** — `@tasks.loop(hours=1)`, `before_loop` awaits `bot.wait_until_ready()`
  ([`discord.ext.tasks.loop`](https://docs.pycord.dev/en/stable/ext/tasks/loop.html)).
  Iterates **every** server (`repo.list_server_ids()`); for each, loads that
  server's registrations + capped_state + guilds, resolves notification channels,
  fetches each player's Tacticus `/api/v1/player` **in parallel** via
  [`asyncio.gather`](https://docs.python.org/3/library/asyncio-task.html#asyncio.gather),
  and pings a player in their guild's notification channel when
  `tokens.current >= tokens.max` and they were not already marked capped. Persists
  `capped_state` only when it changed.

- **`auto_update`** — `@tasks.loop(hours=1)`, `before_loop` awaits
  `bot.wait_until_ready()`
  ([`discord.ext.tasks.loop`](https://docs.pycord.dev/en/stable/ext/tasks/loop.html)).
  Iterates every server; determines the current season from one guild's Tacticus
  `/api/v1/guildRaid` (current) call; then for each guild
  calls `player_service.validate_if_stale` (Chronicler roster refresh if stale),
  fetches `/api/v1/guildRaid/{season}`, and under `file_lock` runs
  `bot.tracker.process_api_response` to merge hits/bombs into the per-guild
  season files. Registers any unknown players via Chronicler. Posts an
  "Auto-update complete" summary to the **single global** `UPDATE_CHANNEL_ID`
  (env). Then calls `_refresh_live_leaderboards` (see below).

**Coincidence note (verified):** both loops are `hours=1` with no per-loop offset,
and both `before_loop` only gate on `wait_until_ready`. They therefore fire at the
same top-of-hour boundary and run concurrently. They do not coordinate. This is
documented as current state, not a defect.

### 2.4 Live-leaderboard edit loop

There is **no separate task loop** for live leaderboards. `_refresh_live_leaderboards`
is invoked at the tail of each `auto_update` iteration, so it piggybacks on
auto_update's hourly cadence and only runs for the server auto_update just
processed. Per live config (`guild:{guild_id}` or `cluster`):

- Same season as the stored `season` → `fetch_message` + `msg.edit(content=...)`
  in place, once per tier.
- New season → leave the old messages untouched as a frozen archive, send a fresh
  set of per-tier messages, repoint the config at the new message IDs and season.
- Legacy configs with `season is None` are adopted to the current season without
  spawning new messages.
- Configs whose channel is missing, or whose messages are gone/forbidden, are
  removed and the config file is rewritten (`dirty` flag).

### 2.5 External calls — Tacticus-direct vs Chronicler  _(closes Gap 2 / feeds ADR-003)_

Two external systems are called today. The doctrine governing which is used for
what is pinned in [ADR-003](adr-003-chronicler-first-data-doctrine.md).

**Tacticus-direct** (`api.tacticusgame.com`, [`httpx.AsyncClient`](https://www.python-httpx.org/async/),
`X-API-KEY` header = a per-guild or per-player API key):

| Endpoint | Used by | Purpose |
|----------|--------|---------|
| `GET /api/v1/player` | `cap_detect`, `token_availability`, `bomb_availability`, `registration register` (validation) | Real-time player progress: raid-token count/max, bomb-token count/max/next, and API-key validation (401 = bad key) |
| `GET /api/v1/guild` | `PlayerService._fetch_roster` | Current guild member `userId` list, used to sync the local player list |
| `GET /api/v1/guildRaid` (current) | `auto_update`, `set_live_leaderboard`, `set_live_cluster_leaderboard` | Discover the current season number |
| `GET /api/v1/guildRaid/{season}` | `auto_update`, `update_leaderboard`, `update_all` | Per-season raid entries (hits + bombs) fed to `process_api_response` |

**Chronicler** (`www.chronicl3r.com`, sync [`requests`](https://requests.readthedocs.io/)
wrapped with [`asyncio.to_thread`](https://docs.python.org/3/library/asyncio-task.html#asyncio.to_thread)
to keep the event loop unblocked; token auth via
`CHRONICL3R_APP_USERNAME/PASSWORD`):

> **Upstream docs (require auth):** Chronicler's own API docs live at
> <https://www.chronicl3r.com/tacticus-guild-raid/api/v1/docs/> — the page is
> gated behind the same Chronicler app credentials, not public. It is the source
> for the deferred **Chronicler API contract** (Gap 4); see
> [overview.md — External service API docs](overview.md#external-service-api-docs).

| Endpoint | Method | Used by | Purpose |
|----------|--------|---------|---------|
| `api/auth/token/` | POST | `chronicl3rClient.authenticate` | Obtain a non-expiring auth token |
| `tacticus-guild-raid/api/v1/player-profile/register/` | POST | `PlayerService.get_or_register` | Register a Tacticus player profile (409 = already exists → fall back to fetch) |
| `tacticus-guild-raid/api/v1/player-profiles/{id}/api-key/` | GET | `get_profile` / `get_player_profile` | Fetch a player profile (display name) |
| `tacticus-guild-raid/api/v1/player-profiles/{id}/api-key/` | PATCH | `set_player_api_key` | Store a Tacticus API key against a profile |

**Which is which today:** Chronicler is the source of **player identity / display
name** (profile lookup + registration) and is consulted whenever the bot needs to
resolve or seed a player. Tacticus-direct is the source of **real-time raid
state** (tokens, bombs, raid hits, season) and of the **guild roster**. The bot
does **not** currently route raid-hit data or token status through Chronicler;
those go direct to Tacticus. See ADR-003 for the doctrine and the enumerated
allow-list of direct-Tacticus calls.

---

### 2.6 Deployment & runtime target

ScrapCode runs as a single long-lived process on a Linux VM; the deploy source is
`origin/main` in a git checkout at `/opt/discord-bot`. The process is supervised by
**systemd** under service name `discord-bot`.

> **Caveat — unit file not inspected in this baseline.** The authoritative unit
> definition lives on the host and must be read directly there:
> ```
> systemctl cat discord-bot      # read ExecStart, WorkingDirectory, User=, Restart=
> systemctl edit discord-bot      # read any drop-in overrides
> ```
> Do **not** assume the values below from memory; confirm them on the server. The
> facts recorded here are the operator-provided intent, not a verified dump of the
> unit.

| Aspect | Value (operator-stated; verify on host) |
|--------|-----------------------------------------|
| Host | Linux VM `discord-bot-vm`, user `krewsayder` |
| Project path | `/opt/discord-bot` (git checkout of this repo; `origin/main` is deploy source) |
| Process manager | systemd, service `discord-bot` |
| Stack | Python (asyncio + `discord.py`); entrypoint `main.py` |
| Dependencies | `requirements.txt`, installed into the project-local `.venv/` shipped with the repo |
| Secrets/config | `.env` via `python-dotenv`: `DISCORD_TOKEN`, `CHRONICL3R_APP_USERNAME`, `CHRONICL3R_APP_PASSWORD` |
| Logging | Local `discord.log` file (append, utf-8) — see `main.py` `FileHandler`; also surfaced via `journalctl -u discord-bot` |
| `ExecStart` / `WorkingDirectory` / `User=` / `Restart=` | **Verify with `systemctl cat discord-bot`** — not inspected here |

**Standard deploy (code-only change):**
```
cd /opt/discord-bot
git pull
sudo systemctl restart discord-bot
```

**When `requirements.txt` changes**, reinstall into the venv *before* restarting.
The exact pip path depends on the unit's `ExecStart`/venv, so confirm against
`systemctl cat discord-bot`; typical shape:
```
cd /opt/discord-bot
git pull
.venv/bin/pip install -r requirements.txt      # adjust to match the unit's venv path
sudo systemctl restart discord-bot
```

**Verifying a deploy landed:**
1. `git log --oneline -1` in `/opt/discord-bot` shows the expected commit on `main`.
2. On startup `main.py:55` prints
   `Logged in as <bot> — v<VERSION> (<git_hash>)`, where `<git_hash>` is
   `_git_hash()` → `git rev-parse --short HEAD`. Confirm that hash matches the
   deployed commit:
   ```
   sudo journalctl -u discord-bot -n 30 --no-pager | grep -i 'logged in as'
   ```
3. `sudo systemctl status discord-bot --no-pager` shows `Active: active (running)`.

**When changes become visible (behavior):**
- Slash commands are synced in `on_ready` to the hardcoded dev guild
  (`main.py:51-62`, `DEV_GUILD_IDS`). The `on_ready` comment notes prod should use
  a global `await bot.tree.sync()` instead — a current **dev/prod gap** (see §2.1,
  §3.2 leak #4).
- **On-demand leaderboards** (`/view_leaderboard`, `/view_bomb_leaderboard`,
  `/view_cluster_leaderboard`) build a fresh embed per call → reflect new code
  immediately after restart. Use these for a fast post-deploy smoke check.
- **Live leaderboards** are existing messages edited in place on the hourly
  `auto_update` tick (§2.4). After a restart they reflect new code only on the
  *next* hourly tick, so they lag up to ~1h — do not rely on them for a post-deploy
  check.

**Data & migration notes:** persistent state is JSON on local disk (the
`clusters/{id}/...` tree in §4, plus the global `replay_index.json`). Render-layer
changes (e.g. emoji/name maps in `bot/getNameAndEmoji.py`) apply at embed-build
time and need no data migration. Data-schema changes (new fields in season/player
JSON, or changes to how `tracker.py` writes entries) require care — extend
`bot/migrations/` rather than hand-editing existing files; existing files are not
auto-migrated on startup unless a migrator does it (the one runtime migrator is
`PlayerListMigrator`, which runs on read inside `load_player_list`).

**External services are not part of the deploy** but affect runtime: Tacticus
(raid data, roster) and Chronicler (profiles) — outages there surface as runtime
errors in `discord.log`/journald, not deploy failures (see §2.5, ADR-003).

> **Scope note:** deployment infra is DEVOPS-wave territory; this section records
> the as-built runtime target for completeness and to support safe agent work. It
> does not prescribe a CI/CD pipeline.

---

## 3. Multi-tenancy  _(closes Gap 3 / decision form in ADR-004)_

The bot serves multiple Discord servers from one process. The isolation model is
"one directory tree per Discord server, keyed everywhere by
`discord_server_id`." Rules an agent must never violate are pinned in
[ADR-004](adr-004-multi-tenancy-isolation.md); this section records *what is true
today*, including the leaks.

### 3.1 What is properly per-tenant

Everything under `clusters/{discord_server_id}/` (see
[§4](#4-data-layout--storage-_closes-gap-1)) is keyed by Discord server:

- The guild registry (`guilds.json`), including `role_tiers` and per-guild
  `member_role_ids`.
- Player registrations, capped state, and live-leaderboard config.
- Per-guild player lists and per-guild per-season hit/bomb data.

Every data-access function in `bot/guilds.py` takes `discord_server_id` as its
first argument and threads it into `repo`. Cogs obtain it from
`interaction.guild_id`. The task loops iterate `repo.list_server_ids()` and
operate on one server at a time, never joining data across servers.

### 3.2 Tenancy leaks (flagged, not fixed)

The following are **not** isolated per Discord server and are documented as
current-state defects an agent must not silently extend:

1. **`replay_index.json` is global.** `replay_cog.py` uses
   `REPLAY_INDEX_FILE = Path("replay_index.json")` at the project root and never
   reads `interaction.guild_id`. Replay submissions from **every** Discord server
   share one index file. (`replay_cog.py` has no `server_id`/`guild_id` references
   at all — verified by grep.)
2. **Replay forum/thread IDs are hardcoded to one server.** `FORUM_CHANNELS` and
   `MAP_THREADS` in `replay_cog.py` are literal channel/thread IDs that all belong
   to one specific Discord server. A `/upload_replay` issued in server B still
   posts into server A's forum threads.
3. **`auto_update` posts to one global channel.** The "Auto-update complete"
   summary is sent to `UPDATE_CHANNEL_ID` from `.env`, regardless of which server
   was just updated.
4. **Command sync is dev-guild-scoped.** `on_ready` syncs to the hardcoded
   `DEV_GUILD_IDS = [1458181638453203099]` only.
5. **Dead/one-off config.** `REPLAY_INDEX_CHANNEL_ID` is defined in `config.py`
   but never imported or used anywhere in the codebase. The one-off scripts under
   `bot/migrations/` hardcode `SERVER_ID = 1458181638453203099` (these are
   historical, run-once migrations, not runtime code).
6. **Single shared `file_lock`.** The `asyncio.Lock` is process-global; it
   serializes writes across all tenants. Not a leak of *data*, but a shared
   resource that couples tenants' write throughput.

---

## 4. Data layout & storage  _(closes Gap 1 / decision form in ADR-002)_

### 4.1 Repository layer

- `bot/repository.py` — `ClusterRepository` (ABC) and the sole implementation
  `JsonClusterRepository(base_path=Path("clusters"))`. A module-level singleton
  `repo = JsonClusterRepository()` is constructed in `bot/guilds.py` and shared
  app-wide.
- `bot/guilds.py` — thin per-feature wrappers (`load_guilds`, `save_guilds`,
  `add_cluster_role`, `load_player_list`, `load_player_registrations`,
  `load_capped_state`, `load_live_leaderboards`, …) over `repo`. **This is the
  API cogs are expected to call**, not `repo` directly (though a few cogs import
  `repo` for `list_server_ids` / `load`).
- `bot/models.py` — `@dataclass` `Guild` and `Cluster`.

### 4.2 On-disk layout

Base path: `clusters/` (gitignored). Everything below is per Discord server.

```
clusters/{discord_server_id}/
├── guilds.json                    # cluster config + guild registry (see 4.3)
├── player_registrations.json      # {discord_id: {api_key, guild_id}}
├── capped_state.json              # {discord_id: bool}
├── live_leaderboards.json         # live LB config (see 4.6)
└── {guild_id}/
    ├── player_list.json           # v2 roster (see 4.4)
    └── data/
        ├── highest_hits_season_{season}.json        # Battle detailed (4.5)
        ├── highest_hits_simple_season_{season}.json # Battle simple  (4.5)
        └── highest_bombs_season_{season}.json       # Bomb          (4.5)
```

Additional **global** files at the project root (NOT per-tenant — see §3.2):

- `replay_index.json` — replay index (global; multi-tenancy leak).
- `discord.log` — log file.
- `.env` — secrets/config.

`.gitignore` excludes `clusters/`, `data/`, `logs/`, `*.json`, `*.log`, so **no
runtime data is tracked in git**. The legacy top-level `data/` dir and root
`*.json` files are leftovers from the pre-`to_cluster_layout` migration
(`bot/migrations/to_cluster_layout.py` moved them into `clusters/{id}/`).

### 4.3 `guilds.json` schema

Written by `JsonClusterRepository.save` and `save_guilds`/`add_cluster_role`/
`add_guild_member_role` in `bot/guilds.py`; read by `load_guilds`/`repo.load` and
indirectly by every permission check.

```jsonc
{
  "update_channel_id": null,          // unused at runtime; ADR-002
  "role_tiers": {
    "admin":   [<role_id>, ...],
    "officer": [<role_id>, ...]
  },
  "guilds": {
    "<guild_id>": {
      "name":                    "<display name>",
      "api_key":                 "<tacticus api key>",
      "role_id":                 <discord role id>,
      "notification_channel_id": <channel id | null>,
      "member_role_ids":         [<role_id>, ...]
    }
  }
}
```

`guild_id` is a short, lowercased, no-space slug produced by
`register_guild` as `guild_id.strip().lower().replace(" ", "_")`. (Inconsistency
flagged: other commands normalize with just `.strip().lower()`, e.g.
`update_leaderboard`. Same key must round-trip; agents should preserve the
`register_guild` normalization.)

### 4.4 `player_list.json` schema (versioned, v2)

Managed by `PlayerService` (Chronicler-backed) and `bot/guilds.py`. Versioned via
`__meta__.version`; `PlayerListMigrator` (`bot/migrations/player_list_migrations.py`)
auto-migrates v1 → v2 on read inside `load_player_list` and rewrites the file when
migrated. `CURRENT_VERSION = 2`. The v1→v2 migration flips the old
`{display_name: tacticus_id}` map to the structure below and sets
`last_validated` to the `1970-01-01T00:00:00Z` epoch so the first
`validate_if_stale` triggers a real Chronicler refresh.

```jsonc
{
  "__meta__": { "version": 2 },
  "players": {
    "<tacticus_user_id>": {
      "display_name":   "<name>",
      "last_validated": "<ISO8601 UTC, e.g. 2026-07-18T10:00:00Z>",
      "is_former":      false
    }
  }
}
```

Readers: `get_player_list` (maps to `{id: display_name}`, appending `" (former)"`
when `is_former`), `PlayerService.refresh_guild`/`validate_if_stale`/
`ensure_player_in_list`, `_config_guilds` in admin cog, `_register_unknown_players`
in update/tasks cogs, `get_display_name`. Writers: `PlayerService` and
`save_player_list`. `is_former` is set `true` when a player leaves the Tacticus
roster; it is **never cleared back to false** once set by `refresh_guild` except by
being re-overwritten on the next roster hit (it is re-written wholesale each
refresh, so a returning player is un-flagged).

### 4.5 Per-season hit/bomb files (tracker.py)

`bot/tracker.py` `process_api_response(api_data, season, data_dir)` reads three
files from `data_dir`, merges Tacticus raid entries into top-N lists, and writes
them back. Top-N constant `TOP_N = 5`. Tracked rarities: `Legendary`, `Mythic`.
Tier keys (`get_tier_key`): `Legendary_0..Legendary_4`, `Mythic`, `Mythic_1`.

Common shape: `{ "boss_hits": { <boss_id>: { <encounter_index>: { <tier_key>: [entries] } } } }`.

- `highest_hits_season_{season}.json` — Battle **detailed**. Entry:
  `{encounterType, damage, user_id, completed_on, hero_details, machine_of_war}`.
  Dedup is **per-player per-roster** (`check_roster=True` in `try_insert`): same
  player + same hero roster + same MoW → keep only the higher damage; same player
  + different roster → separate entry.
- `highest_hits_simple_season_{season}.json` — Battle **simple**. Entry:
  `{damage, user_id, completed_on, encounter_type}`. No roster dedup.
- `highest_bombs_season_{season}.json` — Bomb. Entry:
  `{encounterType, damage, user_id, completed_on}`. No roster dedup.

Sort key everywhere: `(-damage, completed_on)` — i.e. highest damage first, ties
broken by **earliest** `completed_on` (pinned by `bot/tests/test_tracker_tiebreak.py`
after commit `3b0022f`). Lists are truncated to `TOP_N` after insertion.

### 4.6 `live_leaderboards.json` schema

```jsonc
{
  "guild:<guild_id>": {
    "channel_id": <channel id>,
    "guild_id":   "<guild_id>",
    "messages":   { "<tier_value>": <message_id>, ... },  // one per TIER_CHOICES
    "season":     <int | null>                            // null = legacy, adopted on next refresh
  },
  "cluster": {
    "channel_id": <channel id>,
    "messages":   { "<tier_value>": <message id>, ... },
    "season":     <int | null>
  }
}
```

Writers: `set_live_leaderboard`, `set_live_cluster_leaderboard`,
`_refresh_live_leaderboards`. Reader: `_refresh_live_leaderboards`,
`_config_leaderboards`.

### 4.7 `player_registrations.json` & `capped_state.json`

- `player_registrations.json`: `{ "<discord_id_str>": {"api_key": str, "guild_id": str} }`.
  Writers: registration `register`/`unregister`/`move`. Readers: `cap_detect`,
  `token_availability`, `bomb_availability`, `registration list`. The `api_key`
  uniqueness check (one key → one Discord user) is enforced in `register`.
- `capped_state.json`: `{ "<discord_id_str>": bool }`. Writers: `cap_detect`,
  `unregister` (deletes the entry). Reader: `cap_detect`.

### 4.8 Atomicity & corruption caveats  _(known standing data-loss trap — document, do not fix)_

Two patterns combine into a data-loss hazard. Both are intentional current state
for this baseline; neither is to be "fixed" as part of documentation work.

1. **Non-atomic writes.** `JsonClusterRepository._write_json` (and the equivalent
   helpers in `tracker.py` `save_json`, `replay_cog.py` `save_replay_index`) call
   `path.write_text(json.dumps(data, indent=2))` directly. There is no
   write-to-temp-then-`os.replace` pattern. A crash or power loss mid-write leaves
   a truncated/partial JSON file on disk.
2. **Silent empty-on-corruption reads.** The repo `_read_json` swallows **all**
   exceptions and returns `{}`:

   ```python
   def _read_json(self, path: Path) -> dict:
       if not path.exists():
           return {}
       try:
           return json.loads(path.read_text(encoding="utf-8"))
       except Exception:
           return {}
   ```

   `load_player_list` does the same (returns an empty `{__meta__:{version:2},
   players:{}}`), and `tracker.py` `load_json` returns `{"boss_hits": {}}`.
   Combined with (1), a truncated file is read back as **empty** with no error,
   no log, and no backup — the prior contents are effectively lost. For
   `guilds.json` this means a corrupted registry silently resets the cluster
   (empty `Cluster`), which in turn drops all role tiers and guild entries.

   **Inconsistency worth knowing:** `bot/embeds.py` `load_leaderboard_file`
   *does* distinguish failure modes — missing file → `"No data file found."`,
   `JSONDecodeError` → `"Leaderboard file is corrupted."`. So leaderboard
   *view* paths surface corruption to the user, while the repository and tracker
   paths hide it. This divergence is as-built.

3. **Concurrency.** A single process-wide `asyncio.Lock` (`file_lock`) is
   injected into `UpdateCog` and `TasksCog` and acquired **only** around
   `process_api_response`. It does *not* cover the many scattered
   `save_guilds` / `save_player_list` / `save_player_registrations` /
   `save_live_leaderboards` / `save_capped_state` calls in the cogs, nor the
   replay-index writes. The model assumes a single bot process; a second
   process would race with no guard. `validate_if_stale` /
   `ensure_player_in_list` also write the player list outside `file_lock`.

**SQLite is the accepted successor.** The JSON layout documented above is the
current state and the future migration *source*; the schema and access patterns
in this section are what a SQLite migration must preserve or explicitly
supersede. See [ADR-002](adr-002-storage-backend-json-legacy.md).

---

## 5. Conventions  _(closes Gap 5)_

### 5.1 Code placement

| Concern | Location | Notes |
|---------|----------|-------|
| Cogs | `bot/cogs/{name}_cog.py` | One `commands.Cog` subclass per file; async `setup_{name}(bot, ...)` at module bottom. Register in `main.py` `load_cogs()`. |
| Data access | `bot/guilds.py` (wrappers), `bot/repository.py` (impl) | Cogs call `bot/guilds.py` functions; `repo` singleton is shared. |
| Domain models | `bot/models.py` | `@dataclass` `Cluster`, `Guild`. |
| Permissions | `bot/permissions.py` | The **only** place permission checks live (ADR-001). |
| Rendering | `bot/embeds.py` | Message builders + `guild_autocomplete` + `resolve_members`. |
| Unit-name/emoji maps | `bot/getNameAndEmoji.py` | Keyword-substring matching against Tacticus `unitId`s. |
| External services | `bot/services/{service}/` | `chronicl3r/{client,player_service}.py`. |
| One-off migrations | `bot/migrations/` | Run-once scripts (`to_cluster_layout`, `seed_roles`, `player_list_migrations` runtime-migrator). |
| Tests | `bot/tests/test_*.py` | `pytest` + `pytest-asyncio`. Two files today: `test_permissions.py`, `test_tracker_tiebreak.py`. |
| Constants/config | `config.py` (code), `.env` (secrets) | `TIER_CHOICES`, `LABELS`, embed limits, env-derived channel IDs. |
| Version | `bot/__init__.py` `VERSION` | Semver `MAJOR.MINOR.PATCH`. |

### 5.2 Naming

- Cog class `{Name}Cog`; file `{name}_cog.py`; setup `setup_{name}`.
- Slash commands: `@app_commands.command(name="snake_case", ...)`. Sub-commands
  via `app_commands.Group` (e.g. `registration` → `register`/`unregister`/`move`/`list`).
- Permission decorator stacks immediately under the command decorator, before
  `@app_commands.describe`/`@app_commands.autocomplete`/`@app_commands.choices`.
- `guild_id` slugs: lowercased, no spaces. Normalize on ingest.

### 5.3 How permission checks are invoked

Two equivalent forms, both routing through `bot/permissions.py` (ADR-001/ADR-005).
The decorators wrap the predicates in
[`app_commands.check`](https://docs.pycord.dev/en/stable/api/app_commands.html);
a failed check raises
[`app_commands.CheckFailure`](https://docs.pycord.dev/en/stable/api/app_commands.html#discord.app_commands.CheckFailure),
which `main.py`'s `on_app_command_error` handler converts into the standard
ephemeral "You don't have permission" reply:

- **Decorator (preferred for hard gates):**
  `@require_tier("admin")` or `@require_tier("officer")` or
  `@require_guild_member()`.
- **Inline (used when the command needs a custom denial or a conditional gate):**
  `if not await check_tier(interaction, "officer"): <custom ephemeral reply>`.
  Used by e.g. `view_config`, `registration move`, `scrapcode_help`, and the
  admin-impersonation branches in `registration register`/`unregister`.

Admin-impersonation (`target_user`/`user_id` in registration) re-implements the
"admin tier **or** Discord-admin bypass" check inline against
`cluster.role_tiers["admin"]` rather than calling a helper. This duplicates the
logic that `check_tier("admin")` already encapsulates. **Flagged duplication**,
not fixed.

Tiers and bypass semantics are fully specified in
[ADR-005](adr-005-permission-model-tiers-bypass.md).

---

## 6. README / HELP_DATA drift

`README.md` and `bot/cogs/fun_cog.py::HELP_DATA` are hand-maintained in parallel
with the code and are known to drift. The following contradictions were found
during this baseline pass and are **flagged, not resolved** (per scope):

1. **README "Bot Permissions" → "Attach Files — JSON member list template
   downloads."** No command in the codebase downloads or attaches a JSON member
   list template. `get_guild_data_path` only creates a directory. No matching
   feature exists. *(README claims a capability the code does not provide.)*
2. **README sections "Always On Functionality" and "Git Workflow & Deployment"
   are empty stubs** — headers only, no body, file ends immediately after.
3. **`/scrapcode_help` is undocumented.** The command exists in `fun_cog.py` but
   appears in neither the README command tables nor `HELP_DATA`. (`HELP_DATA`
   is what `/scrapcode_help` *renders*, so it cannot list itself, but the README
   omits it too.)
4. **`/scrapcode_attack` tier mismatch.** In code the command has **no**
   permission check — the `@app_commands.checks.has_any_role(...)` line is
   commented out (`fun_cog.py`), so it is open to everyone. `HELP_DATA` lists it
   under the **member** tier (so `/scrapcode_help member` advertises it only to
   members), while the README correctly lists it under "Fun Commands — No role
   restriction". The user-facing help and the actual gate disagree.
5. **`registration move` and `view_config` enforce "officer" via inline
   `check_tier` rather than `@require_tier`.** Functionally equivalent, but it
   diverges from the decorator convention and means the standard
   `on_app_command_error` denial path is not used. (Convention note, not a
   README contradiction.)
6. **`registration register` validates the API key against Tacticus directly**
   (`/api/v1/player`), not via Chronicler, even though Chronicler stores API keys
   (`set_player_api_key`). The bot keeps its own copy in
   `player_registrations.json`. This is consistent with ADR-003's allow-list but
   is worth knowing: API-key validation is a Tacticus-direct call.

---

## 7. Deferred gaps

Per the baseline scope, the following gaps are **not** closed by this work and are
left for later:

- **Gap 4 — Chronicler API contract.** Gates the integration roadmap; written
  later as part of its own kickoff brief. The endpoint table in §2.5 is a
  *usage* summary, not a contract.
- **Gaps 6, 7, 9 — versioning / feature-log conventions and remaining items.**
  Folded into the first delivery features as slices. (The `VERSION` semver
  scheme in `bot/__init__.py` is noted in §5.1 but not formalized into an ADR.)

---

## 8. Changed assumptions

None. This is a brownfield baseline with no DISCUSS or SPIKE artifacts to
contradict. The baseline *establishes* the assumptions future waves will inherit;
it does not alter any prior-wave assumption.

---

## Application Architecture — `sqlite-backend` (DESIGN wave)

> This section is appended by the DESIGN wave for feature `sqlite-backend`
> (branch `docs/architecture-baseline` → feature work). The as-built baseline
> above (§§1–8) is unchanged. Decisions recorded here are normative for the
> feature; see [ADR-006](adr-006-sqlite-storage-backend.md) and
> [ADR-007](adr-007-repo-read-methods-get-guild-data-path-deprecation.md) for
> the full decision text, alternatives, and consequences.

### A. Scope and quality-attribute priorities

A backend data-layer swap: replace the flat JSON files documented in §4 with
a SQLite database via SQLAlchemy 2.0 (ORM) + Alembic (migrations) + aiosqlite
(async). Single process, single VM, single Discord server in production
(ADR-004). The domain model (`bot/models.py`) is **unchanged** — this is a
storage swap behind the existing `ClusterRepository` ABC.

Quality-attribute priorities, in order: **atomicity > parity/zero-regression
> testability > maintainability > time-to-market**. Scalability is explicitly
NOT a priority (one process, one VM, one server).

### B. Architecture pattern

**Modular monolith with dependency-inversion (ports-and-adapters).** The
`ClusterRepository` ABC is the port; `JsonClusterRepository` (existing) and
`SqlAlchemyClusterRepository` (new) are the two driven adapters. The
application/domain layer (cogs, `bot/guilds.py` wrappers, `bot/models.py`
dataclasses) depends only on the ABC. This matches the as-built pattern
(ADR-002 §4: "the repository is already abstract") and the team size.

### C. Correction to §4 (brief undercount)

A DESIGN-wave codebase audit surfaced a contradiction in §4's prose: the
season files are not only read/written by `bot/tracker.py` — they are also
read directly by `bot/embeds.py::load_leaderboard_file`, called from
`view_cog.py`, `admin_cog.py`, and `tasks_cog.py` via
`repo.get_guild_data_path(...)` (5 call sites total, not 1). The
data-dictionary §2.7 / §2.9 are correct ("Readers: `tracker.load_json`,
embeds"); §4's prose undercounts. This is resolved by ADR-007: the ABC grows
`load_battle_hits` / `load_bomb_hits` / `upsert_battle_hits` /
`upsert_bomb_hits` and `get_guild_data_path` is deprecated then removed in
Slice 04. The §4 prose is left intact (as-built snapshot); this section is
the correction.

### D. Component boundaries

| Component (status) | Responsibility | Depends on (inward only) |
|--------------------|----------------|---------------------------|
| `bot/db/models.py` (NEW) | SQLAlchemy 2.0 declarative ORM models for the 8 easy entities + `battle_hits` + `bomb_hits` + `replay_entries` + `replay_threads` (data-dictionary §4). No `update_channel_id`; no `battle_hits_simple` (ADR-006 D4). | `sqlalchemy` only. |
| `bot/db/session.py` (NEW) | `Database` factory: async engine + session factory, WAL pragmas, startup `probe()` (ADR-006 D8), `session_scope()` context manager. Reads `SCRAPCODE_DB_PATH` / `SCRAPCODE_DB_KEY` from env. | `sqlalchemy`, `aiosqlite`, `bot/db/models.py`, `cryptography.fernet`. |
| `bot/db/alembic/` (NEW) | Alembic env + baseline schema revision + data-migration revision + `replay_threads` seed (ADR-006 D10). | `bot/db/models.py`. |
| `bot/db/migrations_json_to_sqlite.py` (NEW, one-shot) | Reads operator-copied `clusters/` tree, runs `PlayerListMigrator._migrate_v1_to_v2` once per v1 file, populates all tables, Fernet-encrypts `api_key` on insert, emits parity report. Idempotent + `alembic downgrade` reversible. | `bot/db/models.py`, `bot/migrations/player_list_migrations.py`, `cryptography.fernet`. |
| `bot/repository_sqlalchemy.py` (NEW) | `SqlAlchemyClusterRepository(ClusterRepository)` — second impl. 11 existing ABC methods + 4 new read/write methods (ADR-007). Decrypts `api_key` on read. | `bot/repository.py` (ABC), `bot/db/session.py`, `bot/db/models.py`, `cryptography.fernet`. |
| `bot/repository.py` (MODIFIED) | ABC gains 4 new methods (ADR-007); `get_guild_data_path` deprecated in Slice 02, removed in Slice 04. | `abc`, `bot.models`. |
| `bot/guilds.py` (MODIFIED) | Composition root. Singleton `repo` (line 7) reads `SCRAPCODE_REPO_BACKEND` env (`json\|sqlite`, default `sqlite` post-cutover). Runs `probe()` on the SQLite impl before start. | One of the two impls based on env. |
| `bot/tracker.py` (MODIFIED, Slice 04) | `process_api_response(api_data, season, discord_server_id, guild_id)` — `data_dir` removed; reads/writes via `repo.upsert_battle_hits` / `upsert_bomb_hits`. `load_json` / `save_json` / `try_insert` / `BATTLE_SIMPLE_FILE` write removed. `get_tier_key` / `get_roster_key` remain. | `bot.repository` ABC. |
| `bot/embeds.py` (MODIFIED, Slice 04) | `load_leaderboard_file` removed; `build_battle_messages` / `build_bomb_messages` consume dicts from `repo.load_battle_hits` / `load_bomb_hits`. | `bot.guilds` (wrappers). |
| `bot/cogs/replay_cog.py` (MODIFIED, Slice 04) | Reads/writes `replay_entries` / `replay_threads` via the repo. `REPLAY_INDEX_FILE` / `load_replay_index` / `save_replay_index` / `FORUM_CHANNELS` / `MAP_THREADS` removed; thread IDs from `replay_threads` (ADR-006 D10). | `bot.guilds`. |
| `bot/cogs/{view,admin,tasks}_cog.py` (MODIFIED, Slice 04) | Read sites rewired from `get_guild_data_path` + `load_leaderboard_file` to `repo.load_battle_hits` / `load_bomb_hits` (ADR-007). | `bot.guilds`. |
| `main.py` (MODIFIED, Slice 04) | `file_lock = asyncio.Lock()` (line 45) removed; `setup_update` / `setup_tasks` no longer receive it (ADR-006 D6). | — |

Genuinely new components are limited to `bot/db/{models,session,alembic/,
migrations_json_to_sqlite.py}` and `bot/repository_sqlalchemy.py`. Every
other modification is a rewire of an existing module. See ADR-006 §D3 for
the per-component justification.

### E. Transaction strategy

SQLite in **WAL mode** (`PRAGMA journal_mode=WAL; synchronous=NORMAL;
foreign_keys=ON`). The hourly `auto_update` multi-file write (today:
scattered `save_player_list` / `save_guilds` / `save_capped_state` /
`save_live_leaderboards` calls outside `file_lock`) becomes **one
transaction per guild** (US-010). A crash mid-cycle leaves that guild's
pre-cycle state intact (transaction rollback). The `file_lock`
process-wide `asyncio.Lock` is retired (ADR-006 D6) — WAL snapshot
isolation handles the two concurrent hourly loops (`cap_detect` and
`auto_update` fire at the top of each hour with no offset, brief §2.3).
All DB I/O is async via aiosqlite so the discord.py event loop is not
blocked.

### F. Secrets

Both `guilds.api_key` and `player_registrations.api_key` are stored as
Fernet ciphertext; the Fernet key is `SCRAPCODE_DB_KEY` from `.env`,
never logged (ADR-006 D7). Decrypt-on-read in
`SqlAlchemyClusterRepository` keeps cogs unchanged (they see plaintext).
The 1:1 `api_key` uniqueness constraint (data-dictionary §2.3) is enforced
on a deterministic HMAC-SHA256 column (`api_key_hmac`), not on the
ciphertext (Fernet ciphertexts are non-deterministic).

### G. Startup probe (Earned Trust)

`bot/db/session.py::probe()` runs at composition time and MUST succeed
before the bot starts (ADR-006 D8): (1) asserts WAL mode; (2) asserts
`alembic_version.version_num` matches the compiled head; (3) round-trips
a known plaintext through Fernet with `SCRAPCODE_DB_KEY`; (4) inserts +
rolls back a throwaway row in `clusters`. Failure raises a structured
`health.startup.refused` event and the bot refuses to start. The probe
contract is enforced at three layers (principle 12): mypy Protocol at the
composition root, an AST pre-commit hook asserting `probe` is defined on
the adapter, and a CI gold-test runner injecting a corrupted DB / wrong
Fernet key / stale alembic version / read-only filesystem.

### H. External integrations

No NEW external integrations are introduced by this feature. The existing
external integrations (Tacticus API, Chronicler — §2.5) are unchanged.
Contract-test annotation (principle 10): the existing Tacticus + Chronicler
integrations remain the highest-risk boundary; this feature does not touch
them and does not add to the contract-test surface. The handoff to
platform-architect includes: "No new external integrations; existing
Tacticus + Chronicler contract-test recommendations unchanged."

### I. Architecture enforcement

Style: modular monolith with dependency-inversion (ports-and-adapters).
Language: Python. Tools: **import-linter** (module-boundary rules) +
**pytest-archon** (composition-root Protocol check). Rules: `bot/cogs/*`
MUST NOT import `sqlalchemy` / `aiosqlite` / `bot.db.*` /
`bot.repository_sqlalchemy`; `bot.tracker.py` MUST NOT import
`pathlib.Path` after Slice 04; `bot.cogs/replay_cog.py` MUST NOT reference
`replay_index.json`; `bot.embeds` MUST NOT import `pathlib.Path` after
Slice 04; the composition root MUST pass the `probe()` Protocol check. See
ADR-006 §"Architecture enforcement" for the full rule set.

### J. Development paradigm

**OOP.** The codebase is OOP (ABCs, dataclasses, repository pattern); the
new components follow the same paradigm (declarative ORM models, a
repository class, a factory). Routes DELIVER to
`@nw-software-crafter`. Recorded for the orchestrator in
`docs/feature/sqlite-backend/design/wave-decisions.md`.

### K. C4 diagrams

Updated diagrams in [c4-diagrams.md](c4-diagrams.md): a new Container
diagram showing the SQLite container + SQLAlchemy/Alembic components + the
repo port; a new Component diagram for the data layer (port + 2 impls +
migration + probe). The System Context diagram (§1) is unchanged — no
new external system is introduced.

### L. Traceability to user stories

| ADR-006 / ADR-007 decision | Driving stories |
|----------------------------|-----------------|
| D1 storage stack | US-003, US-004 |
| D2 architecture pattern | US-001, US-004 |
| D3 component boundaries | US-003, US-004, US-005, US-008, US-009, US-010 |
| D4 `battle_hits_simple` dropped | US-006, US-008 |
| D5 `capped_state` column | US-003 |
| D6 `file_lock` retired | US-010 |
| D7 Fernet `api_key` | US-003, US-005 |
| D8 startup probe | US-004, US-010 |
| D9 env-driven singleton | US-010 |
| D10 `FORUM_CHANNELS` → `replay_threads` seed | US-007, US-009 (scope expansion) |
| D11 replay tenancy | US-007 |
| D12 `update_channel_id` dropped; v1→v2 once | US-003, US-005 |
| D13 OOP paradigm | (paradigm routing) |
| ADR-007 ABC read methods + `get_guild_data_path` deprecation | US-008 (scope expansion) |