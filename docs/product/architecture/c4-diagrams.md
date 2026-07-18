# ScrapCode — C4 Diagrams (as-built)

Mermaid C4 diagrams for the ScrapCode Discord bot baseline. These describe the
system as it exists in code today (see [brief.md](brief.md)). No component here
is a target architecture.

## System Context

```mermaid
C4Context
    title ScrapCode — System Context (as-built)

    Person(cluster_admin, "Cluster Admin / Officer", "Configures guilds, roles, leaderboards per Discord server")
    Person(guild_member, "Guild Member", "Registers API key, views tokens/bombs, submits replays")
    System(scrapcode, "ScrapCode Bot", "Single-process discord.py bot. Per-server JSON storage.")
    System_Ext(discord, "Discord", "Slash commands, channels, forum threads, role gating")
    System_Ext(tacticus, "Tacticus API", "api.tacticusgame.com — raid data, tokens, roster, season")
    System_Ext(chronicler, "Chronicler", "www.chronicl3r.com — player profiles / display names")
    System_Ext(filesystem, "Local Filesystem", "clusters/{server_id}/*.json + replay_index.json + discord.log")

    Rel(cluster_admin, discord, "Uses slash commands")
    Rel(guild_member, discord, "Uses slash commands")
    Rel(discord, scrapcode, "Delivers interactions / gateway events")
    Rel(scrapcode, discord, "Posts/edits messages, syncs command tree")
    Rel(scrapcode, tacticus, "httpx async — player, guildRaid, guild (X-API-KEY)")
    Rel(scrapcode, chronicler, "requests sync — auth, player-profile, api-key")
    Rel(scrapcode, filesystem, "read/write JSON (non-atomic, silent-empty-on-corruption)")

    UpdateRelStyle(scrapcode, tacticus, "Direct-Tacticus (allow-listed, see ADR-003)")
    UpdateRelStyle(scrapcode, chronicler, "Chronicler-first for identity (ADR-003)")
    UpdateRelStyle(scrapcode, filesystem, "JSON today; SQLite is successor (ADR-002)")
```

## Container

```mermaid
C4Context
    title ScrapCode — Container (as-built)

    System_Ext(discord, "Discord", "Gateway + app commands")
    System_Ext(tacticus, "Tacticus API", "raid / player / guild / season")
    System_Ext(chronicler, "Chronicler", "player profiles")
    System_Ext(fs, "Local FS", "JSON files")

    System_Boundary(sc, "ScrapCode (single process)") {
        Container(cogs, "Cogs", "discord.py app_commands", "9 cogs: update, view, admin, registration, tasks, fun, bomb, token, replay")
        Container(tasks, "Task Loops", "discord.ext.tasks", "cap_detect (hourly), auto_update (hourly, drives live-LB refresh)")
        Container(perms, "permissions.py", "python", "Sole source of tier + member checks (ADR-001/005)")
        Container(guilds, "guilds.py + repository.py", "python", "JsonClusterRepository; per-server data access")
        Container(tracker, "tracker.py", "python", "Merges Tacticus raid entries into top-N season files")
        Container(embeds, "embeds.py + getNameAndEmoji.py", "python", "Renders leaderboards, boss name/emoji, autocomplete")
        Container(svc, "services/chronicl3r", "python", "Client + PlayerService (identity, roster refresh)")
    }

    Rel(discord, cogs, "interactions")
    Rel(cogs, discord, "post/edit messages")
    Rel(cogs, perms, "require_tier / require_guild_member / check_*")
    Rel(cogs, guilds, "load_*/save_* (thread discord_server_id)")
    Rel(tasks, guilds, "load_*/save_*; iterates list_server_ids()")
    Rel(tasks, tracker, "process_api_response under file_lock")
    Rel(tasks, svc, "validate_if_stale / ensure_player_in_list")
    Rel(cogs, embeds, "build_*_messages, guild_autocomplete")
    Rel(cogs, svc, "refresh_guild (admin cog register_guild)")
    Rel(cogs, tacticus, "player (token/bomb/register validation), guildRaid (update cogs)")
    Rel(svc, chronicler, "auth, player-profile register/get, api-key patch")
    Rel(svc, tacticus, "guild roster (_fetch_roster)")
    Rel(guilds, fs, "read/write clusters/{id}/*.json")
    Rel(tracker, fs, "read/write per-guild season JSON")
    Rel(cogs, fs, "replay_cog: global replay_index.json (tenancy leak — see brief §3.2)")
```

## Component — TasksCog (the only multi-loop subsystem)

```mermaid
C4Context
    title TasksCog — component (as-built)

    System_Ext(tacticus, "Tacticus API", "player + guildRaid")
    System_Ext(discord, "Discord", "channels / messages")
    System_Ext(fs, "Local FS", "per-server JSON")

    Container_Boundary(tc, "TasksCog") {
        Component(cap_detect, "cap_detect loop", "tasks.loop(hours=1)", "Per-server: fetch /player in parallel, ping on token cap")
        Component(auto_update, "auto_update loop", "tasks.loop(hours=1)", "Per-server: season detect, fetch guildRaid, merge, post summary")
        Component(refresh, "_refresh_live_leaderboards", "method", "Edit-in-place same season; rollover freezes old + sends new")
        Component(register, "_register_unknown_players", "method", "Seed Chronicler profiles for unseen userIds")
    }

    Component(file_lock, "file_lock", "asyncio.Lock (process-global)", "Guards process_api_response only")

    Rel(cap_detect, tacticus, "GET /api/v1/player (asyncio.gather)")
    Rel(cap_detect, fs, "load registrations/capped_state; save capped_state")
    Rel(cap_detect, discord, "send cap ping to notification channel")
    Rel(auto_update, tacticus, "GET /api/v1/guildRaid + /guildRaid/{season}")
    Rel(auto_update, fs, "load guilds; process_api_response -> season files")
    Rel(auto_update, discord, "post summary to global UPDATE_CHANNEL_ID")
    Rel(auto_update, register, "calls")
    Rel(auto_update, refresh, "calls at end of each server iteration")
    Rel(register, fs, "ensure_player_in_list -> player_list.json")
    Rel(refresh, fs, "load live_leaderboards; save on dirty")
    Rel(refresh, discord, "fetch_message + edit, or send new on rollover")
    Rel(auto_update, file_lock, "acquire around process_api_response")
```