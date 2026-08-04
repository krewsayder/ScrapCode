# Remediation plan — `guild-key-integrity`

**Raised:** 2026-08-02 · **Trigger:** adversarial re-review of the shipped
DELIVER wave (4 independent Opus reviewers, one claim each)
**Status:** scoped, not started · **Branch:** `feature/guild-key-integrity-slice-01`
(30 commits, **unpushed — hold**)

## Why this exists

The DELIVER wave closed with a Haiku-model reviewer returning APPROVED on all
ten load-bearing claims, and `feature-delta.md` records several of them as
"adversarial review CLEAN". A re-review at Opus, with each reviewer told to
falsify rather than confirm, broke **four of five** claims.

The finding that matters most is not any single defect. It is the *shape*:

> The policy core is sound. Every gate around it is not.

`verify_and_resolve`, `quarantine`, `release`, `install_guild_key` and
`replace_guild_key` survived every direct attack — no partial write before the
raise, no `api_key`/`api_key_hmac` desync reachable, no CASCADE on the key
path, no raw plaintext key in any record. **AC-003.2 holds exactly as
written.** What failed is the parser feeding the policy, the call sites that
skip it, the composition root that disables it, and the tests that certified
it.

A mutation sweep confirms the suite is **not** blanket Testing Theater —
disabling the quarantine gate reds 12 tests, making `matches()` always-true
reds 16, dropping `order_by(rowid)` reds the season-SPOF test. It has real
teeth where it bites. It does not bite in the places below.

## Requirements currently NOT met

| Requirement | Status | Slice |
|---|---|---|
| US-001 / AC-001.x — only genuine drift quarantines | ✗ six well-formed 200s quarantine a healthy guild | 04 |
| US-003 / AC-004.6 — all seven sites blocked | ✗ `/register_guild` writes a quarantined roster | 05 |
| KPI-2 — zero contaminated rows | ✗ reproduced: wrong roster written, 5 members flipped `is_former` | 05 |
| KPI-4 — zero false positives | ✗ a vendor uuid-case change flips every guild in one cycle | 04 |
| KPI-5 — 100% of guilds unaffected | ✗ measured 0% for `/set_live_cluster_leaderboard` | 05 |
| KPI-6 — no key material in any record | ✗ ciphertext + full 64-hex HMAC reach `discord.log` and Discord | 06 |
| DISCUSS D3 — quarantine is never a trap | ✗ a case-poisoned binding refuses the operator's correct key | 04 |
| ADR-006 D8 — startup probe gates boot | ✗ `repo.probe()` has no production caller | 07 |

Availability regression, not on the original requirement list but the most
severe single defect found: **a HTTP 200 carrying a non-JSON body permanently
stops hourly ingestion for every server** until the process is restarted.

## Slices

Ordered by learning leverage first, then severity, then dependency.

| # | Brief | Goal | Est. |
|---|---|---|---|
| 04 | [slice-04](slices/slice-04-survive-hostile-vendor-output.md) | The classifier is total over anything Tacticus can return | ~1 d |
| 05 | [slice-05](slices/slice-05-close-the-write-holes.md) | A quarantined guild writes zero rows at *every* site | ~1 d |
| 06 | [slice-06](slices/slice-06-admin-command-safety.md) | No admin command leaks a secret or silently destroys history | ~1 d |
| 07 | [slice-07](slices/slice-07-composition-root-integrity.md) | The bot refuses to run in a config where quarantine is inert | ~0.5 d |

## Hard dependency: test integrity is NOT in these slices

Three defects are in acceptance-test assets. Per the standing DELIVER
constraint — *DELIVER does not author acceptance tests; every test body and
the `assert` above it is off limits; escalate, do not edit* — they are
**escalated to `@nw-acceptance-designer`**, not scoped here:

1. **`KeyConsumptionSite` encodes the wrong seven sites.**
   [`domain_types.py:48-63`](../../../tests/acceptance/guild-key-integrity/domain_types.py) omits
   `set_live_leaderboard`, `set_live_cluster_leaderboard` and
   `register_guild → refresh_guild` — precisely where slices 04/05 land — and
   substitutes three others. Two remaining branches call `active_key` and
   raise, driving no production entry point, which makes their
   `call_count == 0` assertion vacuous.
2. **The Tier B property executes zero assertions.** `quarantine_is_never_a_trap`
   is an `@invariant()` that mutates the model (`update_key` releases the
   quarantine) and sorts before `quarantined_guilds_never_write`, which
   therefore always finds the guild ACTIVE and short-circuits. Measured: 0
   assertions across 200 examples × 25 steps.
3. **The fake cannot emit hostile payloads.** The conftest payload builder can
   only produce a well-formed `{"guild": {...}}` with a `guildId` from two
   hand-picked constants, or drop the field. Every slice-04 defect is
   unreachable from the suite by construction.

**Sequencing consequence:** slice 05 **cannot be verified** until (1) is
fixed — the AC that is supposed to prove "all seven sites" is parametrized
over the wrong set. Raise the escalation before dispatching slice 05.

## SSOT corrections owed

Independent of the code fixes, these records are false and should be corrected
when the remediation lands:

- `feature-delta.md:2235` — DDD-6 recorded "adversarial review CLEAN". Not
  supported; the review verified policy branching on a classification it was
  handed and never questioned the classifier's input domain.
- `kpi-contracts.yaml:159-168` — KPI-2 cited as "the only KPI with a
  property-based assertion behind it… holds across every interleaving". It
  holds across zero.
- `kpi-contracts.yaml:60` — `guild.key.ingest.blocked` declared
  `level: WARNING`, `emitter: bot/guild_keys.py`, `fields: [caller]`;
  production emits `INFO` from `bot/cogs/tasks_cog.py` with `key_ref` and no
  `caller`. Line 66 attributes `guild.key.updated` to `admin_cog.py`; the
  actual emitter is `guild_keys.py:334`.
- UD-13 in the DELIVER upstream-issues list describes a potential *flake*. The
  measured defect is *vacuity*. Different class, different fix.

## Deferred — real, non-blocking, not scoped into 04–07

Recorded so they are not rediscovered as new:

- Healthy guilds reported as `no_key_registered` during a Tacticus outage
  (`tasks_cog.py:223-231`), which fires KPI-5's ERROR rule with a false cause
  and posts nothing to Discord.
- `guild.key.alert.sent` emitted for alerts that are never posted — both
  `record_quarantine_alert` returns are `None` and all three call sites
  discard the value, so the 24 h clock is consumed by an invisible alert.
- `guild.key.ingest.blocked` emitted from only two of the block paths;
  `update_cog._verified_key` swallows `GuildQuarantined` silently.
- Quarantine rendered to officers as "has no API key set"
  (`update_cog.py:74-76, 129-130`) with no mention of `/update_guild_key`, the
  only exit.
- Probe/fetch TOCTOU: raid data comes from a second GET issued after the probe
  returns, with no re-check between. PLAUSIBLE only — no deterministic
  reproduction, and `conftest.py:17-19` concedes the suite cannot model it.
- `api_key_hmac`'s UNIQUE constraint is table-global, so two Discord servers
  cannot register the same Tacticus key and one tenant can detect another's
  key by collision. Cross-tenant coupling ADR-004 isolation would not
  sanction. Schema change — needs its own decision, not a slice.
