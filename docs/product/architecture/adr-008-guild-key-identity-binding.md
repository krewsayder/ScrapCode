# ADR-008: Guild API keys are bound to a Tacticus `guildId` and quarantined on drift

- **Status:** Accepted — DESIGN wave, feature `guild-key-integrity`
- **Date:** 2026-07-31
- **Related:** [ADR-003](adr-003-chronicler-first-data-doctrine.md) (extends the
  direct-Tacticus allow-list), [ADR-006](adr-006-sqlite-storage-backend.md) (D7
  secrets, §I architecture enforcement),
  [ADR-007](adr-007-repo-read-methods-get-guild-data-path-deprecation.md) (the
  precedent for growing the ABC with feature-shaped storage methods),
  [ADR-004](adr-004-multi-tenancy-isolation.md) (rule 1 — thread
  `discord_server_id`)
- **Driving stories:** US-001 … US-006 in
  `docs/feature/guild-key-integrity/feature-delta.md`

## Context

A Tacticus API key belongs to a **player**, not to a guild. When a player with
guild-scope permissions changes guild, their key keeps working and starts
returning the **new** guild's data. ScrapCode stores one such key per registered
game guild and has never checked what guild it resolves to.

On approximately 2026-07-28 the guild master of 【UNDV】Word Bearers moved to
【UNDV】Dark Mechanicum. For roughly 72 hours `GET /api/v1/guild` and
`GET /api/v1/guildRaid/{season}` returned Dark Mechanicum data under the
`word_bearers` identity. `PlayerService._fetch_roster` performs no identity
check, and with `STALE_AFTER_HOURS = 1` the roster inverted hourly. Measured
damage: season 106 was 30-of-30 battle rows and 20-of-20 bomb rows off-roster;
30 real members were flipped to `is_former` while 30 non-members were marked
active. Detection was by a human noticing unfamiliar names — there was no error,
no warning, and no log line. Both guild names carry the same 【UNDV】 alliance
prefix, which is very likely why the wrong names did not read as wrong.

Seven distinct call sites read a guild's `api_key` and send it to Tacticus. A
guard added to some of them still leaves the rest contaminating.

Quality-attribute priorities for this feature, in order:
**correctness/provenance > operability > testability > maintainability >
time-to-market**. Scalability is not a priority (one process, one VM — ADR-004).

## Decision

### D1 — `guildId` is the binding; tag and name are display only

`GET /api/v1/guild` returns a `guildId` UUID. It is **not in the published
Tacticus documentation**, but it is returned by the live API — verified against
production on 2026-07-31 (`d71d583f-c970-4493-936f-178c21ab844c` for Dark
Mechanicum, `b64bdba4-36ac-4229-bd29-4b7b6ce7f44f` for Word Bearers). It is
tracked, so it is what we bind on.

| Field | Role | Compared |
|---|---|---|
| `guildId` | the binding | **yes — the only field compared** |
| `guildTag` | alert text, `/view_config` | no |
| `name` | alert text, `/view_config` | no |

`name` is mutable and the alliance prefix makes different guilds look alike, so
it must never gate. `guildTag` is not compared either, so a legitimate retag
cannot trip the lock.

**If `guildId` is absent** the probe is classified `unverifiable`: no
quarantine, no ingestion block, and a loud persistent alert that identity
verification is offline. There is deliberately **no silent fallback to tag
comparison** — a quiet downgrade to a weaker check is the same failure shape as
the incident (the system appears protected while it is not). Blocking would be
worse: the field vanishing affects every guild simultaneously, so quarantining
on it would halt the whole cluster over a vendor change.

### D2 — The identity probe and the roster fetch are one request

`PlayerService._fetch_roster` already calls `GET /api/v1/guild`, and that
response carries both the identity fields and `guild.members`. The probe is
**folded into** that call rather than added alongside it.

Consequences: Tacticus calls per guild per hour are unchanged rather than
doubled, and the probe and the roster **cannot disagree**, because they are the
same response. A separate probe would permit a window where the identity check
passes and the roster then arrives from a different guild.

This also removes the Tacticus-direct call from the Chronicler package.
`PlayerService.refresh_guild` and `validate_if_stale` now take a
`GuildSnapshot` and make no HTTP call at all; `_fetch_roster` is deleted. That
resolves the oddity ADR-003 row #2 flags, and — decisively — it breaks an import
cycle: `PlayerService` imports `bot.guilds`, so the probe could not live in
`PlayerService` if the chokepoint had to call it.

### D3 — One chokepoint: `bot/guild_keys.py`

All seven key-consumption sites route through a single module. It exposes
exactly two entry points:

| Function | Sync? | Probes? | Used by |
|---|---|---|---|
| `verify_and_resolve(server_id, guild_id) -> KeyResolution` | async | **yes** | every ingestion path, before any write |
| `active_key(server_id, guild_id) -> str \| None` | sync | no | season discovery only |

`KeyResolution.status` is one of `ok`, `quarantined`, `dead`, `unreachable`,
`unverifiable`, `no_key`. On `ok` it carries the `GuildSnapshot`, so the caller
gets the verified roster for free and never re-fetches.

The module cannot live in `bot/guilds.py` — that would make the storage/wrapper
layer, which every cog and `PlayerService` import, depend on an HTTP client. It
cannot live in a cog: the seven sites span three cogs plus a service.

**Enforcement.** A wrapper is only a chokepoint if bypassing it is caught.
Per ADR-006 §I the project *defines* four import-linter contracts in
`pyproject.toml`; this feature adds a fifth (`bot.repository*` must not import
`bot.guild_keys`) and an architecture test at
`tests/acceptance/guild-key-integrity/test_architecture_chokepoint.py` that
runs those contracts together with an AST scan as part of the standard `pytest`
gate, and pins `import-linter` and `pytest-archon` into `requirements.txt`. The
AST scan forbids `bot/cogs/*` and `bot/services/*` from reading `api_key` off a
guild dict or `Guild` object by subscript, `.get()` or attribute. The only
sanctioned readers are `bot/guild_keys.py` and the repository adapters.

> Corrected 2026-08-01. This paragraph previously read "the project already
> runs import-linter + AST pre-commit hooks". That was **not true of this
> repository**: there is no `.pre-commit-config.yaml`, no installed hook, and
> neither tool was in `requirements.txt`. The four `lint-imports` contracts are
> real but ran only when invoked by hand. Raised as DEVOPS U3, resolved by
> DEVOPS D10, and independently re-found by the architect reviewer at the Final
> Wave Review Gate. **No decision in this ADR changes — only the description of
> the mechanism.** Note also that `import-linter` alone would not have caught
> the leak this rule exists to prevent: the cogs contract sets
> `allow_indirect_imports = true`, so a cog reaching `bot.db` *through*
> `bot.guild_keys` satisfies the contract while violating its intent. The AST
> scan is the part that closes that.

### D4 — Binding state lives in its own table, not on `guilds`

`guild_key_bindings`, 1:1 with `guilds`, `ondelete="CASCADE"` on the composite
FK. Reached by three new `ClusterRepository` methods (`load_guild_binding`,
`save_guild_binding`, `list_guild_bindings`) — the ADR-007 pattern.

The alternative (columns on `guilds`) was rejected on a specific mechanism.
`SqlAlchemyClusterRepository._upsert_one_guild` assigns five attributes by name
on an existing row, so extra columns *do* survive a cluster save — but
`JsonClusterRepository.save` rebuilds the dict wholesale from `Guild` fields and
would drop them, and the moment anyone adds the fields to the `Guild` dataclass,
`bot/guilds.py:save_guilds` (which constructs `Guild` from a five-key dict)
writes `None` over live binding state on every unrelated admin command such as
`/set_ping_channel`.

A separate table keeps binding state out of `Cluster`, `Guild`, and
`save_guilds` entirely, which makes that clobber **structurally impossible**
rather than guarded by a test someone must remember to keep. It also models a
real difference in lifecycle: guild config is admin-set and rare; binding state
is system-observed hourly.

The JSON adapter implements the three methods by returning "unbound" and
no-oping the write — the same acceptable rollback degradation as
`get_replay_thread` returning `None` for thread IDs (ADR-006 D9).

### D5 — Quarantine blocks roster **and** hits, and is exited only by a human

On `guildId` mismatch: `key_status = 'quarantined'`, `quarantine_reason` records
both identities, `quarantined_at` stamped. `process_api_response` is not called
and `validate_if_stale` / `refresh_guild` are not called. Zero rows written.

Blocking only hits would leave `refresh_guild` free to invert the roster, which
was 60 of the 67 corrupted `players` rows in the incident.

There is no automatic recovery. `/update_guild_key` is the only exit, which is
why it must ship **before** enforcement (feature-delta D3); enforcing first
would make the first quarantine unrecoverable without SSH.

Alerts are rate-limited to once per 24 h per guild via `last_alerted_at` on the
binding row — an hourly loop would otherwise alert hourly forever.

### D6 — Transport failure is not a mismatch

| Probe outcome | Classification | Action |
|---|---|---|
| 200, `guildId` differs | mismatch | quarantine |
| 401 / 403 | dead key | report; **no** quarantine (a dead key returns no data to contaminate) |
| timeout / DNS / connection error / 5xx | unreachable | no state change, retry next cycle |
| 200, no `guildId` | unverifiable | no state change, loud alert (D1) |

Collapsing `unreachable` into `mismatch` would quarantine every guild in the
cluster during a Tacticus outage. `_DEAD_KEY_STATUSES = (401, 403)` from
`bot/cogs/registration_cog.py` is the existing precedent for the 401/403 split
and is reused verbatim.

### D7 — Season discovery must survive a quarantined guild

`auto_update` currently derives the season from `next(iter(guilds.values()))`
and skips the **whole server** when that fails. Quarantining a guild that
happens to be first in the dict would therefore halt every guild in the cluster
— strictly worse than the bug being fixed.

Season discovery iterates guilds, skips any without an `active_key`, and takes
the first that answers. When none remain the server is skipped with an explicit
"all guilds quarantined / no usable key" reason, never a silent `continue`.

### D8 — Trust-on-first-use for existing guilds, announced once

Guild rows predating this feature have no binding and no historical record to
reconstruct one from. The first successful probe adopts the observed identity
and stamps `identity_bound_at`. Every first-bind is announced once in the update
channel naming the guild and the adopted identity, so adoption is never silent.

**Precondition (operator-asserted, 2026-07-31):** no guild's data has drifted
since or during the DISCUSS wave. TOFU is therefore expected to adopt correct
bindings. This is asserted from operator observation, not from a completed
multi-guild sweep — the first-bind announcements are the verification step, and
the operator is expected to read them.

### D9 — `/update_guild_key` uses a `force` parameter, not a stateful confirmation

Admin tier, matching `/register_guild`. The submitted key is probed **before**
being stored; an unverifiable key is never installed. On identity mismatch the
write is refused and both guilds are named; `force:true` installs and re-binds.

A `discord.ui.View` confirmation button was rejected: it requires holding the
submitted plaintext key in process memory attached to the View until click or
timeout. The `force` parameter keeps the secret's lifetime bounded by the
request, at the cost of the key being typed twice in the mismatch case.

## Consequences

- **ADR-003's allow-list is amended.** Row #2 (`GET /api/v1/guild`) changes
  caller from `PlayerService._fetch_roster` to
  `bot/services/tacticus/guild_client.fetch_guild_snapshot`, and its purpose
  broadens from "roster" to "roster + guild identity". No new endpoint is added
  and no new external system is introduced; the call count does not increase.
- **`PlayerService` stops making HTTP calls for rosters.** Its public methods
  `refresh_guild` and `validate_if_stale` change signature from `api_key: str`
  to `snapshot: GuildSnapshot`. `admin_cog.register_guild` is the one external
  caller and is updated in the same slice.
- **The ABC grows three methods**, so both adapters and the parametrised
  contract suite at `tests/acceptance/sqlite-backend/test_repository_contract.py`
  must cover them.
- **A quarantined guild produces no data at all** until a human acts. This is
  intended: silence with an alert is preferable to plausible-looking wrong data.
- **`guildId` remains undocumented.** If Tacticus removes it the feature
  degrades to `unverifiable` — loudly, and without blocking. This is the
  residual risk and it is accepted knowingly.

## Alternatives considered

- **Bind on `guildTag`** (the field originally specified). Rejected once
  `guildId` was confirmed present in the live response: a UUID is stable across
  retags and structurally cannot collide. Tag remains stored for display.
- **Bind on both, requiring both to match.** Rejected as unnecessary complexity
  once `guildId` was confirmed tracked; it also introduced a partial-mismatch
  case with no clean answer, and would quarantine a guild for legitimately
  retagging.
- **Alert without blocking (permanent).** Rejected — it does not stop
  contamination, which is the entire point. Retained only as the *Slice 01*
  intermediate state, deliberately, so the binding can be validated in
  production before it is allowed to block.
- **Quarantine on any probe failure including transport errors.** Rejected: a
  Tacticus outage would quarantine the whole cluster (D6).
- **A guard at each of the seven call sites.** Rejected: six of seven is a
  silent contamination path, and nothing prevents an eighth site being added.
- **Columns on `guilds`.** Rejected on the clobber mechanism in D4.
