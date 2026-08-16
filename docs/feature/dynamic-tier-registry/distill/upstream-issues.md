# Upstream issues — `dynamic-tier-registry` (DISTILL → DISCUSS / DESIGN / DEVOPS)

Findings from writing the executable specifications. Four items. None is
applied to a prior wave's artifacts in place.

**Reconciliation result: 0 hard contradictions.** No DISCUSS decision is
contradicted by DESIGN or DEVOPS. What follows are three documented
supersessions — where a later wave refined a criterion and recorded why — plus
one environment finding that is not about this feature at all.

The distinction matters. A contradiction means two waves want different
behaviour and somebody has to choose. A supersession means one wave found the
earlier wording unbuildable or unmeasurable and wrote down the replacement. All
three below are the second kind, and all three were recorded by the wave that
made them before DISTILL arrived.

---

## UI-1 — AC-002.4 / AC-002.5 are superseded, and still unratified

**Status: the suite is written against the REPLACEMENT. Needs one line from the
product owner.**

DISCUSS US-002:

> **AC-002.4** … the post contains `🆕 New tier observed: {label} — {n} hits
> captured` …
> **AC-002.5** Given the same tier in the following cycle, then no `🆕` line is
> emitted — first observation only.

ADR-009 D5 replaces this with a standing condition
(`📥 Captured but not displayable`) re-evaluated every cycle. The reasoning is
recorded in `design/upstream-changes.md` §1 and restated in DEVOPS: announcing
once requires PERSISTED STATE to de-duplicate a condition that is self-clearing
by construction — once the registry lands, a captured tier is immediately
displayable, so the condition becomes structurally impossible rather than
merely resolved. The original criterion buys a schema change for a signal that
exists only during the transition.

**Why DISTILL wrote against the replacement rather than blocking.** Three
artifacts describe it — ADR-009 D5, the Slice 01 brief's IN-scope list, and the
DEVOPS observability contract (`tier_keys_undisplayable` on `auto_update.cycle`).
Two still describe the original: the DISCUSS user-story text, and
`docs/product/journeys/raid-tier-coverage.yaml` step 2, whose `output` field
still reads `"🆕 New tier observed: Mythic 3 — 14 hits captured"`.

The journey is an SSOT file rather than a feature artifact, which makes it the
more important of the two to fix — a later feature reading it would inherit the
superseded design. **Found by the Final Wave Review Gate, 2026-08-15**, and it
corrects an earlier claim in this document that four artifacts carried the
replacement against one carrying the original.

The split is 3:2, not 4:1. That does not change the recommendation — the
rationale runs one way and no artifact argues FOR announce-once — but it does
mean ratification now has two edits behind it rather than one, and the journey
edit matters beyond this feature.

Blocking the whole wave on two acceptance criteria out of forty would still be
disproportionate.

**What ratification costs if the answer is "no".** Two scenarios reword and one
gains a persisted-state fixture. The scenarios are
`test_a_captured_but_undisplayable_tier_is_reported` and
`test_the_condition_repeats_while_true_and_stops_when_resolved`.

---

## UI-2 — AC-003.2 is written for seven entries and needs eight

**Status: the suite asserts EIGHT. Confirms DEVOPS upstream item 2.**

DISCUSS AC-003.2 pins "the first seven `TIER_CHOICES` entries". The Slice 02
brief's IN-scope list says the assertion "now covers eight entries, not seven".
The two texts disagree and the brief is right.

Writing the test made the reason concrete in a way neither document does. The
eighth entry is the one Slice 02 **deletes and replaces** — Slice 01 added it as
a hand-written literal so the board would be readable on day one, and by the
time Slice 02 deploys the operator has been using it for days. It is the only
entry in the list with a live regression surface, and it was the one outside the
pin.

`test_the_derived_list_reproduces_the_literal_list_and_adds_the_new_tier`
asserts all eight, in order, against a literal copy of the pre-feature list.

---

## UI-3 — `IngestReport`'s field names are pinned here, per DESIGN's instruction

**Status: informational. DESIGN Open Question 2 asked DISTILL to do this.**

Pinned in `bot/tracker.py` as a scaffold: `entries_total`, `entries_written`,
`skip_counts`, `unrecognised_rarities`, `tier_keys_written`, plus
`entries_skipped` and `counts_by_name()` derived.

Two of them are not free choices and DELIVER should not rename them casually:

- **`tier_keys_written`** was fixed by DEVOPS, not left open. TK-2 measures
  capture latency against the first cycle record carrying the key, and no
  wording of an acceptance criterion recovers that after the fact
  (`devops/upstream-changes.md` item 3).
- **`counts_by_name()` returns all three reasons always, including zeros.** An
  absent key is indistinguishable from a counter nobody built. Asserted
  directly by `test_every_reason_is_present_even_at_zero`.

Renaming either requires updating `docs/product/kpi-contracts.yaml` in the same
change — the contract file names emitters and fields, not just targets, so an
operator following it would otherwise write a query returning nothing.

---

## UI-4 — the project's declared quality gate cannot run

**Status: NOT a finding about this feature. Repository-wide, pre-existing.**

`.venv` holds `discord.py`, `pytest` and `pytest-asyncio` and nothing else from
`requirements.txt`. No `alembic`, `sqlalchemy`, `aiosqlite`, `cryptography`,
`hypothesis`, `import-linter` or `pytest-archon`.

Consequences measured today:

| Suite | Result |
|---|---|
| `tests/acceptance/dynamic-tier-registry` | 48 errors, all `ModuleNotFoundError: alembic` |
| `tests/acceptance/guild-key-integrity` | **119 errors**, same cause |

DEVOPS names `pytest tests/unit tests/acceptance` as THE gate for this project
— there is no CI, so it is the only one. `guild-key-integrity` DEVOPS D10
pinned the enforcement tools into `requirements.txt` with the note
*"enforcement that depends on someone having pip-installed the tool by hand is
not enforcement."* The pin held. The environment did not, and neither suite can
say so clearly, because both report it as errors that look like test bugs.

Fix: `.venv\Scripts\python.exe -m pip install -r requirements.txt`, then re-run
the gate. Recorded here rather than fixed silently because installing packages
into somebody's environment is their call, and because the finding is worth more
than the fix — it is the third time this project has discovered that a declared
control was not actually running.
