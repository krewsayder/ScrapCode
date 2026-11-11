# Evolution — chronicler-key-link

> **Status:** Feature complete on `feature/chronicler-key-link`. Not yet
> enabled on the VM — the Chronicl3r v2 liveness check is still to be done
> (see Known follow-ups).
> **Dates:** 2026-10-24 → 2026-10-25 (UTC; one delivery session).

## What happened

ScrapCode holds the Tacticus API keys players give it at
`/registration register`, but Chronicl3r — our backend data store — never
received any of them. The root cause was twofold: the bot never sent a key,
and the only route it had (the old PATCH, `set_player_api_key`) needed a
`tacticus_user_id` that the bot cannot derive from a key. That PATCH now
answers 405.

This feature moves the bot onto Chronicl3r contract v2, where a key is linked
by the key alone. The goal: every key a player gives ScrapCode is linked in
Chronicl3r, and deleting a registration deletes it there. Keys already held
before the feature are backfilled by a rate-limited background loop.

## What shipped (three phases, seven steps)

- **Phase 01 — speak contract v2.**
  - *01-01* — `bot/services/chronicl3r/client.py` gains `link_api_key` (60 s
    timeout), `lookup_api_key` and `delete_api_key` (10 s). Each returns a
    `KeyCallResult(status, code, retry_after, profile)` and never raises:
    timeouts, connection errors and token-fetch failures come back as
    `code="transport"`, a non-JSON 404 as `code="not_deployed"`. `profile` is
    kept out of the repr and the result has no field for the key. The PATCH
    and the duplicate `get_player_profile` are removed.
  - *01-02* — `PlayerService` wraps them with `asyncio.to_thread` as
    `link_key`, `lookup_key`, `unlink_key`. Profiles are cut to
    `{"has_valid_api_key": bool}`. `unlink_key` looks up, DELETEs by the
    returned `tacticus_user_id` and returns a bool; the ID never leaves the
    method.
- **Phase 02 — the key follows the registration.**
  - *02-01* — `/registration register` links the key after the Tacticus check
    and the local save, best-effort, and adds one outcome line plus the
    storage/consent notice. Re-registering with a different key unlinks the
    old one first.
  - *02-02* — `/registration unregister` unlinks, best-effort. The player is
    always unregistered; on failure the reply says removal "could not be
    confirmed". Profiles are never deleted.
  - *02-03* — `/deregister_guild`, once confirmed, collects the guild's keys
    *before* the cascade delete and unlinks each *after* it. A Chronicl3r
    failure cannot block or reverse the deregistration.
- **Phase 03 — backfill the keys already held.**
  - *03-01* — pure schedule in `bot/services/chronicl3r/key_sync.py`: per-code
    retry/park/pause rules, key fingerprints (SHA-256) only, never keys.
  - *03-02* — `chronicler_key_sync` loop in `RegistrationCog`, every 2 min,
    at most 20 lookups and at most 1 link POST per tick (~30 links/h).
    `CHRONICL3R_KEY_SYNC=off` stops it.

Then three clean-up commits:

- `1faf80e` — one shared key-removal helper,
  `bot/cogs/chronicler_key_removal.py`, used by `/registration unregister` and `/deregister_guild`; the
  sync and link steps lifted out of long methods.
- `631e36c` — one fake Chronicl3r transport, `tests/unit/chronicler_key_fakes.py`,
  shared across the key test modules.
- `d542e85` — the import-linter cogs contract now covers the new
  `chronicler_key_removal` module. The adversarial reviewer missed this gap;
  the orchestrator caught it.

Shipped behaviour is recorded in
`docs/product/architecture/chronicler-player-profiles-api.md`, section
"What ScrapCode does with keys".

## Decisions and deviations

1. **Auth failure inside the key-call helper returns `transport`.** A failed
   token fetch is treated like any other transport failure, so the "never
   raises" contract holds and the loop backs off instead of crashing.
2. **`player_service=None` defaults kept** on the cog constructors so the
   pre-existing one-argument tests run unmodified.
3. **The loop starts in `cog_load`, not `__init__`** — it needs a running
   event loop.
4. **Schedule state is in memory.** A restart re-checks every player with the
   cheap lookup rather than persisting outcomes.
5. **`tacticus_user_id` is not stored.** Chronicl3r permits it internally, but
   storing it waits on the pending player-unit-history binding decision.
6. **Unregister failure wording.** The reply says removal "could not be
   confirmed" rather than claiming success or failure.
7. **Backfill rate 30/h, not 60/h.** The contract doc first suggested the full
   60/h throttle; the loop uses half, leaving headroom for live registrations.

## Verification at close

- 7 roadmap steps, each RED → GREEN → COMMIT, all PASS (ADR-025 canon).
  `des-verify-integrity`: all 7 steps complete.
- Suite: 501 passed / 2 skipped / 1 xfailed / 1 failed (up from 450 passed).
  The one failure is pre-existing and out of scope:
  `test_guild_keys_quarantine_gate.py::test_a_quarantined_guild_is_refused_before_any_request_whatever_enforce_says`.
- `lint-imports`: 6 contracts kept, 0 broken.
- Adversarial review APPROVED.
- Mutation testing: skipped, `pre-release` per `CLAUDE.md`.

## Process notes

- Plan mode blocked crafter dispatches until the operator turned it off.
- A weekly usage limit cut step 02-03 mid-GREEN; it was resumed from that
  point, not restarted.

## Known follow-ups

1. **Confirm v2 is live before enabling on the VM.** One lookup with a dummy
   key: a JSON 404 `not_linked` means live; an HTML 404 means not deployed.
   Needs the operator's OK.
2. **`authenticate()` raises `KeyError`** if the token response lacks `token`.
   The cog callers catch it; the client should not raise.
3. **Stale docstring** in `bot/warehouse/resolver.py` still assumes the bot can
   read keys back from Chronicl3r.
4. **No cross-server guard on unlink.** If one server unregisters a key another
   server still holds, the key is removed from Chronicl3r and the loop relinks
   it from the other server on a later tick.

## Commits (this feature)

On `feature/chronicler-key-link`, `e2d1823..306ec43`: seven step commits
(`bf3213a`, `6463aaf`, `c7542f2`, `7100926`, `6835682`, `b272f6d`,
`f974c04`), two refactors (`1faf80e`, `631e36c`), one import-contract chore
(`d542e85`) and the contract/roadmap docs (`306ec43`). The trace record is
`docs/feature/chronicler-key-link/deliver/roadmap.json` +
`execution-log.json`.
