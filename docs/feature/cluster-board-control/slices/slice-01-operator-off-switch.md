# Slice 01 — The operator's off switch for the cluster board

**Feature:** `cluster-board-control` · **Stories:** US-001, US-002, US-003
**Precursor:** US-004 `@infrastructure` (below — lands first, as a commit)
**Effort:** ~1 day including the precursor · **Reference class:** the
`guild-key-integrity` Slice 02 command (`/update_guild_key`) — one
officer-tier slash command, one persisted status field, one `/view_config`
surface.

---

## Goal

An officer can stop the cluster leaderboard publishing, and start it
again, without losing the board or the season's posted messages.

---

## Status — shipped out of process, non-conforming

This slice's user-visible behaviour **is already merged** on
`fix/startup-probe-module-shadowing`. It was written on 2026-09-08 before
any wave artifact existed (see feature-delta § Retrofit Origin).

What shipped and conforms:

- `/disable_cluster_leaderboard`, `/enable_cluster_leaderboard` — officer
  tier, no parameters, shared `_set_cluster_board_state` handler
- The refresh-loop skip, leaving messages untouched
- The `/view_config config:leaderboards` status line
- `/set_live_cluster_leaderboard` re-enabling on setup
- Alembic `0005`, README, `/scrapcode_help`
- 13 tests, 3 mutants killed

What does **not** conform — and is what the precursor exists to fix:

| Shipped | Required by D3 |
|---|---|
| `enabled: bool`, omitted from the config when true | explicit enum-valued status, always present |
| raw `dict` across the port | frozen dataclass, as `GuildBinding` |
| `cfg.get("enabled", True)` — default inferred at each read | default materialised on load by both adapters |

The slice is **not done** until the precursor lands.

---

## Precursor commit — US-004 `@infrastructure`

Not a slice. The slice-composition gate forbids shipping a slice made
only of `@infrastructure` stories, and prescribes landing the work as a
precursor commit instead. It is also the right order on its own merits:
the representation should be settled before commands are built on it.

**Scope of the precursor**

1. Replace `enabled: bool` with an enum-valued status field
   (`active` / `disabled`), modelled on `KeyStatus`.
2. Introduce the frozen config dataclass at the port so cogs stop
   handling raw dicts — the `GuildBinding` boundary, applied here.
3. Normalise the default on load in **both** adapters, so absence
   becomes a materialised value rather than a per-read `.get(k, default)`.
4. Amend the exact-equality literal at
   `tests/acceptance/sqlite-backend/test_repository_contract.py:98-100`
   to carry the status. The contract genuinely changed; amending the test
   to match an intended change is correct, and is a different act from
   bending the production representation to avoid amending it — which is
   what produced the shipped shape.
5. Decide `0005`'s fate: amend in place if still undeployed, else chain
   `0006`. DESIGN owns this (feature-delta § Handoff).

**Blast radius** (measured, not estimated): 4 config-field read sites and
2 write sites across `admin_cog` and `tasks_cog`; both repository
adapters; `bot/db/migrations_json_to_sqlite.py`.

---

## IN scope

- Persisted, enum-valued on/off status for a live board config
- `/disable_cluster_leaderboard` + `/enable_cluster_leaderboard`, officer tier
- Refresh loop honours the status for **every** scope key (D5)
- Status line in `/view_config config:leaderboards`
- `/set_live_cluster_leaderboard` re-enables
- Alembic revision; every pre-existing board upgrades to `active`
- The frozen config dataclass at the port (precursor)

## OUT of scope

- Auto-flagging the silent freeze when no key can answer the season (D1)
- Guild-scoped commands — storage covers it, commands not asked for (D5)
- Any banner or edit on a paused board (D2)
- Flagging quarantined guilds' rows inside the merged cluster board —
  operator ruled the board should cover the whole cluster

---

## Learning hypothesis

**This slice disproves D2 if it fails.**

D2 commits to leaving a paused board's messages completely untouched. If
that is right, `/view_config` is a sufficient signal and the officer is
never misled by a frozen board. If it is wrong, the failure looks like:
an officer reads stale numbers off a paused board and acts on them,
because the channel gave no indication and nobody thinks to run
`/view_config` first.

Confirmed if: the operator pauses the real cluster board, and the
combination of the command's own reply and the `/view_config` status line
is enough that nobody mistakes the frozen board for a live one.

Disproved if: the banner that D2 rejected turns out to be necessary
after all — in which case D2 reopens and the "edit once, add a banner"
option is reconsidered.

---

## Acceptance criteria

Full text in `feature-delta.md` § User Stories. Summary:

| AC | Claim |
|---|---|
| AC-001.1 | Disable persists the status; reply names channel + the way back |
| AC-001.2 | **Zero** Discord calls against a paused board per cycle |
| AC-001.3 | The config is not removed — pause ≠ teardown |
| AC-001.4 | `channel_id`, `messages`, `season` unchanged by a pause |
| AC-001.5 | The pause survives a process restart |
| AC-001.6 | No board configured → refuse, name the setup command, write nothing |
| AC-001.7 | No-op flip → report it, do not call `save_live_leaderboards` |
| AC-001.8 | Below officer tier → refused |
| AC-002.1 | Enable persists `active`; reply names the channel |
| AC-002.2 | Same season on resume → edit in place, no new messages |
| AC-002.3 | Rolled-over season on resume → fresh set; old set frozen |
| AC-002.4 | Already running → report, write nothing |
| AC-002.5 | `/set_live_cluster_leaderboard` returns the board to active |
| AC-003.1 | Paused board's `/view_config` field says so |
| AC-003.2 | Running board's field says so |
| AC-003.3 | The status line is never omitted, in either state |
| AC-004.1 | Cogs receive a dataclass, not a raw dict |
| AC-004.2 | Status values come from a named enum |
| AC-004.3 | Missing status materialises to `active` in **both** adapters |
| AC-004.4 | JSON and SQLite round-trips are equal |
| AC-004.5 | Exactly one predicate decides the skip |
| AC-004.6 | A `guild:` board honours the status identically |

---

## Dependencies

| On | Why |
|---|---|
| `sqlite-backend` | the `live_leaderboards` table and the ABC |
| `guild-key-integrity` | supplies the pattern D3 conforms to |
| Alembic head `0004` | the revision chains from it |

No dependency on the unresolved silent-freeze gap — D1 keeps them apart
deliberately.

---

## Pre-slice SPIKE

**Not required.** Uncertainty is low: the table, the ABC, the refresh loop
and the tier decorator all exist and are exercised by two shipped
features. The one genuinely uncertain item — whether the parity contract
survives an always-present status field — was settled during the shipped
implementation, in the opposite direction, and D3 records why the
`GuildBinding` answer is preferred.

---

## Dogfood moment

Same day: the operator pauses the live cluster board on the production
VM, confirms in the channel that the messages have not moved, runs
`/view_config config:leaderboards` to see the status, waits one hourly
cycle to confirm nothing was edited, then resumes.

Production data, not synthetic — the real board, the real season.
