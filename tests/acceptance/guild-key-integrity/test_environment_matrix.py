"""Environmental realism (Mandate 4). Implements
`acceptance/environment-matrix.feature`.

One test per target environment in
`docs/feature/guild-key-integrity/environments.yaml`, each asserting that
environment's headline invariant rather than re-running the slice suites.
The traceability test at the bottom is what stops the DEVOPS artifact and
this suite from quietly drifting apart.
"""
from __future__ import annotations

import pytest

from domain_types import DARK_MECHANICUM, WORD_BEARERS, Environment
from conftest import (
    GUILD_DM,
    GUILD_WB,
    PROD_SERVER_ID,
    GuildServiceResponse,
    environment_names_from_devops_artifact,
)

RED = pytest.mark.skip(reason="RED scaffold — enable one at a time in DELIVER")


# ===========================================================================
# Traceability — runs GREEN today. Not a scaffold.
# ===========================================================================

@pytest.mark.traceability
def test_environment_names_match_the_devops_artifact():
    """The suite's environment vocabulary and `environments.yaml` are the
    same list, or the suite is testing environments nobody deploys into.

    Deliberately NOT skipped: it asserts a property of two files that both
    exist right now, so it is meaningful before a line of the feature is
    written. If it fails, one of the two documents is wrong.
    """
    from_yaml = environment_names_from_devops_artifact()
    from_suite = [e.value for e in Environment]

    assert from_yaml, "environments.yaml parsed to an empty list"
    assert sorted(from_yaml) == sorted(from_suite), (
        f"environments.yaml declares {sorted(from_yaml)} but the suite covers "
        f"{sorted(from_suite)} — Mandate 4 coverage has a hole"
    )


# ===========================================================================
# One per environment
# ===========================================================================

@pytest.mark.real_io
async def test_clean_adopts_every_identity_once(
    sqlite_repo, fake_guild_service, update_channel, key_events
):
    """`clean` — trust-on-first-use. Also the state EVERY production guild
    is in on the day Slice 01 deploys, which is why it is asserted across
    three guilds rather than one: the announcement volume on deploy day is
    what the operator actually experiences."""
    _register_guilds(sqlite_repo, count=3)
    fake_guild_service.program_default(GuildServiceResponse(identity=WORD_BEARERS))

    await _run_hourly_cycle(fake_guild_service, update_channel)
    assert len(key_events.named("guild.key.bound")) == 3
    assert len(_adoption_messages(update_channel)) == 3

    update_channel.messages.clear()
    await _run_hourly_cycle(fake_guild_service, update_channel)
    assert _adoption_messages(update_channel) == []


@pytest.mark.kpi
async def test_bound_matching_is_completely_silent(
    sqlite_repo, matching_guild, update_channel, ping_channel, key_events
):
    """`bound-matching` — the steady state, and the environment a
    drift-only suite never visits. Every alerting scenario in this feature
    passes against an implementation that alerts unconditionally; this is
    the one that does not."""
    before = _row_counts(sqlite_repo, GUILD_WB)

    await _run_hourly_cycle(matching_guild, update_channel, ping_channel=ping_channel)

    assert not key_events.any_named(
        "guild.key.mismatch", "guild.key.quarantined",
        "guild.key.unverifiable", "guild.key.alert.sent",
    )
    assert ping_channel.messages == []
    assert _row_counts(sqlite_repo, GUILD_WB).battle_hits > before.battle_hits
    assert key_events.named("guild.key.probe.ok")


@RED
@pytest.mark.kpi
async def test_bound_drifted_writes_nothing_once_enforcement_is_on(
    sqlite_repo, drifted_guild, update_channel
):
    """`bound-drifted` — the incident replay, end to end.

    Baseline for KPI-2 is 50 contaminated hit rows plus 60 corrupted player
    rows. The target is zero, and this is the measurement.
    """
    before = _row_counts(sqlite_repo, GUILD_WB)

    await _run_hourly_cycle(drifted_guild, update_channel, enforcement=True)

    assert _row_counts(sqlite_repo, GUILD_WB) == before


@pytest.mark.error
@pytest.mark.kpi
async def test_unverifiable_alerts_loudly_and_blocks_nothing(
    sqlite_repo, fake_guild_service, update_channel, key_events
):
    """`unverifiable` — the vendor-change environment, cluster-wide.

    Asserted across every guild because that is how it would actually
    arrive: Tacticus drops the field and all guilds go unverifiable in the
    same cycle. Blocking would be an outage caused by someone else's
    release.
    """
    _register_guilds(sqlite_repo, count=3)
    fake_guild_service.program_default(
        GuildServiceResponse(identity=WORD_BEARERS, drop_fields=("guildId",))
    )

    await _run_hourly_cycle(fake_guild_service, update_channel, enforcement=True)

    assert _quarantined_guild_ids() == []
    assert _all_guilds_ingested(sqlite_repo)
    assert len(key_events.named("guild.key.unverifiable")) == 3
    assert "verification is offline" in update_channel.text.lower()
    assert not key_events.any_named("guild.key.mismatch")


@pytest.mark.error
@pytest.mark.kpi
async def test_tacticus_unreachable_leaves_every_binding_byte_identical(
    sqlite_repo, fake_guild_service, update_channel
):
    """`tacticus-unreachable` — D6 at cluster scale."""
    import httpx

    _register_guilds(sqlite_repo, count=3)
    before = _all_bindings()
    fake_guild_service.program_default(
        GuildServiceResponse(raises=httpx.ConnectError("no route to host"))
    )

    await _run_hourly_cycle(fake_guild_service, update_channel, enforcement=True)

    assert _quarantined_guild_ids() == []
    assert _all_bindings() == before


@pytest.mark.error
async def test_dead_key_is_reported_not_quarantined(
    sqlite_repo, fake_guild_service, update_channel, key_events
):
    """`dead-key` — 401/403. Nothing to contaminate, so nothing to
    quarantine; the operator just needs to be told."""
    fake_guild_service.program("wb-key", GuildServiceResponse(status=401))

    await _run_hourly_cycle(fake_guild_service, update_channel, enforcement=True)

    assert key_events.named("guild.key.dead")
    assert _quarantined_guild_ids() == []
    assert "update_guild_key" in update_channel.text


@RED
@pytest.mark.kpi
async def test_mixed_cluster_survives_a_quarantined_first_guild(
    sqlite_repo, fake_guild_service, update_channel, key_events
):
    """`mixed-cluster` — the season SPOF, with the ordering pinned.

    The precondition assertion is not decoration: if the fixture stops
    putting the quarantined guild first, this test keeps passing while
    testing nothing, which is the exact failure the environment note in
    `environments.yaml` warns about.
    """
    _register_two_guilds_quarantined_first(sqlite_repo)
    assert _guild_ids_in_order()[0] == GUILD_WB, "ordering precondition lost"

    fake_guild_service.program("dm-key", GuildServiceResponse(identity=DARK_MECHANICUM))

    await _run_hourly_cycle(fake_guild_service, update_channel, enforcement=True)

    (cycle,) = key_events.named("auto_update.cycle")
    assert cycle.season is not None
    assert cycle.guilds_total == 2
    assert cycle.guilds_processed == 1
    assert cycle.guilds_skipped == 1
    assert cycle.skip_reasons


@pytest.mark.error
@pytest.mark.real_io
@pytest.mark.adapter_integration
async def test_json_backend_rollback_goes_inert_without_raising(
    json_repo, fake_guild_service, update_channel, key_events
):
    """`json-backend-rollback` — ADR-006 D9.

    The environment an operator lands in when Slice 01 or 03 has to be
    rolled back under time pressure. "Does not crash" is the whole
    requirement: the feature must go inert, not half-work.
    """
    from bot.guilds import load_guild_binding, save_guild_binding

    assert load_guild_binding(PROD_SERVER_ID, GUILD_WB).is_unbound
    save_guild_binding(PROD_SERVER_ID, GUILD_WB, _a_binding())
    assert load_guild_binding(PROD_SERVER_ID, GUILD_WB).is_unbound, (
        "the JSON adapter persisted a binding — it must no-op the write"
    )

    await _run_hourly_cycle(fake_guild_service, update_channel, enforcement=True)

    assert _quarantined_guild_ids() == []
    assert not key_events.any_named("guild.key.quarantined")


# ===========================================================================
# Environment arrangement that no shared fixture can express
# ===========================================================================

@pytest.fixture
def matching_guild(bound_cluster, fake_guild_service):
    """Module-local override: `bound-matching` is TWO facts, cluster-wide.

    The conftest `matching_guild` programs the key and stops there, because the
    slice-01 scenarios that use it are about trust-on-first-use and would break
    against a pre-existing binding — `test_first_verification_adopts_the_
    identity_and_announces_once` asserts exactly one `guild.key.bound`, which a
    bound guild can never produce. So the shared fixture cannot carry the
    binding, and this module cannot do without it.

    `environments.yaml` defines this environment as "a cluster where EVERY
    guild is bound to the identity its key resolves to". Without the stored
    bindings the cycle takes the adoption path, which is ALSO silent — so
    `test_bound_matching_is_completely_silent` would report "no alert was
    raised" against an implementation whose comparison is broken or absent.
    That is the fifth recurrence of the vacuity pattern this suite keeps
    hitting (UD-4, UD-7), and it is the one scenario whose stated job is to
    fail an implementation that alerts unconditionally.

    Overriding here rather than in conftest.py is deliberate: the cluster-wide
    arrangement belongs to the environment matrix, and pushing it into the
    shared fixture would break the slice-01 adoption scenarios above.
    `bound_cluster` — the reusable half — DOES live in conftest.py.
    """
    fake_guild_service.program(
        "wb-key",
        GuildServiceResponse(identity=WORD_BEARERS, members=["u1", "u2", "u3"]),
    )
    fake_guild_service.program(
        "dm-key",
        GuildServiceResponse(identity=DARK_MECHANICUM, members=["u4", "u5"]),
    )
    return fake_guild_service


# ===========================================================================
# Helpers — wiring only.
#
# The composition is IMPORTED from `test_slice_01_bind_and_report`, not
# re-derived: the double goes in at the Tacticus transport, below
# `bot.services.tacticus.guild_client`, so every classification asserted above
# is made by production code reading a real vendor body. A second, independent
# composition here would be free to drift from the one the slice suites run,
# and the environment matrix would then be certifying a wiring nobody ships.
# ===========================================================================
from typing import NamedTuple  # noqa: E402 — helpers-only dependency

from test_slice_01_bind_and_report import (  # noqa: E402
    _GUILD_REGISTRY,
    _FakeChroniclerClient,
    _entry_total,
    _tacticus_answered_by,
)

# The cluster the environment scenarios draw from. Word Bearers FIRST:
# `auto_update` derives the season from the first guild whose key can answer,
# and the season SPOF only misbehaves in that ordering. The first two entries
# are the slice-01 registry verbatim — the incident's two guilds — so a
# three-guild environment is the two-guild suite plus one, not a parallel
# fixture that could describe a different cluster.
_CLUSTER: dict[str, tuple[str, dict]] = {
    **_GUILD_REGISTRY,
    "iw-key": ("iron_warriors", {
        "name": "Iron Warriors", "api_key": "iw-key", "role_id": 3,
        "notification_channel_id": None, "member_role_ids": [],
    }),
}

# The one phrase `TasksCog._announce_adoption` posts on trust-on-first-use.
# Matched on the sentence rather than the emoji: the emoji is decoration an
# operator never reads out loud, and pinning it would make a cosmetic edit look
# like a lost announcement.
_ADOPTION_PHRASE = "is now bound to"


async def _run_hourly_cycle(service, channel, *, ping_channel=None, enforcement=False):
    """Drive one `auto_update` tick over a WHOLE cluster.

    Registration is left alone when the scenario built its own cluster with
    `_register_guilds` — these are cluster-scale environments, and a helper
    that re-registered would silently shrink a three-guild environment back to
    the two-guild default.

    `enforcement` records the environment's declared posture and is INERT in
    Slice 01. It is not quietly dropped: `guild_keys.verify_and_resolve`
    refuses `enforce=True` with NotImplementedError on purpose (ADR-008 D3 —
    enforcement ships in Slice 03, one slice AFTER `/update_guild_key` provides
    the only exit from quarantine), and `tasks_cog` passes `enforce=False`
    unconditionally. Honouring the flag here would raise, not enforce. What the
    four Slice-01 scenarios that pass it actually assert is that their
    environment produces NO quarantine even when enforcement is requested —
    which is true of this slice by construction and must stay true of the next
    one. Slice 03 is where this parameter starts reaching production.
    """
    _register_the_guilds_the_scenario_programmed(service)
    cog = _tasks_cog_posting_to(channel, ping_channel)
    with _tacticus_answered_by(service):
        await cog.auto_update()


def _register_the_guilds_the_scenario_programmed(service) -> None:
    """Register the guilds whose key the scenario programmed an answer for.

    Idempotent by short-circuit: a scenario that already called
    `_register_guilds` owns its cluster and this leaves it untouched.

    Not every guild unconditionally — `FakeGuildService.answer_for` refuses an
    unprogrammed key precisely so a scenario cannot silently exercise a path it
    did not declare, and registering a guild nobody programmed would trip that
    guard on every single-guild environment.
    """
    from bot.guilds import load_guilds, save_guilds

    if load_guilds(PROD_SERVER_ID):
        return

    programmed = set(_CLUSTER) if service._default is not None else set(service._by_key)
    save_guilds(PROD_SERVER_ID, {
        _CLUSTER[key][0]: _CLUSTER[key][1]
        for key in _CLUSTER
        if key in programmed
    })


def _tasks_cog_posting_to(channel, ping_channel):
    """The real cog, minus the scheduler — same construction as slice 01.

    `TasksCog.__init__` calls `.start()` on both `@tasks.loop`s, which would
    hand the hourly cycle to the event loop and race the assertions.
    """
    from bot.cogs.tasks_cog import TasksCog
    from bot.services.chronicl3r.player_service import PlayerService

    cog = TasksCog.__new__(TasksCog)
    cog.bot = _ClusterBot(channel, ping_channel)
    cog.player_service = PlayerService(_FakeChroniclerClient())
    return cog


class _ClusterBot:
    """Routes by channel id, so "nothing was pinged" is a real observation.

    `UPDATE_CHANNEL_ID` is 0 under test (conftest sets it before collection);
    every OTHER id a scenario can reach is a guild's own
    `notification_channel_id`. Handing back the update channel for both — the
    single-channel shape the slice-01 helper uses — would make
    `ping_channel.messages == []` true no matter what the loop posted, because
    the ping channel would never be handed out at all.
    """

    def __init__(self, update_channel, ping_channel) -> None:
        self._update_channel = update_channel
        self._ping_channel = ping_channel

    def get_channel(self, channel_id: int):
        from config import UPDATE_CHANNEL_ID
        if channel_id == UPDATE_CHANNEL_ID:
            return self._update_channel
        return self._ping_channel or self._update_channel


def _register_guilds(repo, *, count: int) -> None:
    """Register the first `count` guilds of `_CLUSTER`, in cluster order."""
    import bot.guilds as guilds_mod
    from bot.guilds import save_guilds

    assert guilds_mod.repo is repo, (
        "the composition root is pointing at a different repository than the "
        "scenario's — the singleton escaped the fixture and the cycle would "
        "read state this scenario never wrote"
    )
    keys = list(_CLUSTER)[:count]
    assert len(keys) == count, (
        f"the environment asked for {count} guilds and the cluster defines "
        f"{len(_CLUSTER)}"
    )
    save_guilds(PROD_SERVER_ID, {
        _CLUSTER[key][0]: _CLUSTER[key][1] for key in keys
    })


def _register_two_guilds_quarantined_first(repo) -> None:
    raise AssertionError(
        "Not yet implemented — needs `guild_keys.quarantine`, which is a "
        "Slice 03 scaffold. `test_mixed_cluster_survives_a_quarantined_first_"
        "guild` stays @RED until then."
    )


def _guild_ids_in_order() -> list[str]:
    """Registration order as `auto_update` walks it — the season SPOF only
    misbehaves when the unusable guild is first."""
    from bot.guilds import load_guilds
    return list(load_guilds(PROD_SERVER_ID))


class _RowCounts(NamedTuple):
    """Everything the 2026-07-28 incident corrupted, in one comparable value.

    Battle hits, bomb hits AND players together: the incident put 30/30 battle
    rows and 20/20 bomb rows off-roster and inverted 60 of 67 `players` rows,
    so a count that omitted any of the three would report "unchanged" for a
    cluster that had been contaminated in the other two.
    """

    battle_hits: int
    bomb_hits: int
    players: int


def _row_counts(repo, guild_id: str) -> _RowCounts:
    """`SEASON` is resolved HERE, at call time, and not imported at module
    scope — deliberately, and the same way `_hit_counts` does it in slice 01.

    `tests/acceptance/sqlite-backend/conftest.py` and this suite's conftest are
    BOTH top-level module `conftest`, and in a combined
    `pytest tests/unit tests/acceptance` run they collide: whichever one is
    resident in `sys.modules` when a helper does `from conftest import SEASON`
    is the one it gets, and the sqlite-backend suite says 94 where this one
    says 106. A module-scope import here bound 106 while `_tacticus_body` — the
    helper that decides which raid URL the double answers — lazily resolved 94,
    so the cycle wrote season-94 rows and this counted season-106 ones and read
    zero. Resolving it at the same moment the loop does keeps the two ends of
    the assertion on the same season whichever conftest wins.
    """
    from conftest import SEASON
    from bot.guilds import load_player_list
    return _RowCounts(
        battle_hits=_entry_total(repo.load_battle_hits(PROD_SERVER_ID, guild_id, SEASON)),
        bomb_hits=_entry_total(repo.load_bomb_hits(PROD_SERVER_ID, guild_id, SEASON)),
        players=len(load_player_list(PROD_SERVER_ID, guild_id).get("players", {})),
    )


def _quarantined_guild_ids() -> list[str]:
    """Every registered guild whose binding says quarantined.

    Read through the registry rather than through `list_guild_bindings`: a
    guild with no binding row has no quarantine either, and the question these
    environments ask is "did the cluster survive", which is a question about
    registered guilds.
    """
    from bot.guilds import load_guild_binding, load_guilds
    from bot.services.tacticus.guild_client import KeyStatus
    return [
        guild_id
        for guild_id in load_guilds(PROD_SERVER_ID)
        if load_guild_binding(PROD_SERVER_ID, guild_id).key_status
        == KeyStatus.QUARANTINED.value
    ]


def _all_bindings() -> dict:
    """One `GuildBinding` per registered guild, unbound placeholders included.

    `GuildBinding` is frozen and value-compared, so `==` on this mapping is the
    byte-identical claim D6 makes — an implementation that refreshed
    `identity_bound_at`, cleared a display field or dropped a row during an
    outage shows up as an inequality here.
    """
    from bot.guilds import load_guild_binding, load_guilds
    return {
        guild_id: load_guild_binding(PROD_SERVER_ID, guild_id)
        for guild_id in load_guilds(PROD_SERVER_ID)
    }


def _all_guilds_ingested(repo) -> bool:
    """True when EVERY registered guild recorded raid data this cycle.

    Asserts the cluster is non-empty first: "all of an empty set ingested" is
    vacuously true, and this predicate exists to prove a vendor change did not
    take the cluster down.
    """
    from bot.guilds import load_guilds
    guild_ids = list(load_guilds(PROD_SERVER_ID))
    assert guild_ids, "no guild is registered — 'every guild ingested' is vacuous"
    return all(_row_counts(repo, guild_id).battle_hits > 0 for guild_id in guild_ids)


def _adoption_messages(channel) -> list[str]:
    """The trust-on-first-use announcements, and nothing else the cycle posts."""
    return [message for message in channel.messages if _ADOPTION_PHRASE in message]


def _a_binding():
    """A binding worth persisting — so the JSON adapter's no-op write is a
    no-op on real content, not on an empty placeholder that would round-trip
    unbound either way."""
    from bot.repository import GuildBinding
    return GuildBinding(
        tacticus_guild_id=WORD_BEARERS.uuid,
        tacticus_guild_tag=WORD_BEARERS.tag,
        tacticus_guild_name=WORD_BEARERS.name,
        identity_bound_at="2026-07-31T04:00:00Z",
    )
