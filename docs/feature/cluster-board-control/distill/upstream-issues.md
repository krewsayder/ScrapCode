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
