# Slice 04 — The tier picker discovers tiers

**Feature:** `dynamic-tier-registry` · **Story:** US-006
**Estimate:** ~2 days (revised from ~1.25 d after the DESIGN SPIKE) · **Order:** 4 of 4
**Scope:** confirmed in-feature by the operator, 2026-08-15

## Goal

Replace the fixed tier choice list with autocomplete over the registry unioned
with tiers observed in stored data, so the next tier the game ships needs no
code edit, no redeploy, and no command re-sync.

## Why this is the "dynamic" in the feature name

Slices 01–03 make Mythic 3 work. They do not make Mythic 4 work: a
registry-derived `TIER_CHOICES` is still fixed at command-sync time, so the
picker cannot show a tier the running process did not know about at startup.
Autocomplete is evaluated per invocation, which is the only mechanism that
closes the loop. Discord's 25-choice cap makes the change eventually mandatory
regardless.

The codebase already uses this pattern — `boss_autocomplete`
([replay_cog.py:74](../../../../bot/cogs/replay_cog.py#L74)) and
`guild_autocomplete` ([update_cog.py:48](../../../../bot/cogs/update_cog.py#L48))
— so it is an established idiom here, not a new one.

## Why it is last

It carries the only signature-level risk in the feature. Handlers currently
receive `app_commands.Choice[str]` and read **both** attributes:
`tier.value` for the stored key and `tier.name` for the embed title
([embeds.py:83](../../../../bot/embeds.py#L83),
[embeds.py:90](../../../../bot/embeds.py#L90)). Autocomplete delivers a plain
`str`, so every call site needs a resolve step.

Placed last so that if the hypothesis fails, Slices 01–03 have already
delivered the entire Mythic 3 outcome and this slice can be abandoned at no
loss. The fallback position — a registry-derived choice list requiring one
file edit and a redeploy per tier — is still strictly better than today's two
files with an undocumented skew between them.

## IN scope

- Tier autocomplete over registry ∪ `SELECT DISTINCT tier_key`.
- A resolve step turning the submitted label into an object exposing `.name`
  and `.value`, so the three renderers are unmodified.
- Explicit "unknown tier" response naming valid tiers.
- Prefix filtering capped at 25 results.
- Applied to `/view_leaderboard`, `/view_bomb_leaderboard`,
  `/view_cluster_leaderboard`, `/upload_replay`.

## OUT of scope

- Changing `build_battle_messages`, `build_bomb_messages`,
  `build_cluster_messages`. AC-006.3 requires they receive the same shape they
  read today — that constraint is the point, not a convenience.
- A runtime-mutable tier list or an `/add_tier` command. The registry stays
  code, edited in a reviewed commit.
- Autocomplete for any option other than tier.

## Learning hypothesis

**If autocomplete cannot supply a value the existing handlers accept without
modifying the renderers, it disproves the claim that `Choice` → `str` is a
contained change** — meaning the `.name`/`.value` dependency runs deeper than
the three `embeds.py` call sites, and the migration touches the render layer
rather than only the command layer.

**If it succeeds**, it confirms `Choice` is used purely as a transport in this
codebase, and the same substitution is available for any other fixed choice
list.

This is the highest-uncertainty hypothesis in the feature and the only one
where a failure would sensibly end the work rather than redirect it.

## Acceptance criteria

AC-006.1 – AC-006.6. See [feature-delta.md](../feature-delta.md).

Load-bearing:

- **AC-006.3** — the three renderers are byte-unmodified. This is the
  hypothesis as an assertion: if the diff touches `embeds.py`, the hypothesis
  is disproved regardless of whether the tests pass.
- **AC-006.1** — a tier present only in data, with no registry entry, is
  offered. Without this the slice has not achieved anything Slice 02 did not.
- **AC-006.6** — a registry tier with zero rows is still offered. The picker is
  registry ∪ observed, never observed alone, so one malformed row cannot define
  the tier list and a new tier can be selected before its first hit lands.

## Production data requirement

Acceptance uses the operator's real database, where the distinct-tier query
returns the genuine historical set (`Legendary_0..4`, `Mythic`, `Mythic_1`,
and now `Mythic_2`). AC-006.1 needs a tier present in data but absent from the
registry, which can be staged by holding `Mythic_2` out of the registry
temporarily — a real row, a real query, a real gap.

## Dogfood moment (same day)

Operator types `/view_leaderboard season:107 tier:` and watches the dropdown
populate from the database. Confirms Mythic 3 appears with the registry entry
removed — the tier being offered on the strength of stored rows alone is the
proof.

## Dependencies

- **Requires:** Slice 02, for the registry half of the union and the label rule.
- **Blocks:** nothing. Abandonable.

## Reference class

Command-signature migration across four commands plus a resolve helper. Larger
than it looks: the change is mechanical but touches every leaderboard entry
point, and the failure mode is a command that fails at invocation rather than
at import. Comparable to the seven-call-site reroute in `guild-key-integrity`
D6, which was budgeted at ~1 day and needed a parametrised test to prove
completeness.

## Risks

- **Renderer creep — MEASURED, see the SPIKE result below.** The reader count is
  26 across five modules, not three in one. The `Tier` dataclass absorbs this
  (no site is edited), but AC-006.3's verification surface is five modules wide.
- **Permission-tier collision.** `tier.value` also names *permission* tiers in
  `fun_cog.py` (4 sites) and `admin_cog.py:734,736`. A mechanical refactor would
  break `/scrapcode_help` and `/config_role_tier` silently — both still
  type-check. ADR-009 D10 exempts those paths by name.
- **Free-text submissions.** Autocomplete does not constrain input — a user can
  submit unmatched text. AC-006.4 requires an explicit error; an empty board
  would be indistinguishable from a tier with no hits.

## Pre-slice SPIKE — COMPLETE (run during DESIGN, 2026-08-15)

**Result: the hypothesis is answered, unfavourably.** Raid-tier `.name`/`.value`
readers:

| Module | Reads |
|---|---|
| `bot/cogs/tasks_cog.py` | 11 |
| `bot/cogs/view_cog.py` | 6 |
| `bot/cogs/admin_cog.py` | 6 (of 8 `tier.` reads — 2 are permission tiers) |
| `bot/embeds.py` | 5 |
| `bot/cogs/replay_cog.py` | 1 |
| **Total** | **26 across 5 modules** |

The design is unaffected and arguably vindicated: ADR-009 D5's structurally
compatible `Tier` means all 26 sites keep working unmodified, which with only
three sites would have been optional and at 26 is essential.

**Two consequences for this slice.** The ~1.25 day estimate should be
re-examined — the mechanical work is unchanged, but AC-006.3 must now assert
five untouched modules rather than one. And the product owner should weigh
whether this slice still belongs in this feature at all; see
`design/upstream-changes.md` §3.
