# RED classification — feature `guild-key-integrity`

Output of the pre-DELIVER **fail-for-the-right-reason** gate. DELIVER reads
this at PREPARE to confirm RED is genuine before starting the TDD cycle.

> **Two runs are recorded here.** The original DISTILL wave is below, kept
> verbatim as the record of what was believed on 2026-07-31. The
> **remediation wave (slices 04-07, 2026-08-02)** is at the end of this file
> — [jump to it](#remediation-wave--slices-04-07-2026-08-02). Read both: the
> first run reported `0 scenarios in category 2 or 3` and was, for three test
> assets, wrong. What it could not see is set out in the second.

## Original DISTILL wave — 2026-07-31

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

---

# Remediation wave — slices 04-07 (2026-08-02)

**Trigger:** adversarial re-review of the shipped DELIVER wave, four
independent Opus reviewers each told to falsify one load-bearing claim
rather than confirm it. Four of five claims broke. Full write-up:
[`../remediation-plan.md`](../remediation-plan.md).

**Result: 40 RED, all `MISSING_FUNCTIONALITY`. 8 `GREEN_BY_DESIGN`. Zero
blockers.** Two wrong-reason REDs were found and fixed during authoring;
both are recorded below, because they are the gate earning its keep.

> **The count was 39 when this section was first written on 2026-08-02.** It
> moved to 40 on 2026-08-03 when the DELIVER escalation split AC-008.1 into
> AC-008.1 + AC-008.1c — one scenario became two, both RED. Nothing was
> reclassified and nothing was withdrawn. See the [escalation
> section](#escalation-re-authoring--ac-0081--ac-0095-2026-08-03) at the end
> of this file.

## Baseline

Measured **2026-08-02**, against production as it stood before DELIVER took
any remediation slice. Rows are kept as measured rather than refreshed — this
table is the gate's record, not a live dashboard. See the escalation section
for the 2026-08-03 re-measurement and for why the two are not comparable.

| Run | Result (2026-08-02) |
|---|---|
| `pytest tests/unit tests/acceptance` before this work | `246 passed, 2 skipped, 1 xfailed` |
| after, excluding `slice_04..07` | `250 passed, 2 skipped, 1 xfailed` (+4 test-integrity tests, 0 regressions) |
| after, including `slice_04..07` | `39 failed, 258 passed, 2 skipped, 1 xfailed` (**40 failed** once AC-008.1c was added on 08-03) |
| `.venv/Scripts/lint-imports.exe` | `6 kept, 0 broken` |

## Slice 04 — `test_slice_04_hostile_vendor_output.py` (21 RED / 5 green)

| Scenario | AC | Classification | Where it actually fails |
|---|---|---|---|
| `same_guild_written_differently[uppercase, mixed_case, surrounding_whitespace, bom_prefixed, trailing_newline]` | AC-007.1/2 | `MISSING_FUNCTIONALITY` ×5 | own assert — guild quarantined; `matches()` is a raw `==` |
| `same_guild_written_differently[canonical]` | control | `GREEN_BY_DESIGN` | — |
| `unusable_guild_identifier[whitespace_only, not_a_uuid]` | AC-007.3 | `MISSING_FUNCTIONALITY` ×2 | own assert — binding changed; `if not uuid` lets `"   "` through |
| `unusable_guild_identifier[json_number, json_bool]` | AC-007.4 | `MISSING_FUNCTIONALITY` ×2 | `tasks_cog.py:780` `TypeError: 'int'/'bool' object is not subscriptable` (`GuildIdentity.short`) |
| `unusable_guild_identifier[empty_string, json_null]` | control | `GREEN_BY_DESIGN` ×2 | already correct — `if not uuid` catches both |
| `body_that_is_not_a_guild_object[not_json_html, empty, truncated_json, json_null]` | AC-007.5 | `MISSING_FUNCTIONALITY` ×4 | `json/decoder.py` `JSONDecodeError`, raised from `guild_client.py:183` |
| `body_that_is_not_a_guild_object[json_list, json_string, json_bool, guild_not_a_dict]` | AC-007.6 | `MISSING_FUNCTIONALITY` ×4 | `guild_client.py:109/110` `AttributeError: 'list'/'str'/'bool' object has no attribute 'get'` |
| `body_that_is_not_a_guild_object[guild_null]` | control | `GREEN_BY_DESIGN` | `payload.get("guild") or {}` already handles it |
| `one_unreadable_answer_does_not_stop_the_other_guilds` | AC-007.9 | `MISSING_FUNCTIONALITY` | `JSONDecodeError` ends the cycle before the sibling is reached |
| `partially_sent_roster_degrades` | AC-007.7 | `MISSING_FUNCTIONALITY` | `guild_client.py:130` `KeyError: 'userId'` |
| `genuinely_different_guild_still_quarantines` | AC-007.8 | `GREEN_BY_DESIGN` | the regression guard on the whole slice |
| `poisoned_binding_accepts_the_operators_correct_key` | AC-007.10 | `MISSING_FUNCTIONALITY` | own assert — correct key refused; `install_guild_key` compares raw |
| `recorded_vendor_response_matches_after_recasing` | AC-007.11 | `MISSING_FUNCTIONALITY` | own assert — production-data criterion |

## Slice 05 — `test_slice_05_close_the_write_holes.py` (7 RED / 2 green)

*(6 RED as authored 2026-08-02; 7 after the 2026-08-03 AC-008.1 split.)*

| Scenario | AC | Classification | Where it actually fails |
|---|---|---|---|
| `chokepoint_refuses_without_being_asked_twice` | AC-008.3 | `MISSING_FUNCTIONALITY` | own assert — `call_count == 1`; `verify_and_resolve` fetches then reports, never checks `key_status` |
| `registering_over_a_quarantined_guild_names_the_way_out` | AC-008.1 | `MISSING_FUNCTIONALITY` | own assert — reply is `❌ A guild with ID word_bearers is already registered. Choose a different ID or contact an admin to remove the existing entry.` **(re-authored 2026-08-03 — see below)** |
| `registering_over_an_orphaned_quarantined_binding_writes_nothing` | AC-008.1c | `MISSING_FUNCTIONALITY` | own assert — `['dm1','dm2','dm3']` written into the quarantined guild's roster **(new 2026-08-03)** |
| `the_registration_sequence_does_not_flip_real_members_to_departed` | AC-008.1b | `MISSING_FUNCTIONALITY` | own assert — `['tacticus-uid-001','tacticus-uid-002']` flipped to `is_former` |
| `registering_a_never_bound_guild_still_adopts_normally` | AC-008.2 | `GREEN_BY_DESIGN` | trust-on-first-use regression guard |
| `quarantined_guild_first_does_not_disable_the_cluster_leaderboard` | AC-008.4 | `MISSING_FUNCTIONALITY` | own assert — `❌ word_bearers has no usable key`; `next(iter(guilds))` SPOF |
| `quarantined_sibling_does_not_disable_a_healthy_guilds_leaderboard` | AC-008.5 | `GREEN_BY_DESIGN` | **the proposed AC was wrong — see AC corrections** |
| `quarantined_guild_is_not_reported_as_having_no_key` | AC-008.5b | `MISSING_FUNCTIONALITY` | own assert — `❌ Guild word_bearers has no API key set.` |
| `fully_quarantined_cluster_refused_for_a_stated_reason` | AC-008.6 | `MISSING_FUNCTIONALITY` | own assert — refusal says "no usable key", never "quarantined" |

## Slice 06 — `test_slice_06_admin_command_safety.py` (6 RED / 1 green)

| Scenario | AC | Classification | Where it actually fails |
|---|---|---|---|
| `key_held_by_a_sibling_refused_without_disclosing_it[no-force, force]` | AC-009.1/2 | `MISSING_FUNCTIONALITY` ×2 | own assert — reply is the raw `IntegrityError` (captured below) |
| `legitimate_forced_rebind_still_succeeds` | AC-009.3 | `GREEN_BY_DESIGN` | AC-003.4 regression guard |
| `deregistering_states_what_it_destroys_and_waits` | AC-009.4 | `MISSING_FUNCTIONALITY` | own assert — reply still says "Their data folder has been left intact" |
| `re_registering_a_quarantined_slug_does_not_adopt_silently` | AC-009.5 | `MISSING_FUNCTIONALITY` | own assert — reply reads `Bound to: 【UNDV】Dark Mechanicum 【PXGQW】 (d71d583f)`, no mention of the quarantine |
| `parity_rollback_leaves_no_orphaned_bindings` | AC-009.6 | `MISSING_FUNCTIONALITY` | own assert — bindings survive `_rollback_data` |
| `blanking_a_guild_key_is_refused` | AC-009.7 | `MISSING_FUNCTIONALITY` | `DID NOT RAISE ValueError` |

### The captured KPI-6 leak, verbatim

The Discord reply produced by `/update_guild_key` on an hmac collision, as
captured by the scenario:

```
❌ An error occurred: (sqlite3.IntegrityError) UNIQUE constraint failed: guilds.api_key_hmac
[SQL: UPDATE guilds SET api_key=?, api_key_hmac=? WHERE guilds.discord_server_id = ? AND guilds.guild_id = ?]
[parameters: ('gAAAAABqb_R1-xDEix1ITORnWEJXZgmvoIeo4ZLdH8dK…PlE=',
              '2ddd79d059f702562d933b6d1a043883c17e848ac5492f9373c26d7e075800fa',
              1458181638453203099, 'word_bearers')]
```

That is the Fernet ciphertext and the full 64-hex `api_key_hmac` in a Discord
message and — via `main.py:96`'s `print` — in `discord.log` and the journal.
KPI-6 records this as "0 by construction". It is three copies per occurrence.

## Slice 07 — `test_slice_07_composition_root_integrity.py` (6 RED / 0 green)

| Scenario | AC | Classification | Where it actually fails |
|---|---|---|---|
| `missing_encryption_key_stops_the_bot_and_names_itself` | AC-010.1 | `MISSING_FUNCTIONALITY` | `DID NOT RAISE` — `build_repo` returns a JSON repo |
| `malformed_encryption_key_stops_the_bot_at_startup` | AC-010.2 | `MISSING_FUNCTIONALITY` | `DID NOT RAISE` — `if not fernet_key` accepts `"…=\r"` |
| `missing_database_file_stops_the_bot` | AC-010.3 | `MISSING_FUNCTIONALITY` | `DID NOT RAISE` |
| `deliberate_rollback_starts_and_says_what_it_gives_up` | AC-010.4 | `MISSING_FUNCTIONALITY` | own assert — zero log records emitted on the `backend=json` path |
| `startup_health_check_has_a_production_caller` | AC-010.5 | `MISSING_FUNCTIONALITY` | own assert — AST scan of `bot/` + `main.py` finds zero callers of `probe()` |
| `direct_key_read_rule_covers_every_module` | AC-010.6 | `MISSING_FUNCTIONALITY` | own assert — `{'bot/guilds.py': [79, 95], 'bot/db/migrations_json_to_sqlite.py': [444], 'bot/migrations/to_cluster_layout.py': [52, 74]}` |

The two migration modules in that last result are a finding for DELIVER, not
a defect: they read `api_key` legitimately in order to migrate it. Slice 07's
brief already calls for "an explicit exemption list", and these are its first
two entries — with a reason each, per the pattern `EXEMPT_PLAYER_KEY_FUNCTIONS`
already sets.

## Test-integrity fixes — verified NON-vacuous, not merely green

These three are not ACs; they are the escalated defects in the test assets.
They are expected to PASS. "Passes" is exactly the signal that turned out to
be untrustworthy, so each was verified by mutation rather than by running.

| Fix | Verification | Result |
|---|---|---|
| (a) `KeyConsumptionSite` corrected + AST inventory test | replay the OLD enum against the new inventory test | 3 unaccounted (`register_guild`, `set_live_leaderboard`, `set_live_cluster_leaderboard`), 2 stale (`player_service.refresh_guild`, `validate_if_stale`) — the test catches both halves |
| (a) `_exercise_site` real branches | mutate `_is_quarantined` to `return False` | **7 of 8** branches red. The 8th (`register_guild`) survives and is documented in place as the weaker half of a pair whose strong half is AC-008.1 |
| (b) Tier B properties | count assertion-body executions at 200 examples × 25 steps, before and after | `quarantined_guilds_never_write`: **0 → 1037**. `quarantine_is_never_a_trap`: 988 → 1037 (it DID assert, but mutated the model and starved the other) |
| (b) anti-vacuity gate | restore the mutation (rescue on the live model) | `test_both_properties_actually_assert_something` fails with `assert 0 > 0` — the gate catches the exact shipped defect |
| (c) payload builder | the 21 slice-04 REDs above | every one was structurally unwritable before the extension |

A correction to the escalation's own wording, for the record: the Tier B
property was measured at **0 assertions for `quarantined_guilds_never_write`**
— exactly as escalated — but `quarantine_is_never_a_trap` executed its
assertion 988 times. It was not idle; it was mutating. That distinction is
what determined the fix (move the mutation to a `@rule`, run the reachability
check against a deep copy) rather than merely re-ordering the two.

## Wrong-reason REDs found and fixed during authoring

The gate exists for these. Both would have gone green under a crafter who
"fixed" the error without the feature ever being exercised.

1. **`SETUP_FAILURE` — slice 04.** Every scenario failed with
   `FakeGuildService got an unprogrammed key dm-key`: the hourly cycle probes
   both guilds in the cluster, and only Word Bearers' answer was programmed.
   Fixed by programming the sibling's healthy answer in the Background
   helper, where it belongs — it is the cluster the scenarios hold constant,
   not the thing any of them varies.
2. **`SETUP_FAILURE` — slice 05.** `set_live_leaderboard` failed at
   `admin_cog.py:429` with `AttributeError: 'NoneType' object has no
   attribute 'id'`. `conftest.FakeChannel.send` returns `None`, which is
   correct for the alert channels it was built for; both leaderboard commands
   read `msg.id` from the result. Fixed with a channel double whose `send`
   returns a message.

## AC corrections — proposed ACs that did not survive contact

Per the DISTILL brief: the slice briefs' ACs are proposals, and the designer
owns the wording. Detail in [`upstream-issues.md`](upstream-issues.md)
UI-9 … UI-11.

1. **AC-008.5 ("Same for `/set_live_leaderboard`") is not a defect.**
   `set_live_cluster_leaderboard` fails on `next(iter(guilds))` — an
   arbitrary guild unrelated to the request. `set_live_leaderboard`
   (`admin_cog.py:404`) reads `active_key` for the guild the officer NAMED.
   No arbitrary pick, no cross-guild blast radius, nothing to fall through
   to. Verified: the scenario passes today. Kept as a regression guard;
   **substituted** AC-008.5b, the defect the command does have.
2. **AC-008.1 bundles two defects at two different depths.** Revised
   2026-08-03. The zero-rows clause needs "no guild row" (the slash command
   refuses a registered id at `admin_cog.py:83`), and that state cannot carry
   a roster because `players` CASCADEs from `guilds`. The `is_former` clause
   needs a roster, therefore a guild row, therefore not the slash command —
   and the reproduction in `remediation-plan.md` describes a REGISTERED
   guild, so the measured five-members-flipped came from the sequence
   `admin_cog.py:121-124` runs, not from the command. **Split** into
   AC-008.1 (zero rows, via the orphaned binding) and **AC-008.1b** (no
   `is_former` flips, via the registration sequence on a registered
   quarantined guild). Both red. See `upstream-issues.md` UI-10.
3. **AC-009.4's confirmation mechanism is deliberately unpinned.** The
   scenario asserts the guarantee, not the widget.

---

# Escalation re-authoring — AC-008.1 / AC-009.5 (2026-08-03)

**Trigger:** DELIVER escalated mid-implementation. AC-008.1's `Given` called
`_rollback_data`, the exact function AC-009.6 fixes; once fixed, that `Given`
yields an UNBOUND guild and AC-008.1 cannot pass however correctly slice 05 is
built. Full reasoning: [`upstream-issues.md`](upstream-issues.md) UI-13 … UI-15.

**Result: 40 RED (was 39), all `MISSING_FUNCTIONALITY`. 8 `GREEN_BY_DESIGN`.
Zero blockers. Baseline unregressed.**

## What changed

| Asset | Change |
|---|---|
| `test_slice_05` AC-008.1 | re-scoped onto the REGISTERED + quarantined state; renamed `test_registering_over_a_quarantined_guild_names_the_way_out` |
| `test_slice_05` AC-008.1c | **new** — `test_registering_over_an_orphaned_quarantined_binding_writes_nothing`, carrying the original assertions verbatim |
| `_leave_an_orphaned_quarantined_binding` | no longer calls `_rollback_data`; reproduces the pre-fix residue directly and asserts its own postcondition |
| `test_slice_05` AC-008.2 | docstring only — pairs it with AC-008.1c, which is the scenario it actually discriminates against |
| `test_slice_06` `_confirm_if_awaiting` | rewritten; the old `interaction.pending_confirmation` seam is not implementable (`discord.Interaction` has `__slots__`, no `__dict__`) |
| `test_slice_06` `_FakeInteraction` | captures `view=` and gains an `extras` dict, mirroring the real `Interaction` slot |
| `test_slice_06` AC-009.5 | asserts the deregistration actually happened before re-registering |
| `slice-05-…​.feature` | scenario SSOT updated to match, with the split's reasoning inline |

No production file was touched.

## Classification of the two re-authored scenarios

| Scenario | AC | Classification | Where it actually fails |
|---|---|---|---|
| `registering_over_a_quarantined_guild_names_the_way_out` | AC-008.1 | `MISSING_FUNCTIONALITY` | own assert — `assert "quarantin" in reply.lower()` against `❌ A guild with ID word_bearers is already registered. Choose a different ID or contact an admin to remove the existing entry.` |
| `registering_over_an_orphaned_quarantined_binding_writes_nothing` | AC-008.1c | `MISSING_FUNCTIONALITY` | own assert — `assert _roster(GUILD_WB) == {}` returns `['dm1','dm2','dm3']`; the reply reads `✅ Player list populated … Bound to: 【UNDV】Dark Mechanicum` |

Both reach their own assertion. No `ImportError`, no fixture error, no setup
failure, no internal-field coupling — category 1 in both directions.

## Verified against AC-009.6 APPLIED, which is the point of the exercise

A designer who re-authors a scenario to escape a conflict has to show the
conflict is gone rather than moved. `_DATA_TABLES_DELETE_ORDER` was patched to
include `guild_key_bindings` via a session plugin (no production edit) and
slices 05 + 06 re-run:

```
[AC-009.6 SIMULATED] ('guild_key_bindings', 'live_lb_messages', 'live_leaderboards')
12 failed, 4 passed
```

| Claim | Evidence |
|---|---|
| AC-009.6's own scenario goes GREEN under the fix | `test_a_parity_rollback_leaves_no_orphaned_bindings` drops out of the failure list |
| AC-008.1 keeps its RED and its reason | same assertion, same reply text, byte-identical message |
| AC-008.1c keeps its RED and its reason | same assertion, still `['dm1','dm2','dm3']` |
| No other scenario changed classification | slice 05 7 RED / 2 green, slice 06 5 RED / 2 green — the only delta is AC-009.6 passing |

Both ACs now hold simultaneously. That is what the escalation asked for.

## The `Given` collapse, and the guard that now catches it

The failure this re-authoring fixes was not a wrong assertion — it was a
`Given` that silently became a different `Given`. `_leave_an_orphaned_
quarantined_binding` therefore asserts its own postcondition:

```
assert guild_id not in load_guilds(PROD_SERVER_ID)
assert binding.key_status == KeyStatus.QUARANTINED.value
```

Verified by mutation: reintroducing the `_rollback_data` call with the fix
applied fails at the second guard with "the Given collapsed: … `word_bearers`
is UNBOUND and registration will correctly adopt it under trust-on-first-use",
instead of failing 100 lines later on a roster assertion that names the wrong
defect.

## AC-006.2 — third instance of the same shape (2026-08-03, later)

`test_downgrade_restores_the_prior_shape_exactly` spelled its rollback as
`downgrade(cfg, "-1")` — a distance from a moving head, against a fixture
pinned at `0002`. Revision `0004` (the UI-11 tombstone) landed, `-1` stopped
meaning "back to the baseline", and the test red while the migration it
accused was clean. Verified independently: an absolute downgrade restores
`0002` byte-for-byte. **AC-006.2 holds; the defect was the spelling.**

Fixed by naming the baseline once (`conftest.PRE_FEATURE_HEAD`) and having
both the fixture and the scenario read it. Full write-up: UI-16.

| Guard added | Mutation | Result |
|---|---|---|
| the upgrade must change something | set `PRE_FEATURE_HEAD = "0004"` (baseline == head) | fires — "upgrading to head changed nothing, so this scenario is not exercising a rollback" |
| the downgrade must land on the baseline | restore `downgrade(cfg, "-1")` | fires — "the downgrade did not land on the baseline", naming the cause instead of showing a schema diff |

**A wrong-reason RED was found and fixed while fixing it** (UI-17). The first
version read the constant with a function-level `from conftest import ...`.
That passed in isolation (`26 passed`) and raised `ImportError` in a full run,
because two suites ship a bare `conftest` and `sys.modules["conftest"]` holds
whichever was imported last. Only running the whole suite surfaced it. Hoisted
to module level.

Counts after both fixes:

| Run | Result |
|---|---|
| baseline, remediation markers deselected | `255 passed, 2 skipped, 1 xfailed` |
| full suite | `8 failed, 321 passed, 2 skipped, 1 xfailed` |

The 8 are slice 06's AC-009.4 + AC-009.6 (DELIVER steps in flight) and all 6
of slice 07 (not started). No DISTILL asset is among them.

## `_confirm_if_awaiting` — both seams verified, not merely written

Five mutation checks run against the rewritten helper before it was recorded:
a view+button is found and pressed; a zero-arg `extras` callable is invoked;
an async one-arg `extras` callable is awaited with the interaction; no
confirmation offered is a no-op; an unrecognised widget stays a no-op so the
caller's guard fires rather than the scenario passing quietly. All five pass.

## Baseline

```
pytest tests/unit tests/acceptance -m "not slice_04 and not slice_05 and not slice_06 and not slice_07"
250 passed, 2 skipped, 1 xfailed
```

Unchanged from the pre-escalation baseline. No regression.

## Live counts on 2026-08-03, and why they do not match the 08-02 table

Re-measured after the repairs, with DELIVER mid-flight:

| Marker | 2026-08-02 (as authored) | 2026-08-03 (live) |
|---|---|---|
| `slice_04` | 21 RED / 5 green | **10 failed / 16 passed** |
| `slice_05` | 6 RED / 2 green | 7 failed / 2 passed |
| `slice_06` | 6 RED / 1 green | 6 failed / 1 passed |
| `slice_07` | 6 RED / 0 green | 6 failed / 0 passed |
| full suite | 39 failed / 258 passed | 29 failed / 273 passed |

**Slice 04 moved because DELIVER shipped it**, not because anything was
reclassified: commit `c7d957b fix(guild-key-integrity): canonicalise guild
identifier comparison` turns 11 of its REDs green. That is the slice working
as intended. The as-authored column is the DISTILL deliverable and is what
"40 RED" counts; the live column will keep falling as DELIVER lands slices,
which is the point of the exercise.

Stated explicitly because the two numbers invite a wrong inference in both
directions — a reader seeing `29 failed` could conclude the gate over-counted,
and a reader diffing against `39 failed` could conclude scenarios went
missing. Neither happened. Slice 05's `+1` is the AC-008.1 split; slice 04's
`−11` is delivery.

## Handoff

Gate verdict: **PASS.** Zero category-2 and category-3 failures. DELIVER may
take slices 04-07.

Sequencing is unchanged from `remediation-plan.md` — 04 first, then 05 — with
one now-satisfied precondition: the `KeyConsumptionSite` escalation that
blocked slice 05 has landed, and AC-004.6 is parametrized over the real
inventory. Slices 06 and 07 remain independent and can run in parallel.

Running the suite without the remediation markers is the way to see the
pre-existing baseline while this work is in flight:

```
pytest tests/unit tests/acceptance -m "not slice_04 and not slice_05 and not slice_06 and not slice_07"
```
