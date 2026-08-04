# Upstream changes — DEVOPS → DESIGN, feature `guild-key-integrity`

> For the architect (`@nw-solution-architect`). Three items the DEVOPS wave
> found that change or correct DESIGN artifacts. Per the back-propagation
> contract, none of them are silently edited into the prior wave's text —
> they are raised here and recorded in `feature-delta.md`
> `## Wave: DEVOPS / [REF] Changed Assumptions`.
>
> Dated 2026-07-31. Author: Apex (nw-platform-architect), DEVOPS wave.

---

## U1 — A third new module is required: `bot/obs.py`

**Severity:** low — ~10 LOC, no new dependency, no behaviour change.
**Action needed:** update the DESIGN component table; no ADR change.

### What DESIGN says

`## Wave: DESIGN / [REF] Component Decomposition` lists exactly two new
modules and closes with:

> Genuinely new modules: **two**. Everything else is a rewire of an existing
> component.

### What DEVOPS needs

The observability design (DEVOPS D5) requires `bot/guild_keys.py` to emit the
`guild.key.*` event family and `bot/cogs/tasks_cog.py` to emit
`auto_update.cycle`. Both are structured JSON records in the format the
`sqlite-backend` wave established.

The only implementation of that format is `_emit_structured`, which is
**module-private to `bot/db/session.py`**
([session.py:102](../../../../bot/db/session.py#L102)).

### Why it cannot simply be imported

`bot/db/session.py` imports `alembic`, `cryptography` and `sqlalchemy` at
module scope ([session.py:43-48](../../../../bot/db/session.py#L43-L48)).
`bot/guild_keys.py` is imported by three cogs and a service, so importing
`bot.db.session` from it would make the entire SQLite stack a hard import
dependency of the policy layer — **including on the
`SCRAPCODE_REPO_BACKEND=json` rollback path**, where SQLAlchemy is meant to
be untouched (ADR-006 D9). It would also be an import of a private name
across a package boundary.

Note that `import-linter` would **not** catch this. The cogs contract sets
`allow_indirect_imports = true`, so a cog reaching `bot.db` *through*
`bot.guild_keys` passes the contract while violating its intent.

### Recommendation

Promote the helper verbatim into a new dependency-free module:

```
bot/obs.py           # imports json + logging, nothing else
    _emit_structured(level, event, **fields)
```

`bot/db/session.py` imports it instead of defining it; the four existing
`db.probe.*` call sites are unchanged. `bot/guild_keys.py`,
`bot/cogs/tasks_cog.py` and `bot/cogs/admin_cog.py` import it directly.

Duplicating the helper was the alternative and is rejected: two independently
maintained JSON log schemas is precisely the failure structured logging
exists to prevent, and the KPI queries in `kpi-contracts.yaml` assume one
shape.

**Revised count for the DESIGN table: three new modules, one of them ~10 LOC
of infrastructure.** The Reuse Analysis verdict for `bot/obs.py` is EXTEND in
spirit (the code already exists and moves) rather than CREATE NEW.

---

## U2 — KPI-1's measurement definition is vacuous as written

**Severity:** medium — the KPI as specified could not fail.
**Action needed:** none in DESIGN; DISCUSS text is flagged, not rewritten.

### What DISCUSS says

`## Wave: DISCUSS / [REF] Outcome KPIs`, KPI-1 measurement:

> Delta between the first probe returning a mismatched identity and the alert
> message timestamp

### The problem

Those two events occur in the same coroutine on the same tick. The measured
value is approximately zero regardless of whether the feature works. A metric
that cannot fail cannot inform, and it would report success even if the
hourly loop had stopped running entirely — which is the failure mode closest
to the original incident.

### Replacement

```
detection_latency = alerted_at − last_probe_ok_at
```

The gap back to the last probe that *agreed* — the widest window in which
drift could have gone unnoticed. Bounded above by the `@tasks.loop(hours=1)`
interval plus alert latency, so the ≤ 1 h target is meaningful and falsifiable:
a missed cycle, a hung `httpx` call, or a throttled loop all push it over.

Both timestamps come from records already in the event catalog
(`guild.key.probe.ok`, `guild.key.alert.sent`). No new plumbing.

**The target (≤ 1 h) and the intent are unchanged.** Only the formula is
corrected. Recorded in `kpi-contracts.yaml` under `supersedes`.

---

## U3 — ADR-008 D3 asserts an enforcement mechanism that does not exist

**Severity:** medium — the feature's central safety claim rests on it.
**Action needed:** correct the ADR's factual claim when it is next touched.

### What ADR-008 says

D3, *"Enforcement"*:

> A wrapper is only a chokepoint if bypassing it is caught. Per ADR-006 §I
> the project already runs import-linter + AST pre-commit hooks; a new rule
> forbids …

The same claim appears in `## Wave: DESIGN / [REF] Architecture Enforcement`.

### What is actually in the repository (verified 2026-07-31)

| Claim | Reality |
|---|---|
| import-linter runs | 4 contracts are real in `pyproject.toml`, but run only when someone types `lint-imports` |
| AST pre-commit hooks run | no `.pre-commit-config.yaml`; `.git/hooks` contains only `.sample` files |
| `pytest-archon` composition-root check | package installed in `.venv`, imported by **zero** tests |
| both tools are dependencies | **absent from `requirements.txt`** — present only via ad-hoc local `pip install` |

The rules DESIGN specifies are sound. The sentence claiming they are already
enforced is not accurate about the current project state, and it matters here
more than usual: ADR-008 D3's own reasoning is that a bypassable chokepoint
is not a chokepoint.

### How DEVOPS closes it (D10, operator-selected)

All four DESIGN rules are implemented, on the gate that actually runs today:

- `tests/acceptance/guild-key-integrity/test_architecture_chokepoint.py` —
  AST scan: no `api_key` read (subscript, `.get()`, or attribute) in
  `bot/cogs/*` or `bot/services/*`. Sanctioned readers: `bot/guild_keys.py`,
  `bot/repository.py`, `bot/repository_sqlalchemy.py`.
- `pytest-archon` rules in the same file: `bot.services.chronicl3r.*` must
  not import `httpx`; `bot.guilds` must not import `bot.guild_keys` or
  `httpx`.
- A fifth `lint-imports` contract: `bot.repository*` must not import
  `bot.guild_keys`.
- `import-linter` and `pytest-archon` pinned into `requirements.txt`.

**Suggested ADR-008 D3 wording**, for whenever that ADR is next edited:

> Per ADR-006 §I the project defines import-linter contracts; this feature
> adds an architecture test under `tests/acceptance/guild-key-integrity/`
> that runs them together with an AST scan as part of the standard `pytest`
> gate, and pins both enforcement tools into `requirements.txt`.

No decision in ADR-008 changes — only the description of the mechanism.

---

## Not changed

Everything else in DESIGN survives DEVOPS unaltered. Specifically:

- DDD-1 … DDD-11 are all implementable as designed on the existing platform.
- No new external system, no new container, no new managed service, no new
  environment variable, and no new secret — so `brief.md`'s deployment
  topology and the C4 System Context and Container diagrams are **unchanged**
  and were deliberately not edited this wave.
- The endpoint list and per-hour Tacticus call volume are unchanged (ADR-008
  D2 / ADR-003 amendment), so no rate-limit or quota work is required.
