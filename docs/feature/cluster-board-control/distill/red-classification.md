# RED classification — `cluster-board-control` Slice 01

Output of the pre-DELIVER fail-for-the-right-reason gate, run 2026-09-08.
DELIVER reads this at PREPARE phase to confirm the RED is genuine.

```
43 tests   38 failed   5 passed   0 errors
```

**Every failure is an `AssertionError`. Zero `ImportError`, zero
`ModuleNotFoundError`, zero setup errors.** The suite is RED, not BROKEN, and
may hand off.

---

## Why everything is RED

One cause, not thirty-eight. The feature's user-visible behaviour already
ships and works; what does not ship is the ADR-009 representation. The port
still hands out bare dicts carrying an `enabled` key omitted when true,
instead of `LiveBoardConfig` carrying a `BoardStatus`.

| Count | Failure | Classification |
|---|---|---|
| 34 | `the live-board port does not accept/RETURN LiveBoardConfig yet (ADR-009 DDD-2, Slice-01 precursor)` | `MISSING_FUNCTIONALITY` |
| 3 | `alembic head does not add live_leaderboards.board_status yet (ADR-009 DDD-1/DDD-4)` | `MISSING_FUNCTIONALITY` |
| 1 | Tier B — `Not yet implemented -- RED scaffold` from `LiveBoardConfig.is_enabled` | `MISSING_FUNCTIONALITY` |

The 34 enter through `_require_typed_port`, which is called from `_seed` — in
the **test body**, deliberately, not from a fixture. See "Two test bugs the
gate caught" below.

---

## The 5 that pass, and why that is correct

A scenario that passes at DISTILL time is either a legitimate regression
guard or it is vacuous. Both were checked.

| Test | Verdict |
|---|---|
| `test_a_command_against_a_board_that_does_not_exist_is_refused` (×4: 2 backends × 2 commands) | **`GREEN_BY_DESIGN`** — the refusal already ships. Not vacuous: it drives the real command callback, asserts the refusal text names `/set_live_cluster_leaderboard`, and asserts nothing was written. It never calls `_seed`, so it does not depend on the typed port. It must stay green through DELIVER |
| `test_exactly_one_place_decides_whether_a_board_publishes` | **`GREEN_BY_DESIGN`, with a caveat recorded below** |

### The caveat on the architecture guard

This test asserts two halves: the owner (`bot/repository.py`) holds the
publishing-state comparison, and no reader module compares the literal
itself.

It passes today because the **scaffold** put `BoardStatus` in
`bot/repository.py`, satisfying half one textually, while `tasks_cog` and
`admin_cog` still use the boolean and so mention no status literal.

The first version of this test asserted only half two. That version was
**vacuously true**: no module mentioned the literals anywhere, because the
concept did not exist. It would have reported GREEN for a codebase that had
never heard of `board_status` — the reassuring-but-false signal this whole
feature exists to remove. The bidirectional form is what makes it mean
something, and it is why the owner assertion carries its own failure message.

**Action for DELIVER:** this test is the executable form of ADR-009 DDD-7. It
must stay green when `tasks_cog` and `admin_cog` are rewired to read
`is_enabled`. If it goes red there, the comparison has escaped the type.

---

## Three test bugs the gate caught

Both would have given a crafter a false signal at GREEN. Recording them
because the gate exists precisely to find this class, and it found two on its
first run against a suite written the same day.

### 1. `38 ERROR` instead of `38 FAILED` — the guard was in a fixture

The port-contract assertion started life in a `typed_port` fixture. pytest
reports a fixture that raises as **ERROR**, which the gate classifies
`SETUP_FAILURE` — the wrong RED. A crafter reading that concludes the test
harness is broken, not that the feature is missing.

Fixed by moving the assertion into `_require_typed_port`, called from `_seed`
in the test body. The fixture now returns the repository unchecked and says
in its docstring why it must stay that way.

### 2. `FileNotFoundError` and `OperationalError` — two `BROKEN` classifications

- `test_exactly_one_place_decides_whether_a_board_publishes` read
  `Path("bot/cogs/tasks_cog.py")` relative to the cwd. pytest runs with cwd =
  the suite directory, so it raised `FileNotFoundError` → BROKEN. Fixed with
  an absolute path derived from `Path(__file__).parents[3]`.
- `test_upgrading_the_database_pauses_nothing` ran `SELECT board_status …`
  against a database without the column, raising `sqlite3.OperationalError` →
  BROKEN. The missing column is the *correct* thing to be red about, but not
  in that shape. Fixed by asserting on `PRAGMA table_info` first, so the
  absence reports as a named `AssertionError`.

### 3. This suite broke the project's declared gate — module-name collision

The most serious of the three, and it did not show up in the suite's own run
at all. `pytest tests/acceptance/cluster-board-control` was green-as-RED
throughout; `pytest tests/unit tests/acceptance` — the combined command
`pyproject.toml` names as the gate — **failed collection entirely**:

```
ERROR collecting tests/acceptance/guild-key-integrity
  tests/acceptance/guild-key-integrity/conftest.py:58: from domain_types import (
  ImportError
```

pytest imports rootless test directories by prepending each to `sys.path`, so
a bare module name is shared across every acceptance suite in a run and the
first import wins. `cluster-board-control` sorts before `guild-key-integrity`,
so this suite's `domain_types.py` shadowed theirs and their conftest received
a module without the names it needed. The suite directories are hyphenated, so
they cannot be packages and the collision cannot be namespaced away.

**This is the second occurrence of UD-10**, deferred by `guild-key-integrity`'s
DISTILL ("cross-importing these modules collides same-name `conftest`
constants … consolidation needs its own change with its own test run").

Fixed inside this suite by giving every shared name a unique one:

| Was | Now | Why |
|---|---|---|
| `domain_types.py` | `board_domain_types.py` | confirmed to shadow the existing suite's |
| constants + doubles in `conftest.py` | moved into `board_domain_types.py` | `from conftest import X` collides identically — `sqlite-backend/test_atomicity_and_probe.py` already does it |
| `tier_b/` | `tier_b_board_status/` | `tier_b.in_memory_composition` would resolve to whichever suite loaded first |

`conftest.py` now holds fixtures only. Nothing imports it, so nothing can
collide with it.

**The lesson is about the gate, not the fix.** A suite that is green in
isolation and breaks the repository's actual test command is exactly the
false signal this gate exists to catch, and it is only visible if the gate is
run the way an operator runs it. `pyproject.toml` already carries a long
comment about this class of trap; this is the same trap from a new direction.

Full write-up and the recommended permanent fix:
[`upstream-issues.md`](upstream-issues.md).

---

## Coverage delta

DISCUSS recorded 10 of 22 ACs with no executable coverage. All 22 now have it.

| AC | Was | Now |
|---|---|---|
| AC-001.1/.2/.3/.4/.5/.6/.7 | unit-tested | acceptance, both adapters, real I/O |
| **AC-001.8** (tier refusal) | **none** | `test_only_an_officer_may_change_whether_the_board_publishes` — the shipped unit tests built an administrator every time, so the tier decorator was in the call chain but never in an assertion |
| AC-002.1 | unit-tested | acceptance |
| **AC-002.2** (same-season resume edits) | **none** | `test_resuming_within_the_same_season_edits_the_board_already_there` |
| **AC-002.3** (rollover resume posts fresh) | **none** | `test_resuming_after_a_rollover_starts_a_fresh_board` |
| AC-002.4 | unit-tested | folded into the no-op parametrization |
| **AC-002.5** (setup re-enables) | **none** | modelled in Tier B (`officer_sets_it_up_again`); **no Tier A scenario — see Known gap** |
| **AC-003.1/.2/.3** (`/view_config`) | **none** | `test_the_configuration_view_states_the_publishing_state`, parametrized over both states |
| **AC-004.1/.2** (dataclass + enum) | **none** | `test_a_board_arrives_as_a_described_configuration` |
| AC-004.3 | partially | `test_a_board_stored_before_the_switch_existed_reads_as_running` |
| AC-004.4 | partially | `test_both_adapters_agree_about_a_board` — real JSON tree vs real SQLite |
| **AC-004.5** (one comparison) | **none** | `test_exactly_one_place_decides_whether_a_board_publishes` |
| AC-004.6 | partially | parametrized over `ScopeKind` |
| KPI-4 (upgrade pauses nothing) | none | `test_upgrading_the_database_pauses_nothing` — real alembic, raw-SQL seed at revision `0004` |

### Known gap — AC-002.5 has no Tier A scenario

`/set_live_cluster_leaderboard` re-enabling a paused board is asserted only in
the Tier B model. A Tier A scenario would have to drive the full setup
command, which issues a live season lookup and posts a full tier set — a
harness the shipped unit tests also declined to build.

Recorded rather than quietly dropped. DELIVER should either add it or state
why the Tier B coverage suffices. The behaviour today rests on a comment in
`admin_cog` and a Tier B rule, which is thinner than the other 21 ACs.

---

## Handoff verdict

**PASS.** Zero category-2 (`IMPORT_ERROR` / `FIXTURE_BROKEN` /
`SETUP_FAILURE`) and zero category-3 (`WRONG_ASSERTION` /
`OBSERVABLE_NOT_AT_PORT`) failures remain. Every universe assertion names
port-exposed observables — `board.status`, `board.channel_id`,
`board.messages`, `board.season`, `discord.calls` — never an internal field.

DELIVER's precursor commit turns these green by landing ADR-009 DDD-1..8.
