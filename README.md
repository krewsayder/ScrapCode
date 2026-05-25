# ScrapCode Discord Bot

> ScrapCode is a multi-tenant Discord bot for Warhammer Tacticus clusters. It handles admin functions, player management, token cap notifications, and raid leaderboards. Each Discord server gets its own isolated data cluster, and all role permissions are configured dynamically per server.

## Background
I took this tool over from another developer who had started the project to meet a need from cluster leadership.  That developer no longer maintains the project and so I took this project over.  The original developer will be credited at some point 

## Tech Stack
- Bot: Runs on any compute that can maintain a persistent connection
- Backend: Files on local storage
- Logging currently writes to a local log file

## Discord Permissions

### OAuth 2 Scopes

| Scope | Purpose |
|-------|---------|
| `bot` | Core bot functionality |
| `applications.commands` | Slash command registration and usage |

### Bot Permissions

| Permission | Used By |
|------------|---------|
| Send Messages | Leaderboard posts, pings, and update notifications |
| Send Messages in Threads | Replay submission to forum threads |
| Embed Links | Formatted leaderboard embeds |
| Read Message History | Fetching existing messages to edit live leaderboards |
| Attach Files | JSON member list template downloads |

### Privileged Intents

None required. The bot uses default Discord intents only.

### Invite URL

```
https://discord.com/api/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=274878169088&scope=bot%20applications.commands
```

Replace `YOUR_CLIENT_ID` with the bot's application ID from the [Discord Developer Portal](https://discord.com/developers/applications).

## Role Tiers

Permissions are configured per Discord server — no hardcoded role names. There are three tiers:

| Tier | Scope | Who configures it |
|------|-------|-------------------|
| **admin** | Cluster-wide — manage guilds, configure roles | `/set_cluster_role` |
| **officer** | Cluster-wide — view/update leaderboards, manage channels | `/set_cluster_role` |
| **guild member** | Per game guild — register, view tokens/bombs, submit replays | `/set_guild_member_role` |

Discord `Administrator` permission always bypasses tier checks and is used to bootstrap the first configuration.

## Commands

### Admin Commands
*Requires: **admin** tier or Discord Administrator*

| Command | Description | Parameters |
|---------|-------------|------------|
| `/register_guild` | Register a guild into the cluster with its API key and leader role | `name`, `guild_id`, `api_key`, `role` |
| `/deregister_guild` | Remove a guild from the cluster registry | `guild_id` (autocomplete) |
| `/set_cluster_role` | Add a Discord role to the admin or officer tier | `tier` (admin/officer), `role` |
| `/set_guild_member_role` | Add a Discord role as the member role for a game guild | `guild_id` (autocomplete), `role` |

---

### Officer Commands
*Requires: **officer** tier or above*

| Command | Description | Parameters |
|---------|-------------|------------|
| `/view_config` | View cluster configuration — guilds, roles, or live leaderboards. Replaces the retired `/list_guilds` | `config` (guilds/roles/leaderboards) |
| `/check_registered_members` | List all players who have registered their Tacticus API key | — |
| `/set_ping_channel` | Set the channel where token cap notifications are posted for a guild | `guild_id` (autocomplete), `channel` |
| `/set_live_leaderboard` | Set up a Battle leaderboard in a channel that auto-updates every hour | `guild_id` (autocomplete), `channel` |
| `/set_live_cluster_leaderboard` | Set up a cluster-wide leaderboard in a channel that auto-updates every hour | `channel` |
| `/update_leaderboard` | Fetch raid data from Tacticus API and update local records for one guild | `guild_id` (autocomplete), `season` |
| `/update_all` | Fetch raid data for all registered guilds and update local records | `season` |
| `/view_leaderboard` | View top Battle damage leaderboard for a guild and tier | `guild_id` (autocomplete), `season`, `tier` |
| `/view_bomb_leaderboard` | View top Bomb damage leaderboard for a guild and tier | `guild_id` (autocomplete), `season`, `tier` |
| `/view_cluster_leaderboard` | View Battle damage leaderboard across all guilds in the cluster | `season`, `tier` |

---

### Member Commands
*Requires: **guild member** role for the relevant guild (or officer/admin)*

| Command | Description | Parameters |
|---------|-------------|------------|
| `/register` | Register your personal Tacticus API key for token cap notifications | `api_key`, `guild_id` (autocomplete), `target_user` *(admin only, optional)* |
| `/unregister` | Remove your Tacticus API key registration | `target_user` *(admin only, optional)* |
| `/token_availability` | Show raid token status for all registered players in a guild | `guild_id` (autocomplete) |
| `/bomb_availability` | Show bomb token status for all registered players in a guild | `guild_id` (autocomplete) |
| `/upload_replay` | Submit a raid replay link to the index for a boss/map | `boss` (autocomplete), `map_name` (autocomplete), `team`, `tier`, `damage`, `url`, `position` *(optional)*, `comment` *(optional)* |
| `/get_replay` | View replays for a boss/map, optionally filtered by team | `boss` (autocomplete), `map_name` (autocomplete), `team` *(optional)* |
| `/delete_replay` | Remove a replay from the index by its URL | `boss` (autocomplete), `map_name` (autocomplete), `url` |

---

### Fun Commands
*No role restriction*

| Command | Description | Parameters |
|---------|-------------|------------|
| `/scrapcode_attack` | Unleash a random scrapcode transmission upon a target member | `target` |

## Always On Functionality

## Git Workflow & Deployment