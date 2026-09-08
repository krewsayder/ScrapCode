# Upstream issues — `cluster-board-control` DISTILL

Findings this wave produced about artifacts it does not own. Per the
back-propagation contract, they are recorded here rather than fixed in place.

---

## UI-1 — UD-10 has recurred, and it now breaks the declared gate

**Severity: high** — a new acceptance suite can silently break the
repository's test command while being green in isolation.
**Action needed:** a project-level decision on test-module naming. Owner:
whoever runs the next DISTILL.

### What happened

Adding `tests/acceptance/cluster-board-control/domain_types.py` broke
collection of `tests/acceptance/guild-key-integrity` under
`pytest tests/unit tests/acceptance`.

pytest imports rootless test directories by prepending each to `sys.path`. A
bare module name is therefore shared across every acceptance suite in a run,
and the first import wins. `cluster-board-control` sorts before
`guild-key-integrity`, so the new `domain_types` shadowed the existing one and
that suite's conftest received a module without the names it imports.

The suite directories are hyphenated (`guild-key-integrity`), so they cannot be
Python packages and the collision cannot be namespaced away.

### Why it was not caught earlier

`guild-key-integrity`'s DISTILL recorded the hazard as **UD-10** and deferred
it:

> "UD-10 records that cross-importing these modules collides same-name
> `conftest` constants, so consolidation needs its own change with its own test
> run, and this wave's job was to make the defects reachable rather than to
> refactor the harness."

That was a reasonable deferral with two suites. It stops being reasonable at
three: the collision moved from "cross-importing is awkward" to "adding a
suite breaks an unrelated suite's collection."

There is already evidence it had begun to bite before this wave.
`tests/acceptance/sqlite-backend/test_atomicity_and_probe.py:565` does
`from conftest import HERM_FERNET_KEY`, and under some path arguments that
resolves to `guild-key-integrity`'s conftest and raises. It happens to pass
under the exact combined invocation, which is why nobody has seen it.

### What this wave did

Worked around it inside its own suite only — every shared name made unique:
`board_domain_types.py`, `tier_b_board_status/`, and constants + doubles moved
out of `conftest.py` so nothing imports it.

That fixes the symptom for this suite and does nothing for the next one.

### Recommended permanent fix

Pick one, project-wide:

1. **`--import-mode=importlib` with `consider_namespace_packages`** in
   `pyproject.toml`. Removes the `sys.path` prepending that causes this
   entirely. Cheapest, but changes import semantics for every existing test
   module and needs its own full run to verify.
2. **Rename the suite directories to valid identifiers**
   (`guild_key_integrity`, `sqlite_backend`, `cluster_board_control`) and add
   `__init__.py`. Makes every module properly namespaced. Touches every
   `pytest.ini` `pythonpath` and every intra-suite import.
3. **Mandate unique prefixes per suite** — what this wave did, applied as a
   convention. Zero infrastructure change, but relies on every future author
   remembering, and the failure mode is a broken gate rather than an error
   message.

Option 1 is the smallest change with the largest effect and is the
recommendation. It should be its own change with its own test run, exactly as
UD-10 said.

**Until it lands, `pytest tests/unit tests/acceptance` — not the per-suite
command — is the only run that proves a new suite is safe.**

---

## UI-2 — AC-002.5 has no Tier A scenario

**Severity: low** — one AC of twenty-two rests on Tier B alone.
**Action needed:** DELIVER either adds the Tier A scenario or records why the
model-level coverage suffices.

`/set_live_cluster_leaderboard` bringing a paused board back on is asserted in
the Tier B state machine (`officer_sets_it_up_again`) and nowhere else. A Tier
A scenario would have to drive the full setup command, which performs a live
season lookup and posts a complete tier set — a harness the shipped unit tests
also declined to build.

The behaviour today rests on a comment in `admin_cog` plus a Tier B rule, which
is thinner than the other twenty-one ACs get. Recorded rather than quietly
dropped.

---

## UI-3 — `Scenario Outline` examples that the executable spec collapses

**Severity: informational** — no action required, recorded so a reader
comparing the two files is not surprised.

Three `Scenario Outline` blocks in the `.feature` file map to a single
parametrized test rather than one test per example row:

| `.feature` | executable spec |
|---|---|
| "Asking for the state a board is already in writes nothing" (2 rows) | `test_asking_for_the_state_it_is_already_in_writes_nothing`, parametrized over `BoardCommand` |
| "A board's state is always stated, never left to be inferred" (2 rows) | folded into `test_the_configuration_view_states_the_publishing_state` |
| "Both ways of storing a board agree about its state" (2 rows) | `test_both_adapters_agree_about_a_board`, parametrized over `BoardStatus` |

The scenario count in the `.feature` file (21 scenarios) and the collected test
count (43, after backend and parameter expansion) therefore differ by design.
The `.feature` file stays the human-readable SSOT; the executable spec is free
to express the same claim with fewer functions.
---

## UI-4 — DESIGN undercounted the port blast radius, and it hid a driving port

**Severity: high** — found by the Final Wave Review Gate's cross-wave check,
2026-09-08. Already fixed; recorded because of how it was missed.

DESIGN and the slice brief both stated the DDD-2 blast radius as "4
config-read sites and 2 write sites". That number counted config-FIELD reads
(`cfg.get("channel_id")` and friends) and reported them as CALL sites — two
different measurements. The verified inventory is **5 `load_live_leaderboards`
call sites and 5 `save_live_leaderboards` call sites**.

The undercount hid a whole driving port. `/set_live_leaderboard` — the
GUILD-scoped setup command — writes a raw dict literal into the mapping at
`admin_cog.py:589-596` and saves it, so DDD-2 breaks it. It appeared in no
component table, no reuse-analysis row and no scenario.

**Why nothing else would have caught it.** The command IS exercised by
`guild-key-integrity` slices 03 and 05 and by
`tests/unit/test_leaderboard_season_fall_through.py` — but every one of those
uses a repository DOUBLE. The write never reaches an adapter, so a type error
at the port would not surface. Those suites would have stayed green while
production broke.

**Fixed:** the counts are corrected in ADR-009 and the slice brief, and
`test_setting_up_a_guild_board_still_stores_something_readable` now drives the
command against both real adapters. It fails RED with the offending dict in
the message.

**The transferable lesson:** "measured, not estimated" was written in the
slice brief beside a number that was measured — just not the thing it claimed
to measure. A blast-radius figure should name the grep that produced it.

---

## UI-5 — D2's residual risk is larger than DISCUSS assessed (cross-wave)

**Severity: medium** — neither reviewer could see this alone.
**Action needed:** DELIVER should treat the Slice 01 dogfood as a hard gate.

Two reviewers found the same risk at different altitudes and neither saw the
other:

- **Eclipse (DISCUSS):** a paused board is visually identical to a live one in
  the channel. Both mitigations — the ephemeral reply and `/view_config` —
  require the officer to REMEMBER. It raised the multi-officer case DISCUSS
  did not consider: Officer A pauses, Officer B reads stale numbers, having
  never seen the reply.
- **Forge (DEVOPS):** the skip emits only a `print(...)`, so no query answers
  "which boards are paused, and since when?"

Together they say something neither says alone: **`/view_config` is the only
signal at BOTH altitudes.** DISCUSS D2 accepted the in-channel invisibility on
the grounds that the status line covers it, but the status line is a
point-in-time query a human must think to run. There is no ambient signal for
the officer and no queryable record for the operator.

This does not reopen D2 — the operator chose "leave the messages untouched"
against the banner alternative, twice. It does mean the Slice 01 learning
hypothesis carries more weight than DISCUSS gave it, and that OD-1 in
`kpi-contracts.yaml` (structured record on pause) is the cheaper of the two
available mitigations.

**Recommendation:** treat the dogfood moment as a gate rather than an
observation, and resolve OD-1 in the same slice.
