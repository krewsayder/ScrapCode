# Upstream changes — `dynamic-tier-registry` (DESIGN → DISCUSS)

For product-owner review. Three items. Item 2 has already been applied (it was a
user decision, not a DESIGN inference); items 1 and 3 are recorded here and
**not** applied to the DISCUSS artifacts.

---

## 1. AC-002.4 / AC-002.5 — announce-once becomes a standing condition

**Status: proposed, not applied.**

### Original (DISCUSS, US-002)

> **AC-002.4** Given a tier key observed for the first time in this
> `(server, season)`, then the post contains `🆕 New tier observed: {label} —
> {n} hits captured`, distinct from the `⚠️` skip line.
>
> **AC-002.5** Given the same tier in the following cycle, then no `🆕` line is
> emitted — first observation only. Re-announcing every hour is the alert
> fatigue the persona already calls out.

### Proposed replacement

> **AC-002.4′** Given entries captured under a tier key that no picker can
> currently select, then the post contains
> `📥 Captured but not displayable: {key} — {n} hits`, distinct from the `⚠️`
> skip line.
>
> **AC-002.5′** Given the condition still holds in the following cycle, then the
> line is emitted again, at most once per cycle. Given the tier has become
> selectable, then no line is emitted.

### Rationale

"First observation in this `(server, season)`" requires persisted state — a new
column or table recording which tiers have been announced — to de-duplicate an
event that is **self-clearing by construction**. Once the registry lands
(ADR-009 D1/D4), a captured tier is immediately displayable, so the condition
becomes structurally impossible rather than merely resolved. The original AC
buys schema for a signal that exists only during the transition.

The replacement reports a condition rather than an event: *"I am storing data
you cannot see."* It is stateless, derived per cycle from that cycle's data, and
it turns itself off.

### What the PO should weigh

The persona's alert-fatigue concern is real and is why AC-002.5 was written.
Under the replacement, a genuinely unanticipated tier produces a line every hour
until the operator acts. Two mitigations are available if that is judged too
noisy:

- Accept it. The condition is rare (it requires a tier the registry's derivation
  rule cannot label, which after Slice 02 is close to impossible) and it is
  actionable every time it fires.
- Reuse the 24-hour suppression pattern already shipped in
  `bot/guild_keys.py::record_quarantine_alert`. This adds state, but it is state
  the codebase already knows how to manage, and the precedent is a persistent
  condition of exactly this shape.

DESIGN recommends accepting it. Adding suppression to a signal that should
essentially never fire is optimising the wrong case.

---

## 2. Slice 01 gains the one-line `Mythic 3` choice

**Status: APPLIED** (operator decision, 2026-08-15).

### Original (`slices/slice-01-capture-and-report.md`, OUT of scope)

> `TIER_CHOICES`, the picker, and every display surface. **Mythic 3 rows will
> exist in the database and be unreachable from Discord after this slice.**

### Applied change

Slice 01 adds one literal to `config.py`:

```python
app_commands.Choice(name="Mythic 3", value="Mythic_2"),
```

Slice 02 deletes it when the registry-derived list lands.

### Rationale

The registry serves Job 2 (`add-a-tier-without-a-release`, opportunity 12). It is
not what makes Mythic 3 visible — one literal is. Gating a minutes-long edit
behind a day of registry work bought tidier slice boundaries at the cost of a day
of captured-but-unreadable data.

Nothing is orphaned: `"Mythic 3"` is a new label, so no historical replay row
keyed by display name is affected. AC-003.2's byte-identity pin covers the first
seven entries and is unaffected by an eighth.

Slice 01 and Slice 02 briefs updated accordingly.

---

## 3. Slice 04's learning hypothesis is answered — unfavourably

**Status: informational; estimate revision recommended.**

### Original (`slices/slice-04-dynamic-tier-picker.md`)

> **Pre-slice SPIKE. Recommended, timeboxed to 1 hour.** Grep `bot/` for
> `tier.name` and `tier.value` and enumerate every reader. […] If it returns
> sites outside `embeds.py`, re-scope before writing any code.

and

> **AC-006.3** […] the renderers are not modified by this story.

### Finding

The SPIKE was run during DESIGN. It returns **26 raid-tier reads across five
modules**:

| Module | Raid-tier reads |
|---|---|
| `bot/cogs/tasks_cog.py` | 11 |
| `bot/cogs/view_cog.py` | 6 |
| `bot/cogs/admin_cog.py` | 6 (of 8 `tier.` reads) |
| `bot/embeds.py` | 5 |
| `bot/cogs/replay_cog.py` | 1 |

The DISCUSS framing — "the three `embeds.py` call sites" — was wrong by an order
of magnitude.

### Consequences

**The design is unaffected and arguably vindicated.** ADR-009 DDD-5 specifies a
`Tier` dataclass structurally compatible with `app_commands.Choice[str]`, so all
26 sites keep working unmodified. With only 3 sites, a plain refactor would have
been viable; with 26, structural compatibility is the only tractable option.

**Two things do change:**

1. **Slice 04's ~1.25 day estimate should be re-examined.** The mechanical work
   is unchanged (the sites are not edited), but the verification surface is much
   larger: AC-006.3 must now assert that five modules are untouched, not one.
2. **A hazard was found that nobody had noticed.** `tier` names two unrelated
   concepts. `fun_cog.py` (4 sites) and `admin_cog.py:734,736` read `tier.value`
   as a **permission** tier (`member`/`officer`/`admin`), not a raid tier. A
   global `tier.value` refactor would silently break `/scrapcode_help` and
   `/config_role_tier` — both would still type-check. ADR-009 DDD-10 adds an
   explicit path-based exemption to the enforcement rule.

The SPIKE recommendation in the slice-04 brief is now marked complete rather than
pending, and the brief records the true count.

### Scope decision — RESOLVED

**Slice 04 stays in this feature** (operator decision, 2026-08-15). The option
to defer it to its own feature was raised and declined.

Consequences accepted with that decision:

- **Estimate revised from ~1.25 d to ~2 d.** The mechanical work is unchanged —
  no call site is edited — but AC-006.3's verification surface grows from one
  module to five, and the permission-tier exemption (ADR-009 D10) needs its own
  assertion.
- **Feature total revised from ~3.5 d to ~4.25 d** across four slices.
- **The abandon-cheaply property is retained.** Slice 04 remains last and
  blocks nothing. If its hypothesis fails during DELIVER, Slices 01–03 have
  already delivered the entire Mythic 3 outcome and the fallback (a
  registry-derived choice list, one file edit per tier) is already shipped by
  Slice 02. Keeping it in scope does not put the rest of the feature at risk.
- **DISTILL must treat AC-006.3 as a five-module assertion**, not a claim about
  `embeds.py`. Parametrising it across `tasks_cog`, `view_cog`, `admin_cog`,
  `embeds` and `replay_cog` is the intended shape.
