# Slice 03 — Live boards grow a message for a new tier

**Feature:** `dynamic-tier-registry` · **Story:** US-005
**Estimate:** ~0.75 day · **Order:** 3 of 4

## Goal

Make the always-on leaderboard channel add a message for a newly registered
tier on the next hourly refresh, instead of waiting for a season rollover.

## The behaviour being replaced

`_refresh_live_leaderboards` skips any tier lacking a stored `message_id`
([tasks_cog.py:663-666](../../../../bot/cogs/tasks_cog.py#L663)):

```python
for tier in TIER_CHOICES:
    msg_id = message_ids.get(tier.value)
    if not msg_id:
        continue
```

The rollover branch ([tasks_cog.py:684](../../../../bot/cogs/tasks_cog.py#L684))
*does* send a full set. So a new tier appears weeks later, on its own, with no
action taken — worse than either consistent alternative, because it looks like
a bug that fixed itself and teaches the operator nothing about when to expect it.

## IN scope

- Same-season reconciliation: any registry tier without a stored `message_id`
  gets a message sent and its ID persisted, in registry order.
- Idempotence keyed on tier value.
- Partial-failure handling: a `Forbidden` or rate-limited send retains existing
  IDs unchanged and retries next cycle without duplicating.
- Both live config shapes — `guild:{id}` and `cluster`.

## OUT of scope

- **Deleting messages for tiers absent from the registry.** Reconciliation is
  additive only (D5). A vanished tier's board is frozen in place.
- Reordering or rewriting existing messages. A tier inserted between two
  existing ones appears at the bottom of the channel; Discord message order is
  chronological and rewriting history to fix that is not worth it.
- Season rollover logic, beyond ensuring it and reconciliation do not both fire.
- The `/config_leaderboards` admin surface.

## Learning hypothesis

**If reconciliation produces duplicate or churning messages, it disproves the
claim that `messages` can be safely extended in place** — meaning rollover is
the only safe creation point and the current skip-if-absent behaviour is load
bearing rather than an oversight.

**If it succeeds**, it confirms that `messages` is a plain additive map, which
is also the assumption behind D3's freezing of stored keys.

The risk is real: `_refresh_live_leaderboards` runs hourly against live Discord
messages, and `config["messages"]` is rewritten wholesale on the rollover path
([tasks_cog.py:701](../../../../bot/cogs/tasks_cog.py#L701)). A reconciliation
that races that rewrite duplicates the whole board, publicly.

## Acceptance criteria

AC-005.1 – AC-005.7. See [feature-delta.md](../feature-delta.md).

Load-bearing:

- **AC-005.2** — a second refresh sends nothing. Idempotence is the single
  property that makes this safe to run hourly.
- **AC-005.6** — rollover and a new tier in the same cycle produce exactly one
  set of messages. This is the race that duplicates a live board.
- **AC-005.3** — a failed send leaves stored IDs unchanged and does not
  duplicate on retry. Partial failure is the normal case under rate limiting.

## Production data requirement

Acceptance runs against a real live leaderboard config in the operator's
Discord server, refreshing on the real hourly cadence with real Mythic 3 rows
behind it. Duplicate-message and ordering behaviour cannot be honestly
verified against a mocked channel, because the failure mode is a real Discord
send succeeding after the local state believed it had failed.

## Dogfood moment (same day)

Operator watches one hourly refresh: the live channel gains exactly one
Mythic 3 message. Then watches the next: it gains none. Two consecutive
refreshes is the whole test, and both happen within the same day.

## Dependencies

- **Requires:** Slice 02, for the registry that reconciliation iterates.
- **Blocks:** nothing.

## Reference class

One function modified, one new branch, plus idempotence and failure handling.
Comparable to the season-rollover fall-through work in `guild-key-integrity`
Slice 03 (`OUT-5`), which was similar in shape: a loop over guilds that had to
keep working when one entry was unusable.

## Risks

- **Public duplication.** The failure mode is visible to every guild member in
  the channel, not just the operator. AC-005.6 and AC-005.2 exist for this;
  they should be the first tests written, not the last.
- **Config write during a partially failed send.** Persisting a partial
  `messages` map that omits an already-sent message causes a duplicate on the
  next cycle. AC-005.3 requires the retained-unchanged behaviour rather than a
  best-effort partial write.

## Pre-slice SPIKE

Not required. The seam is one function whose behaviour is fully readable, and
the rollover branch already demonstrates that sending a full set works.
