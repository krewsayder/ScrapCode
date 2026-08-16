# Upstream changes — `dynamic-tier-registry` (DEVOPS → DESIGN / DISCUSS)

Three items. **None is applied** to the DESIGN or DISCUSS artifacts; all three
are recorded here for architect and product-owner review, per the
back-propagation contract.

Two are corrections to things that cannot be measured as written. One is a
coverage gap that becomes a live regression surface the day Slice 02 deploys.

---

## 1. TK-4's measurement has no instrument

**Status: proposed, not applied. DEVOPS D11.**

### Original (DISCUSS, Outcome KPIs)

> | TK-4 | Files that must be edited to support a new tier | **2** (`config.py`,
> `bot/tracker.py`), with an undocumented off-by-one between them | **1** after
> Slice 02, **0** after Slice 04 | **Diff review at the next real tier
> addition** |

### Proposed replacement

> | TK-4 | Files that must be edited to support a new tier | 2 | 1 after Slice
> 02, 0 after Slice 04 | **The tier-literal AST test** — `"Mythic_"` and
> `"Legendary_"` appear only in `bot/tiers.py` and its own tests. Asserted on
> every test run, not at the next tier addition. |

### Rationale

The next real tier addition may be a year away. Tacticus shipped Mythic 3 after
Mythic 2 had stood for a long time, and nothing suggests a faster cadence.

A KPI whose only instrument is an event that has not happened yet cannot fail
during this feature's life — and a metric that cannot fail cannot inform. This
is the same objection `guild-key-integrity` raised against its own KPI-1
formula, which measured a delta between two events that fire in the same
coroutine on the same tick and was therefore approximately zero whether the
feature worked or not.

The architecture test that Slice 02 already commits to (AC-004.4, and DEVOPS
D10) is a direct instrument for the same quantity. If tier literals exist only
in `bot/tiers.py`, adding a tier requires editing exactly that file. TK-4 = 1 is
then true **by construction and by assertion**, on every test run, from the day
Slice 02 lands.

### One sharpening the PO should confirm

TK-4's target of **0 after Slice 04** is correct but easy to over-read. What
Slice 04 achieves is zero files edited **for correctness**: a brand-new tier is
captured (Slice 01's generalised parser), selectable (the picker unions the
registry with observed keys) and renderable (AC-003.7 renders an unregistered
key under its raw key).

It arrives in the picker as `Mythic_4`, not `Mythic 5`. Making it *look* right
is still a one-line edit to `bot/tiers.py`'s override table.

That is the intended behaviour — the data is never lost or hidden while a human
decides what to call it — but "0 files" would be a misleading claim if read as
"and it looks right too." Recommend the KPI text say **"0 files to work, 1 to
label"** so DISTILL does not author an assertion against the stronger reading.

---

## 2. AC-003.2 pins seven entries; Slice 02 needs it to pin eight

**Status: proposed, not applied. Coverage gap, not a contradiction.**

### The two texts, which disagree

DISCUSS, AC-003.2 (via `slices/slice-02-registry-and-display.md`):

> **AC-003.2** — the first seven `TIER_CHOICES` entries are byte-identical in
> name, value **and order** to the current literal list.

`slices/slice-02-registry-and-display.md`, IN scope:

> Slice 01's hand-written `Mythic 3` literal **deleted**, replaced by the
> derived entry. The byte-identity assertion (AC-003.2) now covers eight
> entries, not seven.

The slice brief already anticipates this. The AC text was written before the
Slice 01 one-liner was folded in (`design/upstream-changes.md` §2, operator
decision 2026-08-15) and never caught up.

### Why it matters more than a stale sentence

Sequencing makes the eighth entry the *only* one with a live regression surface.

By the time Slice 02 deploys, the operator has been using the hand-written
`Mythic 3` picker entry for days — it is the entry that made R1 worth shipping.
Slice 02 then **deletes it** and replaces it with a derived one. The seven
entries the AC does pin have not changed since before the feature started; the
one entry that is actually being swapped out underneath a working surface is the
one outside the assertion.

### Proposed

AC-003.2 asserts **eight** entries byte-identical in name, value and order —
the seven historical plus `Choice(name="Mythic 3", value="Mythic_2")` — against
the literal list as it stands at the end of Slice 01, not against a
re-derivation of it.

Recorded as a deployment assumption in `environments.yaml` and as R2's
post-deploy verification step either way, so the gap is covered operationally
even if the AC is not amended.

---

## 3. `tier_keys_written` is an instrumentation requirement, not a spelling

**Status: informational. Constrains DISTILL's AC authoring.**

### Original (DESIGN, Open Questions)

> 2. **`IngestReport` field names.** DISTILL will pin them when writing the ACs.
>    The design fixes the shape (per-reason counts + tier keys written), not the
>    spelling.

### The narrowing

Leaving the *names* to DISTILL is right. But TK-2 —

> | TK-2 | Latency from a tier's first hit to its rows existing in the DB | ∞ |
> ≤1 hourly cycle | `MIN(completed_on)` for the new `tier_key` vs. the cycle
> timestamp that first wrote it |

— is unmeasurable unless *some* field on the per-cycle record carries the set of
tier keys written that cycle. `MIN(completed_on)` supplies one half of the
formula from SQL; the other half exists nowhere today, and no amount of AC
wording recovers it after the fact.

So the *existence* of that field is fixed by DEVOPS rather than left open;
only its name remains DISTILL's. The DEVOPS wave writes it as
`tier_keys_written` on `auto_update.cycle` (see
`docs/product/kpi-contracts.yaml`), and DISTILL may rename it provided the
contract file is updated in the same change.

The same reasoning applies, less sharply, to `tier_keys_undisplayable`: ADR-009
D5 specifies the `📥` standing condition on the Discord surface only, and a
condition that is reported to a human but never to the log cannot be reviewed
retrospectively — which is how long an operator would take to notice it had been
firing for a month.

### Not a contradiction

DESIGN's Component Decomposition already routes the counters onto `_CycleReport`
and classifies it EXTEND for exactly this reason (*"a second report object would
split one cycle's truth across two records"*). This item makes the field set
explicit; it does not change the design.
