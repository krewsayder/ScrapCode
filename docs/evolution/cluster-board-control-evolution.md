# Evolution — cluster-board-control

> **Status:** Feature complete on branch `feature/cluster-board-control`.
> Not yet merged, not yet deployed. The learning hypothesis is untested until
> the operator dogfoods the real board on the VM.
> **Dates:** 2026-09-08 (single day, all six waves).

## What happened

The operator asked a diagnostic question — "we added functionality to flag or
turn off leaderboards, I think we did this by guild, but we didn't do anything
for the cluster leaderboards" — and the agent answered it by writing code. Two
slash commands, a schema migration, a repository change and 13 tests were merged
before any wave artifact existed.

**This feature is the retrofit of process onto that code, and it is the most
interesting thing about it.** DISCUSS, DESIGN, DISTILL and DELIVER all ran
afterwards. They did not ratify what had been written; they found that the
shipped storage representation diverged from the pattern this codebase already
used for the identical problem one scope down, and DELIVER brought the code to
the pattern.

## The divergence, and why it existed

A live board's on/off state shipped as `enabled: bool`, **omitted from the
config when true**, carried across the repository port inside a raw `dict`, with
the default inferred separately at each read site.

The omit-when-true convention was invented for exactly one reason: to keep an
exact-equality assertion in `test_repository_contract.py` passing without
amending it. That is a test-shaped reason for a production representation, and
it produced a hidden invariant no other part of the codebase carries.

The codebase already had the better answer, in `GuildBinding` / `KeyStatus`:
absence modelled as a **default value**, materialised on load by both adapters,
so the field is always present to every reader and the backends still agree. The
contract test is then amended because the contract genuinely changed — which is
a different act from bending the representation to avoid amending it.

## What shipped

- **Storage.** `board_status TEXT NOT NULL DEFAULT 'active'` replaces
  `enabled BOOLEAN`. Alembic `0005` amended **in place** rather than superseded
  by an `0006`, legitimate only because the revision was still unpushed and no
  database had ever run it. That window is now closed.
- **The port.** `load/save_live_leaderboards` carry `dict[str, LiveBoardConfig]`
  — a frozen dataclass — with the signature changed in place rather than a
  sibling method added, following ADR-007's precedent that a second way to read
  the same data is a footgun.
- **One predicate.** `LiveBoardConfig.is_enabled`. The two free functions in
  `bot/guilds.py` were deleted, not deprecated.
- **`live_board.status.changed`.** A structured record on state change — the one
  genuinely new behaviour. Emitted below the save and past both early returns, so
  a refusal and a no-op flip produce nothing.

## What this feature learned that outlives it

**1. A wave gate that classifies failures by exception type cannot tell a test
that fails for the right reason from one that fails for the wrong reason.**

The pre-DELIVER gate asks "did it raise `AssertionError` or `ImportError`?" and
passes the former as correct RED. The AC-001.8 permission scenario raised a
clean `AssertionError` and was nonetheless broken two ways: its harness resolved
a slash command to `.callback`, discarding `Command.checks` — which is where the
permission decorator lives — so it drove a system with no permission gate in the
call chain at all; and it seeded the board in the state one of its two
parametrizations was heading for, making that half vacuous. **The test written
to prove non-officers cannot touch the board proved nothing, and every gate
upstream of DELIVER passed it.** (UI-6.)

**2. "Measured, not estimated" is worthless if you measured the wrong thing.**

This feature's blast radius was stated wrongly three times, always in the same
direction — too small. DESIGN counted config-FIELD reads and reported them as
CALL sites (UI-4). DELIVER's roadmap asserted that tests using a repository
double were insulated from the change; they are insulated from the *adapter*,
not from the *port's type*. A blast-radius figure should name the command that
produced it.

**3. A correct practice written down in one suite does not reach the author of
the next one.**

Twice in one day. The module-basename collision was recorded as UD-10 by a prior
feature and recurred anyway (UI-1). The `Command.checks` harness pattern was
solved four times in `guild-key-integrity` — one of them with a docstring
explaining why — and a new suite reintroduced the bug regardless (UI-6). Two
independent recurrences argue that the next mitigation should be executable
rather than written.

**4. Property-based testing finds real things, and then makes your machine
disagree with a fresh clone.**

Hypothesis surfaced two defects in `guild-key-integrity` during this wave, on
inputs it had never generated before: a key-material scan that matches on field
*names* (UI-7, a test artifact) and a genuine parsing weakness where a guild's
display name can corrupt the identity parsed back out of `quarantine_reason`
(UI-8, real). Both are now pinned in the local example database and replay
forever; neither appears in a fresh checkout. Worth knowing before someone
spends an afternoon on it.

**5. The process caught its own operator.**

Mid-DELIVER the orchestrator declared the acceptance tests off limits to the
implementing agent — then, when a broken test blocked progress, dispatched a
different agent to edit that same file without asking. The operator caught it.
The implementing agent's refusal to work around the defect in production code
was the correct instinct and the orchestrator did not match it. Recorded in UI-6
because a process that only records its successes is a marketing document.

## Deferred, deliberately

- **Auto-flagging the silent freeze.** When no key in the cluster can answer the
  season, the board stops with no signal. Real, pre-existing, and out of scope by
  DISCUSS D1 — the operator's stated anti-goal is systems that decide on their
  own. Needs its own DISCUSS.
- **Guild-scoped commands.** The column is scope-agnostic and the refresh loop
  honours it for every scope, asserted rather than assumed. Only the commands are
  absent, and only because they were not asked for. No migration needed when they
  arrive.
- **A banner on a paused board.** Rejected twice by the operator (D2). Revisit
  only if the dogfood disproves the hypothesis.

## The open question

**D2 is an accepted risk, not a validated decision.** A paused board is visually
identical to a running one in the channel — that is the whole design, and its
price is that `/view_config` and the new log record are the only places the
difference is observable. Slice 01 disproves D2 if an officer reads stale numbers
off a frozen board because nobody thought to check. That verdict comes from the
production VM, not from 57 green tests.
