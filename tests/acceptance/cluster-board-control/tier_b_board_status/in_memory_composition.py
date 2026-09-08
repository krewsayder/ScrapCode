"""In-memory composition root for the Tier B board-status state machine.

Mandate 10: Tier A wires the shared step vocabulary through the production
composition root (real repository, real cog callbacks); Tier B wires the SAME
vocabulary through this. The step-method NAMES are the contract.

WHAT THIS CANNOT MODEL, stated because a double that hides a failure mode is
worse than no double:

  * Storage. There is no adapter here, so nothing about JSON/SQLite parity,
    the NOT NULL default, or the alembic upgrade is exercised — those are
    Tier A's `@real-io` scenarios (AC-004.3, AC-004.4, KPI-4).
  * Discord transport. `edited` / `sent` are counters, not API calls; a
    `discord.Forbidden` or a vanished channel is Tier A's ground.
  * Concurrency. One cycle at a time. The two hourly loops firing together
    (brief §2.3) is not modelled anywhere in this feature.

What it DOES model is the thing no single example can: that across every
interleaving of turn-off / turn-on / set-up / hourly-cycle, a board that is
off is never touched and never loses state.
"""
from __future__ import annotations

from dataclasses import replace

from bot.repository import BoardStatus, LiveBoardConfig

CHANNEL_ID = 777
SEASON = 106


class InMemoryComposition:
    """The live-board world, with storage and Discord replaced by counters."""

    def __init__(self) -> None:
        self.config: LiveBoardConfig | None = None
        self.edited: int = 0
        self.sent: int = 0
        self.replies: list[str] = []

    # -- shared step vocabulary (same names Tier A invokes) -----------------

    def Given_a_configured_cluster_leaderboard(self, *, season: int = SEASON) -> None:
        self.config = LiveBoardConfig(
            channel_id=CHANNEL_ID,
            messages={"Legendary_0": 999},
            season=season,
            board_status=BoardStatus.ACTIVE,
        )

    def When_an_officer_turns_the_board_off(self) -> None:
        self._set_status(BoardStatus.DISABLED)

    def When_an_officer_turns_the_board_on(self) -> None:
        self._set_status(BoardStatus.ACTIVE)

    def When_an_officer_sets_the_board_up_again(self, *, season: int = SEASON) -> None:
        """Setup rebuilds the config from scratch, so it always comes back ON.

        Carrying a stale pause across setup would hand the officer a
        freshly-posted set of messages that then never update — a silent no-op
        wearing a success message.
        """
        self.config = LiveBoardConfig(
            channel_id=CHANNEL_ID,
            messages={"Legendary_0": 999},
            season=season,
            board_status=BoardStatus.ACTIVE,
        )
        self.sent += 1

    def When_the_hourly_cycle_runs(self, *, season: int = SEASON) -> None:
        if self.config is None:
            return
        if not self.config.is_enabled:
            return  # THE claim — see the invariant in the state machine
        if self.config.season == season:
            self.edited += 1
        else:
            self.sent += 1
            self.config = replace(self.config, season=season)

    def Then_the_board_is(self, status: BoardStatus) -> None:
        assert self.config is not None
        assert self.config.board_status is status

    # -- the Universe ------------------------------------------------------

    def capture_universe(self) -> dict:
        """Port-exposed observable names, never internal fields.

        A universe naming `self.config` or `_status` couples the property to a
        private attribute and reds on a rename — a refactoring-hostile signal.
        These five are what an officer or the hourly cycle can actually see.
        """
        return {
            "board.status": None if self.config is None else self.config.board_status,
            "board.channel_id": None if self.config is None else self.config.channel_id,
            "board.messages": None if self.config is None else dict(self.config.messages),
            "board.season": None if self.config is None else self.config.season,
            "discord.calls": (self.edited, self.sent),
        }

    def _set_status(self, status: BoardStatus) -> None:
        if self.config is None:
            self.replies.append("refused: no board configured")
            return
        if self.config.board_status is status:
            self.replies.append("already in that state")
            return  # no write — see AC-001.7
        self.config = replace(self.config, board_status=status)
