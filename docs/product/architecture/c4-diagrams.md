# ScrapCode — Architecture Diagrams (as-built)

> **As-built.** These diagrams describe the system as it exists in code today
> (see [brief.md](brief.md)). They use **standard Mermaid `flowchart`** so they
> render cleanly in VS Code and on GitHub with no C4 plugin. (The filename stays
> `c4-diagrams.md` for link stability; the content is flowcharts, not `C4Context`.)
>
> Doc index: [overview.md](overview.md).

## 1. System Context

```mermaid
flowchart LR
    Admin(["Cluster Admin / Officer"])
    Member(["Guild Member"])
    Discord[("Discord\nslash commands · channels · forum threads")]
    Bot["ScrapCode Bot\n(single process, 9 cogs)"]
    Tacticus[("Tacticus API\napi.tacticusgame.com")]
    Chronicler[("Chronicler\nwww.chronicl3r.com")]
    FS[("Local Filesystem\nclusters/{id}/*.json\n+ replay_index.json")]

    Admin -- "slash commands" --> Discord
    Member -- "slash commands" --> Discord
    Discord -- "interactions / events" --> Bot
    Bot -- "post/edit messages\nsync command tree" --> Discord
    Bot -- "httpx async: player, guildRaid, guild\n(X-API-KEY)" --> Tacticus
    Bot -- "requests sync: auth, profiles, api-key" --> Chronicler
    Bot -- "read/write JSON\nnon-atomic (ADR-002)" --> FS
```

## 2. Container (single process)

```mermaid
flowchart TD
    Discord[("Discord")]
    Tacticus[("Tacticus API")]
    Chronicler[("Chronicler")]
    FS[("Local FS\nJSON files")]

    subgraph Proc["ScrapCode process"]
        direction TB
        Cogs["Cogs (9)<br/>update · view · admin<br/>registration · tasks<br/>fun · bomb · token · replay"]
        Perms["permissions.py<br/>sole check source<br/>ADR-001/005"]
        Guilds["guilds.py + repository.py<br/>JsonClusterRepository<br/>per-server access"]
        Tracker["tracker.py<br/>top-N merge into season files"]
        Embeds["embeds.py + getNameAndEmoji.py<br/>render · autocomplete"]
        Svc["services/chronicl3r<br/>Client + PlayerService<br/>identity + roster"]
        Tasks["TasksCog loops<br/>cap_detect · auto_update<br/>+ live-LB refresh"]
    end

    Discord -- "interactions" --> Cogs
    Cogs -- "post/edit" --> Discord
    Cogs -- "require_tier / require_guild_member" --> Perms
    Cogs -- "load_*/save_*  (thread discord_server_id)" --> Guilds
    Cogs -- "build_*_messages" --> Embeds
    Cogs -- "refresh_guild" --> Svc
    Tasks -- "process_api_response under file_lock" --> Tracker
    Tasks -- "load_*/save_*; iterate list_server_ids" --> Guilds
    Tasks -- "validate_if_stale" --> Svc
    Cogs -- "player, guildRaid" --> Tacticus
    Tasks -- "guildRaid, player" --> Tacticus
    Svc -- "guild roster" --> Tacticus
    Svc -- "auth, profiles" --> Chronicler
    Guilds -- "read/write clusters/{id}" --> FS
    Tracker -- "read/write season JSON" --> FS
    Cogs -. "replay_cog: global replay_index.json (tenancy leak)" .-> FS
```

## 3. TasksCog component (the only multi-loop subsystem)

```mermaid
flowchart TD
    Tacticus[("Tacticus API")]
    Discord[("Discord")]
    FS[("Local FS")]

    subgraph TC["TasksCog"]
        direction TB
        Cap["cap_detect loop<br/>tasks.loop(hours=1)<br/>fetch /player in parallel<br/>ping on token cap"]
        Auto["auto_update loop<br/>tasks.loop(hours=1)<br/>season detect → guildRaid → merge"]
        Refresh["_refresh_live_leaderboards<br/>edit-in-place same season<br/>rollover: freeze + send new"]
        Reg["_register_unknown_players<br/>seed Chronicler profiles"]
        Lock[("file_lock<br/>asyncio.Lock, process-global")]
    end

    Cap -- "GET /api/v1/player (asyncio.gather)" --> Tacticus
    Cap -- "load registrations/capped_state" --> FS
    Cap -- "save capped_state" --> FS
    Cap -- "send cap ping" --> Discord

    Auto -- "GET /api/v1/guildRaid + /guildRaid/{season}" --> Tacticus
    Auto -- "load guilds" --> FS
    Auto -- "acquire around process_api_response" --> Lock
    Lock -- "serialize merge" --> MergeWrite["tracker → season files"]
    MergeWrite -- "write" --> FS
    Auto -- "calls" --> Reg
    Auto -- "calls (end of each server)" --> Refresh
    Reg -- "ensure_player_in_list → player_list.json" --> FS
    Refresh -- "load/save live_leaderboards" --> FS
    Refresh -- "fetch_message + edit, or send new on rollover" --> Discord
```

## Notes

- All three are **as-built**; none describe a target architecture.
- Edge labels carry the interaction and the key library/app construct (e.g.
  `tasks.loop(hours=1)`, `asyncio.gather`, `file_lock`). See the
  [library reference index](overview.md#library-reference-index) for doc links.
- The dotted edge in diagram 2 marks the **multi-tenancy leak**
  (`replay_index.json` is global — see [brief §3.2](brief.md#32-tenancy-leaks-flagged-not-fixed)).