# RED classification — feature `guild-key-integrity`

Output of the pre-DELIVER **fail-for-the-right-reason** gate. DELIVER reads
this at PREPARE to confirm RED is genuine before starting the TDD cycle.

Run 2026-07-31 with the scaffold skip marks stripped, so every scenario
actually executed:

```
87 failed, 4 passed, 3 skipped
```

*(Re-run 2026-08-01 after the Final Wave Review Gate added one KPI-1 scenario.
Prior run: 86 failed / 4 passed / 3 skipped, 170 assertion sites.)*

Failure-type census across all 172 assertion sites:

| Exception type | Count | Classification |
|---|---:|---|
| `AssertionError` | 172 | ✅ **RED** — implementation missing, test correct |
| `ImportError` / `ModuleNotFoundError` | 0 | — |
| `AttributeError` / `TypeError` / `NameError` | 0 | — |
| fixture errors / setup failures | 0 | — |

**Verdict: 0 scenarios in category 2 (test bug) or category 3 (wrong Universe
shape). Handoff to DELIVER is not blocked.**

One wrong-reason RED was found and fixed during the gate rather than shipped:
`test_import_linter_contracts_all_pass` imported
`importlinter.application.use_cases.read_configuration`, which does not exist
in the installed version 2.13 — an `ImportError`, i.e. BROKEN, not RED. It now
invokes the real console command as a subprocess, which is also the truer
assertion: it asserts the exit code an operator's shell would see.

## The four that pass, and why that is correct

These are not scaffolds. They assert properties of files that exist right now,
so they are meaningful before any implementation lands and they act as
regression guards while it does.

| Test | What it holds today |
|---|---|
| `test_environment_names_match_the_devops_artifact` | the suite's 8 environments and `environments.yaml` are the same list |
| `test_the_guilds_wrapper_layer_stays_free_of_policy_and_http` | `bot/guilds.py` imports neither `bot.guild_keys` nor `httpx` — the cycle DDD-3 avoids |
| `test_storage_never_imports_policy` | neither repository adapter imports `bot.guild_keys` |
| `test_import_linter_contracts_all_pass` | all 4 existing contracts kept, with the 3 new scaffold modules present |

## The three that skip, and why

| Test | Skip reason | Resolution |
|---|---|---|
| `tier_b/test_key_status_state_machine.py` (whole module) | `hypothesis` not installed | pin it — see upstream-issues UI-3 |
| `test_the_live_service_still_returns_a_stable_identifier` | `SCRAPCODE_TACTICUS_CONTRACT_KEY` unset | set it to run the live contract check |
| `test_the_live_response_still_carries_every_recorded_field` | same | same |

The two `@requires_external` skips deserve a note rather than a shrug. They
are the **only** tests in the suite capable of detecting that Tacticus changed
or dropped `guildId` — the undocumented field this entire feature binds on.
Every other test passes against a fixture while production goes blind. They
should be run at least once per slice deploy.

## Architecture tests that are RED for the right reason

Worth reading as a picture of the work DELIVER has to do — each failure names
a real, current property of the repository:

```
test_no_cog_or_service_reads_a_guild_api_key_directly
  → bot/cogs/tasks_cog.py, bot/cogs/update_cog.py, bot/cogs/admin_cog.py,
    bot/cogs/registration_cog.py, bot/services/chronicl3r/player_service.py

test_the_chronicler_package_makes_no_http_calls
  → bot/services/chronicl3r/player_service.py still imports httpx

test_both_enforcement_tools_are_pinned
  → import-linter and pytest-archon absent from requirements.txt
```

## Coexistence, verified after the scaffolds landed

`bot/guilds.py` gained three scaffold functions and three new modules were
added. Both existing suites still pass:

```
tests/acceptance/sqlite-backend   100 passed, 1 xfailed
tests/unit                          7 passed
lint-imports                        4 contracts kept, 0 broken
```
