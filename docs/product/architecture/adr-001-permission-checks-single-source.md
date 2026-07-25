# ADR-001: Permission checks live in `permissions.py` only

- **Status:** Accepted — as-built, retroactive
- **Date:** 2026-07-18 (recorded; decision predates this baseline)
- **Closes:** Gap 8 (seed ADR) — the *single-source* aspect of the permission model
- **Related:** [ADR-005](adr-005-permission-model-tiers-bypass.md) (the tier/bypass
  model), [ADR-004](adr-004-multi-tenancy-isolation.md)

## Context

ScrapCode gates every command by Discord role membership against a per-server
configuration. Before this was consolidated, gate logic was easy to scatter across
cogs (and some still is — see "Consequences"). A prior architecture review flagged
the risk that an agent might add a new ad-hoc role check inside a cog, diverging
from the canonical behavior.

## Decision

**All permission checks live in `bot/permissions.py` and nowhere else.** The module
exposes exactly two predicate functions and two decorators built on them:

| Symbol | Kind | Purpose |
|--------|------|---------|
| `check_tier(interaction, tier)` | async predicate | tier check (admin/officer) + Discord-admin bypass |
| `check_guild_member(interaction)` | async predicate | guild-member check + tier cascade + Discord-admin bypass |
| `require_tier(tier)` | decorator | wraps `check_tier` in `app_commands.check` |
| `require_guild_member()` | decorator | wraps `check_guild_member` |

Cogs consume these via the decorators (`@require_tier(...)`, `@require_guild_member()`)
or the inline predicates (`if not await check_tier(...)`). No cog reads
`interaction.user.roles` and re-implements tier math itself, and no new code may.

The canonical failure path for the decorators is `main.py::on_app_command_error`,
which converts `CheckFailure` into the standard ephemeral "You don't have
permission" reply.

## Consequences (as observed today)

- **Positive:** one place to audit, one place to test. `bot/tests/test_permissions.py`
  pins the full matrix (admin/officer/member cascades, Discord-admin bypass,
  wrong-guild rejection, empty-config rejection).
- **Negative / drift:** the admin-impersonation branches in `registration_cog`
  (`register` with `target_user`, `unregister` with `target_user`/`user_id`)
  re-implement the "admin tier **or** Discord-admin" check inline against
  `cluster.role_tiers["admin"]` rather than calling `check_tier("admin")`. This
  is flagged duplication in [brief.md §5.3](brief.md#53-how-permission-checks-are-invoked),
  not fixed. Future work should route these through `permissions.py` so the rule
  holds without exception.

## Alternatives considered

- **Per-cog inline checks (status quo ante).** Rejected as the baseline posture:
  it is exactly what produced divergence. Recorded here only because the
  impersonation branches are a surviving example.
- **A new permission service object.** Not warranted at current scale; the module
  functions plus the shared `repo` singleton already centralize the logic.