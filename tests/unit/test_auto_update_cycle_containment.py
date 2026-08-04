"""Property tests for the hourly cycle's per-server containment (slice 04, 06-03).

WHY-NEW-FILE: tests/unit/test_auto_update_cycle_containment.py
  CLOSEST-EXISTING: tests/unit/test_guild_body_totality.py
  EXTENSION-COST: every property there is quantified over a decoded JSON value
    handed to `parse_guild_snapshot`, a synchronous pure classifier with no
    Discord objects in sight; hosting these would drag a cog, a fake bot, a
    fake channel and an `asyncio.run` per example into a module whose stated
    universe is a single parse's observable slots.
  PARALLEL-RATIONALE: the two modules sit on opposite sides of the boundary the
    architecture test enforces — `test_guild_body_totality` drives
    `bot/services/tacticus/guild_client.py` (the vendor adapter), this drives
    `bot/cogs/tasks_cog.py` (the scheduler). 06-02 made the adapter total;
    06-03 makes the cycle survive the exception sources nobody has enumerated
    yet, and merging them would let a change to the adapter's parse universe
    silently re-scope the cycle's containment claim.

WHY PROPERTIES AND NOT EXAMPLES. The acceptance suite proves the ONE exception
source 06-02 closed no longer stops the cycle. The claim this step makes is
different and universal: "no server's failure, wherever it falls in the
cluster, costs any OTHER server its turn". That is quantified over cluster
size and over failure position, and no enumerated example can establish it —
a fixture with the failure last passes against a cycle that aborts on first
failure, which is precisely the bug.

The failure is injected at `load_guilds`, the storage port `_update_one_server`
reads each server through. Nothing about the cog is stubbed: the real
`auto_update` body, the real per-server guard and the real emit path run.

DECLARED UNIVERSE. `_surface()` captures every observable the cycle produces
for this contract, and each property asserts the WHOLE dict rather than one
slot, so a guard that contained the failure while quietly dropping a sibling,
losing a record or leaking the exception text fails here:

    servers_reached          — the ordered server ids the cycle actually read
    escaped                  — the exception that got past `auto_update`, if any
    servers_reported_failed  — the server ids named in a failure record
    records_leaking_secrets  — records whose text carries the injected key
"""

import asyncio
import logging
import os
import sys
from contextlib import contextmanager

import pytest

hypothesis = pytest.importorskip(
    "hypothesis",
    reason="hypothesis is not installed — DISTILL pins it into requirements.txt",
)

import hypothesis.strategies as st  # noqa: E402
from hypothesis import given, settings  # noqa: E402


def _tasks_cog():
    """Import `bot.cogs.tasks_cog` LATE, and never at module scope.

    Two preconditions make this a function rather than a top-level import:

    `config.py` reads UPDATE_CHANNEL_ID / REPLAY_INDEX_CHANNEL_ID at import
    time via `int(os.getenv(...))` and raises TypeError when either is unset,
    and importing any cog imports config — the wrong-reason RED the
    pre-DELIVER gate exists to catch.

    More importantly, importing the cog imports `bot.guilds`, which builds the
    process-wide `ClusterRepository` singleton from whatever environment
    exists AT THAT MOMENT. At collection time no fixture has run, so the
    singleton is built without `SCRAPCODE_DB_KEY` and falls back to the JSON
    backend — poisoning every acceptance test that later runs in the same
    process. The acceptance suite imports the cog inside its helpers for
    exactly this reason; this module follows it rather than inventing a
    different rule.
    """
    os.environ.setdefault("UPDATE_CHANNEL_ID", "0")
    os.environ.setdefault("REPLAY_INDEX_CHANNEL_ID", "0")
    from bot.cogs import tasks_cog

    return tasks_cog


@pytest.fixture(scope="module", autouse=True)
def _leave_the_cog_module_as_this_file_found_it():
    """Un-import the cog once this module's tests are done.

    `bot/cogs/tasks_cog.py` binds `repo` and `load_guilds` BY VALUE
    (`from bot.guilds import ...`), resolved once, at import time. The
    acceptance suite patches `bot.guilds.repo` per test and depends on
    importing the cog afterwards to pick the patched object up — an ordering
    it satisfies today only because nothing imports the cog earlier in the
    session. Importing it here would freeze `tasks_cog.repo` to the repository
    that existed during the unit phase, and thirty-three acceptance scenarios
    would then read a database nobody wrote to.

    Dropping the module puts the next importer back in the position it holds
    when this file is absent. Module-scoped so the teardown lands before any
    other test module runs, whatever order pytest picked.
    """
    yield
    sys.modules.pop("bot.cogs.tasks_cog", None)
    cogs_package = sys.modules.get("bot.cogs")
    if cogs_package is not None:
        # `from bot.cogs import tasks_cog` would otherwise resolve the stale
        # module object off the package attribute without consulting
        # `sys.modules` at all.
        stale_module = getattr(cogs_package, "tasks_cog", None)
        if stale_module is not None:
            delattr(cogs_package, "tasks_cog")


# The exception message every injected failure carries. A real failure below
# `_update_one_server` can be built from a request, a row or a header holding
# an `api_key`, so KPI-6 forbids the raw text reaching a record. This sentinel
# is what makes that leak visible rather than theoretical.
LEAKED_SECRET = "X-API-KEY=sk-live-do-not-log-me"


@st.composite
def _clusters_with_failing_servers(draw):
    """A cluster and the servers in it whose pass will raise.

    Position is drawn rather than pinned because position is the whole
    property: a cluster whose only failure is last cannot distinguish a
    contained cycle from one that abandons the rest on the first exception.
    """
    server_ids = draw(
        st.lists(st.integers(min_value=1, max_value=10**12),
                 unique=True, min_size=1, max_size=8)
    )
    positions = draw(
        st.sets(st.integers(min_value=0, max_value=len(server_ids) - 1),
                min_size=1, max_size=len(server_ids))
    )
    # In CYCLE order, not sorted: the failure records come out in the order the
    # cycle met them, and comparing against a sorted list would pass against a
    # cycle that reported them in any order at all.
    failing = [sid for index, sid in enumerate(server_ids) if index in positions]
    return server_ids, failing


@given(cluster=_clusters_with_failing_servers())
@settings(max_examples=100, deadline=None)
def test_a_failing_server_never_costs_its_siblings_their_turn(cluster):
    """AC-007.9 / KPI-5 at cycle scope.

    `discord.ext.tasks.Loop` stops on an unhandled exception and announces
    nothing, so before the guard existed one server's defect ended hourly
    ingestion for every server on the bot, invisibly. Every server the cluster
    contains is still read, in order, however many of them fail and wherever
    they fall.
    """
    server_ids, failing = cluster

    surface = _run_one_cycle_over(server_ids, failing=failing)

    assert surface == {
        "servers_reached": tuple(server_ids),
        "escaped": None,
        "servers_reported_failed": tuple(failing),
        "records_leaking_secrets": (),
    }


@given(cluster=_clusters_with_failing_servers())
@settings(max_examples=50, deadline=None)
def test_a_contained_failure_is_reported_and_never_merely_absorbed(cluster):
    """The guard's other half, and the one that is easy to get wrong.

    A per-server `try` that logs nothing turns "the loop died" into "the loop
    is quietly doing nothing", which looks healthy and is strictly worse than
    the crash it replaced. One `auto_update.server.failed` record per failing
    server, at ERROR, naming the server and the exception type — and never the
    exception text, which may carry key material.
    """
    server_ids, failing = cluster

    records = _records_from_one_cycle_over(server_ids, failing=failing)
    reported = records.named(_tasks_cog().SERVER_FAILED_EVENT)

    assert [r.server_id for r in reported] == list(failing)
    assert {r.levelno for r in reported} == {logging.ERROR}
    assert {r.error_type for r in reported} == {"RuntimeError"}


@given(consecutive_failures=st.integers(min_value=1, max_value=50))
@settings(max_examples=100, deadline=None)
def test_the_restart_wait_grows_and_then_stops_growing(consecutive_failures):
    """Restarting a loop whose dependency is down re-hits it immediately.

    `before_loop` only awaits `wait_until_ready`, and a relative-interval loop
    runs its body at once, so an unwaited restart hammers the failing
    dependency at event-loop speed — its own outage. The wait is therefore
    strictly positive, never shrinks as failures accumulate, and is bounded so
    it can never become an unbounded stall either.
    """
    cycle = _tasks_cog()
    delay = cycle._restart_delay_seconds(consecutive_failures)
    previous = cycle._restart_delay_seconds(max(consecutive_failures - 1, 1))
    ceiling = max(cycle._RESTART_BACKOFF_SECONDS)

    assert delay > 0
    assert delay >= previous
    assert delay <= ceiling


async def test_the_loop_error_handler_says_so_before_it_waits_to_restart():
    """A stopping loop announces itself, and the announcement comes FIRST.

    The record is emitted before the backoff so a loop that dies during the
    wait still left the operator the reason. Asserted by letting the handler
    run up to its first suspension point and then cancelling it — waiting out
    a real production backoff would be a minute of nothing.
    """
    # bypass: single-shot wiring of one coroutine to one record; there is no
    # input axis to quantify over. The schedule it wraps is covered by the
    # property above.
    cycle = _tasks_cog()
    cog = _cog_over([])
    records = _RecordedEvents()
    handler_attached = _listening_to(cycle.logger, records)

    with handler_attached:
        announcing = asyncio.create_task(
            cog.on_auto_update_error(RuntimeError(LEAKED_SECRET))
        )
        await asyncio.sleep(0)
        announcing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await announcing

    failed = records.named(cycle.LOOP_FAILED_EVENT)
    assert len(failed) == 1
    assert failed[0].levelno == logging.ERROR
    assert failed[0].error_type == "RuntimeError"
    assert failed[0].restarting is True
    assert not records.leaking(LEAKED_SECRET)


async def test_a_loop_that_keeps_dying_is_abandoned_loudly_not_silently():
    """Giving up is allowed. Giving up quietly is the failure being removed.

    Past the restart budget the handler stops restarting — three tight
    restarts against a dependency that is genuinely gone is enough, and more
    is an outage the bot inflicts on itself. It says so in a record whose only
    meaning is "hourly ingestion is over until a human acts", which is the one
    thing the old silent stop never said.
    """
    # bypass: the interesting axis (how the wait grows) is quantified in the
    # property above; this pins the single terminal transition at the budget.
    cycle = _tasks_cog()
    cog = _cog_over([])
    cog._loop_failures = cycle._MAX_LOOP_RESTARTS
    records = _RecordedEvents()

    with _listening_to(cycle.logger, records):
        await cog.on_auto_update_error(RuntimeError(LEAKED_SECRET))

    abandoned = records.named(cycle.LOOP_ABANDONED_EVENT)
    assert len(abandoned) == 1
    assert abandoned[0].levelno == logging.ERROR
    assert abandoned[0].restarting is False
    assert not records.named(cycle.LOOP_FAILED_EVENT)
    assert not records.leaking(LEAKED_SECRET)


# ===========================================================================
# Driving the real cycle
# ===========================================================================

def _run_one_cycle_over(server_ids, *, failing) -> dict:
    """One `auto_update` tick over `server_ids`, as an observable surface."""
    cycle = _tasks_cog()
    storage = _GuildStorage(failing)
    records = _RecordedEvents()
    escaped = None

    with _cluster_of(server_ids, storage=storage), _listening_to(cycle.logger, records):
        cog = _cog_over(server_ids)
        try:
            asyncio.run(cog.auto_update())
        except BaseException as exc:  # noqa: BLE001 — the surface IS the escape
            escaped = exc

    return {
        "servers_reached": tuple(storage.reached),
        "escaped": escaped,
        "servers_reported_failed": tuple(
            record.server_id for record in records.named(cycle.SERVER_FAILED_EVENT)
        ),
        "records_leaking_secrets": tuple(records.leaking(LEAKED_SECRET)),
    }


def _records_from_one_cycle_over(server_ids, *, failing) -> "_RecordedEvents":
    storage = _GuildStorage(failing)
    records = _RecordedEvents()
    with _cluster_of(server_ids, storage=storage), _listening_to(_tasks_cog().logger, records):
        asyncio.run(_cog_over(server_ids).auto_update())
    return records


def _cog_over(server_ids):
    """The real cog, minus the scheduler (same shape as the acceptance suite)."""
    cog_class = _tasks_cog().TasksCog
    cog = cog_class.__new__(cog_class)
    cog.bot = _FakeBot(_FakeChannel())
    cog.player_service = None  # never reached: the pass fails or returns early
    return cog


@contextmanager
def _cluster_of(server_ids, *, storage):
    """Point the cycle's two storage ports at this cluster.

    Both are module-level names in `tasks_cog`, which is where production looks
    them up; the real `repo` object is replaced rather than mutated so a failed
    restore cannot leave a poisoned singleton behind for the next test.
    """
    cycle = _tasks_cog()
    original_repo = cycle.repo
    original_load_guilds = cycle.load_guilds
    cycle.repo = _ServerDirectory(server_ids)
    cycle.load_guilds = storage
    try:
        yield
    finally:
        cycle.repo = original_repo
        cycle.load_guilds = original_load_guilds


@contextmanager
def _listening_to(logger, records):
    logger.addHandler(records)
    previous_level = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        yield
    finally:
        logger.removeHandler(records)
        logger.setLevel(previous_level)


class _ServerDirectory:
    """Stands in for `ClusterRepository` — only `list_server_ids` is reached."""

    def __init__(self, server_ids) -> None:
        self._server_ids = list(server_ids)

    def list_server_ids(self):
        return list(self._server_ids)


class _GuildStorage:
    """Stands in for `bot.guilds.load_guilds`, the port each pass opens with.

    A healthy server answers with no guilds, which ends its pass immediately —
    this module's contract is that the pass HAPPENS, not what it then does.
    A failing server raises the way a broken read genuinely would, carrying an
    exception message with key material in it.
    """

    def __init__(self, failing) -> None:
        self.reached: list[int] = []
        self._failing = set(failing)

    def __call__(self, server_id):
        self.reached.append(server_id)
        if server_id in self._failing:
            raise RuntimeError(f"guild storage refused server {server_id}: {LEAKED_SECRET}")
        return {}


class _RecordedEvents(logging.Handler):
    """Every structured record the cycle emitted, unparsed."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def named(self, event: str) -> list[logging.LogRecord]:
        return [r for r in self.records if getattr(r, "event", None) == event]

    def leaking(self, secret: str) -> list[str]:
        """Records whose message or any attached field carries `secret`."""
        return [
            getattr(record, "event", record.name)
            for record in self.records
            if secret in record.getMessage()
            or any(secret in str(value) for value in vars(record).values())
        ]


class _FakeBot:
    def __init__(self, channel) -> None:
        self._channel = channel

    def get_channel(self, channel_id: int):
        return self._channel


class _FakeChannel:
    id = 1

    async def send(self, content):
        return None
