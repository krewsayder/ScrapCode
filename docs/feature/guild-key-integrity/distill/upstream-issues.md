# Upstream issues — DISTILL, feature `guild-key-integrity`

> Gaps and stale text the DISTILL wave found in prior waves. Per the
> back-propagation contract none of them are silently edited into the prior
> wave's text; they are raised here and reflected in the scenarios.
>
> Dated 2026-07-31. Author: Sentinel (nw-acceptance-designer), DISTILL wave.

Reconciliation gate: **passed, 0 unresolved contradictions.** DISCUSS D1–D6
map onto DDD-1/3/5/6/8 and the DEVOPS slice ordering with no conflict. The
three supersessions in play (DESIGN's "Correction to DISCUSS", DEVOPS U2,
DEVOPS U3) are all documented and authorized, so they are resolved history
rather than open ambiguity.

---

## UI-1 — AC-006.1 and AC-006.2 describe a schema DDD-4 replaced

**Severity:** low — the intent survives intact; only the wording is stale.
**Action needed:** reword when US-006 is next touched. No decision changes.

### What DISCUSS says

> AC-006.1 — Given the Alembic revision is applied to a copy of the production
> database, when it completes, then **every existing guild row gains the new
> columns** with `key_status = 'active'` and null identity fields, and no
> existing row is otherwise altered.

US-006's title and body likewise say the migration "adds `tacticus_guild_id`
… to `guilds`".

### Why it no longer holds

DDD-4 moved binding state into its own `guild_key_bindings` table precisely
so `save_guilds` cannot clobber it. Under DDD-4 the migration adds **no**
column to `guilds` and there is **no** backfill (trust-on-first-use populates
the table). The AC as literally written is now unsatisfiable.

DESIGN's "Correction to DISCUSS" section addressed AC-001.7 and AC-006.3 and
confirmed both still run. It did not revisit AC-006.1 or AC-006.2.

### How DISTILL resolved it

The scenarios are written against DDD-4 — the LOCKED decision — and assert
the AC's actual intent, which is stronger than the original text:

```
tests/.../test_slice_01_bind_and_report.py
  ::test_upgrade_creates_the_binding_store_and_touches_no_guild_record
```

asserts that after `alembic upgrade head` the `guilds` rows are byte-identical
AND its column list is unchanged. The original AC would have been satisfied by
a migration that added columns; the scenario now fails if one does.

**Suggested AC-006.1 wording:**

> Given the revision is applied to a copy of the production database, when it
> completes, then a `guild_key_bindings` table exists and is empty, and every
> existing `guilds` row is byte-identical including its column list.

---

## UI-2 — `environments.yaml` cross-references two ACs to the wrong environments

**Severity:** cosmetic — traceability labels only, no scenario affected.
**Action needed:** correct the two `exercises:` lists.

The DEVOPS artifact lists `AC-001.3` (the first-bind announcement) under
`bound-matching`, and `AC-001.2`/`AC-001.6` under `clean`. The mapping should
be:

| AC | Environment it actually belongs to | Why |
|---|---|---|
| AC-001.3 (announce once on first bind) | `clean` | there is no binding yet — that is the definition of `clean` |
| AC-001.4 (do not re-announce) | `bound-matching` | requires an existing binding |
| AC-001.2 (`/view_config` shows the binding) | `bound-matching` | needs something to show |

The suite maps them correctly regardless; this is a documentation label, not
a coverage hole.

---

## UI-3 — `hypothesis` is required and is not in `requirements.txt`

**Severity:** medium — one test layer cannot run without it.
**Action needed:** pin it, alongside the two tools DEVOPS D10 already pins.

DISTILL emits a Tier B state-machine layer
(`tier_b/test_key_status_state_machine.py`) because the key-status model *is*
a state machine — three states, six commands — which is the Hebert ch.11
trigger. Two of its properties are claims about the whole state space that no
enumerated example establishes:

* `quarantine_is_never_a_trap` — from every reachable quarantined state there
  exists a path back to active. DISCUSS D3 is exactly this claim, and a single
  example only shows that one path works.
* `quarantined_guilds_never_write` — zero rows under every interleaving of
  probes and key updates, not just the orderings someone wrote down.

`requirements.txt` has `pytest` and `pytest-asyncio` and no property-testing
library. The layer currently `importorskip`s with a clear reason, so the suite
is honest about it rather than silently missing coverage — but it is missing
coverage until the pin lands.

Combined with DEVOPS D10, three lines are needed:

```
hypothesis
import-linter
pytest-archon
```

`test_architecture_chokepoint.py::test_both_enforcement_tools_are_pinned`
already fails until the last two arrive.

---

## UI-4 — `bot/services/tacticus/guild_client.py` now owns three shared types

**Severity:** low — a clarification to the DESIGN component table, not a change.
**Action needed:** none, recorded so DELIVER does not re-derive them.

DESIGN describes the module as `fetch_guild_snapshot(api_key) -> GuildSnapshot`,
~40 LOC. DISTILL additionally placed `ProbeOutcome`, `KeyStatus` and
`GuildIdentity` there, because the acceptance suite must compare against the
*same* enum objects production uses — a test-side copy of an enum compares
unequal under `is`, and the copy that silently drifts is always the one nobody
runs in production.

`tests/.../domain_types.py` re-exports them rather than re-declaring them
(Mandate-12 criterion 2). The module stays import-light: `dataclasses` and
`enum` at module scope, `httpx` only inside `fetch_guild_snapshot`.

---

## Not an issue, recorded because it looks like one

**Three architecture tests pass today.** `test_the_guilds_wrapper_layer_stays_free_of_policy_and_http`,
`test_storage_never_imports_policy` and `test_import_linter_contracts_all_pass`
are green before a line of the feature is written. They are not vacuous — they
are regression guards on properties the repo currently has and this feature
could easily break, and one of them (`lint-imports`, 4 contracts kept)
confirms that adding `bot/guild_keys.py`, `bot/obs.py` and
`bot/services/tacticus/` broke none of the existing contracts.
