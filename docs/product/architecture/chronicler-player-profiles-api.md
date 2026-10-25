# Chronicler player-profiles API — what ScrapCode needs to know

Status as of 2026-10-23, against Chronicler's player-profiles **contract v2** and
the key lookup. The source of truth is `docs/api/player-profiles.md` in the
**Chronicler** repo, and the live schema is at
`https://www.chronicl3r.com/tacticus-guild-raid/api/v1/docs/` (auth-gated). This
page restates only what the bot depends on, and maps it onto this codebase. If the
two disagree, Chronicler's doc wins.

v2 and the lookup ship in one Chronicler deploy, and no date has been given. The
contract's own detection rule: **a v2 server puts a `code` field in every key-link
error body, and a route that isn't deployed returns Django's HTML 404 page**, while
every application response is JSON. Gate on that, not on a date.

The v2 contract answers the questions ScrapCode raised on 2026-10-23 (see
[Questions we raised](#questions-we-raised-and-the-answers)).

## Why this matters

Every profile the bot has registered in Chronicler has an empty API key. The bot
registers profiles by `tacticus_user_id` (roster sync and raid ingest), but has
never sent a key: `chronicl3rClient.set_player_api_key` (PATCH) has no call site in
any commit. The PATCH could not have been wired in, because it is addressed by
`tacticus_user_id`, and the bot never learns which ID a key belongs to. Tacticus
`GET /api/v1/player` does not return the player's ID (SPIKE-3,
[findings-spike-3.md](../../feature/player-unit-history/spike/findings-spike-3.md)).

The new link endpoint removes that obstacle: the bot sends just the key, and
Chronicler works out which registered profile it belongs to. Its 200 returns that
profile's `tacticus_user_id`, which the bot may store (see
[Identity binding](#identity-binding-for-player-unit-history)).

## Route map

Base: `https://www.chronicl3r.com/tacticus-guild-raid/api/v1/`. Auth is unchanged:
`Authorization: Token <token>` from `POST /api/auth/token/`, for an account in the
`api_user` group (env vars `CHRONICL3R_APP_USERNAME` / `CHRONICL3R_APP_PASSWORD`).
Timestamps are ISO-8601 UTC with `Z`.

| Method | Path | Throttle | ScrapCode caller |
|---|---|---|---|
| POST | `player-profile/register/` | none | `PlayerService.get_or_register` |
| GET | `player-profiles/<tacticus_user_id>/api-key/` | none | `chronicl3rClient.get_profile` |
| POST | `player-profiles/api-keys/` — link a key | 60/h | none yet |
| POST | `player-profiles/lookup/` — which player holds a key | 6000/h, own bucket | none yet |
| DELETE | `player-profiles/<tacticus_user_id>/api-key/` — remove the key | none | none yet |
| DELETE | `player-profiles/<tacticus_user_id>/` — remove the profile | none | none yet |
| PATCH | `player-profiles/<tacticus_user_id>/api-key/` | — | **Removed, 405.** `set_player_api_key` (dead code) |

Every route is scoped to rows **this account** registered. Another account's row
looks exactly like a missing one.

401, 403, 405 and 415 keep DRF's `{"detail": ...}` body with no `code` on every
route. They mean a configuration error; the status is enough.

## Response shapes the bot relies on

- **Register 201:** `{"data": {"id", "tacticus_user_id", "tacticus_display_nm",
  "has_valid_api_key", "last_updated"}}`. 409 means this account already
  registered the ID. Unchanged by v2.
- **GET 200 / lookup 200:** the same eight keys, with **no** `data` envelope:
  `id`, `tacticus_user_id`, `tacticus_display_nm`, `created_at`, `updated_at`,
  `has_valid_api_key`, `api_key_verified_at`, `api_key_expires_at`. With no key
  stored, the last three are `false`, `null`, `null`. A `null` expiry on a linked
  key means it never expires. The key itself is never returned.
- **Link 200:** `tacticus_user_id`, `tacticus_display_nm`, `has_valid_api_key`,
  `api_key_verified_at`, `api_key_expires_at`.
- **DELETE 204:** no body, also when there was nothing to remove. 404 (no `code`)
  if this account did not register the ID.

Existing code only reads `tacticus_display_nm`, so the new GET fields don't break
anything.

## Linking a key

```
POST player-profiles/api-keys/
{"tacticus_player_api_key": "<personal-api-key>"}
```

### How Chronicler matches the key

1. It reads the key's player from Tacticus `/api/v1/player`: name, power level and
   unit progress.
2. Candidates are this account's rows whose stored name is exactly the key's name,
   at most 5, oldest first.
3. If the key is already stored on a candidate, it returns 200 for that row
   immediately, without calling the game server or writing anything
   (`api_key_verified_at` is not moved).
4. Otherwise it checks each candidate on the game server: name and power level
   equal, and every showcase unit with XP equal on XP, level, rank and progression,
   with at least 3 units compared.
5. If exactly one candidate matches, it stores the key on that row, sets
   `api_key_verified_at` / `api_key_expires_at`, and refreshes the stored name.
   Otherwise it writes nothing.

A different key for an already-linked player replaces the stored one (rotation). A
failed POST leaves any stored key unchanged.

### Error codes, and what this bot should do

Every error body is `{"code": "...", "detail": "..."}`. **Branch on `code`**: it
is contract, and `detail` is prose that may change. Never show `detail` to players
verbatim.

| Status | `code` | What it means for ScrapCode | Bot action |
|---|---|---|---|
| 200 | — | Linked | Store `tacticus_user_id` internally. Done |
| 400 | `missing_key` | Bug in our request | Log, don't retry |
| 400 | `invalid_key` | Tacticus rejected the key | Tell the player the key is bad. Rare, since `/registration register` checks the key first |
| 404 | `no_profiles` | This account has no registered players at all | Configuration problem: roster sync hasn't run. Alert, don't retry per player |
| 404 | `no_name_match` | No registered row has the key's name | See below |
| 400 | `no_match` | Candidates exist, none verified | Retry once after a few minutes. Not tightly: every try costs a slot |
| 400 | `insufficient_data` | Fewer than 3 levelled showcase units | Don't retry. Tell the player why |
| 409 | `ambiguous` | Two candidates verified | Don't retry. Report to the Chronicler owner |
| 503 | `upstream_rate_limited` | Tacticus returned 429 | Retry after `Retry-After` |
| 503 | `upstream_unavailable` | Tacticus or game server down | Retry after `Retry-After`, with backoff |
| 429 | `throttled` | Over 60/h | Wait at least `Retry-After`, then retry with backoff |

**`no_name_match` for this bot.** Chronicler's generic advice is "register the
player, then retry". The bot can't do that from a key, because the key doesn't
reveal the `tacticus_user_id`. Players are registered only by roster sync
([player_service.py:215-245](../../../bot/services/chronicl3r/player_service.py#L215-L245))
and raid ingest
([tasks_cog.py:534-544](../../../bot/cogs/tasks_cog.py#L534-L544)). So
`no_name_match` means one of two things:

- the player isn't in a tracked guild's roster yet, or
- they renamed since Chronicler last read their name. Chronicler re-reads names
  older than 24 hours, so a link succeeds within about a day of a rename.

Either way, retry later (for example once after the next roster sync, then once a
day), and stop after a few days.

### Throttle

60 POSTs per hour **per Chronicler account**, counting failures and re-POSTs of an
already-linked key. The bot uses one account for every Discord server, so live
`/registration register` calls and any backfill share one budget. There is no
special backfill allowance. Register, GET, both DELETEs and the lookup don't spend
this budget.

### Timeouts

A typical link POST takes 1–3 s, about 45 s at worst (Tacticus 10 s, plus up to 5
game-server calls of 10 s × 3 attempts each, stopping at the first error). Use a
**60 s client timeout**. Every other route makes no upstream call and should be
fast; 10 s is plenty.

## Looking up a held key

```
POST player-profiles/lookup/
{"tacticus_player_api_key": "<personal-api-key>"}
```

Returns this account's profile whose stored key is the submitted one. It makes no
upstream calls and writes nothing: Chronicler keeps a keyed HMAC of each linked key
and matches on it.

| Status | `code` | Bot action |
|---|---|---|
| 200 | — | The key is linked to that profile. Check `has_valid_api_key` |
| 400 | `missing_key` | Bug in our request |
| 404 | `not_linked` | Never linked, removed, rotated away, or linked by another account. Link it with the POST above |
| 429 | `throttled` | Wait at least `Retry-After` |

An expired key still matches, with `has_valid_api_key: false`.

Use the lookup, not a link re-POST, to find the player behind a key the bot holds.
It has its own 6000/h bucket, so it never spends a link slot.

## Key status, expiry and revocation

- `has_valid_api_key` is true when a key is stored and `api_key_expires_at` is
  `null` or in the future. It flips to `false` on expiry. The expired key is kept
  until it's replaced or deleted.
- **Chronicler can't detect a revoked key.** It doesn't call Tacticus with stored
  keys yet, so `has_valid_api_key: true` means "linked and not expired", not
  "known to work". The bot's own Tacticus calls with the key are the real check.
- No periodic re-POST is needed. Re-POST only when a player gives the bot a new
  key.
- **Retention:** Chronicler keeps a key until it's deleted, rotated, or its
  profile is deleted. There is no automatic purge, so **the bot must DELETE the
  key when a player unregisters.**

## Identity binding for player-unit-history

The link 200 (and the lookup 200) return the profile's `tacticus_user_id`.
Chronicler's contract says it's the immutable Tacticus account ID, a stable field,
and the bot **may store it internally** as a key-to-player binding, provided it's
**never shown to players**.

This changes the plan for
[`IdentityResolver`](../../../bot/warehouse/resolver.py), which is still a RED
scaffold. Its docstring assumes the bot reads other players' keys from Chronicler
and compares HMACs locally. Chronicler never returns keys, so that design isn't
possible. The available path is: link each held key, then record the returned
`tacticus_user_id` as the binding.

Before adopting it, decide whether a Chronicler link counts as a `MATCHED` binding.
The resolver's rule is "exact equality on a secret, never a guess". The link match
itself isn't equality on a secret. It's exact equality on name, power and at least
3 units' progression, against the player the key proves, and Chronicler refuses
when more than one candidate passes. The lookup afterwards *is* exact equality on a
keyed hash, but only of keys the link already placed.

## What ScrapCode does with keys (shipped, feature `chronicler-key-link`)

1. **Client methods** in [client.py](../../../bot/services/chronicl3r/client.py):
   `link_api_key` (60 s timeout), `lookup_api_key` and `delete_api_key` (10 s).
   Each returns a `KeyCallResult(status, code, retry_after, profile)` and never
   raises: a timeout, connection error or failed token fetch comes back as
   `code="transport"`, and a non-JSON 404 as `code="not_deployed"`. `profile` is
   left out of the repr, and the result has no field for the key.
   `set_player_api_key` (the PATCH, now 405) and the duplicate
   `get_player_profile` are gone.
2. **`PlayerService`** ([player_service.py](../../../bot/services/chronicl3r/player_service.py))
   wraps the calls with `asyncio.to_thread`: `link_key`, `lookup_key` and
   `unlink_key`. Every result it returns has its profile cut down to
   `{"has_valid_api_key": bool}`. `unlink_key` looks the key up, DELETEs by the
   `tacticus_user_id` it gets back, and returns a bool. The ID never leaves that
   method.
3. **`/registration register`** links the key after the Tacticus check and the
   local save, and the link is best-effort. The reply gets one line in our own
   words: linked, "will retry automatically", or the `insufficient_data`
   explanation. It also carries the storage notice: "Your key is also stored in
   Chronicl3r, our backend data store, which may allow roster analytics.
   Unregistering deletes it." When a player re-registers with a different key,
   the old key is unlinked first.
4. **`/registration unregister`** removes the key from Chronicl3r, best-effort.
   If removal fails, the player is still unregistered and the reply says removal
   couldn't be confirmed. Profiles are never deleted.
5. **`/deregister_guild`**, once confirmed, collects that guild's keys before the
   deletion cascades and unlinks each one after the deletion. A Chronicl3r failure
   can't block or reverse the deregistration.
6. **Backfill** runs as a background loop, `chronicler_key_sync` in
   `RegistrationCog`, every 2 minutes.
   - Each tick makes at most 20 lookups and at most 1 link POST. That is about
     30 links an hour, half of the 60/h budget.
   - A pure schedule in [key_sync.py](../../../bot/services/chronicl3r/key_sync.py)
     decides what is due:
     - `no_match` retries every 15 min, up to 4 tries.
     - `no_name_match` retries after 24 h.
     - `insufficient_data`, `ambiguous`, `invalid_key` and `missing_key` park the
       player until the key changes.
     - `no_profiles` and `not_deployed` pause the loop for 1 h.
     - Throttling, transport errors and unknown codes pause the loop for
       `Retry-After` (300 s if absent), doubling on repeats up to 1 h.
   - The schedule holds key fingerprints (SHA-256), never keys. It is
     **in memory**, so a restart re-checks everyone with the cheap lookup instead
     of persisting outcomes.
   - Set `CHRONICL3R_KEY_SYNC=off` to stop the loop.
7. **`tacticus_user_id` is not stored.** That depends on the player-unit-history
   binding decision above.
8. **Logs** carry only `server_id`, `discord_id` and an outcome code, plus pause
   seconds for loop pauses. Every cog path catches its own exceptions, so nothing
   reaches the global handler at [main.py:104](../../../main.py#L104), which
   echoes raw exception text.

## Questions we raised, and the answers

| We asked | Chronicler v2 |
|---|---|
| Machine-readable error codes | `code` on every key-link and lookup error |
| Tacticus 429 looked like "invalid key" | Now 503 `upstream_rate_limited` with `Retry-After` |
| 404 ambiguous; can't register from a key | Split into `no_profiles` / `no_name_match`. Not registering from a key is by design. A rename catches up within about 24 h |
| No way to remove a key or profile | Two DELETE routes, idempotent, unthrottled |
| No key-status read | GET now has `has_valid_api_key`, `api_key_verified_at`, `api_key_expires_at` |
| Expiry and revocation | Expiry is reflected in `has_valid_api_key`. Revocation isn't detected. No periodic re-POST |
| Throttle versus backfill | Raised to 60/h. No backfill path. Re-POST of a linked key is cheap upstream, but still costs a slot |
| Fewer than 3 showcase units | New `insufficient_data`, don't retry |
| May we store `tacticus_user_id`? | Yes, internally. Never display it |
| Timeouts | Use 60 s. `Retry-After` on every 503 and 429 |
| Deploy timing | No date. Detect via `code` in error bodies / HTML 404 |
| (new) Find the player behind a key | `POST player-profiles/lookup/` |

Still open: the deploy date.

## Docs in this repo that are now stale

- [overview.md — External service API docs](overview.md#external-service-api-docs)
  and brief.md §7 list the Chronicler API contract (Gap 4) as unwritten. This page
  covers the player-profile part of it.
- [bot/warehouse/resolver.py](../../../bot/warehouse/resolver.py) docstring assumes
  the bot can read profile keys from Chronicler. It can't (see
  [Identity binding](#identity-binding-for-player-unit-history)).
