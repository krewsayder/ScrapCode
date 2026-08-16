"""In-memory composition root for the Tier B live-board state machine.

Mandate 10. Tier A drives the production composition root through
`_refresh_live_leaderboards`; Tier B drives THE SAME step vocabulary through
these in-memory doubles, so the journey can be explored across every
interleaving rather than the handful somebody enumerated.

WHY TIER B AT ALL FOR THIS FEATURE. The gate is "the MODEL is a state machine",
not "the user perceives states". The live board is one: its state is
(remembered message ids × registered tiers × stored season), and the commands
are refresh / register a tier / roll the season over / refuse a send. The
invariant that has to hold across every ordering of those — exactly one message
per tier, never a duplicate, never a delete — is not expressible as an example.

The parse rule, by contrast, is a pure function of two fields. It gets
parametrized examples in Tier A and no state machine, because it has no state.

WHAT THIS CANNOT MODEL. Real Discord. A send here either succeeds or raises;
it cannot succeed at Discord and then be lost on the way back, which is the
failure that actually duplicates a board. `SendFailure.SENT_THEN_PERSIST_FAILED`
in Tier A simulates the consequence; only the production run in Slice 03's
dogfood moment covers the cause.

__SCAFFOLD__: the board's behaviour is the thing under construction. Every
method that would implement reconciliation raises until DELIVER.
"""
from __future__ import annotations

from dataclasses import dataclass, field

__SCAFFOLD__ = True

_RED = "Not yet implemented — RED scaffold (in_memory_composition)"


@dataclass
class InMemoryLiveBoard:
    """One always-on leaderboard channel, as state plus four commands.

    `messages` maps a stored tier value to a message id — the same shape
    `live_leaderboards[key]["messages"]` has in production, deliberately, so
    the invariants asserted here are invariants about the real structure and
    not about a convenient test-only one.

    `sent_log` is append-only and never pruned. The duplicate this feature
    fears is not "two ids in the map" — the map cannot hold two — it is TWO
    SENDS for one tier, where the second overwrites the first id and leaves an
    orphan message visible in the channel forever. Only a log can see it.
    """

    season: int = 107
    messages: dict[str, int] = field(default_factory=dict)
    registered: tuple[str, ...] = ()
    sent_log: list[str] = field(default_factory=list)
    deleted_log: list[str] = field(default_factory=list)
    refuse_sends: bool = False
    _next_id: int = 1000

    # -- commands ---------------------------------------------------------

    def register_tier(self, tier_key: str) -> None:
        """A tier joins the registry — the Slice 02 event, from Slice 03's view."""
        if tier_key not in self.registered:
            self.registered = self.registered + (tier_key,)

    def roll_season(self, new_season: int) -> None:
        """The season advances. Production rewrites `messages` WHOLESALE here."""
        raise AssertionError(_RED)

    def refresh(self) -> None:
        """One hourly refresh: rollover if the season moved, else reconcile."""
        raise AssertionError(_RED)

    # -- observables (the Universe) ---------------------------------------

    def capture_universe(self) -> dict:
        """Port-exposed observable names, never internal fields.

        These are the four things a human can check by looking at the channel
        and the stored config. A universe naming `_next_id` or the dataclass
        internals would red on a rename, which is a refactoring-hostile signal
        rather than a correctness one.
        """
        return {
            "board.messages.keys": tuple(sorted(self.messages)),
            "board.sends_per_tier": {
                k: self.sent_log.count(k) for k in set(self.sent_log)
            },
            "board.deleted": tuple(sorted(self.deleted_log)),
            "board.season": self.season,
        }


class InMemoryComposition:
    """The Tier B wiring. Same step vocabulary as Tier A, different adapters."""

    def __init__(self) -> None:
        self.board = InMemoryLiveBoard()

    def capture_universe(self) -> dict:
        return self.board.capture_universe()
