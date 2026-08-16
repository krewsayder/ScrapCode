# Slice 02 — Tier registry, and Mythic 3 on every view command

**Feature:** `dynamic-tier-registry` · **Stories:** US-004 (precursor), US-003
**Estimate:** ~1 day · **Order:** 2 of 4

## Goal

Put tier knowledge in one module, and make Mythic 3 selectable and renderable
on all three view commands.

## Slice composition

US-004 is `@infrastructure` and has no user-visible output. It does **not**
ship as its own slice — per the composition gate, an infrastructure-only slice
is a structural failure. It lands as the **precursor commit** of this slice
(sanctioned option (b)), and US-003 is the value story that makes the slice
releasable.

This also satisfies the carpaccio taste test "ship the abstraction first":
first *within* the slice, rather than as a separate one. Recorded because the
two rules genuinely conflict and a reviewer reading only the taste-test table
would otherwise flag it.

## IN scope

**Precursor commit (US-004):**
- One registry module owning: parse rule (payload → key), label rule (key →
  label), ordering rule, override table.
- `config.TIER_CHOICES` derived from it; `tracker.get_tier_key` delegates to it.
- Architecture test: `"Mythic_"` and `"Legendary_"` literals appear only inside
  the registry module and its tests — in the manner of the existing chokepoint
  test (`OUT-2`).

**Value commit (US-003):**
- Slice 01's hand-written `Mythic 3` literal **deleted**, replaced by the
  derived entry. The byte-identity assertion (AC-003.2) now covers eight
  entries, not seven.
- Mythic 3 in the picker for `/view_leaderboard`, `/view_bomb_leaderboard`,
  `/view_cluster_leaderboard` — unchanged from Slice 01's behaviour, which is
  the point: the user sees nothing new, and that is how you know the derivation
  is right.
- Unregistered stored keys render under their raw key rather than being hidden.
- Startup failure if `TIER_CHOICES` would exceed Discord's 25-choice cap
  (loud, rather than a silently rejected command sync).

## OUT of scope

- Live leaderboards (Slice 03).
- Autocomplete and the `Choice[str]` → `str` migration (Slice 04). This slice
  keeps `@app_commands.choices`.
- `/upload_replay`'s tier picker — it uses `TIER_CHOICES` and therefore gains
  Mythic 3 for free, but no replay behaviour is changed or tested here.
- Any change to stored `tier_key` values (D3).

## Learning hypothesis

**If registry-derived `TIER_CHOICES` does not render identically to today's
hand-written list, it disproves the claim that display labels are purely
derivable from stored keys** — meaning a hidden coupling exists to the
literal list's order or exact strings. The two suspected coupling points are
`replay_cog.tier_order` ([replay_cog.py:54](../../../../bot/cogs/replay_cog.py#L54)),
which filters rendering by display name, and `live_leaderboards.messages`,
keyed by tier value.

**If it succeeds**, it confirms D3's central bet: labels can be derived
without touching a single stored row.

## Acceptance criteria

AC-003.1 – AC-003.7, AC-004.1 – AC-004.6. See
[feature-delta.md](../feature-delta.md).

Load-bearing:

- **AC-003.2** — the first seven `TIER_CHOICES` entries are byte-identical in
  name, value **and order** to the current literal list. This is the whole
  hypothesis, expressed as one assertion.
- **AC-003.5** — parametrised across all three view commands. Fixing one
  surface and missing the others is the realistic failure.
- **AC-003.7** — an unregistered key renders raw. D2's principle on the read
  path: never hide a row you cannot name.

## Production data requirement

The Mythic 3 board rendered in acceptance uses the real rows captured by
Slice 01 — a genuine season with genuine hits and real player names. A board
of fixture data proves the renderer, which was never in doubt.

## Dogfood moment (same day)

Operator runs `/view_leaderboard season:107 tier:"Mythic 3"` and posts the
result to the guild channel. Then runs `/get_replay` on a map with
pre-existing "Mythic 1" replays and confirms they still appear — the cheapest
possible check that labels did not shift under D3.

## Dependencies

- **Requires:** Slice 01, for Mythic 3 rows to exist. Strictly, the picker
  works without them and renders an empty board — but shipping a selectable
  tier that is always empty inverts the dogfood moment.
- **Blocks:** Slices 03 and 04, both of which consume the registry.

## Reference class

One new module plus derived call sites. Comparable to introducing the D6 key
accessor in `guild-key-integrity` (one chokepoint plus seven call sites
rerouted, ~1 day). Fewer call sites here; the added cost is the architecture
test and the byte-identity pin.

## Risks

- **Silent label drift.** A derivation that produces "Mythic I" or "Mythic-1"
  instead of "Mythic 1" orphans historical replay rows from `/get_replay`
  while leaving them in the database. AC-003.2 is the guard, and it must
  assert against the literal current list, not against a re-derivation of it.
- **Ordering drift.** `replay_cog.tier_order` and the live-board message order
  both read registry order. AC-004.6 pins it.

## Pre-slice SPIKE

Not required. Both label and order rules are fully determined by the existing
literal list, which is in the repository and can be asserted against directly.
