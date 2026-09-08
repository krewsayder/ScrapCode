"""Domain vocabulary and test doubles for the `cluster-board-control` suite.

NAMED `board_domain_types`, NOT `domain_types`, and that is load-bearing.

pytest imports rootless test directories by prepending each one to `sys.path`,
so a bare module name is shared across every acceptance suite in the run and
the first one imported wins. The suite directories are hyphenated
(`cluster-board-control`), so they cannot be packages and the collision cannot
be namespaced away.

`cluster-board-control` sorts BEFORE `guild-key-integrity`, so a
`domain_types.py` here shadowed theirs and broke their conftest during
`pytest tests/unit tests/acceptance` — the project's declared gate. Recorded
as the second occurrence of the hazard `guild-key-integrity`'s
`distill/upstream-issues.md` UD-10 deferred.

The same reasoning is why the constants and doubles below live HERE rather
than in `conftest.py`: `from conftest import X` collides identically, and
`tests/acceptance/sqlite-backend/test_atomicity_and_probe.py` already does it.
`conftest.py` in this suite holds fixtures only — pytest injects those by
name, so nothing ever imports it.

Mandate-12 criterion (1): every domain noun in the `.feature` file has a typed
representation reachable from here, so the scenario text, the executable specs
and the Tier B state machine all name the same things the same way.

`BoardStatus` and `LiveBoardConfig` are RE-EXPORTED from production, not
re-declared. A test-side copy of an enum compares unequal to the production one
under `is`, and the copy that drifts is always the one nobody runs in
production — so there is exactly one definition and the suite imports it. Same
discipline as `guild-key-integrity/domain_types.py`.

Everything defined locally below is genuinely test-only: the two commands, the
two storage backends, and the scope kinds.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# SSOT: defined in production, reused here (Mandate-12 criterion 2).
# RED scaffold at DISTILL time — DELIVER replaces the body, not this import.
from bot.repository import BoardStatus, LiveBoardConfig  # noqa: F401


class BoardCommand(Enum):
    """The two officer-facing commands that flip the switch.

    Carries the command name AND the status it drives the board to, so a
    parametrized scenario states the transition once instead of pairing a
    command string with an expected status at every call site.
    """

    TURN_OFF = ("disable_cluster_leaderboard", BoardStatus.DISABLED)
    TURN_ON = ("enable_cluster_leaderboard", BoardStatus.ACTIVE)

    @property
    def command_name(self) -> str:
        return self.value[0]

    @property
    def drives_to(self) -> BoardStatus:
        return self.value[1]

    @property
    def no_op_when(self) -> BoardStatus:
        """The starting status that makes this command a no-op.

        Identical to `drives_to` — asking for the state you are already in.
        Named separately because the scenario reads about a no-op, not about
        a destination, and the two meanings drift the day a third status
        exists.
        """
        return self.drives_to


class Backend(Enum):
    """The two storage adapters the parity scenario compares.

    Both are REAL (Infrastructure Policy — real SQLite in tmp_path, real JSON
    tree in tmp_path). Neither is a double: ADR-006 D9's rollback path is only
    protected while it stays exercised.
    """

    JSON = "json"
    SQLITE = "sqlite"


class ScopeKind(Enum):
    """The two scope keys a live board config can carry.

    `board_status` is scope-agnostic by construction (ADR-009 DDD-5) even
    though only the cluster board has commands driving it. Parametrizing over
    this enum is what stops the guild-scoped commands, when they arrive,
    finding a switch that silently only ever worked for one key.
    """

    CLUSTER = "cluster"
    GUILD = "guild:neuro"

    @property
    def scope_key(self) -> str:
        return self.value

    @property
    def guild_id(self) -> str | None:
        return None if self is ScopeKind.CLUSTER else self.value.split(":", 1)[1]


@dataclass(frozen=True)
class DiscordCalls:
    """What a refresh pass did to a channel.

    KPI-1 is `edited == 0 and sent == 0` for a paused board. Both counts are
    carried together because "nothing was edited" alone is satisfied by a pass
    that DELETED the messages and re-sent them, which is the failure D2 is
    written against.
    """

    edited: tuple[int, ...] = ()
    sent: tuple[str, ...] = ()

    @property
    def is_silent(self) -> bool:
        return not self.edited and not self.sent


# The refresh-loop decision sites AC-004.5 quantifies over.
#
# Declared as data rather than asserted in prose so the AST scan has a set to
# compare against, in the same spirit as `guild-key-integrity`'s
# `KeyConsumptionSite`. The invariant is that exactly ONE production module
# compares a board's status literal — every other site reads `is_enabled`.
STATUS_COMPARISON_OWNER = "bot/repository.py"

# The structured record a board's state change must leave behind.
#
# Dotted, like every other event this bot emits (`auto_update.cycle`,
# `guild.key.quarantined`). The name is asserted rather than described because
# the operator's grep and this test have to break together — a renamed event
# that only breaks the grep is a dashboard that silently stops returning rows.
#
# Emitted on CHANGE, never per cycle: an hourly record would be ~720 entries a
# month for a single paused board, and a log nobody can skim is a log nobody
# reads. Operator decision, 2026-09-08.
BOARD_STATUS_CHANGED_EVENT = "live_board.status.changed"

# Modules that READ whether a board publishes, but must never COMPARE the
# literal themselves.
STATUS_READER_MODULES: tuple[str, ...] = (
    "bot/cogs/tasks_cog.py",
    "bot/cogs/admin_cog.py",
)


# ---------------------------------------------------------------------------
# Constants and doubles.
#
# Here rather than in conftest.py — see the module docstring. `conftest.py`
# holds fixtures, which pytest injects by name; nothing imports it, so nothing
# collides.
# ---------------------------------------------------------------------------

SERVER_ID = 4242
CHANNEL_ID = 777
SEASON = 106
EARLIER_SEASON = 105
FERNET_KEY = "0" * 43 + "="  # a syntactically valid Fernet key for tests


class FakeMessage:
    def __init__(self, message_id: int, channel: "FakeChannel") -> None:
        self.id = message_id
        self._channel = channel

    async def edit(self, **kwargs):
        self._channel.edited.append(self.id)


class FakeChannel:
    """Captures every Discord call so "nothing was posted" is assertable.

    KPI-1 needs BOTH counts. A pass that deleted the messages and re-sent them
    would satisfy "nothing was edited" while doing exactly what DISCUSS D2
    forbids, so `sent` is recorded beside `edited` rather than instead of it.
    """

    def __init__(self, channel_id: int = CHANNEL_ID) -> None:
        self.id = channel_id
        self.mention = f"<#{channel_id}>"
        self.edited: list[int] = []
        self.sent: list[str] = []
        self._next_id = 1000

    async def fetch_message(self, message_id: int):
        return FakeMessage(message_id, self)

    async def send(self, content: str = "", **kwargs):
        self.sent.append(content)
        self._next_id += 1
        return FakeMessage(self._next_id, self)

    @property
    def calls(self) -> DiscordCalls:
        """What this pass did, as the typed record the ACs assert on."""
        return DiscordCalls(edited=tuple(self.edited), sent=tuple(self.sent))


class FakeUser:
    """`require_tier` reads the caller's roles.

    `is_officer=False` produces a member holding no tier and no Discord
    administrator bit — the precondition for AC-001.8, which the shipped unit
    tests never exercised because they only ever built an administrator.
    """

    def __init__(self, *, is_officer: bool = True) -> None:
        self.id = 1
        self.roles: list = []
        self.guild_permissions = type(
            "_Perms", (), {"administrator": bool(is_officer)}
        )()


class FakeResponse:
    async def defer(self, *args, **kwargs) -> None:
        return None

    async def send_message(self, content: str = "", **kwargs) -> None:
        return None


class FakeFollowup:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.embeds: list = []

    async def send(self, content: str = "", **kwargs) -> None:
        if content:
            self.messages.append(content)
        embed = kwargs.get("embed")
        if embed is not None:
            self.embeds.append(embed)


class FakeInteraction:
    def __init__(self, *, is_officer: bool = True) -> None:
        self.guild_id = SERVER_ID
        self.user = FakeUser(is_officer=is_officer)
        self.response = FakeResponse()
        self.followup = FakeFollowup()

    @property
    def reply(self) -> str:
        return self.followup.messages[-1] if self.followup.messages else ""

    @property
    def embed(self):
        return self.followup.embeds[-1] if self.followup.embeds else None


class FakeBot:
    def __init__(self, channel: FakeChannel) -> None:
        self._channel = channel

    def get_channel(self, channel_id: int):
        return self._channel if channel_id == self._channel.id else None


def alembic_config(db_path):
    """Real alembic config pointed at a tmp_path database.

    Lives here rather than in `conftest.py` because the migration scenario
    imports it directly, and `from conftest import ...` collides across suites
    — see the module docstring. `conftest.py` uses it too, importing from
    here, so there is one definition.
    """
    from pathlib import Path

    from alembic.config import Config

    import bot.db

    db_pkg = Path(bot.db.__file__).parent
    cfg = Config(str(db_pkg / "alembic.ini"))
    cfg.set_main_option("script_location", str(db_pkg / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg
