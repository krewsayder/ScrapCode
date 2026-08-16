# Pre-DELIVER fail-for-the-right-reason gate — `dynamic-tier-registry`

Run 2026-08-15, `.venv\Scripts\python.exe -m pytest tests/acceptance/dynamic-tier-registry`
with `SCRAPCODE_RED_GATE=1`.

DELIVER reads this file at PREPARE to confirm RED is genuine.

```
50 failed, 5 passed, 1 skipped, 48 errors  (104 collected)
```

| Class | Count | Verdict |
|---|---|---|
| `MISSING_FUNCTIONALITY` — the assertion fires because behaviour is unimplemented | 50 | ✅ correct RED |
| Correctly GREEN — architecture/traceability assertions about the world as it is | 5 | ✅ intended |
| `ENVIRONMENT_INCOMPLETE` — the test never reaches its assertion | 48 | ⚠️ **not a test defect — see below** |
| Skipped — `hypothesis` absent | 1 | ⚠️ same cause |
| `IMPORT_ERROR` / `FIXTURE_BROKEN` / `WRONG_ASSERTION` | **0** | ✅ |

## The 48 errors are one missing package, not 48 problems

Every one of them is the same line:

```
tests/acceptance/dynamic-tier-registry/conftest.py:137: ModuleNotFoundError
E   ModuleNotFoundError: No module named 'alembic'
```

`.venv` contains `discord.py`, `pytest` and `pytest-asyncio` and **nothing else
from `requirements.txt`** — no `alembic`, `sqlalchemy`, `aiosqlite`,
`cryptography`, `hypothesis`, `import-linter` or `pytest-archon`.

This is **pre-existing and not caused by this feature**. The same command
against the existing suite returns:

```
tests/acceptance/guild-key-integrity:  1 failed, 21 passed, 5 skipped, 119 errors
```

— 119 errors from the identical cause. Fixed by `pip install -r requirements.txt`
in `.venv`, after which the 48 become RED or GREEN on their merits and the
Tier B machine stops skipping.

It is worth naming plainly because `guild-key-integrity` DEVOPS D10 pinned
`import-linter` and `pytest-archon` into `requirements.txt` with the comment
*"enforcement that depends on someone having pip-installed the tool by hand is
not enforcement."* The pin held; the environment did not. **The gate DEVOPS
names as THE quality gate for this project cannot currently run**, and neither
this feature's suite nor the previous one's can tell anybody that, because both
report it as errors that look like test bugs.

## The gate did its job on our own work

`test_the_registry_owns_all_four_rules` **PASSED against a scaffold whose every
function raises**. As first written it asserted `callable(tiers.parse)` and
three siblings — true of any module that defines the names, including one that
does nothing.

That is the `WRONG_ASSERTION` category exactly: a test that cannot fail is worse
than a missing one, because it occupies the space where the real assertion would
go and reports success. Rewritten to exercise all four rules; it now fails for
the right reason. This is the second time in this project's history that a
green tick has been found standing where a property should have been — the
first was `guild-key-integrity`'s Tier B invariant, which executed its assertion
body zero times across 200 examples.

## A second defect the gate caught: a module-name collision

The standalone run was green-shaped while `pytest tests/unit tests/acceptance`
— the command DEVOPS names as THE gate — failed the **entire**
`guild-key-integrity` suite with:

```
ImportError: cannot import name 'DARK_MECHANICUM' from 'domain_types'
  (tests/acceptance/dynamic-tier-registry/domain_types.py)
```

Every acceptance suite here is a directory with no `__init__.py`, so pytest's
rootdir-prepend import mode puts each on `sys.path` and a bare module name is
shared across all of them. `sys.modules["domain_types"]` holds whichever suite
was collected FIRST, and `dynamic-tier-registry` sorts before
`guild-key-integrity`.

This suite's vocabulary module is named `tier_types.py` for that reason, and its
CONSTANTS live there too rather than in `conftest.py` — `SEASON` is 107 here and
106 in `guild-key-integrity`, so a test module doing `from conftest import
SEASON` in a combined run would bind the wrong one **silently** and assert
against the wrong season.

It is the same hazard `guild-key-integrity`'s conftest already documents for
`sys.modules["conftest"]`, arriving through a second door, and it is invisible
in a standalone run. Recorded here because the next feature to add an acceptance
suite will hit it a third time.

## The 5 that pass, and why that is correct

They assert about artifacts as they are today, not about behaviour being built:

| Test | What it pins |
|---|---|
| `test_environment_names_match_devops_artifact` | the `Environment` enum and `environments.yaml` cannot drift apart |
| `test_this_feature_adds_no_alembic_revision` | ADR-009 D4 — and it must STAY green through DELIVER |
| `test_the_registry_imports_nothing_but_the_standard_library` | DDD-2 purity, true of the scaffold and required to stay true |
| `test_the_permission_tier_paths_named_in_the_exemption_still_exist` | the ADR-009 D10 exemption has not rotted into a lie |
| `test_the_exemption_actually_covers_permission_tier_readers` | the exempted files really do read a permission tier |

A green test in a RED suite is a smell worth stating rather than hiding: each
of these is an invariant DELIVER must not break, not a scenario DELIVER must
implement.

## Handoff verdict

**Not blocked on test defects — zero.** Blocked on one environment step that is
outside this feature and affects the whole repository:

```
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Re-run the gate afterwards and replace the 48-error row before DELIVER starts.
