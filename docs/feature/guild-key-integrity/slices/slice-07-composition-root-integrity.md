# Slice 07 — Composition-root integrity

**Feature:** `guild-key-integrity` · **Remediates:** ADR-006 D8, ADR-006 D9, DDD-3
**Estimate:** ~0.5 day (≤3 h crafter dispatch) · **Order:** 4th of the remediation set

## Goal

The bot refuses to start — loudly — in any configuration where the quarantine
guard is silently inert, and the enforcement of DDD-3 no longer depends on a
scan that cannot see the module holding the keys.

## Learning hypothesis

**Disproves** "enforcement is on in production" **if** a reachable startup
configuration leaves it off without stopping the bot.

**Confirms** that the guarantee is a property of the deployed system rather
than of the code read in isolation. Every other slice is conditional on this
one: a perfect chokepoint that is bypassed by the composition root is not a
chokepoint.

## IN scope

- **Wire the ADR-006 D8 startup probe.** D8 states the probe "runs at
  composition time and MUST succeed before the bot starts".
  `SqlAlchemyClusterRepository.probe()` exists
  ([`repository_sqlalchemy.py:782`](../../../../bot/repository_sqlalchemy.py#L782))
  and has **no production caller** — `grep '\.probe()'` finds only tests. Call
  it at startup and fail hard on failure.
- **Distinguish deliberate rollback from misconfiguration.**
  [`guilds.py:39-58`](../../../../bot/guilds.py#L39) routes to
  `JsonClusterRepository` in three cases. Only the first is a decision:
  - `SCRAPCODE_REPO_BACKEND=json` → **keep** today's behaviour. The inert
    guard is documented, reasoned, and correct for a rollback under time
    pressure. Make it loud at startup, not just a WARNING mid-log.
  - `backend=sqlite` + missing/empty `SCRAPCODE_DB_KEY` → **refuse to start.**
    Nobody chose this; it is a broken deploy wearing a working deploy's
    clothes.
  - `backend=sqlite` + `SCRAPCODE_DB_PATH` gone → **refuse to start.**
- **Validate the key, not its truthiness.** `if not fernet_key` accepts any
  non-empty string, so a CRLF-mangled or truncated `SCRAPCODE_DB_KEY` passes,
  the SQLite repo is constructed, and the first `decrypt_api_key` fails
  mid-cycle. A trailing `\r` from a Windows-edited `.env` has already broken
  auth on this VM once. The startup probe is the gate that catches it.
- **Extend the AST chokepoint scan.**
  [`test_architecture_chokepoint.py:37`](../../../../tests/acceptance/guild-key-integrity/test_architecture_chokepoint.py#L37)
  sets `GUARDED_TREES = ("bot/cogs", "bot/services")`. Unscanned:
  [`bot/guilds.py:79`](../../../../bot/guilds.py#L79) — which puts the
  plaintext key into a dict handed to every cog and is *not* in
  `SANCTIONED_KEY_READERS` — plus `bot/tracker.py`, `bot/embeds.py`,
  `bot/models.py`, `bot/db/`, `main.py`, and **any new top-level module**.
  Scan `bot/` wholesale with an explicit exemption list.

## OUT of scope

- Encoding the chokepoint rule as an import-linter contract. It is not
  expressible: the bypass shape is a dict-key read through an explicitly
  permitted import, so no contract tuning reaches it. Recorded so the gap is
  known rather than re-litigated.
- Removing the plaintext key from `load_guilds`' return dict. The right fix
  (hand out a `GuildSnapshot`/handle, never the key) is a wider refactor
  touching every cog — worth doing, but its own slice.
- Making the JSON adapter implement real bindings. DDD-4 deliberately gives
  the binding store no JSON representation; that decision stands.

## Required behaviour (proposed AC-010.x — tests owned by `@nw-acceptance-designer`)

1. `backend=sqlite`, `SCRAPCODE_DB_KEY` unset → the bot **does not start**, and
   the failure names the missing variable.
2. `backend=sqlite`, `SCRAPCODE_DB_KEY` set to a malformed value → the bot
   does not start; the probe reports it, not a mid-cycle traceback.
3. `backend=sqlite`, `SCRAPCODE_DB_PATH` missing → the bot does not start.
4. `backend=json` → the bot **does** start, and announces at startup that the
   guild-key guard is inert (not merely a WARNING among many).
5. The AST scan fails when a new top-level module reads `api_key` directly.
6. `bot/guilds.py` is either in `SANCTIONED_KEY_READERS` with a stated reason,
   or refactored out of the scan's way.

## Production-data criterion

Not synthetic. Run against the real `.env` on the VM with `SCRAPCODE_DB_KEY`
deliberately CRLF-mangled (`printf 'KEY=abc\r\n'`) — the exact failure recorded
on this machine — and assert the bot refuses to start rather than coming up
with enforcement silently off.

## Dogfood moment (same day)

Operator comments out `SCRAPCODE_DB_KEY` and restarts. Today the bot comes up,
logs one WARNING, and runs an hour later with quarantine fully inert — alerts
firing while contaminated data is written. After this slice it refuses to
start and says why.

## Dependencies

None. Independent of 04/05/06 and can run in parallel.

## Reference class

`sqlite-backend` Slice 02 — the probe was *built* in that slice and its four
health checks are already specified and tested. This slice is wiring, not
design: the hard part is already done and was simply never called.

## Pre-slice SPIKE

**Not required.** The fallback behaviour was reproduced end-to-end by two
independent reviewers: with a quarantined guild in SQLite and no `DB_KEY`,
`active_key` returns the drifted key and `quarantine()` becomes a no-op.

## Operator decision — 2026-08-02

**A bad `.env` takes the bot down.** Hard stop, not degraded-but-running. The
"boot but disable `auto_update`" fallback was considered and **rejected**: a
bot that is up but not ingesting looks healthy to everyone except the person
reading the log, which is the same reassuring-but-false signal this feature
exists to remove.

## Risk

**Refusing to start is an availability trade.** A config that previously
degraded now halts the bot, so a typo in `.env` becomes a visible outage
instead of a silent one. That is the accepted trade per the decision above.

The obligation it creates: the startup failure message must name the **exact
variable** and the **exact fix**, because it is now the only thing standing
between the operator and a bot that will not come up. A generic "probe failed"
here would convert a five-second fix into an incident. Test the message text,
not just the exit code.
