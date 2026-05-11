# ScrapCode Discord Bot

> The scrapcode discord bot is a bot for the UNDV cluster in the Warhammer Tacticus Mobile game to perform admin functions, player management functions, and create/analyze leaderboards.
>
> This bot will be updated over time to be more dynamic so other guilds could perhaps import their data to use the functionality. Currently, the app is hard coded for the UNDV cluster.

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

## Commands

### Admin Commands
*Required roles: Guild Leader, Dark Tech, Tech-Priest (Captain for some)*

| Command | Description | Parameters | Roles |
|---------|-------------|------------|-------|
| `/register_guild` | Register a guild into the cluster with its API key and leader role | `name`, `guild_id`, `api_key`, `role` | Guild Leader, Dark Tech, Tech-Priest |
| `/deregister_guild` | Remove a guild from the cluster registry | `guild_id` (autocomplete) | Guild Leader, Dark Tech, Tech-Priest |
| `/list_guilds` | List all registered guilds and their status | — | Captain, Guild Leader, Dark Tech, Tech-Priest |
| `/get_member_template` | Download an empty player list JSON template | — | Captain, Guild Leader, Dark Tech, Tech-Priest |
| `/upload_member_list` | Upload your guild's filled player list JSON file | `file` | Captain, Guild Leader, Dark Tech, Tech-Priest |
| `/set_live_leaderboard` | Set up a Battle leaderboard in a channel that auto-updates every hour | `guild_id` (autocomplete), `channel` | Captain, Guild Leader, Dark Tech, Tech-Priest |
| `/set_live_cluster_leaderboard` | Set up a cluster-wide leaderboard in a channel that auto-updates every hour | `channel` | Captain, Guild Leader, Dark Tech, Tech-Priest |

---

### Update Commands
*Required roles: Captain, Guild Leader, Dark Tech, Tech-Priest*

| Command | Description | Parameters |
|---------|-------------|------------|
| `/update_leaderboard` | Fetch raid data from Tacticus API and update local records for one guild | `guild_id` (autocomplete), `season` |
| `/update_all` | Fetch raid data for all registered guilds and update local records | `season` |

---

### View Commands
*Required roles vary — see table*

| Command | Description | Parameters | Roles |
|---------|-------------|------------|-------|
| `/view_leaderboard` | View top Battle damage leaderboard for a guild and tier | `guild_id` (autocomplete), `season`, `tier` | Captain, Guild Leader, Dark Tech, Tech-Priest |
| `/view_bomb_leaderboard` | View top Bomb damage leaderboard for a guild and tier | `guild_id` (autocomplete), `season`, `tier` | Tech-Priest only |
| `/view_cluster_leaderboard` | View Battle damage leaderboard across all guilds in the cluster | `season`, `tier` | Captain, Guild Leader, Dark Tech, Tech-Priest |

---

### Player Registration Commands
*Required roles vary — see table*

| Command | Description | Parameters | Roles |
|---------|-------------|------------|-------|
| `/register` | Register your personal Tacticus API key for token cap notifications | `api_key`, `guild_id` (autocomplete), `target_user` *(admin only, optional)* | Veteran of the Long War |
| `/unregister` | Remove your Tacticus API key registration | `target_user` *(admin only, optional)* | Veteran of the Long War |
| `/check_registered_members` | List all players who have registered their Tacticus API key | — | Captain, Guild Leader, Dark Tech, Tech-Priest |

---

### Token & Bomb Commands
*Required roles: Veteran of the Long War, Captain, Guild Leader, Dark Tech, Tech-Priest*

| Command | Description | Parameters |
|---------|-------------|------------|
| `/token_availability` | Show raid token status for all registered players in a guild | `guild_id` (autocomplete) |
| `/bomb_availability` | Show bomb token status for all registered players in a guild | `guild_id` (autocomplete) |

---

### Replay Commands
*Required roles: Veteran of the Long War*

| Command | Description | Parameters |
|---------|-------------|------------|
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