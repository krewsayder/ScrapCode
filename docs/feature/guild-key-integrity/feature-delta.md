# Feature Delta — `guild-key-integrity`

> Single narrative file per the nWave lean-wave-documentation contract.
> Density: `lean` + `ask-intelligent` (DISCUSS hard default, Decision 4
> 2026-04-28). Tier-1 `[REF]` sections only; Tier-2 expansions are listed
> in the wave-end menu and rendered on request.

---

## Wave: DISCUSS / [REF] Incident Origin

This feature exists because of a specific production incident, fully
diagnosed on 2026-07-31. It is recorded here because every decision below
traces to it.

The guild master of 【UNDV】Word Bearers moved to 【UNDV】Dark Mechanicum
around **2026-07-28**. His Tacticus API key — the key registered as
`word_bearers`' guild key — moved with him and retained guild-scope
permissions in the *new* guild. From that moment `GET /api/v1/guild` and
`GET /api/v1/guildRaid/{season}` returned **Dark Mechanicum** data under
the `word_bearers` identity.

`PlayerService._fetch_roster` ([player_service.py:117](../../../bot/services/chronicl3r/player_service.py#L117))
performs no guild-identity check — it takes whatever roster comes back.
`refresh_guild` then writes those IDs as `is_former: False` and flips
everyone absent to `is_former: True`. With `STALE_AFTER_HOURS = 1`, this
inverted the roster every hour for three days.

Measured damage (audit run 2026-07-31, after the key was corrected):

| Signal | Value |
|---|---|
| `players` rows for `word_bearers` | 67 = 30 Dark Mechanicum (active) + 30 real Word Bearers wrongly `is_former` + 7 genuine ex-members |
| `battle_hits` season 106 off-roster | **30 of 30** |
| `bomb_hits` season 106 off-roster | **20 of 20** |
| `battle_hits` seasons 100–105 off-roster | 4–7 (normal churn — uncontaminated) |
| Time to detect | **~3 days**, and only because a human noticed names that looked wrong |

Both guilds carry the 【UNDV】 alliance prefix, which is very likely why
the wrong names in the leaderboard did not read as obviously wrong.

Remediation already performed manually: key replaced, season 106 hits
deleted (79 battle + 22 bomb rows), bot restarted, data re-ingested.

---

## Wave: DISCUSS / [REF] Persona ID

**`cluster-admin`** (primary) — the single operator-developer who owns the
bot, holds the guild API keys, and is the only person with VM access.
Registers guilds, replaces keys, and is the one who currently has to SSH
in when anything about a key changes.

**`guild-officer`** (secondary) — holds the officer tier, reads and posts
leaderboards, runs `/update_leaderboard` and `/registration validate_keys`.
Consumes the data's correctness but cannot fix a key.

---

## Wave: DISCUSS / [REF] JTBD One-Liners

- **`trust-guild-data-provenance`** — When my guild's leaderboard updates,
  I want certainty the numbers came from *my* guild and nobody else's, so
  I can make roster and performance calls without second-guessing them.
- **`swap-a-guild-key-without-a-console`** — When a guild's API key stops
  being valid for that guild, I want to install the replacement from
  Discord in under a minute, so I restore hourly updates without SSH, a
  hand-written Python script, and a database backup.

Opportunity scores (ODI: `importance + max(importance − satisfaction, 0)`,
1–10 scale, scored by the operator against the incident):

| Job | Importance | Satisfaction | Opportunity | Rank |
|---|---|---|---|---|
| `trust-guild-data-provenance` | 9 | 1 | **17** | 1 |
| `swap-a-guild-key-without-a-console` | 8 | 2 | **14** | 2 |

Both clear the "worth building" bar (>10). Job 1 ranks higher because its
satisfaction is near-zero: there is currently **no** signal of any kind
when a key changes guilds.

Full dimensions and four-forces analysis live in
`docs/product/jobs.yaml`; the Tier-2 `jtbd-narrative` expansion renders
them inline on request.

---

## Wave: DISCUSS / [REF] Locked Decisions

### D1 — Bind on `guildId`; tag and name are display only

**Verdict: LOCKED — `guildId` is the sole discriminator (operator
selection, 2026-07-31).**

The Tacticus documentation does not list a guild ID, but the **live
response returns one**: the audit script run against production on
2026-07-31 printed `guildId d71d583f-c970-4493-936f-178c21ab844c` for
Dark Mechanicum and `guildId b64bdba4-36ac-4229-bd29-4b7b6ce7f44f` for
Word Bearers. It is tracked, so it is what we bind on.

| Field | Role | Compared? | Mutable |
|---|---|---|---|
| `guildId` | **the** identity binding | **yes — the only field compared** | no (UUID) |
| `guildTag` | display in alerts and `/view_config` | no | no (per operator) |
| `name` | display only | no | **yes** |

`guildId` is a UUID: stable across renames and retags, and structurally
incapable of collision. In the incident the two UUIDs differed, so this
catches it exactly.

`guildTag` and `name` are still **stored** — an alert saying
"resolves to 【UNDV】Dark Mechanicum [PXGQW]" is far more actionable
than one quoting two UUIDs — but neither participates in the comparison.
Keeping them out of the comparison means a legitimate retag or rename
never trips the lock.

**If `guildId` is absent from a probe response** (Tacticus removes the
undocumented field), classify the probe as **`unverifiable`**: do not
quarantine, do not block ingestion, but raise a loud, persistent alert
stating that identity verification is offline.

Deliberately **no silent fallback to tag comparison**. A quiet downgrade
to a weaker check is the same failure shape as the original incident —
the system appears protected while it is not. If the field disappears,
the operator finds out and decides; the code does not decide for them.
Blocking instead would be worse: the field vanishing affects every guild
at once, so quarantining on it would halt the entire cluster over a
vendor change. Adding tag comparison as an explicit fallback later is a
small change if it is ever wanted.

### D2 — Quarantine is a hard stop, and it blocks **both** roster and hits

**Verdict: LOCKED (operator selection).**

On identity mismatch the guild's key is marked quarantined in the
database. Roster sync (`refresh_guild` / `validate_if_stale`) **and** hit
ingestion (`process_api_response`) are both skipped for that guild until
an officer installs a new key. Zero rows written.

Both halves are required. Blocking only hits would still let
`refresh_guild` invert the roster — which was 60 of the 67 corrupted
`players` rows in this incident.

### D3 — Quarantine must not ship before the recovery path

**Verdict: LOCKED (dependency, discovered during slicing).**

`/update_guild_key` is the **only** way out of quarantine. If D2 lands
first, the first quarantine event sends the operator straight back to SSH
— the exact pain this feature exists to remove, made *worse* because
updates are now also stopped. Slice order is therefore
`01 detect → 02 command → 03 enforce`, not the intuitive
`detect → enforce → command`. See the story map.

### D4 — Transport failure is not a mismatch

**Verdict: LOCKED.**

Three outcomes must be distinguished and must never collapse into one
another:

| Probe outcome | Classification | Action |
|---|---|---|
| HTTP 200, identity differs | **mismatch** | quarantine |
| HTTP 401/403 | **dead key** | report, do not quarantine (nothing to contaminate — the key returns no data) |
| timeout / DNS / connection error, or 5xx | **unreachable** | no state change, retry next cycle |

Collapsing "unreachable" into "mismatch" would quarantine every guild in
the cluster during a Tacticus outage. `_DEAD_KEY_STATUSES = (401, 403)`
([registration_cog.py:24](../../../bot/cogs/registration_cog.py#L24)) is
the existing precedent for the 401/403 split and is reused.

### D5 — Trust-on-first-use for existing guilds, reported once

**Verdict: LOCKED.**

Guild rows that predate this feature have no stored identity. There is no
historical record to reconstruct one from, so the first successful probe
adopts whatever identity it sees and records `identity_bound_at`. To stop
that being silent, every first-bind is announced once in the update
channel naming the guild and the identity adopted, so the operator can
eyeball it. Adoption happens exactly once per guild; every later probe
compares.

### D6 — The guard belongs at one chokepoint, not at each call site

**Verdict: LOCKED (constraint on DESIGN).**

Seven distinct call sites read a guild's `api_key` and fire it at Tacticus
today:

| # | Call site | Line |
|---|---|---|
| 1 | `tasks_cog.auto_update` — season detection | [tasks_cog.py:174](../../../bot/cogs/tasks_cog.py#L174) |
| 2 | `tasks_cog.auto_update` — per-guild raid fetch | [tasks_cog.py:197](../../../bot/cogs/tasks_cog.py#L197) |
| 3 | `update_cog.update_leaderboard` | [update_cog.py:48](../../../bot/cogs/update_cog.py#L48) |
| 4 | `update_cog.update_all` | [update_cog.py:104](../../../bot/cogs/update_cog.py#L104) |
| 5 | `admin_cog.set_live_leaderboard` | [admin_cog.py:311](../../../bot/cogs/admin_cog.py#L311) |
| 6 | `admin_cog.set_live_cluster_leaderboard` | [admin_cog.py:387](../../../bot/cogs/admin_cog.py#L387) |
| 7 | `admin_cog.register_guild` → `refresh_guild` | [admin_cog.py:96](../../../bot/cogs/admin_cog.py#L96) |

A guard added to only some of these leaves the rest contaminating.
DESIGN must introduce a single accessor that resolves a guild's usable
key — returning the key only if the guild is not quarantined — and route
all seven through it. Enumerating them here is the DISCUSS contribution;
choosing the seam shape is DESIGN's.

---

## Wave: DISCUSS / [REF] Pre-requisites

- **SQLite backend live.** Confirmed on the production VM 2026-07-31 —
  all three file handles on `/opt/discord-bot/data/scrapcode.db`. The
  binding columns are a schema change, so this feature assumes SQLAlchemy
  + Alembic are the persistence path (they are, post-cutover).
- **`api_key` is Fernet ciphertext at rest** with a deterministic
  `api_key_hmac` (HKDF-SHA256) carrying a UNIQUE constraint
  ([db/models.py:82-83](../../../bot/db/models.py#L82-L83)). Any key write
  must produce both values in the same transaction. Raw-SQL key edits are
  forbidden.
- **`bot/models.py:Guild` and `bot/guilds.py:save_guilds` must both be
  extended.** `save_guilds` reconstructs each `Guild` from a five-key dict
  ([guilds.py:83-97](../../../bot/guilds.py#L83-L97)). Any field not
  threaded through *both* `load_guilds` and `save_guilds` is silently
  destroyed by the next unrelated admin command — `/set_ping_channel`
  alone would wipe the binding. This is a hard trap and is called out as
  an AC, not left to DESIGN to rediscover.
- **Season detection is a cluster-wide single point of failure.**
  `auto_update` derives the season from `next(iter(guilds.values()))`
  ([tasks_cog.py:173](../../../bot/cogs/tasks_cog.py#L173)); if that one
  guild's key fails, `season is None` and the **entire server** is skipped
  ([tasks_cog.py:187-189](../../../bot/cogs/tasks_cog.py#L187-L189)).
  Quarantining a guild that happens to be first in the dict would
  therefore halt every guild. Fixed as part of Slice 03.

---

## Wave: DISCUSS / [REF] Driving Ports

| Surface | Type | Tier | Status |
|---|---|---|---|
| `/update_guild_key` | Discord slash command | `admin` | **new** (Slice 02) |
| `/view_config config:guilds` | Discord slash command | `officer` | extended (Slice 01) |
| `auto_update` hourly loop | background task | — | extended (Slices 01, 03) |
| Update channel post | Discord message | — | extended (Slices 01, 03) |
| Guild ping channel post | Discord message | — | extended (Slice 03) |

`/update_guild_key` is `admin`, matching `/register_guild`
([admin_cog.py:45](../../../bot/cogs/admin_cog.py#L45)). Officer tier is
deliberately not sufficient: the key grants read access to a guild's full
roster and raid history.

---

## Wave: DISCUSS / [REF] WS Strategy

**Strategy C — no walking skeleton.** Brownfield feature against a live
production database with an established end-to-end path
(Discord → cog → `ClusterRepository` → SQLite). Every seam this feature
needs already exists and is exercised by the `sqlite-backend` acceptance
suite. A skeleton would re-prove plumbing that Slice 01 uses on day one.

Mandate 5 note: the walking-skeleton obligation is discharged by
`docs/feature/sqlite-backend/distill/walking-skeleton.md`, which
established the same path.

---

## Wave: DISCUSS / [REF] Scope Assessment

**PASS — right-sized.** Elephant Carpaccio early gate, evaluated before
journey investment:

| Oversized signal | Threshold | Actual | Fires? |
|---|---|---|---|
| User stories | >10 | 6 | no |
| Bounded contexts / modules | >3 | 3 (`cogs`, `services`, `db`) | no |
| WS integration points | >5 | n/a (no WS) | no |
| Estimated effort | >2 weeks | ~3 days | no |
| Independent shippable outcomes | multiple | 2, and they are **sequenced**, not independent (D3) | no |

Zero signals fire; two are required to trigger a split. Proceeding
undivided, decomposed into three carpaccio slices.

---

## Wave: DISCUSS / [REF] Journey — Happy Path

Journey `guild-key-integrity`, persona `cluster-admin`. Schema at
`docs/product/journeys/guild-key-integrity.yaml`.

| # | Step | Output the user sees |
|---|---|---|
| 1 | Admin runs `/update_guild_key guild_id:word_bearers api_key:<new>` | ephemeral: "Probing key…" |
| 2 | Bot probes `GET /api/v1/guild` with the **submitted** key before storing | — |
| 3 | Bot reports the resolved identity for confirmation | ephemeral: `Key resolves to 【UNDV】Word Bearers [EUVQZ]. This matches the stored binding. Install? [Confirm]` |
| 4 | Admin confirms | ephemeral: `✅ Key updated for Word Bearers. Binding: EUVQZ · b64bdba4. Quarantine cleared.` |
| 5 | Next hourly `auto_update` probes identity before any write | update channel: `🔄 Auto-update complete — Season 106` (unchanged) |
| 6 | Admin verifies at leisure | `/view_config config:guilds` → `Bound to: 【UNDV】Word Bearers [EUVQZ] · verified 2026-07-31 04:00` |

Emotional arc (lightweight — one line per step, monotonically rising):
`wary → wary → informed → in control → unremarkable → confident`.

The design intent is that step 3 is where doubt is resolved: the admin
sees the guild the key *actually* belongs to **before** anything is
written, which is precisely the information that did not exist during the
incident.

---

## Wave: DISCUSS / [REF] Journey — Error Paths

| Failure | Detection | Recovery | Slice |
|---|---|---|---|
| Submitted key resolves to a different guild | probe identity ≠ stored binding | command refuses the write, names both guilds, offers `force:true` for a deliberate re-bind | 02 |
| Stored key drifts to another guild (the incident) | hourly probe identity ≠ stored binding | quarantine, alert both channels, all writes stopped | 03 |
| Key is dead (401/403) | probe status | reported as dead key, **not** quarantined | 01 |
| Tacticus unreachable / 5xx | transport exception or 5xx | no state change, retry next hour, no alert on first failure | 01 |
| `guildId` absent from response | field missing | classify `unverifiable` — no block, loud persistent alert that verification is offline, **no** silent fallback to tag | 01 |
| `guildTag` or `name` absent | field missing | non-fatal — bind on `guildId`, render the display field as `—` | 01 |
| Guild has no stored binding (legacy row) | `guild_uuid` null | trust-on-first-use, announced once (D5) | 01 |
| Quarantined guild is first in the dict | season detection uses its key | season detection skips quarantined guilds; falls through to the next usable key | 03 |
| Every guild in a server is quarantined | no usable key for season detection | server skipped with an explicit reason, not a silent `continue` | 03 |

---

## Wave: DISCUSS / [REF] Shared Artifacts

| Artifact | Single source of truth | Consumed by |
|---|---|---|
| `${guild_uuid}` | `guilds.tacticus_guild_id` column | **the** identity comparison, `/view_config` |
| `${guild_tag}` | `guilds.tacticus_guild_tag` column | alert text, `/view_config` — display only, never compared |
| `${guild_name}` | `guilds.tacticus_guild_name` column | alert text, `/view_config` — display only, never compared |
| `${key_status}` | `guilds.key_status` column (`active` \| `quarantined`) | the D6 chokepoint accessor |
| `${quarantine_reason}` | `guilds.quarantine_reason` column | alert text, `/view_config` |
| `${identity_bound_at}` | `guilds.identity_bound_at` column | `/view_config`, TOFU gate |
| `${probe_result}` | the single identity-probe function | every consumer; never re-probed per call site |
| `${api_key}` | `guilds.api_key` (Fernet) + `guilds.api_key_hmac` | written only via `save_guilds`, never raw SQL |

Column names are a DISCUSS **recommendation**; DESIGN owns the final
schema. The constraint that matters is one source per artifact.

---

## Wave: DISCUSS / [REF] User Stories

All stories trace to a `job_id` in `docs/product/jobs.yaml`. Every
non-`@infrastructure` story carries an Elevator Pitch.

### US-001 — Bind a guild's Tacticus identity and show it

`job_id: trust-guild-data-provenance` · Slice 01

As a **cluster-admin**, I want each registered guild to carry the Tacticus
identity its key resolves to, so that "which guild does this key actually
belong to" is a stored fact rather than an investigation.

**Elevator Pitch**
Before: There is no record of which Tacticus guild a stored key belongs to; answering it takes an SSH session and a hand-written decrypt script.
After: run `/view_config config:guilds` → sees `Bound to: 【UNDV】Word Bearers [EUVQZ] · b64bdba4 · verified 2026-07-31 04:00` under each guild.
Decision enabled: The admin can confirm at a glance that every key still points at the guild it was registered for, and spot a drifted key without leaving Discord.

**Acceptance criteria**
- AC-001.1 — Given a guild whose key resolves to uuid `b64bdba4…` with tag `EUVQZ`, when the identity probe runs, then `tacticus_guild_id`, `tacticus_guild_tag`, `tacticus_guild_name` and `identity_bound_at` are persisted on that guild row.
- AC-001.2 — Given a guild with a stored binding, when `/view_config config:guilds` is run, then its field shows the bound tag, the uuid's first 8 characters, and the `identity_bound_at` date.
- AC-001.3 — Given a guild row with no stored binding, when the first successful probe completes, then the identity is adopted, `identity_bound_at` is set, and exactly one message naming the guild and adopted identity is posted to the update channel (D5).
- AC-001.4 — Given a guild that already has a binding, when a later probe succeeds, then `identity_bound_at` is refreshed and **no** first-bind announcement is posted.
- AC-001.5 — Given a probe response with no `guildId` field, when the identity is compared, then the probe is classified `unverifiable`, ingestion is **not** blocked, and an alert stating that identity verification is offline is raised (D1). No fallback to `guildTag` comparison occurs.
- AC-001.6 — Given a probe response with no `guildTag` or no `name`, when binding is attempted, then binding still succeeds on `guildId` alone and the missing display field renders as `—`. *(Display fields are never load-bearing.)*
- AC-001.7 — Given `/set_ping_channel` is run against a guild with a stored binding, when `save_guilds` writes, then the binding fields survive unchanged. *(Guards the [guilds.py:83-97](../../../bot/guilds.py#L83-L97) round-trip trap.)*
- AC-001.8 — Given a probe returning 401 or 403, when classified, then the outcome is `dead key`, no binding is written, and no quarantine occurs (D4).
- AC-001.9 — Given a probe that raises a transport exception or returns 5xx, when classified, then the outcome is `unreachable`, no column is written, and the stored binding is left intact (D4).

---

### US-002 — Report an identity mismatch without blocking

`job_id: trust-guild-data-provenance` · Slice 01

As a **cluster-admin**, I want the hourly update to tell me when a guild's
key has started resolving to a different Tacticus guild, so that I learn
about it in an hour instead of three days.

**Elevator Pitch**
Before: A key that silently moved to another Tacticus guild is indistinguishable from a healthy one; the Jul 28 swap ran 3 days undetected.
After: run `/update_leaderboard guild_id:word_bearers season:106` → sees `⚠️ Identity mismatch — key is bound to 【UNDV】Word Bearers [EUVQZ] but now resolves to 【UNDV】Dark Mechanicum [PXGQW]. Data was still ingested. Run /update_guild_key to fix.`
Decision enabled: The admin knows immediately which guild the key drifted to and can decide whether to replace the key or re-bind deliberately.

**Acceptance criteria**
- AC-002.1 — Given a guild bound to `EUVQZ`/`b64bdba4…` whose key now resolves to `PXGQW`/`d71d583f…`, when an ingestion path runs, then a mismatch is reported naming **both** the bound and the resolved identity by tag and name.
- AC-002.2 — Given a mismatch is detected in Slice 01, when ingestion proceeds, then data is **still written** — this slice reports only. *(Enforcement is Slice 03; shipping the block before the recovery path violates D3.)*
- AC-002.3 — Given a mismatch, when the hourly `auto_update` completes, then the mismatch line appears in the update-channel summary alongside the per-guild results.
- AC-002.4 — Given a guild whose resolved identity matches its binding, when an ingestion path runs, then no mismatch message is produced anywhere.
- AC-002.5 — Given a guild whose `guildId` matches its binding but whose `guildTag` **or** `name` has changed, when compared, then this is **not** a mismatch and no alert is raised — a legitimate retag or rename must never trip the lock (D1).
- AC-002.7 — Given a mismatch is reported, when the message is rendered, then it names both guilds by `name` and `guildTag` and shows both `guildId` values truncated to 8 characters — the comparison is on uuid, but the human-readable fields are what make the alert actionable.
- AC-002.6 — Given the same mismatch persists across consecutive hourly cycles, when the second cycle runs, then the alert is still emitted (no suppression in this slice) and the repetition is recorded as a KPI-4 input.

---

### US-003 — Replace a guild's Tacticus API key from Discord

`job_id: swap-a-guild-key-without-a-console` · Slice 02

As a **cluster-admin**, I want to install a new Tacticus key for a guild
with one slash command, so that I never again have to stop the service,
back up the database, and run a hand-written script to change one field.

**Elevator Pitch**
Before: Replacing a guild key means SSH, `systemctl stop`, a database backup, a hand-written Python script against `save_guilds`, and a restart — or `/deregister_guild` + `/register_guild`, which CASCADE-deletes every player and hit row for that guild.
After: run `/update_guild_key guild_id:word_bearers api_key:<new>` → sees `✅ Key updated for Word Bearers. Resolves to 【UNDV】Word Bearers [EUVQZ] · b64bdba4. Binding confirmed.`
Decision enabled: The admin restores hourly updates in under a minute and confirms from the response that the new key points at the right guild before it is trusted.

**Acceptance criteria**
- AC-003.1 — Given a valid new key for an existing guild, when `/update_guild_key` is run by an admin, then `api_key` and `api_key_hmac` are both updated in one transaction and the command replies with the resolved identity.
- AC-003.2 — Given the command completes, when the guild's `players`, `battle_hits` and `bomb_hits` rows are counted before and after, then the counts are **identical** — no CASCADE, no data loss. *(This is the property that makes the command safe where `/deregister_guild` is not.)*
- AC-003.3 — Given a submitted key that resolves to a **different** guild than the stored binding, when the command runs without `force`, then the write is refused, both identities are named, and the stored key is unchanged.
- AC-003.4 — Given the same mismatch, when the command is re-run with `force:true`, then the key is installed **and** the binding is updated to the new identity, with the re-bind recorded.
- AC-003.5 — Given a submitted key that returns 401/403, when the command runs, then the write is refused with a dead-key message and the stored key is unchanged.
- AC-003.6 — Given Tacticus is unreachable, when the command runs, then the write is refused with an "could not verify" message and the stored key is unchanged. *(Never install an unverifiable key.)*
- AC-003.7 — Given the command is invoked by a user holding only the officer tier, when the permission check runs, then it is refused.
- AC-003.8 — Given any outcome, when the response is sent, then it is ephemeral and **no** API key value appears in the response, in `discord.log`, or in any print statement.
- AC-003.9 — Given a guild in quarantine, when a valid matching key is installed, then `key_status` returns to `active` and `quarantine_reason` is cleared. *(Slice 03 makes this reachable; the clearing logic ships here so quarantine is never a trap.)*
- AC-003.10 — Given a `guild_id` that is not registered, when the command runs, then it fails with the list of registered guild IDs and writes nothing.

---

### US-004 — Enforce quarantine on identity mismatch

`job_id: trust-guild-data-provenance` · Slice 03

As a **cluster-admin**, I want a guild whose key has drifted to another
Tacticus guild to stop ingesting entirely, so that not one row of another
guild's data reaches my leaderboards.

**Elevator Pitch**
Before: A drifted key keeps writing another guild's roster and hits every hour — 30 of 30 battle rows and 20 of 20 bomb rows in season 106 came from the wrong guild.
After: run `/update_leaderboard guild_id:word_bearers season:106` → sees `⛔ word_bearers is quarantined — its key resolves to 【UNDV】Dark Mechanicum [PXGQW], not the bound 【UNDV】Word Bearers [EUVQZ]. No data was written. Run /update_guild_key to restore.`
Decision enabled: The admin knows with certainty that nothing was contaminated, and that the only action needed is installing the right key.

**Acceptance criteria**
- AC-004.1 — Given a guild whose probe identity differs from its binding, when any ingestion path runs, then `key_status` is set to `quarantined`, `quarantine_reason` records both identities, and `quarantined_at` is stamped.
- AC-004.2 — Given a quarantined guild, when `process_api_response` would be called, then it is **not** called — zero `battle_hits` and zero `bomb_hits` rows are written for that guild.
- AC-004.3 — Given a quarantined guild, when `validate_if_stale` / `refresh_guild` would run, then they are **not** called — zero `players` rows are inserted, updated, or flipped to `is_former` (D2).
- AC-004.4 — Given a guild enters quarantine, when the alert is emitted, then it goes to both the update channel and that guild's `notification_channel_id` when one is set.
- AC-004.5 — Given a guild is already quarantined, when subsequent hourly cycles run, then the alert is emitted at most once per 24 hours per guild while the state persists. *(Prevents the hourly alert-fatigue failure mode.)*
- AC-004.6 — Given all seven key-consumption call sites in D6, when each is exercised against a quarantined guild, then every one refuses. *(Parametrised across the seven sites — a guard on some but not all is the failure this AC exists to catch.)*
- AC-004.7 — Given a quarantined guild that is **first** in the guild dict, when `auto_update` derives the season, then it skips that guild's key and uses the next usable one — the server is **not** skipped. *(Fixes the [tasks_cog.py:173](../../../bot/cogs/tasks_cog.py#L173) SPOF.)*
- AC-004.8 — Given a server where every guild is quarantined, when `auto_update` runs, then the server is skipped with an explicit "all guilds quarantined" reason, not a silent `continue`.
- AC-004.9 — Given a quarantined guild and a healthy guild in the same server, when `auto_update` runs, then the healthy guild updates normally and its results appear in the summary.
- AC-004.10 — Given a probe classified as `unreachable`, when a guild is currently `active`, then it stays `active` — a Tacticus outage never quarantines anything (D4).

---

### US-005 — Surface quarantine state in `/view_config`

`job_id: swap-a-guild-key-without-a-console` · Slice 03

As a **guild-officer**, I want to see that a guild is quarantined and why,
so that I understand why its leaderboard has stopped moving without
having to ask the admin.

**Elevator Pitch**
Before: A guild that has stopped updating looks identical to one that is updating fine; the officer's only signal is a leaderboard that quietly stops changing.
After: run `/view_config config:guilds` → sees `**API key:** ⛔ Quarantined — resolves to 【UNDV】Dark Mechanicum [PXGQW], expected [EUVQZ] (since 2026-07-31)`.
Decision enabled: The officer can tell the difference between "nothing happened this hour" and "this guild is blocked and needs an admin", and escalate accordingly.

**Acceptance criteria**
- AC-005.1 — Given a quarantined guild, when `/view_config config:guilds` is run, then its API-key field shows the quarantine state, the resolved and expected tags, and the quarantine date.
- AC-005.2 — Given an active guild with a binding, when the same command runs, then its API-key field shows `✅` plus the bound tag and the last verification date.
- AC-005.3 — Given a guild with no API key at all, when the same command runs, then the existing `❌ Missing` rendering is unchanged. *(No regression to the current output for the unbound case.)*
- AC-005.4 — Given any state, when the embed is rendered, then no API key value or uuid beyond its first 8 characters appears.

---

### US-006 — `@infrastructure` — Schema migration for identity and quarantine columns

`job_id: trust-guild-data-provenance` · Slice 01 · **precursor commit, not a shipped slice**

Adds `tacticus_guild_id` (the binding), `tacticus_guild_tag` and
`tacticus_guild_name` (display only), `identity_bound_at`, `key_status`,
`quarantine_reason`, `quarantined_at` to `guilds`, threads them through
`bot/models.py:Guild`, `load_guilds`, and `save_guilds`, and provides the
Alembic revision.

No Elevator Pitch: this story has no user-visible output. Per the slice
composition gate it **cannot** be released as a slice of its own — it
lands as a precursor commit inside Slice 01, which also contains US-001
and US-002 (both user-visible).

**Acceptance criteria**
- AC-006.1 — Given the Alembic revision is applied to a copy of the production database, when it completes, then every existing guild row gains the new columns with `key_status = 'active'` and null identity fields, and no existing row is otherwise altered.
- AC-006.2 — Given the revision is downgraded, when it completes, then the schema matches the prior revision exactly.
- AC-006.3 — Given a `Guild` is loaded and re-saved with no modification, when the row is compared before and after, then every field including the new ones is byte-identical. *(The [guilds.py:83-97](../../../bot/guilds.py#L83-L97) round-trip trap.)*

---

## Wave: DISCUSS / [REF] Story Map

### Backbone

`Register a guild` → `Verify its key belongs to it` → `Ingest raid data` → `Notice a key has drifted` → `Replace the key` → `Resume ingestion`

### Slices

| Slice | Stories | Learning hypothesis | Est. |
|---|---|---|---|
| **01 — Bind and report identity** | US-006 (precursor), US-001, US-002 | *Disproves "`/api/v1/guild` returns a stable identity we can bind on" if the response lacks a usable discriminator or it drifts between calls.* | ~1 day |
| **02 — `/update_guild_key`** | US-003 | *Disproves "a guild key can be replaced through the existing `save_guilds` seam without touching dependent rows" if the write turns out to require more than the guild row.* | ~0.5 day |
| **03 — Enforce quarantine** | US-004, US-005 | *Disproves "one chokepoint can gate all seven key-consumption sites" if any site bypasses it, and disproves "quarantining one guild is survivable" if the season SPOF cannot be cleanly fixed.* | ~1 day |

### Taste tests

| Test | Verdict |
|---|---|
| Any slice ships 4+ new components? | **Pass.** 01 = migration + probe + report (3). 02 = one command (1). 03 = chokepoint + season fix + embed (3). The original single-slice draft had 5 and was split. |
| Every slice depends on a new abstraction? | **Pass.** Only Slice 03 needs the D6 chokepoint; 01 and 02 call the probe directly. The abstraction ships in the slice that needs it. |
| Does any slice disprove a pre-commitment? | **Pass.** Slice 01 directly tests the D1 binding decision against production before Slice 03 makes it blocking — this is the slice that resolves the documented-vs-observed `guildId` question empirically. |
| Synthetic data only? | **Pass.** Slice 01's acceptance runs against the live production key for `word_bearers` and asserts the resolved identity is `EUVQZ`/`b64bdba4…`. Slice 03 re-uses the known-bad Dark Mechanicum key as the negative case. |
| Two slices identical except for scale? | **Pass.** All three differ in kind. |

### Prioritisation

1. **Slice 01 first — learning leverage.** It carries the feature's only
   real uncertainty (D1). If `/api/v1/guild` turns out not to expose a
   usable discriminator, the entire feature changes shape, and finding
   that out costs one day here versus three days after quarantine is
   built on it. Ships non-blocking, so a wrong guess writes nothing bad.
2. **Slice 02 second — dependency, per D3.** `/update_guild_key` is the
   only exit from quarantine. It **must** exist before enforcement, or
   the first quarantine is unrecoverable without SSH. Independently
   valuable on day one regardless of the rest: it retires the manual
   key-replacement procedure immediately.
3. **Slice 03 last — enforcement.** Safe only once 01 has proved the
   binding is reliable and 02 has provided the recovery path.

Dogfood moment per slice: 01 — the operator reads the first-bind
announcement for all registered guilds within the hour. 02 — the operator
re-installs the current `word_bearers` key through the command (a no-op
that proves the path). 03 — the operator installs the known Dark
Mechanicum key against a scratch guild and watches it refuse.

---

## Wave: DISCUSS / [REF] Outcome KPIs

| # | KPI | Baseline | Target | Measurement |
|---|---|---|---|---|
| KPI-1 | Time to detect a wrong-guild key | **~72 h** (Jul 28 → Jul 31, human-noticed) | **≤1 h** | Delta between the first probe returning a mismatched identity and the alert message timestamp |
| KPI-2 | Contaminated rows written after a mismatch is detectable | **50** (30 battle + 20 bomb, season 106) | **0** | `COUNT(*)` of `battle_hits` + `bomb_hits` for a quarantined guild with `completed_on > quarantined_at` |
| KPI-3 | Wall-clock to replace a guild key | **~25 min**, 1 SSH session, 1 DB backup, 1 throwaway script | **≤60 s**, 0 SSH sessions | Operator-timed; corroborated by absence of an `sshd` session in the journal during the swap |
| KPI-4 | False-positive quarantines | n/a (feature does not exist) | **0 in 30 days** | Count of quarantine events the operator subsequently confirms were on a correct key |
| KPI-5 | Guilds unaffected by another guild's quarantine | **0%** — the season SPOF halts the whole server | **100%** | `auto_update` completes for every non-quarantined guild in a server containing ≥1 quarantined guild |
| KPI-6 | Key values leaked to logs or Discord | 1 known exposure (this incident's replacement key appeared in shell history and a temp file) | **0** | Grep `discord.log`, journal, and command responses for the stored plaintext during the acceptance run |

---

## Wave: DISCUSS / [REF] Definition of Ready

| # | DoR item | Status | Evidence |
|---|---|---|---|
| 1 | Business value articulated | ✅ | Incident Origin section; 50 contaminated rows, 60 corrupted roster rows, ~72 h undetected |
| 2 | Story format with job traceability | ✅ | 6 stories; 5 trace to a real `job_id`, US-006 is `@infrastructure` and rides as a precursor commit |
| 3 | Acceptance criteria testable | ✅ | 43 ACs, each with a concrete given/when/then and named values from the real incident |
| 4 | Dependencies identified | ✅ | Pre-requisites section; D3 slice-order dependency; D6 seven call sites enumerated |
| 5 | Sized appropriately | ✅ | 3 slices at ~1 / ~0.5 / ~1 day; all five carpaccio taste tests pass |
| 6 | Technical approach feasible | ✅ | Every seam exists and was exercised in production this week: `save_guilds` key write proven on the VM, `api_key_hmac` recomputation proven, `/api/v1/guild` identity fields observed live |
| 7 | Test approach defined | ✅ | Contract + parity suites at `tests/acceptance/sqlite-backend/` are the pattern; AC-004.6 is parametrised across all seven call sites; production key used as the positive case, known Dark Mechanicum key as the negative |
| 8 | Non-functional requirements stated | ✅ | KPI-6 (no key in logs), AC-003.8 (ephemeral, never printed), AC-004.5 (alert rate limit), D4 (outage must not quarantine) |
| 9 | Definition of Done agreed | ✅ | See below |

**Requirements completeness: 0.98** — 43 ACs across 6 stories; every
journey step and every error path in the error-path table maps to at
least one AC. The residual shortfall is that `guildId` remains
undocumented by Tacticus even though it is tracked and returned; AC-001.5
makes its disappearance a loud, non-blocking failure rather than a silent
one, which is the most that can be specified without vendor guarantees.

### Definition of Done

1. All 43 ACs pass as automated tests.
2. Alembic revision applies and downgrades cleanly against a copy of the production database.
3. All seven D6 call sites route through the chokepoint, verified by the parametrised AC-004.6.
4. `load_guilds` / `save_guilds` round-trip preserves every new field (AC-001.7, AC-006.3).
5. No API key value appears in `discord.log`, the journal, or any Discord response (KPI-6).
6. `/update_guild_key` leaves `players`, `battle_hits`, `bomb_hits` counts unchanged (AC-003.2).
7. Quarantining one guild does not stop any other guild (AC-004.7, AC-004.9, KPI-5).
8. The known Dark Mechanicum key is refused against `word_bearers` in an end-to-end run.
9. `docs/product/jobs.yaml`, `journeys/`, and `personas/` updated; ADR-003's direct-Tacticus call table extended with the identity probe.

---

## Wave: DISCUSS / [REF] Out of Scope

Explicit non-goals for this feature:

- **Contaminated-data remediation.** No purge command, no `is_former`
  cleanup, no re-ingestion tooling. Operator selection, 2026-07-31.
  Season 106 was already cleaned manually. The 30 lingering Dark
  Mechanicum `is_former` rows in `word_bearers` remain a back-end task.
- **Rotating the exposed key.** The current `word_bearers` key was
  exposed during the manual remediation (shell history, a temp file).
  Rotating it is an operational action, not a feature.
- **Per-player registration key identity.** This feature binds **guild**
  keys only. `player_registrations.api_key` is out of scope;
  `/registration validate_keys` already covers liveness there.
- **Multi-tenant replay partitioning**, and any other `sqlite-backend`
  deferral. Unchanged by this feature.
- **Moving the roster fetch to Chronicler.** ADR-003 flags
  `GET /api/v1/guild` as a possible future Chronicler migration. This
  feature adds an identity check to the existing direct call and does not
  move it.
- **Alerting outside Discord** (email, webhook, pager). Update channel and
  guild ping channel only.
- **Automatic key recovery.** A quarantined guild stays quarantined until
  a human installs a key. No retry-until-it-works behaviour.

---

## Wave: DISCUSS / [REF] Upstream Changes

None contradicted. This feature **extends** ADR-003's allow-listed
direct-Tacticus call table: `GET /api/v1/guild` gains a second documented
caller (the identity probe) alongside `PlayerService._fetch_roster`. The
ADR's row #2 note — "Currently direct; roster may move to Chronicler
later" — is unchanged and still stands.

`docs/product/jobs.yaml` previously held one placeholder job with
`jtbd_skipped: true`, bootstrapped by the `sqlite-backend` feature. That
entry is preserved verbatim; two validated jobs are appended alongside
it. This is the first feature in this repository to run a real JTBD pass.

---

## Wave: DISCUSS / [REF] Handoff

**To:** `nw-solution-architect` (DESIGN — full artifact set) and
`nw-platform-architect` (DEVOPS — KPIs only).

Open questions DESIGN owns:

1. **Chokepoint shape (D6).** A method on `ClusterRepository`, a wrapper
   service, or a `Guild` property? DISCUSS enumerates the seven call
   sites and requires one seam; the shape is DESIGN's call.
2. **Column layout.** The seven recommended columns are a suggestion. A
   separate `guild_key_bindings` table is acceptable if DESIGN prefers it.
3. **Probe caching.** `auto_update` would otherwise probe
   `/api/v1/guild` once per guild per hour on top of the existing roster
   fetch, which hits the same endpoint. DESIGN should decide whether the
   identity probe and `_fetch_roster` share one call — they hit the same
   URL and the roster response already contains the identity fields.
   Folding them is likely correct and costs one fewer request per guild
   per hour.
4. **Confirmation step.** Journey step 3 shows a confirm-before-install
   interaction. A Discord modal, a two-step button, or a `force:true`
   parameter are all viable; ACs are written against the `force:true`
   form as the simplest, and DESIGN may substitute.

**All four resolved in DESIGN below** — DDD-3 (chokepoint), DDD-4 (columns),
DDD-2 (probe fold), DDD-9 (confirmation).

---

# DESIGN

> Wave 3 of 6. Scope: **application / components** (Decision 0). Interaction
> mode: **propose** (Decision 1). Density: `lean` — Tier-1 `[REF]` only.
> Full decision text, alternatives and consequences:
> [ADR-008](../../product/architecture/adr-008-guild-key-identity-binding.md).

## Wave: DESIGN / [REF] Correction to DISCUSS

DISCUSS asserted (Pre-requisites) that any field not threaded through both
`load_guilds` and `save_guilds` "is silently destroyed by the next unrelated
admin command." That is true on the JSON path — `JsonClusterRepository.save`
rebuilds the dict wholesale from `Guild` fields
([repository.py:268-283](../../../bot/repository.py#L268-L283)) — but **false
for SQLite**: `_upsert_one_guild` assigns five attributes by name on an existing
row ([repository_sqlalchemy.py:171-176](../../../bot/repository_sqlalchemy.py#L171-L176)),
so unlisted columns survive untouched.

The real hazard is the inverse: the clobber is *created* by adding binding
fields to the `Guild` dataclass, because `save_guilds` constructs `Guild` from a
five-key dict and would then write `None` defaults over live binding state.

This reframes the storage decision and is why DDD-4 puts binding state in its
own table. AC-001.7 and AC-006.3 remain valid and still run — they now guard the
JSON rollback path and the "nobody wires it into `Guild` later" invariant.

## Wave: DESIGN / [REF] DDD List

| # | Decision | Verdict | One-line rationale |
|---|---|---|---|
| DDD-1 | `guildId` is the sole binding; tag/name display only | **LOCKED** | UUID is stable across retag/rename and cannot collide; a retag must never trip the lock |
| DDD-2 | Identity probe folded into the roster fetch | **LOCKED** | Same endpoint, same response — probe and roster cannot disagree, and the call count does not rise |
| DDD-3 | One chokepoint module `bot/guild_keys.py` | **LOCKED** | Seven call sites across three cogs plus a service; six-of-seven is a silent contamination path |
| DDD-4 | Binding state in its own `guild_key_bindings` table | **LOCKED** | Keeps it out of `Cluster`/`Guild`/`save_guilds`, making the clobber structurally impossible |
| DDD-5 | Quarantine blocks roster **and** hits | **LOCKED** | Roster inversion was 60 of 67 corrupted `players` rows; blocking hits alone leaves it running |
| DDD-6 | Transport failure ≠ mismatch | **LOCKED** | Collapsing them quarantines the whole cluster during a Tacticus outage |
| DDD-7 | Season discovery skips quarantined guilds | **LOCKED** | Otherwise quarantining one guild halts every guild — worse than the bug being fixed |
| DDD-8 | Trust-on-first-use, announced once | **LOCKED** | No historical record exists to reconstruct a binding from; the announcement is the verification step |
| DDD-9 | `force:true` parameter, not a stateful confirmation | **LOCKED** | A `View` button holds the plaintext key in process memory until click or timeout |
| DDD-10 | `unverifiable` degrades loudly, never silently to tag | **LOCKED** | A quiet downgrade to a weaker check is the same failure shape as the incident |
| DDD-11 | Paradigm: OOP, unchanged | **LOCKED** | Already pinned in `CLAUDE.md` and ADR-006 D13; routes DELIVER to `@nw-software-crafter` |

## Wave: DESIGN / [REF] Component Decomposition

| Component | Path | Change | Responsibility |
|---|---|---|---|
| Tacticus guild client | `bot/services/tacticus/guild_client.py` | **NEW** | `fetch_guild_snapshot(api_key) -> GuildSnapshot`; the only module issuing `GET /api/v1/guild`. Returns identity + members from one response. Classifies 401/403/5xx/transport per DDD-6. ~40 LOC. |
| Key policy chokepoint | `bot/guild_keys.py` | **NEW** | `verify_and_resolve` (async, probes, enforces, quarantines) and `active_key` (sync, storage-only). The single sanctioned reader of a guild `api_key`. ~60 LOC. |
| Binding ORM row | `bot/db/models.py` | MODIFIED | `GuildKeyBindingRow` — composite PK + FK to `guilds`, `ondelete="CASCADE"`. |
| Alembic revision | `bot/db/alembic/versions/` | **NEW** | Creates `guild_key_bindings`. Upgrade + downgrade. No data backfill (TOFU populates). |
| Repository port | `bot/repository.py` | MODIFIED | ABC gains `load_guild_binding` / `save_guild_binding` / `list_guild_bindings` (ADR-007 pattern). |
| JSON adapter | `bot/repository.py` | MODIFIED | Returns "unbound", no-ops the write — acceptable rollback degradation (ADR-006 D9). |
| SQLite adapter | `bot/repository_sqlalchemy.py` | MODIFIED | Real impl against `GuildKeyBindingRow`. |
| Storage wrappers | `bot/guilds.py` | MODIFIED | `load_guild_binding` / `save_guild_binding` thin wrappers. `Guild` dataclass and `save_guilds` **untouched**. |
| Player service | `bot/services/chronicl3r/player_service.py` | MODIFIED | `_fetch_roster` **deleted**. `refresh_guild` / `validate_if_stale` take `snapshot: GuildSnapshot` instead of `api_key: str`. No HTTP remains in this module. |
| Admin cog | `bot/cogs/admin_cog.py` | MODIFIED | `/update_guild_key` (new, admin tier). `_config_guilds` renders binding + quarantine. `register_guild` writes the initial binding. |
| Update cog | `bot/cogs/update_cog.py` | MODIFIED | `update_leaderboard` / `update_all` route through `verify_and_resolve`. |
| Tasks cog | `bot/cogs/tasks_cog.py` | MODIFIED | `auto_update` routes through `verify_and_resolve`; season discovery uses `active_key` with fall-through (DDD-7). |
| Architecture rule | `.importlinter` / AST hook | MODIFIED | Forbids `api_key` reads outside `bot/guild_keys.py` and the adapters. |

Genuinely new modules: **three** — `bot/services/tacticus/guild_client.py`,
`bot/guild_keys.py`, and `bot/obs.py` (~10 LOC, promoted verbatim from
`bot/db/session.py::_emit_structured` so the policy layer can emit structured
records without importing the SQLite stack). Everything else is a rewire of an
existing component.

> Corrected 2026-08-01 from "**two**" after the Final Wave Review Gate. The
> third module was raised by DEVOPS U1 and independently re-found by the
> architect reviewer. See `## Wave: DEVOPS / [REF] Changed Assumptions` U1.

## Wave: DESIGN / [REF] Driving Ports

| Port | Surface | Tier | Slice |
|---|---|---|---|
| `/update_guild_key guild_id api_key [force]` | Discord slash command | `admin` | 02 |
| `/view_config config:guilds` | Discord slash command | `officer` | 01, 03 |
| `/update_leaderboard`, `/update_all` | Discord slash commands | `officer` | 01, 03 |
| `auto_update` | `@tasks.loop(hours=1)` | — | 01, 03 |
| Update-channel post | Discord message | — | 01, 03 |
| Guild ping-channel post | Discord message | — | 03 |

## Wave: DESIGN / [REF] Driven Ports and Adapters

| Driven port | Adapter | Notes |
|---|---|---|
| `ClusterRepository` (binding methods) | `SqlAlchemyClusterRepository` / `JsonClusterRepository` | JSON returns unbound; SQLite is the live path |
| Tacticus guild endpoint | `bot/services/tacticus/guild_client.py` | ADR-003 allow-list row #2, caller amended |
| Chronicler profiles | `chronicl3rClient` | **unchanged** — `PlayerService` keeps its Chronicler calls; only the Tacticus call leaves |
| Discord messages | `discord.py` | unchanged |

## Wave: DESIGN / [REF] Technology Choices

No new dependencies. Every library is already pinned in `requirements.txt`:
`SQLAlchemy 2.0` (ORM), `Alembic` (migration), `aiosqlite` (driver), `httpx`
(the probe reuses the existing async client pattern), `discord.py`
(app-commands), `cryptography` (Fernet + HKDF, untouched by this feature).

Python, OOP paradigm — unchanged, already recorded in `CLAUDE.md`.

## Wave: DESIGN / [REF] Reuse Analysis

Hard gate. Every component with overlapping responsibility, classified.

| Existing component | File | Overlap | Decision | Justification |
|---|---|---|---|---|
| `PlayerService._fetch_roster` | [player_service.py:117](../../../bot/services/chronicl3r/player_service.py#L117) | Already calls `GET /api/v1/guild` — the exact endpoint the probe needs | **EXTEND** (fold + relocate) | Same call, same response. Folding is ~15 LOC; a parallel probe would double Tacticus calls and permit probe/roster disagreement. |
| `ClusterRepository` ABC | [repository.py:96](../../../bot/repository.py#L96) | Storage port for all cluster/guild state | **EXTEND** | ADR-007 set the precedent for feature-shaped storage methods on the ABC. 3 methods vs a parallel persistence path. |
| `JsonClusterRepository` | [repository.py:218](../../../bot/repository.py#L218) | JSON impl | **EXTEND** | Must implement the 3 methods to keep the parametrised contract suite green on the rollback path. Degrades to "unbound" — same pattern as `get_replay_thread` returning `None`. |
| `SqlAlchemyClusterRepository` | [repository_sqlalchemy.py:54](../../../bot/repository_sqlalchemy.py#L54) | SQLite impl | **EXTEND** | Same. |
| `bot/db/models.py` | [db/models.py](../../../bot/db/models.py) | ORM model module | **EXTEND** | One additional row class alongside twelve existing. |
| `bot/guilds.py` | [guilds.py](../../../bot/guilds.py) | The documented cog-facing wrapper layer | **EXTEND** | brief §4.1 and ADR-004 rule 1 make this the sanctioned layer. Two wrappers vs a new access path. |
| `_DEAD_KEY_STATUSES` | [registration_cog.py:24](../../../bot/cogs/registration_cog.py#L24) | The 401/403 dead-key classification | **EXTEND** (reuse verbatim) | Same taxonomy, verified against Tacticus 2026-07-25. Lifted to a shared constant rather than duplicated. |
| `_probe_api_keys` / `_format_key_validation` | [registration_cog.py:27](../../../bot/cogs/registration_cog.py#L27), [:64](../../../bot/cogs/registration_cog.py#L64) | Probes keys, classifies, formats an officer-facing report | **CREATE NEW** (probe only) | The *classification vocabulary* is reused. But these hit `/api/v1/player` with `{discord_id: key}` batching for registration keys and return status codes; the guild probe hits `/api/v1/guild` for one guild and must return an identity payload. Different endpoint, different cardinality, different return type — extending would mean a function that means two things. |
| `admin_cog.register_guild` | [admin_cog.py:52](../../../bot/cogs/admin_cog.py#L52) | Installs a guild + key, then refreshes the roster | **EXTEND** | `/update_guild_key` is a sibling in the same cog; `register_guild` additionally gains the initial binding write. |
| `bot/permissions.py:require_tier` | [permissions.py:48](../../../bot/permissions.py#L48) | Tier gate | **EXTEND** (use unchanged) | ADR-001: permission checks live in exactly one place. `@require_tier("admin")` applied as-is. |
| `bot/embeds.py:guild_autocomplete` | [embeds.py](../../../bot/embeds.py) | Guild-id autocomplete for slash commands | **EXTEND** (use unchanged) | `/update_guild_key` reuses it directly. |
| — | `bot/services/tacticus/guild_client.py` | No existing module owns Tacticus-direct guild calls | **CREATE NEW** | Layering, not a literal cycle *(wording corrected 2026-08-01, see below)*. Putting the probe in `PlayerService` would make the policy chokepoint depend on a Chronicler domain service, and would keep a Tacticus HTTP call inside the package DDD-2 is emptying of it. |
| — | `bot/guild_keys.py` | No existing module owns key policy | **CREATE NEW** | Cannot live in `bot/guilds.py` (would make the layer every cog imports depend on an HTTP client) nor in a cog (seven sites span three cogs plus a service). |

Two CREATE NEW decisions, both justified by layering rather than by
complexity. No existing class is duplicated.

> **Correction, 2026-08-01 (Final Wave Review Gate).** The `guild_client.py`
> row previously claimed that siting the probe in `PlayerService` "is an import
> cycle". Traced explicitly, it is not: the edges would be `guild_keys →
> player_service → bot.guilds` plus `guild_keys → bot.guilds`, and no edge runs
> back — `player_service` does not import `guild_keys` (DESIGN has its two
> methods take a `GuildSnapshot`, not an `api_key`), and `bot/guilds.py`
> importing `guild_keys` is forbidden by the enforcement rules and asserted by
> `test_the_guilds_wrapper_layer_stays_free_of_policy_and_http`, which passes
> today. **The decision is unchanged and still correct** — it rests on layering
> and package cohesion, which is what the row now says. Flagged so a future
> architect does not cite a cycle that isn't there as precedent.

## Wave: DESIGN / [REF] Architecture Enforcement

Extends the ADR-006 §I rule set:

- `bot/cogs/*` and `bot/services/*` MUST NOT read `api_key` from a guild dict or
  a `Guild` object. Sanctioned readers: `bot/guild_keys.py` and the two
  repository adapters. *(AST pre-commit hook — the mechanism ADR-006 §I already
  uses for the `probe()` assertion.)*
- `bot/services/chronicl3r/*` MUST NOT import `httpx`. *(Enforces DDD-2: the
  Tacticus call has left the Chronicler package for good.)*
- `bot/guilds.py` MUST NOT import `bot.guild_keys` or `httpx`. *(Prevents the
  cycle DDD-3 is built to avoid.)*
- `bot/guild_keys.py` MUST NOT be imported by `bot/repository*.py`. *(Policy
  depends on storage, never the reverse.)*

## Wave: DESIGN / [REF] Outcome Collision Check

`nwave-ai outcomes check-delta docs/feature/guild-key-integrity/feature-delta.md`
→ **exit 0**, `0 outcomes checked, 0 collisions found across 0 outcomes`.

Recorded honestly: `docs/product/outcomes/registry.yaml` does not exist in this
repository, so the gate passed **vacuously** rather than meaningfully. It is not
evidence of no duplication; it is evidence that no registry is being maintained
here. Reuse Analysis above is the gate that actually did work.

## Wave: DESIGN / [REF] Open Questions (deferred to DISTILL/DELIVER)

1. **Alert-suppression window.** DDD-5 fixes 24 h per guild. Whether that should
   be configurable is a DELIVER question; a constant is right until a second
   operator exists.
2. **`GuildSnapshot` member shape.** `_fetch_roster` returns `set[str]` today.
   The snapshot must carry at least that; whether it should carry full member
   dicts for future use is a DISTILL call, driven by what the AT needs.
3. **Contract test for the Tacticus guild response.** ADR-006 §H names Tacticus
   as the highest-risk boundary. A recorded-response contract test for
   `GET /api/v1/guild` — including a fixture with `guildId` absent — is
   recommended to DISTILL. Not designed here.
4. **Backfill of `identity_bound_at` for the 30 lingering `is_former` rows.**
   Out of feature scope (DISCUSS), noted so DELIVER does not rediscover it.

---

# DEVOPS

> Wave 4 of 6. Density: `lean` — Tier-1 `[REF]` only. The platform baseline
> (single VM, systemd, no containers, no CI, GitHub Flow, pre-release
> mutation) is inherited unchanged from the `sqlite-backend` DEVOPS wave;
> only the deltas this feature forces are re-decided here.
> Machine artifact: [`environments.yaml`](environments.yaml).
> KPI instrumentation SSOT: [`docs/product/kpi-contracts.yaml`](../../product/kpi-contracts.yaml).

## Wave: DEVOPS / [REF] Decisions

| # | Decision | Verdict | Source |
|---|---|---|---|
| D1 | Deployment target: on-premise single Linux VM, `/opt/discord-bot`, systemd | **CARRIED** | `sqlite-backend` DEVOPS D1 |
| D2 | Container orchestration: none | **CARRIED** | `sqlite-backend` DEVOPS D2 |
| D3 | CI/CD platform: none — local `pytest` remains the gate | **CARRIED** | `sqlite-backend` DEVOPS D3 |
| D4 | Existing infrastructure reused: VM, systemd unit, `.venv`, SQLite DB, backup timer | **CARRIED** | `sqlite-backend` DEVOPS D4 |
| D5 | Observability: structured JSON records into the existing `discord.log`; **Discord is the alerting surface** | **EXTENDED** | new event family `guild.key.*` |
| D6 | Deployment strategy: Recreate (`systemctl restart`), **three sequenced deploys**, migration-before-restart | **EXTENDED** | slice ordering, ADR-008 D3 |
| D7 | Continuous learning: no A/B, no flags, no canary analysis | **CARRIED** | one process, one operator |
| D8 | Branching: GitHub Flow, short-lived branches → PR → `main` | **CARRIED** | `sqlite-backend` DEVOPS D8 |
| D9 | Mutation testing: **pre-release** | **CARRIED — not re-asked** | already in `CLAUDE.md` |
| **D10** | **The chokepoint rule is enforced by a pytest architecture test**, not a pre-commit hook | **NEW** | operator selection, 2026-07-31 |
| **D11** | **Slice 01 soaks 7 days in production before Slice 03 deploys** | **NEW** | operator selection, 2026-07-31 |

### D10 — why this is the load-bearing DEVOPS decision

ADR-008 D3 states the rule plainly: *"A wrapper is only a chokepoint if
bypassing it is caught."* DESIGN specified an AST pre-commit hook and cited
"the mechanism ADR-006 §I already uses." **That mechanism does not exist.**
Verified 2026-07-31:

| Claimed | Actual |
|---|---|
| AST pre-commit hook for the `probe()` assertion | no `.pre-commit-config.yaml`, no installed hook in `.git/hooks` |
| `pytest-archon` composition-root check | installed in `.venv`, **imported by zero tests** |
| `import-linter` contracts | 4 contracts real in `pyproject.toml`, run only when someone types `lint-imports` |
| Both tools declared as dependencies | **absent from `requirements.txt`** — present only because someone `pip install`ed them locally |

So the enforcement DESIGN leans on is presently aspirational. D10 converts it
into a test that runs on the gate that already exists:

- `tests/acceptance/guild-key-integrity/test_architecture_chokepoint.py` —
  AST scan asserting no `bot/cogs/*` or `bot/services/*` module reads
  `api_key` from a dict subscript, `.get()`, or attribute access. Sanctioned
  readers: `bot/guild_keys.py`, `bot/repository.py`, `bot/repository_sqlalchemy.py`.
- Two `pytest-archon` rules in the same file: `bot.services.chronicl3r.*`
  must not import `httpx`; `bot.guilds` must not import `bot.guild_keys` or
  `httpx`.
- One new `lint-imports` contract mirroring the `guild_keys` ↛ `repository*`
  direction.
- `import-linter` and `pytest-archon` **pinned into `requirements.txt`**, so
  the enforcement is reproducible on the VM and in any future CI.

A pre-commit hook was rejected: it needs `pre-commit install` per clone and
is bypassable with `--no-verify`, so the guard would be strongest exactly
where it is least needed (the operator's laptop) and absent where the code
actually lands. A CI workflow was offered and declined — the tests are
written so a future `.github/workflows/ci.yml` running `pytest` +
`lint-imports` picks them up with no changes.

### D11 — why 7 days

Slice 01 binds and reports without blocking; Slice 03 makes the same
comparison quarantine. The gap between them is the only window in which a
wrong binding is observable but harmless, so it is the entire empirical
basis for KPI-4 (0 false-positive quarantines in 30 days). Seven days is
~168 probes per guild, which distinguishes a stable binding from an
intermittent one; 24 h would not, and one cycle proves only that the code
ran once.

**Gate to deploy Slice 03:** across the 7-day window, every registered guild
shows a first-bind announcement, and `grep 'guild.key.mismatch\|guild.key.unverifiable' discord.log`
returns zero records the operator cannot explain. Any unexplained record
resets the window.

## Wave: DEVOPS / [REF] Environment Matrix

Machine-readable form (the artifact DISTILL parses): [`environments.yaml`](environments.yaml).

| Env | What it exercises | Platform | Key preconditions |
|---|---|---|---|
| `clean` | TOFU first-bind (DDD-8): fresh DB at the new head, `guild_key_bindings` empty | linux, wsl, macos | migration applied, no binding rows, live-shaped probe fixture |
| `bound-matching` | The steady state — probe equals binding, no alert, no state change | linux, wsl, macos | binding row present, `guildId` equal to probe response |
| `bound-drifted` | **The incident replay.** Probe returns a different `guildId` | linux, wsl, macos | binding = Word Bearers `b64bdba4…`, probe returns Dark Mechanicum `d71d583f…` |
| `unverifiable` | DDD-10 — response has no `guildId`; must alert loudly and **not** quarantine | linux, wsl, macos | probe fixture with `guildId` key removed |
| `tacticus-unreachable` | DDD-6 — timeout / 5xx / DNS must not change state | linux, wsl, macos | transport error injected at the `httpx` boundary |
| `dead-key` | DDD-6 — 401/403 reports but does not quarantine | linux, wsl, macos | probe returns 401 |
| `mixed-cluster` | **DDD-7 / KPI-5** — one quarantined guild in a server of ≥2 | linux, wsl, macos | ≥2 guilds, the quarantined one **first in dict order** |
| `json-backend-rollback` | ADR-006 D9 degradation — bindings unbound, feature inert, no crash | linux, wsl, macos | `SCRAPCODE_REPO_BACKEND=json` |

`mixed-cluster` deliberately pins the quarantined guild **first** in
iteration order. The season SPOF (`next(iter(guilds.values()))`) only
misbehaves in that ordering; a test that happens to put it second passes
while the bug is fully present.

## Wave: DEVOPS / [REF] CI/CD Pipeline Outline

No CI platform (D3). The pipeline is a documented local stage list; GitHub
Flow (D8) supplies the trigger rules. Every stage is a command the operator
runs, and every command is CI-portable unchanged.

| # | Stage | Trigger | Command | Blocking |
|---|---|---|---|---|
| 1 | Unit + acceptance | any branch, before push | `pytest tests/unit tests/acceptance` | yes |
| 2 | Architecture | same run as 1 | `pytest tests/acceptance/guild-key-integrity/test_architecture_chokepoint.py` and `lint-imports` | yes — D10 |
| 3 | Migration rehearsal | feature branch, before merge | `alembic upgrade head` then `alembic downgrade -1` against a **copy** of the production DB | yes — DoD 2 |
| 4 | Merge | PR → `main` | operator review; stages 1–3 green | yes |
| 5 | Deploy | manual, `main` only | see Deployment Strategy below | — |
| 6 | Post-deploy verify | after each restart | probe records + first-bind announcements | yes |

Branch rules: feature branches off `main`, PR, merge, delete. There is no
server-side branch protection because there is no CI to enforce it — the
gate is the operator running stages 1–3 before merging. That is the honest
description of the control, not a weaker one dressed up.

## Wave: DEVOPS / [REF] Monitoring Contracts

One row per outcome KPI. Full collection recipes, event schemas and
thresholds: [`docs/product/kpi-contracts.yaml`](../../product/kpi-contracts.yaml).

| KPI | Instrument | Signal | Collection | Alert |
|---|---|---|---|---|
| KPI-1 — detect ≤1 h | `guild.key.probe.ok` (each successful probe) + `guild.key.mismatch` + `guild.key.alert.sent` | log | `alerted_at − last_probe_ok_at` for the guild | the Discord alert **is** the instrument |
| KPI-2 — 0 contaminated rows | `guild.key.ingest.blocked` + SQL count | log + DB | `SELECT COUNT(*) FROM battle_hits WHERE guild_id=? AND completed_on > ?` (and `bomb_hits`), `?` = `quarantined_at` | none — verified at incident review |
| KPI-3 — key swap ≤60 s | `guild.key.updated` with `elapsed_ms` | log | interaction receipt → write commit, measured in-command | none |
| KPI-4 — 0 false-positive quarantines | `guild.key.quarantined` records over 30 days | log | operator reviews each record; every one must map to a real guild move | none — review cadence |
| KPI-5 — 100% of guilds survive a sibling's quarantine | `auto_update.cycle` with `guilds_total` / `guilds_processed` / `guilds_skipped` / `skip_reasons` | log | `guilds_processed == guilds_total − guilds_quarantined` | `ERROR` when `guilds_processed == 0` and `guilds_total > 0` |
| KPI-6 — 0 key values in logs or Discord | grep artifact + a schema test | test | `grep -F "$(decrypted key)" discord.log` returns nothing; a test asserts no `guild.key.*` event schema carries an `api_key` field | none — build gate |

### KPI-1's measurement definition is corrected here

DISCUSS defines KPI-1 as *"delta between the first probe returning a
mismatched identity and the alert message timestamp."* Those two events
occur in the same coroutine on the same tick, so that quantity is
approximately zero **always** — it would pass whether the feature worked or
not. It is a vacuous metric as written.

The measurable quantity that carries the intended meaning is:

```
detection_latency = alerted_at − last_probe_ok_at
```

i.e. the gap back to the last probe that *agreed*, which is the widest
window in which drift could have occurred unnoticed. It is bounded above by
the `@tasks.loop(hours=1)` interval plus alert latency, so the ≤1 h target
is meaningful and falsifiable — a missed cycle, a hung `httpx` call, or a
throttled loop all push it over. Both fields come from two log records with
no new plumbing. Recorded as a Changed Assumption below.

### KPI-6's correlation ID

`guild.key.*` events carry `key_ref` — the **first 8 hex characters of
`api_key_hmac`** — never the key, never the ciphertext. `api_key_hmac` is
already an HKDF-SHA256 derivation keyed by `SCRAPCODE_DB_KEY` (ADR-006 D7),
so a truncated prefix is not key material and cannot be reversed without the
Fernet key. This lets the operator follow one key across bind → mismatch →
quarantine → update records while keeping KPI-6 at zero by construction.

## Wave: DEVOPS / [REF] Deployment Strategy

**Recreate** — `systemctl restart discord-bot`. One process on one VM; there
is no second instance to shift traffic to, so blue-green, canary and rolling
are all inapplicable. The three slices deploy as **three separate releases**,
because Slice 01's whole purpose is to be observed in production before
Slice 03 is allowed to act on it (D11).

**Ordering is not interchangeable.** The probe compares the DB's
`alembic_version` against the *compiled* head and refuses on any inequality
([session.py:224](../../../bot/db/session.py#L224)) — in **both** directions.

```
DEPLOY (slice 01, the only one with a migration):
  1. verify the hourly backup timer ran            systemctl status discord-bot-backup.timer
  2. stop                                          systemctl stop discord-bot
  3. pull                                          git pull
  4. deps                                          .venv/bin/pip install -r requirements.txt
  5. MIGRATE                                       .venv/bin/alembic upgrade head
  6. start                                         systemctl start discord-bot
  7. verify                                        journalctl -u discord-bot -n 50 | grep db.probe

ROLLBACK (the mirror image — downgrade BEFORE checkout):
  1. stop                                          systemctl stop discord-bot
  2. DOWNGRADE                                     .venv/bin/alembic downgrade -1
  3. checkout the previous tag                     git checkout "$(git describe --tags --abbrev=0 HEAD^)"
  4. start                                         systemctl start discord-bot
```

Step 3 resolves the most recent tag strictly before `HEAD`. Do not guess the
revision under pressure — if the repo has no tags, use
`git checkout "$(git rev-parse HEAD~1)"` and record the SHA before starting.

Reversing steps 2 and 3 of the rollback leaves the DB **ahead** of the code
and the probe refuses to start — the unit lands in `failed` and the bot is
down until someone works out why. This is the single most likely operational
mistake in this feature, so it is stated here rather than left to be
rediscovered.

Slices 02 and 03 carry no migration: `stop → pull → start → verify`.

**Failure containment.** A failed deploy auto-stops rather than half-runs:
probe failure → non-zero exit → systemd `failed`. That property is inherited
from ADR-006 D8 and is not re-derived here.

## Wave: DEVOPS / [REF] Observability Stack

| Signal class | Tool | Rationale |
|---|---|---|
| Logs | structured single-line JSON into the existing `discord.log` (`RotatingFileHandler`, 10 MB × 5) + `journalctl -u discord-bot` | the pattern already in `bot/db/session.py`; no new sink, no new file, no new handler |
| Metrics | **none** | one process, one VM, one operator — Prometheus/Datadog would be more moving parts than the thing being watched |
| Traces | **none** | single process, no service hops to correlate |
| Alerting | **Discord** — update channel + guild ping channel | designed in ADR-008 D5; rate-limited to 1 per 24 h per guild via `last_alerted_at` |
| Health | ADR-006 `probe()` at startup | unchanged |

### Event catalog — the `guild.key.*` family

Extends the `db.*` catalog from the `sqlite-backend` wave. Every record
carries `ts`, `level`, `event`, `server_id`, `guild_id`, `key_ref`.

| Event | Level | Emitted by | When | Extra fields |
|---|---|---|---|---|
| `guild.key.probe.ok` | INFO | `bot/guild_keys.py` | probe returned 200 and identity matched | `tacticus_guild_id` |
| `guild.key.bound` | INFO | `bot/guild_keys.py` | TOFU first-bind (DDD-8) | `tacticus_guild_id`, `tacticus_guild_tag`, `name` |
| `guild.key.mismatch` | ERROR | `bot/guild_keys.py` | probed `guildId` ≠ bound `guildId` | `bound_id`, `observed_id`, `observed_tag`, `observed_name` |
| `guild.key.quarantined` | ERROR | `bot/guild_keys.py` | quarantine written | `reason`, `quarantined_at` |
| `guild.key.ingest.blocked` | WARNING | `bot/guild_keys.py` | a caller was refused a quarantined key | `caller` |
| `guild.key.unverifiable` | ERROR | `bot/guild_keys.py` | 200 with no `guildId` (DDD-10) | `reason: "guildId_absent"` |
| `guild.key.unreachable` | WARNING | `bot/guild_keys.py` | timeout / DNS / 5xx (DDD-6) | `reason`, `status` |
| `guild.key.dead` | ERROR | `bot/guild_keys.py` | 401/403 (DDD-6) | `status` |
| `guild.key.alert.sent` | INFO | `bot/guild_keys.py` | Discord alert posted | `channel_id`, `suppressed_until` |
| `guild.key.alert.suppressed` | DEBUG | `bot/guild_keys.py` | alert withheld inside the 24 h window | `last_alerted_at` |
| `guild.key.updated` | INFO | `bot/cogs/admin_cog.py` | `/update_guild_key` committed | `forced`, `elapsed_ms`, `rebound_from` |
| `auto_update.cycle` | INFO | `bot/cogs/tasks_cog.py` | each hourly cycle, per server | `guilds_total`, `guilds_processed`, `guilds_skipped`, `skip_reasons`, `season` |

`auto_update.cycle` is the one genuinely new instrument — `auto_update`
emits nothing structured today, which is precisely why a whole-server skip
was invisible during the incident. KPI-5 is unmeasurable without it.

### The operator's dashboard

Three commands, in the spirit of the `sqlite-backend` design:

```bash
# Any guild in trouble right now?
sudo journalctl -u discord-bot --since '24 hours ago' --no-pager \
  | grep -E 'guild\.key\.(mismatch|quarantined|unverifiable|dead)'

# Did the last cycle process every guild it should have?
sudo journalctl -u discord-bot --since '2 hours ago' --no-pager \
  | grep 'auto_update.cycle'

# Which guilds are bound to what?
/view_config config:guilds        # in Discord — no SSH
```

The third being a Discord command rather than a shell command is the point
of KPI-3: routine identity questions stop requiring a terminal.

## Wave: DEVOPS / [REF] Mutation Testing Strategy

**pre-release** — unchanged, already recorded in `CLAUDE.md` and selected
during the `sqlite-backend` DEVOPS wave. Not re-asked, and `CLAUDE.md` is
not rewritten. No mutation tool is in `requirements.txt` yet, so the
strategy remains a stated intent rather than a running gate; the quality
gates that actually run for this feature are the parametrised acceptance
suite and the D10 architecture test.

## Wave: DEVOPS / [REF] Branching Strategy

**GitHub Flow**, unchanged. Feature branch → PR → `main` → delete.

| Branch | Stages that must pass | Enforced by |
|---|---|---|
| `feature/guild-key-integrity-slice-NN` | 1, 2, 3 | operator, locally |
| `main` | 1, 2 before merge; 5, 6 on deploy | operator, locally |

Trunk-based was rejected for the same reason as last wave: it presumes a
fast automated commit stage, and there is none. GitFlow is overkill for a
single-operator bot. `origin` is `github.com/krewsayder/ScrapCode`, so the
CI option remains available at any time without rework — stages 1–3 are
already a workflow in all but syntax.

## Wave: DEVOPS / [REF] Coexistence Matrix

| Tool / mechanism | Must not break | Interaction with this feature |
|---|---|---|
| systemd `discord-bot` unit | ✅ | reused unchanged; probe refusal must surface as `failed`, not a `Restart=always` loop |
| `discord-bot-backup.timer` | ✅ | **must have run before the Slice 01 migration** — it is the only rollback for a bad `alembic upgrade` |
| ADR-006 `probe()` | ✅ | the new revision changes the compiled head; migrate-before-restart and downgrade-before-checkout (Deployment Strategy) |
| Alembic revision chain | ✅ | head is `0002`; the new revision **must** set `down_revision = "0002"` |
| `import-linter` contracts (4 in `pyproject.toml`) | ✅ | must still pass with `bot/guild_keys.py` and `bot/services/tacticus/` added; one contract is added |
| `pytest` gate (`tests/unit`, `tests/acceptance`) | ✅ | new suite added under `tests/acceptance/guild-key-integrity/` |
| `.venv` / `pip` | ✅ | `import-linter` + `pytest-archon` move from ad-hoc installs into `requirements.txt` |
| `.env` (`SCRAPCODE_DB_KEY`, `SCRAPCODE_REPO_BACKEND`, `DISCORD_TOKEN`) | ✅ | **no new secret introduced** — `key_ref` derives from the existing `api_key_hmac` |
| JSON rollback path (`SCRAPCODE_REPO_BACKEND=json`) | ✅ | binding methods return unbound; the feature goes inert without raising |
| Discord API rate limits | ✅ | alerts capped at 1 per 24 h per guild (`last_alerted_at`) |
| pre-commit / husky | ✅ | neither installed; D10 deliberately does not introduce them |

## Wave: DEVOPS / [REF] Pre-requisites

Platform obligations this wave accepts from DESIGN:

1. **`quarantined_at` must be stored ISO-8601 UTC in the same shape as
   `battle_hits.completed_on`.** KPI-2's query compares them as strings
   (`completed_on` is `String(32)`, sourced verbatim from Tacticus). If the
   shapes differ the comparison silently returns the wrong set rather than
   erroring. DELIVER must confirm the real `completed_on` format against a
   production row before relying on the string comparison.
2. **A dependency-free structured-log helper must exist.**
   `_emit_structured` is private to `bot/db/session.py`, which imports
   `sqlalchemy`, `alembic` and `cryptography` at module scope.
   `bot/guild_keys.py` importing it would drag SQLAlchemy into the JSON
   rollback path. See Changed Assumptions U1.
3. **The migration is additive and reversible.** `guild_key_bindings` is a
   new table with no backfill (TOFU populates it), so `downgrade()` is a
   plain `drop_table` and no production data is at risk in either direction.
4. **`bot/services/tacticus/` needs an `__init__.py`.** `bot/services/` is a
   package with exactly one subpackage today; the new one must be importable
   for the `import-linter` contracts to resolve it.
5. **No new environment variable, no new secret, no new port, no new
   external system.** ADR-008's consequences already establish that the
   endpoint list and per-hour call volume are unchanged.

## Wave: DEVOPS / [REF] Changed Assumptions

### U1 — a shared structured-logging module is required (component addition)

**Original (DESIGN, `## Wave: DESIGN / [REF] Component Decomposition`):**
the component table lists exactly two new modules,
`bot/services/tacticus/guild_client.py` and `bot/guild_keys.py`, and states
*"Genuinely new modules: two."*

**New assumption:** a third, trivial module is required —
`bot/obs.py`, holding the `_emit_structured` helper, promoted verbatim from
`bot/db/session.py` (which then imports it).

**Rationale:** the DEVOPS observability design (D5) requires
`bot/guild_keys.py` and `bot/cogs/tasks_cog.py` to emit structured records.
The only implementation of that pattern today is private to
`bot/db/session.py`, and that module imports `sqlalchemy`, `alembic` and
`cryptography` at module scope
([session.py:43-48](../../../bot/db/session.py#L43-L48)). Importing it from
`bot/guild_keys.py` would make the SQLite stack a hard import dependency of
the policy layer — including on the `SCRAPCODE_REPO_BACKEND=json` rollback
path, where SQLAlchemy is meant to be untouched. Duplicating the helper is
the other option and was rejected: two divergent JSON log schemas is the
failure mode structured logging exists to prevent.

`bot/obs.py` imports only `json` and `logging`. It is ~10 LOC and adds no
dependency in any direction. Written up in
[`devops/upstream-changes.md`](devops/upstream-changes.md) for the
architect.

### U2 — KPI-1's measurement definition is replaced

**Original (DISCUSS, `## Wave: DISCUSS / [REF] Outcome KPIs`):** KPI-1
measured by *"Delta between the first probe returning a mismatched identity
and the alert message timestamp."*

**New:** `detection_latency = alerted_at − last_probe_ok_at`.

**Rationale:** the original two events fire in the same coroutine on the
same tick, so the original definition measures approximately zero
regardless of whether the feature works — it cannot fail, therefore it
cannot inform. The replacement measures the interval during which drift
could have gone unnoticed, is bounded by the loop period, and does fail if
the hourly loop stalls. The KPI's **target (≤1 h) and intent are unchanged**;
only the formula is corrected. The DISCUSS text is not rewritten, per the
"contradictions are flagged, not rewritten" rule.

### U3 — DESIGN's stated enforcement mechanism does not exist

**Original (DESIGN, `## Wave: DESIGN / [REF] Architecture Enforcement`, and
ADR-008 D3):** *"the project already runs import-linter + AST pre-commit
hooks."*

**New:** `import-linter` contracts exist in `pyproject.toml` but are
manual-only; there is no pre-commit config, no installed hook, and no test
importing `pytest-archon`. Neither tool is in `requirements.txt`. The
enforcement is delivered as a pytest architecture test instead (D10).

**Rationale:** evidence, listed in the D10 table above. The *rules* DESIGN
specifies are unchanged and all four are implemented; only the mechanism
that runs them differs. ADR-008 D3's claim about the existing project state
is inaccurate and should be corrected when that ADR is next touched — flagged
in [`devops/upstream-changes.md`](devops/upstream-changes.md), not silently
edited.

## Wave: DEVOPS / [REF] Deferred

Recorded so DELIVER does not rediscover them:

- **CI workflow.** Offered and declined (D10). Stages 1–3 are CI-portable
  unchanged; the cheapest version stays a ~30-line GitHub Actions file.
- **`db.tx.commit` / `db.tx.rollback`.** Designed in the `sqlite-backend`
  observability wave, never implemented — `bot/repository_sqlalchemy.py`
  emits no structured records. Not this feature's scope, but it means
  per-transaction outcomes are still invisible.
- **The JSON log formatter.** `_emit_structured` writes a JSON *message
  string*; the formatter that would render `extra` as a proper JSON record
  was never built. Records remain greppable, which is all the KPI queries
  need, so this is noted rather than fixed here.
- **`OnFailure=` → Discord webhook.** The bot alerts about guild keys; it
  cannot alert about being down. Still the cheapest future win.
- **Mutation tooling.** No `cosmic-ray` / `mutmut` in `requirements.txt`;
  the pre-release strategy has no tool behind it yet.

---

# DISTILL

> Wave 5 of 6. Density: `lean` — Tier-1 `[REF]` only. The `.feature` files
> under `tests/acceptance/guild-key-integrity/acceptance/` are the SSOT for
> scenarios; the sections below are pointers and structured summaries.
>
> Project convention that overrides the generic nWave examples: this project
> does **not** use `pytest-bdd`. `.feature` files are the human-readable
> scenario SSOT; the `test_*.py` modules beside them are the executable
> specs, in plain pytest + `pytest-asyncio`. Precedent:
> `tests/acceptance/sqlite-backend/`. Recorded in
> `docs/architecture/atdd-infrastructure-policy.md`.

## Wave: DISTILL / [REF] Reconciliation

**Passed — 0 unresolved contradictions.**

DISCUSS D1–D6 map onto DDD-1/3/5/6/8 and the DEVOPS slice ordering without
conflict. Three supersessions are in play and all three are already
authorized in writing, so they are resolved history rather than open
ambiguity:

| Supersession | Authorized by |
|---|---|
| Binding columns on `guilds` → a separate `guild_key_bindings` table | DESIGN `Correction to DISCUSS` + DDD-4 |
| KPI-1's measurement formula | DEVOPS U2 |
| The enforcement mechanism ADR-008 D3 describes | DEVOPS U3 + D10 |

Four gaps found and recorded in
[distill/upstream-issues.md](distill/upstream-issues.md) — none blocking.
UI-1 (AC-006.1/.2 describe a schema DDD-4 replaced) and UI-3 (`hypothesis`
required, not pinned) are the two that need action.

## Wave: DISTILL / [REF] Scenario List

93 collected tests across 5 `.feature` files, plus one architecture module
and one Tier B state machine. 43 acceptance criteria covered.

| File | Scenarios | Tests | Covers |
|---|---:|---:|---|
| `slice-01-bind-and-report.feature` | 18 | 28 | US-006, US-001, US-002 |
| `slice-02-update-guild-key.feature` | 10 | 17 | US-003 |
| `slice-03-quarantine-enforcement.feature` | 15 | 30 | US-004, US-005 |
| `environment-matrix.feature` | 9 | 9 | Mandate 4 — one per environment |
| `tacticus-guild-contract.feature` | 6 | 7 | DESIGN Open Question 3 |
| `test_architecture_chokepoint.py` | — | 7 | DEVOPS D10 |
| `tier_b/test_key_status_state_machine.py` | — | 2 | Mandate 10 Tier B |

Tag vocabulary in use: `@slice-01/02/03`, `@us-00N`, `@driving_port`,
`@real-io`, `@adapter-integration`, `@error`, `@kpi`, `@property`,
`@requires_external`, `@traceability`, `@env-*`.

**Error-path coverage: 44 of 93 tests (47%)** carry `@error` or exercise a
failure classification — above the 40% floor. That is not padding: the
feature is a classifier, and four of its five outcomes are failures.

Three scenarios deserve naming because they carry disproportionate weight:

- **`test_a_matching_guild_is_completely_silent`** — every other alerting
  scenario passes against an implementation that alerts unconditionally.
  This is the one that fails it, and it is the empirical basis for KPI-4.
- **`test_every_key_consumption_site_refuses_a_quarantined_guild`** —
  parametrized over the seven-member `KeyConsumptionSite` enum. It also
  asserts `call_count == 0`, so an implementation that fetches the other
  guild's data and then discards it still fails.
- **`test_a_quarantined_guild_listed_first_does_not_stop_the_server`** — the
  fixture pins the quarantined guild FIRST in dict order and asserts that
  precondition, because `next(iter(guilds.values()))` only misbehaves in
  that ordering.

## Wave: DISTILL / [REF] WS Strategy

**Inherited: Strategy C — no new walking skeleton.** DISCUSS discharged the
Mandate 5 obligation against `sqlite-backend`'s skeleton, which established
the same end-to-end path (Discord → cog → `ClusterRepository` → SQLite).

One scenario nonetheless carries `@walking_skeleton @driving_port`:
`slice-02` *"An admin installs a new key and is told which guild it belongs
to"*. It closes the loop through the production composition root and is the
scenario a non-technical stakeholder confirms as "yes, that is what we
need" — the whole feature in one command.

Retired per the current skill: the A/B/C/D per-feature choice. Port class →
treatment now comes from the Architecture of Reference, and the concrete
mechanism from
[docs/architecture/atdd-infrastructure-policy.md](../../architecture/atdd-infrastructure-policy.md),
bootstrapped this wave.

## Wave: DISTILL / [REF] Adapter Coverage

Mandate 6 — every driven adapter has at least one real-I/O scenario, or a
contract smoke where the external is costly.

| Adapter | `@real-io` scenario | Covered by |
|---|---|---|
| `SqlAlchemyClusterRepository` | YES | migration + binding round-trip, real SQLite in `tmp_path` |
| `JsonClusterRepository` | YES | `json-backend-rollback` env, real JSON tree |
| Alembic revision | YES | upgrade-from-0002 and downgrade, real `alembic.command` |
| Tacticus guild endpoint | YES — contract smoke | `tacticus-guild-contract.feature`; recorded response for the parser, `@requires_external` for the live shape |
| Discord channel send | fake with capture | non-deterministic external; `FakeChannel` records text so "nothing posted" is assertable |
| Structured log sink | YES | real `logging` via `caplog`, asserting `record.event` |
| `chronicl3rClient` | n/a | untouched by this feature |

Zero `NO — MISSING` rows.

The Tacticus row is the one worth reading twice. A fake can never tell us
the vendor dropped `guildId`, so the `@requires_external` pair is the only
instrument that can detect the feature's central residual risk. They skip
unless `SCRAPCODE_TACTICUS_CONTRACT_KEY` is set, and should be run at least
once per slice deploy.

## Wave: DISTILL / [REF] Driving Adapter Coverage

Every user-facing entry point in DESIGN mapped to at least one scenario that
enters through it, not through a service function.

| Driving port | Scenario | Protocol |
|---|---|---|
| `/update_guild_key` | slice-02, 10 scenarios | app-command callback + interaction double |
| `/view_config config:guilds` | slice-01 ×1, slice-03 ×4 | app-command callback, asserting rendered embed text |
| `/update_leaderboard`, `/update_all` | slice-03 `KeyConsumptionSite` rows | app-command callback |
| `auto_update` hourly loop | slice-01, slice-03, all 8 env scenarios | loop body awaited directly |
| Update-channel post | every alerting scenario | `FakeChannel.text` |
| Guild ping-channel post | slice-03 both-channels scenario | `FakeChannel.text` |

Zero uncovered entry points. Permission tier is exercised through the real
`require_tier` decorator (ADR-001), and
`test_an_officer_cannot_replace_a_guild_key` additionally asserts the probe
never fired — otherwise the command is an oracle for whether a key is valid.

## Wave: DISTILL / [REF] Two-Tier Composition

**Tier A + Tier B.** Tier B is added because the *model* is a state machine —
three states, six commands — which is the Hebert ch.11 trigger, not because
the input space is wide.

Two Tier B properties are claims about the whole state space that no
enumerated example establishes:

- `quarantine_is_never_a_trap` — from every reachable quarantined state there
  exists a path back to active. DISCUSS D3 is exactly this claim; a single
  example only shows that one path works.
- `quarantined_guilds_never_write` — zero rows under every interleaving of
  probes and key updates, not just the orderings someone thought to write.

Plus one negative test (Hebert ch.6): relax "the admin installs a matching
key" and the property must FAIL without `force`, or the force gate is
decorative.

Requires `hypothesis`, which is not in `requirements.txt`. The module
`importorskip`s with a clear reason, so the gap is visible rather than
silent — see upstream-issues UI-3.

## Wave: DISTILL / [REF] Scaffolds

Mandate 7 — RED, never BROKEN. All carry `__SCAFFOLD__ = True`; all methods
raise `AssertionError`.

| Path | Kind | Note |
|---|---|---|
| `bot/services/tacticus/__init__.py` | NEW | real content — the package marker import-linter needs |
| `bot/services/tacticus/guild_client.py` | NEW scaffold | also the SSOT for `ProbeOutcome`, `KeyStatus`, `GuildIdentity`, `DEAD_KEY_STATUSES` |
| `bot/guild_keys.py` | NEW scaffold | the chokepoint |
| `bot/obs.py` | NEW scaffold | DEVOPS U1's dependency-free log helper |
| `bot/guilds.py` | MODIFIED | three wrapper scaffolds appended; nothing existing touched |

`bot/db/models.py` and `bot/repository*.py` were deliberately **not**
scaffolded. No test imports `GuildKeyBindingRow`: binding state is reached
through the repository port, which keeps the Universe port-exposed and stops
a test reddening on an internal rename. The migration scenario introspects
the table with SQL instead.

`docs/product/outcomes/registry.yaml` gained OUT-1…OUT-5.

## Wave: DISTILL / [REF] Test Placement

```
tests/acceptance/guild-key-integrity/
  pytest.ini            conftest.py         domain_types.py
  acceptance/*.feature  (5 files — scenario SSOT)
  fixtures/guild_response_recorded.json
  test_slice_01_bind_and_report.py
  test_slice_02_update_guild_key.py
  test_slice_03_quarantine_enforcement.py
  test_environment_matrix.py
  test_tacticus_guild_contract.py
  test_architecture_chokepoint.py
  tier_b/  in_memory_composition.py  test_key_status_state_machine.py
```

Precedent: `tests/acceptance/sqlite-backend/`, same shape, its own
`pytest.ini` with `pythonpath = . ../../..`. `pytest tests/unit
tests/acceptance` remains THE gate (no CI — DEVOPS D3).

## Wave: DISTILL / [REF] Domain Language

Mandate-12. `tests/acceptance/guild-key-integrity/domain_types.py` holds the
vocabulary; the four criteria:

1. **Types module exists** — yes.
2. **Typed parameters** — `ProbeOutcome`, `KeyStatus`, `GuildIdentity` and
   `DEAD_KEY_STATUSES` are **re-exported from production**, not re-declared.
   A test-side copy of an enum compares unequal under `is`, and the copy that
   drifts is always the one nobody runs in production. Test-only types
   (`Environment`, `KeyConsumptionSite`, `TransportFailure`) stay local.
3. **No logic in test bodies** — helpers are separate and each raises; test
   bodies are arrange/act/assert with no control flow.
4. **Step-reuse ratio** — n/a as specified. The ratio is defined over
   pytest-bdd step decorators, and this project has none. The equivalent
   measure is parametrization density: **93 tests from 64 test functions
   (1.45×)**, in the same band as the 1.43× natural ceiling recorded for a
   config-shaped feature. Recorded as the data point, not compared to a
   target.

`DeadKeyStatus` asserts at import time that it still matches the production
constant, so a change to the taxonomy cannot leave the suite testing the old
one.

## Wave: DISTILL / [REF] RED Gate

**Passed. Handoff to DELIVER is not blocked.** Full output:
[distill/red-classification.md](distill/red-classification.md).

Run with the scaffold skips stripped so every scenario actually executed:

```
87 failed, 4 passed, 3 skipped

AssertionError                            172   RED — implementation missing
ImportError / ModuleNotFoundError           0
AttributeError / TypeError / NameError      0
fixture / setup errors                      0
```

One wrong-reason RED was found and fixed during the gate rather than
shipped: `test_import_linter_contracts_all_pass` imported an internal
`importlinter` name that does not exist in the installed 2.13 — an
`ImportError`, i.e. BROKEN. It now invokes the real console command as a
subprocess, which is also the truer assertion.

Four tests pass today and are not scaffolds — they are regression guards on
properties the repo currently has and this feature could break: the
environments-list traceability check, the two import-boundary checks, and
`lint-imports` (4 contracts kept with the new modules present).

Coexistence re-verified after the scaffolds landed: `sqlite-backend` 100
passed / 1 xfailed, `tests/unit` 7 passed, `lint-imports` 4 kept 0 broken.

## Wave: DISTILL / [REF] Pre-requisites

| # | Item | Owner | Blocking |
|---|---|---|---|
| 1 | `hypothesis` in `requirements.txt` | DELIVER | Tier B only |
| 2 | `import-linter`, `pytest-archon` in `requirements.txt` (DEVOPS D10) | DELIVER | 2 architecture tests |
| 3 | Fifth `lint-imports` contract: `bot.repository*` must not import `bot.guild_keys` | DELIVER | no |
| 4 | Alembic revision with `down_revision = "0002"` | DELIVER | migration scenarios |
| 5 | `SCRAPCODE_TACTICUS_CONTRACT_KEY` for the live contract pair | operator | 2 tests skip without it |
| 6 | Re-record `fixtures/guild_response_recorded.json` from a live response | operator | contract scenarios assert the shape |

Item 6 is worth doing before DELIVER starts, not after. The committed
fixture is the shape as documented; if production differs, four contract
scenarios encode the wrong shape and the parser is built against it.

## Wave: DISTILL / [REF] KPI Instrumentation Links

`docs/product/kpi-contracts.yaml` gains per-KPI scenario links, a
measurement window, and a soft-vs-hard gate classification. Summary:

| KPI | Scenario | Gate |
|---|---|---|
| KPI-1 detection latency | slice-01 drift + `auto_update.cycle` | soft — reviewed |
| KPI-2 contaminated rows | slice-03 raid + roster, `bound-drifted` env | **hard** — 0 or the slice does not ship |
| KPI-3 key-swap wall clock | slice-02 install, `elapsed_ms` | soft |
| KPI-4 false positives | `test_a_matching_guild_is_completely_silent` + the 7-day soak | **hard** at the soak gate |
| KPI-5 blast radius | `mixed-cluster` env, `auto_update.cycle` fields | **hard** — the SPOF fix is the slice |
| KPI-6 key leakage | slice-02 ×5 outcomes, slice-03 ×4 states | **hard** — any leak blocks |

## Wave: DISTILL / [REF] Final Wave Review Gate

Ran 2026-08-01. Four reviewers in parallel against the full 4-wave file.

| Reviewer | Scope | Verdict | Blockers filed | Blockers upheld |
|---|---|---|---:|---:|
| Eclipse (`nw-product-owner-reviewer`) | DISCUSS | APPROVED | 0 | 0 |
| Architect (`nw-solution-architect-reviewer`) | DESIGN | NEEDS_REVISION | 2 | 2 |
| Forge (`nw-platform-architect-reviewer`) | DEVOPS | REJECTED | 4 | 2 |
| Sentinel (`nw-acceptance-designer-reviewer`) | DISTILL + suite | APPROVED | 0 | 0 |

**Cross-wave check:** no new contradictions. All three prose reviewers
independently converged on the same items already filed as U1, U3 and UI-1 —
corroboration, not conflict.

### Upheld and fixed

| # | Finding | Fix |
|---|---|---|
| A1 | DESIGN said "two new modules"; there are three | Component Decomposition corrected to three, `bot/obs.py` named |
| A2 | ADR-008 D3 claimed enforcement that does not exist | ADR-008 D3 rewritten with the U3 wording; decision unchanged |
| A3 | The `guild_client.py` CREATE NEW justification cited an "import cycle" that is not one | Reuse Analysis row and a correction note rewritten to say layering; **decision unchanged and still correct** |
| F4 | `environments.yaml` names the hourly backup as the only undo for a bad migration, with no restore procedure anywhere | [devops/runbook.md](devops/runbook.md) written — 4 procedures; the load-bearing step is deleting the stale `-wal`/`-shm` |
| F5 | `git checkout <prev>` was ambiguous under pressure | Concrete command in the rollback block and the runbook |
| F2 | No scenario asserted KPI-1's formula was *computable*, only that its three records were emitted | One scenario added; suite 92 → 93 tests |

A3 is worth keeping visible: the decision survived, the stated reason did not.
A future architect citing a cycle that isn't there would be reasoning from a
false precedent.

F4 surfaced something larger than the finding itself. Writing the restore
procedure meant reading the `sqlite-backend` cutover runbook, which records
under *Known gaps at cutover time* that **the backup timer was never
installed**. If that is still true, the only rollback for the Slice 01
migration does not exist. `environments.yaml` and the runbook now both open
with that check.

### Filed and not upheld

| # | Finding | Why not |
|---|---|---|
| F1 | "`mixed-cluster` dict ordering is not guaranteed in the acceptance scenario" | It is. `test_slice_03…:179` and `test_environment_matrix.py:190` both assert `_guild_ids_in_order()[0] == GUILD_WB` before acting. Forge was scoped to the DEVOPS sections and did not have the test files; Sentinel, which did, confirmed the assertion. |
| F3 | "`test_architecture_chokepoint.py` is not provided for audit" | It exists with all four DESIGN rules plus the pinning assertion. Same scoping gap — a defect in the review prompt, not the artifact. |

Both were prompt-scoping artifacts on my side, recorded rather than quietly
dropped: a reviewer that cannot see a file will report it missing, and the
consolidated gate is the wrong place to learn that.

### Accepted as open, not fixed

- **F6 — the 7-day soak gate rests on operator judgement.** "Any unexplained
  `guild.key.mismatch` resets the window" has no machine check behind it. A
  helper that parses `discord.log` and surfaces unexplained records for
  confirmation would harden it. Real, and left open deliberately: it is a
  process control by design, and automating the *judgement* is not possible —
  only the enumeration is.

## Wave: DISTILL / [REF] Deferred

- **`nwave-ai outcomes register` is broken in this install** —
  `FileNotFoundError` on its own bundled `outcomes/schema.json`, missing from
  the wheel. The five OUT rows were written by hand in the adapter's own
  schema shape. `check-delta` still reports "0 outcomes checked across 0
  outcomes" against the now-populated registry, so that gate remains
  non-functional here regardless of the rows.
- **`scripts/shared/telemetry.py` and `scripts/validation/validate_feature_layout.py`
  do not exist in this repository** — no density event was emitted and no
  layout validation ran, for the third wave running.
- **Alert-suppression window configurability** (DESIGN OQ1) — the 24-hour
  constant is asserted as a constant. Still right until a second operator
  exists.
- **`GuildSnapshot` member shape** (DESIGN OQ2) — resolved: `frozenset[str]`,
  matching what `_fetch_roster` returned, with a contract scenario asserting
  the sets are identical. Full member dicts deferred until something needs
  them.

---

# DELIVER

> Wave 6 of 6. Scope: **Slice 01 only** — US-006 (binding store), US-001 (bind
> and show), US-002 (report drift). Slices 02 and 03 are separate deploys per
> DEVOPS D6/D11. Roadmap and DES audit log:
> [`deliver/roadmap.json`](deliver/roadmap.json),
> [`deliver/execution-log.json`](deliver/execution-log.json).

## Wave: DELIVER / [WHY] Upstream Issues

Defects found in prior-wave artifacts while implementing them. Per the
back-propagation contract none are silently edited into the prior wave's text;
they are recorded here with what was changed and why.

Four of the five are defects in the **acceptance suite itself** — tests that
could not pass, could not fail, or tested the wrong object. That concentration
is worth naming: the DISTILL RED gate classified every scenario by **exception
type** (`AssertionError` = RED, `ImportError` = BROKEN) and reported 172/0/0.
That check cannot see a test that fails for a correct-looking reason while
asserting nothing, nor one whose fixtures make its central assertion vacuous.
A future RED gate should also ask, per scenario, *"is this satisfiable inside
the slice's scope, and would it fail if the behaviour regressed?"*

### UD-1 — the chokepoint scan could not pass within the feature's scope

**Found:** step 01-01, before any crafter ran.
**Artifact:** `tests/acceptance/guild-key-integrity/test_architecture_chokepoint.py`.

`test_no_cog_or_service_reads_a_guild_api_key_directly` allowlisted by MODULE.
The scan matches the identifier `api_key`, and this repository stores two
unrelated secrets under that one name: `guilds.api_key` (this feature's
subject) and `player_registrations.api_key` (explicitly out of scope per
DISCUSS `Out of Scope`). It therefore named six modules, only three of which
hold guild keys. It could only have gone green by rewriting the token-cap,
bomb and registration paths — code this feature is forbidden to touch.

`bot/cogs/tasks_cog.py` is the proof a per-module allowlist cannot work:
`cap_detect` (line 75) reads a PLAYER key, `auto_update` (lines 174, 197) reads
a GUILD key.

**Resolved** with operator approval, by two different mechanisms because they
are two different problems:

- Player-key sites are exempted by **enclosing function**, named individually
  in `EXEMPT_PLAYER_KEY_FUNCTIONS`, with a companion test
  `test_the_player_key_exemptions_still_describe_real_code` that fails if any
  name goes stale. A new player-key site elsewhere still fails until someone
  classifies it consciously.
- `admin_cog._config_guilds`'s read is exempted **structurally**, not by name:
  `_is_presence_test` allows a read that is the direct test of an `If`/`IfExp`
  or the operand of `not`. AC-005.3 pins the `Missing` rendering that read
  produces, so it must survive — but exempting the whole function by name would
  also stop the test noticing if someone later *used* the key there, which is
  exactly a seven-becomes-eight regression.

Scan now reports precisely the six guild-key sites (D6 #1–#6).

### UD-2 — the migration guard was blind to the regression it exists to catch

**Found:** step 02-01, by the crafter, routed rather than edited — correct
DELIVER discipline.
**Artifact:** `test_upgrade_creates_the_binding_store_and_touches_no_guild_record`.

The scenario requested the `sqlite_repo` fixture and never used it in the body.
`sqlite_repo` depends on `migrated_db`, which upgrades the **same**
`sqlite_db_path` to head. So the migration had already run before the body read
`before`, the body's own `command.upgrade(..., "head")` was a no-op, and both
`guild_cols_before` and `guild_cols_after` were sampled from an already-migrated
database.

That made the column-list assertion vacuous — and that assertion guards DDD-4's
entire reason for existing.

**Demonstrated rather than argued.** Adding `op.add_column('guilds', ...)` to
revision 0003:

| Signature | Result against a revision that adds a `guilds` column |
|---|---|
| `(db_at_previous_head, sqlite_repo)` — as authored | **PASSED** |
| `(db_at_previous_head)` — corrected | **FAILED** |

**Resolved:** unused parameter dropped, with a docstring note against
reintroducing it. The alembic log now shows `Running upgrade 0002 -> 0003`
during the test.

### UD-3 — the suite wrote to the real `clusters/` tree

**Found:** step 02-02, by the crafter. The most consequential of the five.
**Artifact:** `tests/acceptance/guild-key-integrity/conftest.py`.

`bot/guilds.py:61` evaluates `repo = build_repo()` at **import** time, reading
`SCRAPCODE_REPO_BACKEND` / `SCRAPCODE_DB_PATH` / `SCRAPCODE_DB_KEY` at that
moment. The `env_vars` fixture sets those with `monkeypatch.setenv` **during**
the test — far too late to affect a singleton that already exists.

Two consequences. Every call through a `bot/guilds.py` wrapper exercised
whichever repository was built at first import, not the `tmp_path` one the test
configured, so those tests could pass or fail for reasons unrelated to their
subject. And with no Fernet key present, `build_repo`'s safety net falls back to
`JsonClusterRepository()`, whose `base_path` is the real `clusters/` tree: a
full-suite run created `clusters/1458181638453203099/guilds.json` at the
repository root. On a machine holding a live JSON tree that is a write to
production data, and `save_guilds` overwrites rather than appends.

The stray file was inspected before removal — 71 bytes, `{"guilds": {},
"role_tiers": {}}`, no key material, created by that day's test run. Nothing was
lost. **The hazard was structural, not hypothetical**: the same run on the VM,
or on any checkout still holding its pre-cutover JSON tree, would have targeted
real data.

**Resolved:** autouse fixture `_repo_singleton_never_escapes_tmp_path` rebinds
`bot.guilds.repo` to a `tmp_path` JSON repository for every test in the suite,
and `sqlite_repo` / `json_repo` each rebind it to their own instance. Autouse
and unconditional, so a test that forgets to request a repository fixture still
cannot reach the real tree; the default is a JSON repo under `tmp_path` so the
failure mode of forgetting is an empty cluster, not a production write.
Verified: `clusters/` is absent after a full suite run.

### UD-4 — two scenarios declared a Given that no fixture supplied

**Found:** step 02-02, by the crafter.
**Artifact:** `test_slice_01_bind_and_report.py`, both round-trip scenarios.

`slice-01-bind-and-report.feature` says `Given a guild with a stored binding`.
Neither Python body implemented it, and no such fixture existed.

- `test_changing_the_ping_channel_leaves_the_binding_untouched` raised
  `KeyError: 'word_bearers'` — `load_guilds` returns `{}` on a freshly migrated
  database — before reaching its assertion.
- `test_load_and_save_unchanged_preserves_every_field` **passed vacuously**,
  comparing an unbound placeholder against an unbound placeholder and `{}`
  against `{}`. Its own docstring conceded it "currently cannot fail". A
  tripwire with no wire is worse than no tripwire: it reports coverage it does
  not have.

**Resolved:** `registered_guilds` and `bound_guild` fixtures added to
`conftest.py`; both scenarios repointed. `registered_guilds` pins Word Bearers
FIRST in insertion order, because `auto_update` derives the season from
`next(iter(guilds.values()))` and the SPOF only misbehaves in that ordering.
`bound_guild` binds to the identity from the real incident so a failure prints
the values in the postmortem.

Mutation-tested: with `save_guilds` patched to wipe bindings, **both scenarios
fail**; unpatched, both pass. They are real tripwires now.

### UD-5 — the declared pytest gate had never run as one command

**Found:** step 01-01, by the crafter, as a "pre-existing cross-suite failure".
**Artifact:** DEVOPS `## Wave: DEVOPS / [REF] CI/CD Pipeline Outline` stage 1.

DEVOPS stage 1 names `pytest tests/unit tests/acceptance` as the blocking gate
before every push, and the deployment strategy leans on it. It did not work.

Three suites each ship their own `pytest.ini`, and pytest honours exactly one
config per invocation. Given two directories it resolves rootdir to the
repository root and selects `pyproject.toml`, which declared no pytest section —
so `asyncio_mode = auto` was dropped and every `async def` test failed with
`async def functions are not natively supported`. Three unit tests failed that
way in the combined run while passing standalone.

It stayed hidden because every wave ran the suites one at a time and reported
them one at a time, so the numbers always looked right.

**Resolved:** `[tool.pytest.ini_options]` added to `pyproject.toml`. Standalone
runs are unaffected — a per-suite `pytest.ini` sits closer to its own directory
and still wins — so this governs only the combined invocation, which is the one
an operator and any future CI actually type. Before: 3 failed / 104 passed.
After: 111 passed / 91 skipped / 1 xfailed.

### UD-6 — an empty member list would invert the roster silently

**Found:** step 01-03, by the crafter. **Open — carried into step 03-02.**
**Artifact:** DESIGN Open Question 2 (`GuildSnapshot` member shape).

`parse_guild_snapshot` reads members tolerantly, so a 200 response carrying a
`guildId` but no `members` key yields an identity with an **empty** member set.
Once `refresh_guild` consumes the snapshot, an empty roster means every player
is absent — and `refresh_guild` flips everyone absent to `is_former`.

That is the incident's exact failure shape reached by a different route: roster
inversion accounted for 60 of the 67 corrupted `players` rows. No acceptance
scenario covers this response shape, so the crafter flagged it rather than
inventing a classification — the right call.

Treatment carried into step 03-02: a snapshot with no usable member list must
not be allowed to drive a roster write. Recorded for `nw-acceptance-designer` as
a scenario the contract suite should carry.

### UD-7 — `drifted_guild` programmed only half its environment

**Found:** step 03-03. **Artifact:** `conftest.py`.

`environments.yaml` defines `bound-drifted` as TWO facts: the guild is bound to
Word Bearers, AND its key now resolves to Dark Mechanicum. The fixture
programmed only the second. On a clean database the guild is unbound, so
trust-on-first-use adopted Dark Mechanicum and there was no mismatch at all —
five scenarios quietly became TOFU scenarios and passed against an
implementation that never compares anything.

**The suite had reproduced the feature's own failure mode inside itself.**

**Resolved:** `drifted_guild` now depends on `bound_guild`; the three scenarios
that program their own service take `bound_guild` explicitly. Verified by
neutering the identity comparison in `bot/guild_keys.py`: four scenarios now
fail that previously passed.

A helper written during 03-03 to work around the gap — seeding a binding by
inspecting the programmed response — was removed. Keying the arrangement off
the expected outcome is not a precondition, it is a prediction.

### UD-8 — a two-cycle scenario cleared the channel but not the log

**Found:** step 03-03. **Artifact:** `test_second_verification_refreshes_the_date_without_announcing`.

The scenario runs two cycles and asserts the second announces nothing. It
cleared `update_channel.messages` between them but not the captured log, so
cycle one's mandatory adoption record was still visible to a
`key_events.named("guild.key.bound") == []` assertion about cycle two —
**unsatisfiable by any implementation**, since trust-on-first-use requires
exactly one adoption on cycle one.

**Resolved:** added `key_events.clear()` — the capability the author's own
`messages.clear()` implies — and unskipped the scenario.

### UD-9 — the KPI-4 scenario was vacuous

**Found:** step 03-05, while proving non-vacuity across the environment matrix.
**Artifact:** `test_a_matching_guild_is_completely_silent`.

The most load-bearing scenario in the slice, by its own docstring: *"A suite
that only covers drift passes against an implementation that alerts on every
cycle. This is the scenario that fails it, and it is the empirical basis for
KPI-4's zero-false-positive target."*

It was asserting nothing. With only `sqlite_repo` the guild was unbound, so the
cycle took the trust-on-first-use path and never compared. The silence it
asserted was the silence of a check that never ran.

| `GuildIdentity.matches` hard-wired to `False` | Result |
|---|---|
| as authored | **PASSED** |
| taking `bound_guild` | **FAILED** |

**Resolved:** takes `bound_guild`.

### UD-10 — the two acceptance suites share a `conftest` module name

**Found:** step 03-05. **Open — recorded, not fixed.**

`tests/acceptance/sqlite-backend/conftest.py` declares `SEASON = 94`;
`tests/acceptance/guild-key-integrity/conftest.py` declares `SEASON = 106`.
Neither directory has an `__init__.py`, so both import as top-level module
`conftest`. A *lazy* `from conftest import SEASON` inside a helper therefore
resolves through `sys.modules["conftest"]` — whichever suite got there first.

In the combined `pytest tests/unit tests/acceptance` run that is the
sqlite-backend one, so a slice-01 helper answers the raid URL for season 94
while believing it is 106. It stays green only because the counting helper
resolves the same wrong constant, so both ends agree by accident. Hoisting the
import to module scope desynchronises them and fails two scenarios **only in
the combined run**.

Latent, currently harmless, and a genuine cross-suite contamination hazard: any
future constant sharing a name across the two conftests silently takes the
other suite's value. Worth a dedicated fix (unique module names, or a shared
`tests/support/` package) rather than a per-helper workaround.

Note this became reachable only once UD-5 made the combined invocation work at
all. It was always latent; the gate that would have surfaced it had never run.

## Wave: DELIVER / [REF] The pattern behind six of the ten findings

Six of the ten items above (UD-1, UD-2, UD-3, UD-4, UD-7, UD-8, UD-9) are
defects in the acceptance suite, and five of those are one pattern:

> **a scenario declares a precondition about stored state in Gherkin, and no
> fixture supplies it.**

The failure is asymmetric and that is what makes it dangerous. A missing
precondition that raises `KeyError` gets noticed immediately. One that merely
leaves the system in its *default* state does not — the scenario runs, takes a
different code path than intended, and passes. Every instance here degraded a
comparison test into a trust-on-first-use test, which is silent by design.

The DISTILL RED gate classified all 93 scenarios by **exception type** —
`AssertionError` = RED, `ImportError` = BROKEN — and reported 172 / 0 / 0. That
check is necessary and it is not sufficient. It cannot distinguish a test that
fails because the implementation is missing from one that fails for a
correct-looking reason while asserting nothing, and it says nothing at all
about tests that PASS vacuously.

Two additional gates would have caught all five:

1. **Satisfiability** — for each scenario, is it reachable inside the slice's
   declared scope? UD-1 (a rule requiring out-of-scope modules to change) and
   UD-8 (an assertion no implementation can satisfy) are both scope/logic
   errors visible by inspection, before any code exists.
2. **Non-vacuity, by mutation** — for each scenario, break the behaviour it
   claims to assert and confirm it fails. The 03-05 crafter ran exactly this
   over ten mutations and it is what surfaced UD-9. It is cheap, it is
   mechanical, and it is the only check that catches a green test asserting
   nothing.

Recommendation for the next DISTILL wave: run mutation-based non-vacuity on
every scenario whose Gherkin contains a `Given` about stored state, before
handing off. The tooling exists — it is `git checkout --` and a loop.
