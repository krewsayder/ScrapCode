# ADR-005: Permission model — tiers + bypass, single-source checks

- **Status:** Accepted — as-built, retroactive
- **Date:** 2026-07-18 (recorded; decision predates this baseline)
- **Closes:** Gap 8 (seed ADR) — the *model* (tiers + bypass)
- **Related:** [ADR-001](adr-001-permission-checks-single-source.md) (the
  single-source rule), [brief.md §5.3](brief.md#53-how-permission-checks-are-invoked)

## Context

ScrapCode has no hardcoded role names. Per Discord server, an admin maps Discord
roles onto permission tiers via `/set_cluster_role` and `/set_guild_member_role`.
The model needs to be pinned so an agent understands who can run what and how the
gates compose.

## Decision

### Tiers

| Tier | Scope | Configured by | Config stored |
|------|-------|---------------|---------------|
| **admin** | Cluster-wide: register/deregister guilds, set tier & member roles | `/set_cluster_role tier=admin` | `guilds.json` → `role_tiers["admin"]` |
| **officer** | Cluster-wide: view/update leaderboards, set ping/live-leaderboard channels | `/set_cluster_role tier=officer` | `guilds.json` → `role_tiers["officer"]` |
| **guild member** | Per in-game guild: register, view tokens/bombs, submit/get/delete replays | `/set_guild_member_role guild_id=...` | `guilds.json` → `guilds[gid].member_role_ids` |

There is no "member" cluster tier; member-ness is per-guild, keyed by the
`guild_id` in the command's namespace.

### Composition / cascade rules (as implemented in `bot/permissions.py`)

1. **Discord `Administrator` always bypasses every check** (`_is_discord_admin`).
   This is the bootstrap path: before any role is mapped, a Discord server admin
   can still run `/set_cluster_role` to configure the first tier.
2. **`officer` inherits `admin`.** `check_tier(interaction, "officer")` unions
   `role_tiers["officer"]` with `role_tiers["admin"]`. An admin-tier role
   therefore satisfies officer-gated commands.
3. **`admin` does *not* inherit officer** — `check_tier(interaction, "admin")`
   checks `role_tiers["admin"]` only.
4. **`check_guild_member` cascade:** passes if the user is a Discord admin **or**
   holds any `officer` or `admin` tier role **or** holds a `member_role_ids` entry
   for the targeted guild (when `interaction.namespace.guild_id` is set) **or**
   holds a member role for *any* guild (when no `guild_id` is in the namespace, the
   "any-guild" check).
5. **Targeted-guild exactness:** when a `guild_id` is in the namespace, the member
   check is satisfied only by that guild's `member_role_ids` (or the tier cascade);
   a different guild's member role does **not** satisfy it (pinned by
   `test_guild_member_wrong_guild_fails`).

### Single source

The model is implemented **only** in `bot/permissions.py` (see ADR-001). Two
equivalent consumption forms: the `@require_tier` / `@require_guild_member`
decorators (preferred hard gates, standard denial via `on_app_command_error`) and
the inline `if not await check_tier(...)` predicate (custom/conditional denials).

## Consequences

- The full matrix is pinned by `bot/tests/test_permissions.py`: Discord-admin
  bypass, tier cascade (officer ← admin), empty-config rejection, wrong-guild
  rejection, any-guild vs targeted-guild behavior. Changes to the model must keep
  these green.
- **Admin-impersonation duplication (flagged, not fixed):** `registration_cog`
  re-implements the "admin tier **or** Discord-admin" gate inline for
  `target_user`/`user_id` rather than calling `check_tier("admin")`. Same logic,
  two locations — a drift risk ADR-001 also calls out.
- **Inline-vs-decorator drift:** `view_config` and `registration move` use inline
  `check_tier("officer")` with a custom ephemeral denial instead of
  `@require_tier("officer")`. Functionally identical; diverges from the decorator
  convention and bypasses the standard `on_app_command_error` denial path.

## Alternatives considered

- **A single flat "role allow-list" per command.** Rejected: loses the tier
  cascade (officer ← admin) and the per-guild member scoping that the model depends
  on.
- **Hardcoded role names.** Rejected from the start (and already not the case):
  each cluster maps its own Discord roles, so names cannot be hardcoded.