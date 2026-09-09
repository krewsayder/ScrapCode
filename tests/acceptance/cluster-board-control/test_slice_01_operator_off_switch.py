"""Executable specs for `acceptance/slice-01-operator-off-switch.feature`.

The `.feature` file is the human-readable scenario SSOT; this module is the
executable form (project convention — no pytest-bdd, see
`docs/architecture/atdd-infrastructure-policy.md`).

WHY EVERY SCENARIO HERE IS RED AT DISTILL TIME. The feature's user-visible
behaviour already ships and works. What does NOT ship is the representation
ADR-009 requires: the port still hands out bare dicts carrying an
`enabled` key that is OMITTED when true, instead of `LiveBoardConfig`
carrying a `BoardStatus`. Every scenario below is written against the ADR-009
contract, so every scenario fails until the Slice-01 precursor lands.

That is the intended RED. The `typed_port` fixture converts what would
otherwise be a scatter of TypeErrors and AttributeErrors — which classify
BROKEN, not RED — into one AssertionError that names the missing capability,
so the pre-DELIVER gate reads `MISSING_FUNCTIONALITY` rather than
`SETUP_FAILURE`.
"""
from __future__ import annotations

import asyncio

import pytest

from board_domain_types import (
    BOARD_STATUS_CHANGED_EVENT,
    CHANNEL_ID,
    EARLIER_SEASON,
    SEASON,
    SERVER_ID,
    STATUS_COMPARISON_OWNER,
    STATUS_READER_MODULES,
    BoardCommand,
    BoardStatus,
    FakeBot,
    FakeChannel,
    LiveBoardConfig,
    ScopeKind,
)

pytestmark = [pytest.mark.slice_01]


# ===========================================================================
# The port-contract guard
# ===========================================================================

@pytest.fixture
def typed_port(either_repo):
    """Both real adapters, unchecked.

    The port-contract assertion deliberately does NOT live here. A fixture
    that raises makes pytest report ERROR, which the pre-DELIVER gate
    classifies as SETUP_FAILURE — the wrong RED, and the exact signal that
    tells a crafter the test is broken rather than the feature missing. The
    check runs in the test BODY instead, via `_seed`, so every scenario
    reports FAILED with a message naming the missing capability.
    """
    return either_repo


def _require_typed_port(repo) -> None:
    """Assert the live-board port speaks `LiveBoardConfig` (ADR-009 DDD-2).

    Called from `_seed`, i.e. from the test body, so the failure classifies
    MISSING_FUNCTIONALITY. Converts what would otherwise be a scatter of
    TypeErrors and AttributeErrors deep inside an adapter into one named
    contract failure.
    """
    probe = LiveBoardConfig(channel_id=CHANNEL_ID, messages={}, season=SEASON)
    try:
        repo.save_live_leaderboards(SERVER_ID, {"cluster": probe})
        loaded = repo.load_live_leaderboards(SERVER_ID)
    except Exception as exc:  # noqa: BLE001 — any adapter failure is the same finding
        raise AssertionError(
            "the live-board port does not accept LiveBoardConfig yet "
            f"(ADR-009 DDD-2, Slice-01 precursor). Adapter raised: {exc!r}"
        ) from exc

    assert isinstance(loaded.get("cluster"), LiveBoardConfig), (
        "the live-board port does not RETURN LiveBoardConfig yet "
        "(ADR-009 DDD-2, Slice-01 precursor). Got "
        f"{type(loaded.get('cluster')).__name__}."
    )
    repo.save_live_leaderboards(SERVER_ID, {})


def _a_complete_board() -> dict[str, int]:
    """A message id for every tier the bot posts — what a real board carries.

    DERIVED from `TIER_CHOICES`, never hard-coded, and that is the whole point.
    A board carrying fewer messages than there are tiers became a MEANINGFUL
    state when the new-tier backfill shipped: the refresh loop now adopts a
    missing tier by POSTING it, so that a tier added to the game mid-season
    stops being invisible.

    A fixture pinned to one tier therefore reads as "seven tiers need
    backfilling", and `test_resuming_within_the_same_season_edits_the_board_
    already_there` fails on seven sends that are the backfill working
    correctly. Hard-coding eight would fix it until the ninth tier ships and
    then fail the same way, for the same reason, on a scenario about resuming
    that has nothing to do with tiers.
    """
    from config import TIER_CHOICES

    return {tier.value: 900 + index for index, tier in enumerate(TIER_CHOICES)}


def _seed(repo, *, status=BoardStatus.ACTIVE, season=SEASON,
          scope=ScopeKind.CLUSTER, messages=None) -> LiveBoardConfig:
    """`Given a <kind> leaderboard that is <state>`."""
    _require_typed_port(repo)
    config = LiveBoardConfig(
        channel_id=CHANNEL_ID,
        messages=_a_complete_board() if messages is None else messages,
        season=season,
        guild_id=scope.guild_id,
        board_status=status,
    )
    repo.save_live_leaderboards(SERVER_ID, {scope.scope_key: config})
    return config


def _stored(repo, scope=ScopeKind.CLUSTER) -> LiveBoardConfig | None:
    return repo.load_live_leaderboards(SERVER_ID).get(scope.scope_key)


def _run_command(admin_cog, command: BoardCommand, interaction, repo):
    """`When an officer <command> the cluster leaderboard`.

    Direct invocation of the app command with an interaction double — the
    mechanism the Infrastructure Policy records for Discord slash commands
    (`discord.py` app-commands cannot be driven over the wire in a test).
    """
    originals = {
        "load_live_leaderboards": admin_cog.load_live_leaderboards,
        "save_live_leaderboards": admin_cog.save_live_leaderboards,
    }
    admin_cog.load_live_leaderboards = lambda sid: repo.load_live_leaderboards(sid)
    admin_cog.save_live_leaderboards = lambda sid, data: repo.save_live_leaderboards(sid, data)
    try:
        asyncio.run(_invoke(admin_cog, command.command_name, interaction))
    finally:
        for name, original in originals.items():
            setattr(admin_cog, name, original)


async def _invoke(admin_cog, name: str, interaction, **kwargs):
    """Drive the REAL `app_commands.Command`: its `checks` FIRST, then its callback.

    THE CHECKS ARE THE POINT, and skipping them is the defect this helper
    exists to remove. `require_tier` is `app_commands.check(predicate)`
    (`bot/permissions.py:48-51`), and in this `discord.py` build
    `app_commands.check` APPENDS the predicate to `Command.checks` — it does
    not wrap the callback. So a harness that resolves a command to its
    `.callback` and awaits that runs the handler with the permission gate
    excluded from the call chain outright, and every scenario about who may
    drive the board asserts against a system that has no gate in it.

    Running the checks by hand mimics `main.py::on_app_command_error`: a failed
    predicate sends the ephemeral denial and returns WITHOUT reaching the
    callback. The denial text is copied verbatim from `main.py:94` — no
    assertion in this suite reads it (the interaction double discards
    `response.send_message` content), but a paraphrase here would be a second
    and wrong statement of what a denied officer is told, and the day that
    message changes a grep has to find this line with the other four.

    Same shape, for the same reason, as `guild-key-integrity`'s
    `test_slice_02_update_guild_key.py::_invoke_update_guild_key` and its
    siblings in slices 03, 05 and 06 — where this repository solved this four
    times before this suite reintroduced it.
    """
    cmd = _find_command(admin_cog, name)
    for chk in cmd.checks:
        # This build stores the predicate coroutine directly in `checks`;
        # others wrap it in a `Check` carrying `.predicate`. Accept either.
        predicate = chk.predicate if hasattr(chk, "predicate") else chk
        if not await predicate(interaction):
            await interaction.response.send_message(
                "❌ You don't have permission to use this command.", ephemeral=True
            )
            return
    cog = admin_cog.AdminCog.__new__(admin_cog.AdminCog)
    await cmd.callback(cog, interaction, **kwargs)


def _find_command(admin_cog, name: str):
    """The `Command` OBJECT — never its `.callback`.

    Returning `.callback` discards `Command.checks`, which is where
    `@require_tier` actually lives. See `_invoke`.
    """
    for command in admin_cog.AdminCog.__cog_app_commands__:
        if command.name == name:
            return command
    raise AssertionError(
        f"no `{name}` command is registered on AdminCog — delete the command "
        "method and this harness errors, which is the port-to-port litmus test"
    )


def _run_cycle(tasks_cog, repo, channel: FakeChannel, *, season=SEASON) -> FakeChannel:
    """`When the hourly cycle runs`.

    Direct await of the loop body with the decorator bypassed — the schedule is
    `discord.py`'s concern, the cycle body is ours (Infrastructure Policy).
    """
    originals = {
        "load_live_leaderboards": tasks_cog.load_live_leaderboards,
        "save_live_leaderboards": tasks_cog.save_live_leaderboards,
        "repo": tasks_cog.repo,
        "get_player_list": tasks_cog.get_player_list,
    }
    tasks_cog.load_live_leaderboards = lambda sid: repo.load_live_leaderboards(sid)
    tasks_cog.save_live_leaderboards = lambda sid, data: repo.save_live_leaderboards(sid, data)
    tasks_cog.repo = _NoRaidRows()
    tasks_cog.get_player_list = lambda sid, gid: {}
    try:
        cog = tasks_cog.TasksCog.__new__(tasks_cog.TasksCog)
        cog.bot = FakeBot(channel)
        asyncio.run(
            cog._refresh_live_leaderboards(SERVER_ID, season, {"neuro": {"name": "Neuro"}})
        )
    finally:
        for name, original in originals.items():
            setattr(tasks_cog, name, original)
    return channel


class _NoRaidRows:
    def load_battle_hits(self, server_id, guild_id, season):
        return {"boss_hits": {}}


# ===========================================================================
# US-001 — turn it off
# ===========================================================================

@pytest.mark.driving_port
@pytest.mark.real_io
def test_an_officer_turns_the_board_off_and_is_told_what_survived(
    typed_port, admin_cog, officer
):
    """AC-001.1. The reply states what was NOT done as prominently as what was.

    An officer who has just switched something off needs to know the board
    still exists — the mental model this persona carries is that turning a
    thing off means deleting it (`personas/guild-officer.yaml`).
    """
    _seed(typed_port)

    _run_command(admin_cog, BoardCommand.TURN_OFF, officer, typed_port)

    assert _stored(typed_port).board_status is BoardStatus.DISABLED
    reply = officer.reply
    assert f"<#{CHANNEL_ID}>" in reply, "the reply does not name the channel"
    assert "left exactly as they are" in reply, (
        "the reply does not say the posted messages survive"
    )
    assert "/enable_cluster_leaderboard" in reply, (
        "the reply does not name the way back"
    )


@pytest.mark.kpi
@pytest.mark.real_io
def test_a_board_that_is_off_is_never_touched_by_the_cycle(
    typed_port, tasks_cog, board_channel
):
    """AC-001.2 + AC-001.3 — KPI-1.

    BOTH counts, not just `edited`. A pass that deleted the messages and
    re-sent them satisfies "nothing was edited" while doing exactly what
    DISCUSS D2 forbids.

    `config_survived` is the slot separating a pause from a teardown: the loop
    already removes configs whose channel has vanished, and a skip written
    into that branch would delete the board it was asked to pause.
    """
    _seed(typed_port, status=BoardStatus.DISABLED)

    calls = _run_cycle(tasks_cog, typed_port, board_channel).calls

    assert calls.is_silent, f"a paused board was touched: {calls!r}"
    assert _stored(typed_port) is not None, "a pause removed the board's config"


@pytest.mark.kpi
@pytest.mark.real_io
def test_turning_a_board_off_changes_nothing_except_the_switch(
    typed_port, admin_cog, officer
):
    """AC-001.4. A pause that dropped `messages` would orphan the posted board."""
    before = _seed(typed_port)

    _run_command(admin_cog, BoardCommand.TURN_OFF, officer, typed_port)
    after = _stored(typed_port)

    assert after.channel_id == before.channel_id
    assert after.messages == before.messages
    assert after.season == before.season
    assert after.board_status is BoardStatus.DISABLED


@pytest.mark.kpi
@pytest.mark.real_io
def test_a_pause_outlives_the_process(typed_port, admin_cog, officer):
    """AC-001.5 — KPI-2.

    A status held only in the loaded dict comes back ON at the next restart,
    which for a board an operator deliberately paused is the same as not
    having the feature. Re-reading through the port is the restart: the
    process-wide singleton is rebuilt from storage on boot and nothing else
    survives.
    """
    _seed(typed_port)
    _run_command(admin_cog, BoardCommand.TURN_OFF, officer, typed_port)

    reloaded = typed_port.load_live_leaderboards(SERVER_ID)["cluster"]

    assert reloaded.board_status is BoardStatus.DISABLED


@pytest.mark.error
@pytest.mark.driving_port
@pytest.mark.parametrize("command", list(BoardCommand), ids=lambda c: c.command_name)
def test_a_command_against_a_board_that_does_not_exist_is_refused(
    typed_port, admin_cog, officer, command: BoardCommand
):
    """AC-001.6. A status stored against a board that does not exist would be
    silently discarded by the next `/set_live_cluster_leaderboard`, which
    rebuilds the config from scratch. The refusal says so instead of appearing
    to work."""
    typed_port.save_live_leaderboards(SERVER_ID, {})

    _run_command(admin_cog, command, officer, typed_port)

    assert officer.reply.startswith("❌")
    assert "/set_live_cluster_leaderboard" in officer.reply
    assert typed_port.load_live_leaderboards(SERVER_ID) == {}


@pytest.mark.error
@pytest.mark.driving_port
@pytest.mark.parametrize("command", list(BoardCommand), ids=lambda c: c.command_name)
def test_asking_for_the_state_it_is_already_in_writes_nothing(
    typed_port, admin_cog, officer, command: BoardCommand
):
    """AC-001.7 + AC-002.4.

    The write matters as much as the words: `save_live_leaderboards` rewrites
    EVERY board on the server, so a no-op that saved anyway would be a
    whole-table rewrite triggered by a command that changed nothing.
    """
    _seed(typed_port, status=command.no_op_when)
    writes: list = []
    original = typed_port.save_live_leaderboards
    typed_port.save_live_leaderboards = lambda sid, data: writes.append(data)
    try:
        _run_command(admin_cog, command, officer, typed_port)
    finally:
        typed_port.save_live_leaderboards = original

    assert "already" in officer.reply, (
        f"a no-op flip did not say so: {officer.reply!r}"
    )
    assert writes == [], "a no-op flip wrote to storage"


@pytest.mark.error
@pytest.mark.driving_port
@pytest.mark.parametrize("command", list(BoardCommand), ids=lambda c: c.command_name)
def test_only_an_officer_may_change_whether_the_board_publishes(
    typed_port, admin_cog, non_officer, command: BoardCommand
):
    """AC-001.8.

    The AC the shipped unit tests never exercised: they built an administrator
    every time, so the tier decorator was in the call chain but never in the
    assertion.

    THIS TEST THEN SHIPPED THE MIRROR IMAGE OF THAT BUG. Its premise was
    exactly backwards: the decorator was not merely absent from the assertion,
    it was never in the call chain at all, because the harness resolved the
    command to its `.callback` and `@require_tier` lives on `Command.checks`.
    The test written to close the gap had the same hole, pointed the other way.
    `_invoke` is the fix; this docstring is the record.

    The second, quieter hole: seeding ACTIVE for BOTH commands made the
    `enable` case vacuous, because ACTIVE is where `enable` was going anyway.
    The callback's own no-op branch satisfied the assertion, so that
    parametrization would have stayed green with the tier gate deleted. Each
    command is now seeded in the state it WOULD move the board out of, so the
    unchanged status is evidence about the gate rather than about the no-op.
    """
    # Any status that is not this command's destination is one it would change.
    starts_from = next(s for s in BoardStatus if s is not command.drives_to)
    _seed(typed_port, status=starts_from)

    _run_command(admin_cog, command, non_officer, typed_port)

    assert _stored(typed_port).board_status is starts_from, (
        f"a member below the officer tier drove the board to {command.drives_to.value}"
    )
    assert non_officer.reply == "", (
        "the callback ran far enough to answer a member the tier gate should "
        f"have stopped: {non_officer.reply!r}"
    )


# ===========================================================================
# US-002 — turn it back on
# ===========================================================================

@pytest.mark.driving_port
@pytest.mark.real_io
def test_an_officer_turns_the_board_back_on(typed_port, admin_cog, officer):
    """AC-002.1."""
    _seed(typed_port, status=BoardStatus.DISABLED)

    _run_command(admin_cog, BoardCommand.TURN_ON, officer, typed_port)

    assert _stored(typed_port).board_status is BoardStatus.ACTIVE
    assert f"<#{CHANNEL_ID}>" in officer.reply
    assert "next hourly cycle" in officer.reply


@pytest.mark.kpi
@pytest.mark.real_io
def test_resuming_within_the_same_season_edits_the_board_already_there(
    typed_port, admin_cog, officer, tasks_cog, board_channel
):
    """AC-002.2. Resume must not post a duplicate set beneath the frozen one —
    that would make pausing more destructive than never pausing."""
    _seed(typed_port, status=BoardStatus.DISABLED, season=SEASON)

    _run_command(admin_cog, BoardCommand.TURN_ON, officer, typed_port)
    calls = _run_cycle(tasks_cog, typed_port, board_channel, season=SEASON).calls

    assert calls.edited, "a resumed board on the same season was not edited"
    assert not calls.sent, (
        f"a same-season resume posted new messages: {calls.sent!r}"
    )


@pytest.mark.driving_port
@pytest.mark.real_io
def test_setting_a_board_up_again_brings_it_back_on(
    typed_port, admin_cog, officer, board_channel
):
    """AC-002.5, end-to-end. Previously covered only by the Tier B model.

    The gap was recorded rather than closed because driving the full setup
    command needs a season lookup and a complete tier post. That harness now
    exists — it was built for the `/set_live_leaderboard` regression the
    review gate turned up — so the reason to skip this stopped applying.

    The trap it guards is a silent one. Setup rebuilds the config from
    scratch, so a paused board must come back ON; if a stale pause survived,
    the officer would get a freshly-posted set of messages that then never
    update — a no-op wearing a success message, which is this feature's own
    failure mode arriving from the opposite direction.
    """
    import httpx

    from bot import guild_keys

    _seed(typed_port, status=BoardStatus.DISABLED)
    guilds = {"neuro": {"name": "Neuro", "api_key": "key-for-neuro"}}

    originals = {
        (admin_cog, "load_guilds"): lambda sid: dict(guilds),
        (admin_cog, "load_live_leaderboards"): lambda sid: typed_port.load_live_leaderboards(sid),
        (admin_cog, "save_live_leaderboards"): lambda sid, data: typed_port.save_live_leaderboards(sid, data),
        (admin_cog, "repo"): _NoRaidRows(),
        (admin_cog, "get_player_list"): lambda sid, gid: {},
        (guild_keys, "active_key"): lambda sid, gid: "key-for-neuro",
        (httpx, "AsyncClient"): lambda *a, **k: _FakeSeasonLookup(),
    }
    previous = {(m, t): getattr(m, t) for m, t in originals}
    try:
        for (module, target), replacement in originals.items():
            setattr(module, target, replacement)
        asyncio.run(_invoke(
            admin_cog, "set_live_cluster_leaderboard", officer,
            channel=board_channel,
        ))
    finally:
        for (module, target), original in previous.items():
            setattr(module, target, original)

    assert _stored(typed_port).board_status is BoardStatus.ACTIVE, (
        "setting the board up again left it paused — the officer would get a "
        "freshly-posted board that never updates"
    )


@pytest.mark.real_io
def test_resuming_after_a_rollover_starts_a_fresh_board(
    typed_port, admin_cog, officer, tasks_cog, board_channel
):
    """AC-002.3. `season` is deliberately NOT advanced while paused, so the
    rollover stays detectable on resume. Advancing it would make the resume
    edit last season's messages with this season's numbers."""
    _seed(typed_port, status=BoardStatus.DISABLED, season=EARLIER_SEASON)

    _run_command(admin_cog, BoardCommand.TURN_ON, officer, typed_port)
    calls = _run_cycle(tasks_cog, typed_port, board_channel, season=SEASON).calls

    assert calls.sent, "a rolled-over resume did not post a fresh set"
    assert not calls.edited, (
        f"a rolled-over resume edited the frozen archive: {calls.edited!r}"
    )


# ===========================================================================
# US-005 — a pause leaves a trace an operator can find later
# ===========================================================================

@pytest.mark.kpi
@pytest.mark.real_io
@pytest.mark.parametrize(
    "starting,command,went_from,went_to",
    [
        (BoardStatus.ACTIVE, BoardCommand.TURN_OFF, BoardStatus.ACTIVE, BoardStatus.DISABLED),
        (BoardStatus.DISABLED, BoardCommand.TURN_ON, BoardStatus.DISABLED, BoardStatus.ACTIVE),
    ],
    ids=["turned-off", "turned-on"],
)
def test_a_state_change_is_recorded_where_an_operator_can_find_it(
    typed_port, admin_cog, officer, board_events,
    starting: BoardStatus, command: BoardCommand,
    went_from: BoardStatus, went_to: BoardStatus,
):
    """`/view_config` answers "is it paused NOW". Nothing answers "since when".

    A board paused months ago is exactly the one nobody remembers pausing, and
    the status line is a point-in-time query a human has to think to run. This
    record is the only thing that makes the pause reconstructable afterwards.

    Asserted on `record.event` rather than on the rendered line, because
    `emit_structured` attaches fields via `extra=` so readers need not re-parse
    JSON. The event NAME is pinned because the operator's grep and this test
    have to break together.
    """
    _seed(typed_port, status=starting)
    board_events.clear()

    _run_command(admin_cog, command, officer, typed_port)

    records = board_events.named(BOARD_STATUS_CHANGED_EVENT)
    assert len(records) == 1, (
        f"expected exactly one {BOARD_STATUS_CHANGED_EVENT} record for a real "
        f"state change, got {len(records)}"
    )
    record = records[0]
    assert getattr(record, "scope_key", None) == "cluster"
    assert getattr(record, "from_status", None) == went_from.value
    assert getattr(record, "to_status", None) == went_to.value


@pytest.mark.error
@pytest.mark.real_io
@pytest.mark.parametrize("command", list(BoardCommand), ids=lambda c: c.command_name)
def test_a_no_op_flip_records_nothing(
    typed_port, admin_cog, officer, board_events, command: BoardCommand
):
    """The record follows the CHANGE, not the command.

    A command that changed nothing is not a state change, and recording it
    would make the log answer "who ran a command" instead of "when did this
    board's state actually move" — which is the question the record exists for.
    """
    _seed(typed_port, status=command.no_op_when)
    board_events.clear()

    _run_command(admin_cog, command, officer, typed_port)

    assert board_events.named(BOARD_STATUS_CHANGED_EVENT) == [], (
        "a no-op flip left a state-change record"
    )


@pytest.mark.error
@pytest.mark.real_io
def test_an_hour_passing_on_a_paused_board_records_nothing(
    typed_port, tasks_cog, board_channel, board_events
):
    """On change, never per cycle — the operator's explicit call (2026-09-08).

    An hourly record would be roughly 720 entries a month for ONE paused
    board. A log nobody can skim is a log nobody reads, and this feature
    already has one failure mode built on a signal nobody looks at.
    """
    _seed(typed_port, status=BoardStatus.DISABLED)
    board_events.clear()

    for _ in range(24):
        _run_cycle(tasks_cog, typed_port, board_channel)

    assert board_events.named(BOARD_STATUS_CHANGED_EVENT) == [], (
        "the hourly cycle recorded a state change for a board whose state did "
        "not change"
    )


# ===========================================================================
# US-003 — see which boards are off
# ===========================================================================

@pytest.mark.driving_port
@pytest.mark.kpi
@pytest.mark.parametrize(
    "status,expected",
    [(BoardStatus.DISABLED, "Turned off"), (BoardStatus.ACTIVE, "Updating hourly")],
    ids=["turned-off", "running"],
)
def test_the_configuration_view_states_the_publishing_state(
    typed_port, admin_cog, officer, status: BoardStatus, expected: str
):
    """AC-003.1 + AC-003.2 + AC-003.3 — KPI-3.

    Load-bearing, not cosmetic. Because a pause leaves the posted messages
    alone (DISCUSS D2), a frozen board and a live one are identical in the
    channel. This view is the only surface where they differ, which is why
    the line renders for BOTH states and is never omitted — absence of a
    warning must not be the only signal that a board is healthy.
    """
    _seed(typed_port, status=status)

    original = admin_cog.load_live_leaderboards
    admin_cog.load_live_leaderboards = lambda sid: typed_port.load_live_leaderboards(sid)
    try:
        cog = admin_cog.AdminCog.__new__(admin_cog.AdminCog)
        embed = cog._config_leaderboards(SERVER_ID)
    finally:
        admin_cog.load_live_leaderboards = original

    rendered = " ".join(f"{f.name} {f.value}" for f in embed.fields)
    assert expected in rendered, (
        f"the configuration view does not state the board is {status.value}: "
        f"{rendered!r}"
    )
    assert f"<#{CHANNEL_ID}>" in rendered, "the channel is no longer shown"


# ===========================================================================
# US-004 — @infrastructure — the conformance precursor
# ===========================================================================

@pytest.mark.infrastructure
@pytest.mark.real_io
def test_a_board_arrives_as_a_described_configuration(typed_port):
    """AC-004.1 + AC-004.2. Cogs receive a described value, not a bare record,
    and its state is one of the named states."""
    _seed(typed_port)

    config = _stored(typed_port)

    assert isinstance(config, LiveBoardConfig)
    assert isinstance(config.board_status, BoardStatus)
    assert config.is_enabled is True


@pytest.mark.infrastructure
@pytest.mark.kpi
@pytest.mark.real_io
def test_a_board_stored_before_the_switch_existed_reads_as_running(typed_port):
    """AC-004.3.

    Every board that exists today. Materialised as a default VALUE by both
    adapters, exactly as `load_guild_binding` returns `GuildBinding()` rather
    than None — so no reader infers it and no reader needs a `.get(k, default)`.
    """
    _seed(typed_port, status=BoardStatus.ACTIVE)

    config = _stored(typed_port)

    assert config.board_status is BoardStatus.ACTIVE
    assert config.is_enabled is True


@pytest.mark.infrastructure
@pytest.mark.kpi
@pytest.mark.adapter_integration
@pytest.mark.real_io
@pytest.mark.parametrize("status", list(BoardStatus), ids=lambda s: s.value)
def test_both_adapters_agree_about_a_board(json_repo, sqlite_repo, status: BoardStatus):
    """AC-004.4 — KPI-5.

    The parity contract, and the reason the representation changed. The
    shipped shape omitted the field when true PURELY to keep an exact-equality
    assertion passing; ADR-009 DDD-3 holds parity by normalising the default
    in both adapters instead, which is the same move `GuildBinding` makes.
    """
    config = LiveBoardConfig(
        channel_id=CHANNEL_ID,
        messages={"Legendary_0": 999},
        season=SEASON,
        board_status=status,
    )

    json_repo.save_live_leaderboards(SERVER_ID, {"cluster": config})
    sqlite_repo.save_live_leaderboards(SERVER_ID, {"cluster": config})

    assert (
        json_repo.load_live_leaderboards(SERVER_ID)
        == sqlite_repo.load_live_leaderboards(SERVER_ID)
        == {"cluster": config}
    )


@pytest.mark.infrastructure
def test_exactly_one_place_decides_whether_a_board_publishes():
    """AC-004.5.

    ADR-009 DDD-7 as an executed claim rather than a prose one. A predicate
    beside the data can be forgotten by the next call site; one on the type
    cannot — but only while nothing else compares the literal. Mirrors
    ADR-008's "cogs never compare `key_status` themselves".
    """
    from pathlib import Path

    # Absolute, derived from this file. pytest runs with cwd = the suite
    # directory, so a relative path here raises FileNotFoundError — which
    # classifies BROKEN and tells a crafter the test is wrong rather than the
    # code. Caught by the pre-DELIVER gate on 2026-09-08.
    repo_root = Path(__file__).resolve().parents[3]

    def _mentions_a_status_literal(path) -> bool:
        source = path.read_text(encoding="utf-8")
        return any(
            f'"{s.value}"' in source or f"'{s.value}'" in source for s in BoardStatus
        )

    # Half one: the owner MUST hold the comparison. Without this the assertion
    # below is vacuously true today — no module mentions the literals at all,
    # because the concept does not exist yet. A one-sided check would report
    # GREEN for a codebase that has never heard of board_status, which is the
    # reassuring-but-false signal this whole feature exists to remove.
    owner = repo_root / STATUS_COMPARISON_OWNER
    assert _mentions_a_status_literal(owner), (
        f"{STATUS_COMPARISON_OWNER} does not define the publishing-state "
        "comparison yet (ADR-009 DDD-7, Slice-01 precursor) — so 'nobody else "
        "compares it' is true only because nobody compares it anywhere"
    )

    # Half two: nobody else may.
    offenders = [
        f"{module} compares a publishing-state literal"
        for module in STATUS_READER_MODULES
        if _mentions_a_status_literal(_existing(repo_root / module, module))
    ]

    assert not offenders, (
        "the publishing-state comparison has escaped "
        f"{STATUS_COMPARISON_OWNER}: " + "; ".join(offenders)
    )


def _existing(path, label: str):
    assert path.exists(), f"{label} no longer exists — update STATUS_READER_MODULES"
    return path


@pytest.mark.infrastructure
@pytest.mark.driving_port
@pytest.mark.real_io
def test_setting_up_a_guild_board_still_stores_something_readable(
    typed_port, admin_cog, officer, board_channel
):
    """Regression guard for a driving port DESIGN undercounted.

    `/set_live_leaderboard` — the GUILD-scoped setup command — writes a raw
    dict literal into the same mapping the cluster commands use
    (`admin_cog.py:589-596`) and saves it. DDD-2 changes that mapping to hold
    `LiveBoardConfig`, so this command breaks, and it appeared in no component
    table and no scenario.

    Found by the Final Wave Review Gate's cross-wave check on 2026-09-08:
    DESIGN's blast-radius figure counted config-FIELD reads and reported them
    as CALL sites, which hid this port entirely.

    NOTHING ELSE WOULD CATCH IT. The command IS exercised by
    `guild-key-integrity` slices 03 and 05 and by
    `tests/unit/test_leaderboard_season_fall_through.py` — but every one of
    those uses a repository double, so the write never reaches an adapter and
    a type error at the port would not surface. This test drives it against
    the real store, which is the whole point.
    """
    import httpx

    from bot import guild_keys

    guild_id = "neuro"
    guilds = {guild_id: {"name": "Neuro", "api_key": "key-for-neuro"}}

    originals = {
        (admin_cog, "load_guilds"): lambda sid: dict(guilds),
        (admin_cog, "load_live_leaderboards"): lambda sid: typed_port.load_live_leaderboards(sid),
        (admin_cog, "save_live_leaderboards"): lambda sid, data: typed_port.save_live_leaderboards(sid, data),
        (admin_cog, "repo"): _NoRaidRows(),
        (guild_keys, "active_key"): lambda sid, gid: "key-for-neuro",
        (httpx, "AsyncClient"): lambda *a, **k: _FakeSeasonLookup(),
    }
    previous = {(m, t): getattr(m, t) for m, t in originals}
    try:
        for (module, target), replacement in originals.items():
            setattr(module, target, replacement)
        asyncio.run(_invoke(
            admin_cog, "set_live_leaderboard", officer,
            guild_id=guild_id, channel=board_channel,
        ))
    finally:
        for (module, target), original in previous.items():
            setattr(module, target, original)

    stored = typed_port.load_live_leaderboards(SERVER_ID).get(f"guild:{guild_id}")

    assert isinstance(stored, LiveBoardConfig), (
        "/set_live_leaderboard wrote something the port cannot hand back as a "
        f"LiveBoardConfig — got {type(stored).__name__}. The guild-scoped setup "
        "command is in the DDD-2 blast radius."
    )
    assert stored.board_status is BoardStatus.ACTIVE
    assert stored.guild_id == guild_id


class _FakeSeasonLookup:
    """The current-season endpoint `/set_live_leaderboard` calls before writing."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url: str, headers: dict | None = None, **kwargs):
        import httpx

        return httpx.Response(
            200, json={"season": SEASON}, request=httpx.Request("GET", url)
        )


@pytest.mark.infrastructure
@pytest.mark.kpi
@pytest.mark.real_io
@pytest.mark.parametrize("scope", list(ScopeKind), ids=lambda s: s.name.lower())
def test_any_board_can_be_turned_off_not_only_the_cluster_one(
    typed_port, tasks_cog, board_channel, scope: ScopeKind
):
    """AC-004.6 — ADR-009 DDD-5.

    The column and the loop are scope-agnostic by construction. Pinning it
    here is what stops the guild-scoped commands, when they arrive, finding a
    switch that silently only ever worked for one key.
    """
    _seed(typed_port, status=BoardStatus.DISABLED, scope=scope)

    calls = _run_cycle(tasks_cog, typed_port, board_channel).calls

    assert calls.is_silent, f"a paused {scope.name} board was touched: {calls!r}"


# ===========================================================================
# The upgrade — KPI-4
# ===========================================================================

@pytest.mark.kpi
@pytest.mark.adapter_integration
@pytest.mark.real_io
def test_upgrading_the_database_pauses_nothing(db_before_the_switch):
    """KPI-4. A schema change that pauses every live board on the cluster is
    the outage this feature exists to prevent.

    Drives real alembic against a real database seeded through raw SQL at the
    PREVIOUS revision — setup must not go through the code under test, or a
    migration under construction would silently reshape its own precondition.
    """
    import sqlite3

    from alembic import command

    from board_domain_types import alembic_config

    connection = sqlite3.connect(db_before_the_switch)
    connection.execute("INSERT INTO clusters (discord_server_id) VALUES (?)", (SERVER_ID,))
    connection.execute(
        "INSERT INTO live_leaderboards (discord_server_id, scope_key, guild_id, "
        "channel_id, season) VALUES (?, 'cluster', NULL, ?, ?)",
        (SERVER_ID, CHANNEL_ID, SEASON),
    )
    connection.execute(
        "INSERT INTO live_lb_messages (config_id, tier_value, message_id) "
        "VALUES (1, 'Legendary_0', 999)"
    )
    connection.commit()
    connection.close()

    command.upgrade(alembic_config(db_before_the_switch), "head")

    connection = sqlite3.connect(db_before_the_switch)
    # Assert the column BEFORE selecting it. A bare SELECT on a missing column
    # raises OperationalError, which classifies BROKEN — the gate reads it as
    # a test bug rather than as the migration being unwritten. Caught by the
    # pre-DELIVER gate on 2026-09-08.
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(live_leaderboards)")
    }
    assert "board_status" in columns, (
        "alembic head does not add live_leaderboards.board_status yet "
        f"(ADR-009 DDD-1/DDD-4, Slice-01 precursor). Columns: {sorted(columns)}"
    )
    rows = connection.execute(
        "SELECT board_status, channel_id, season FROM live_leaderboards"
    ).fetchall()
    messages = connection.execute("SELECT COUNT(*) FROM live_lb_messages").fetchone()[0]
    connection.close()

    assert rows == [(BoardStatus.ACTIVE.value, CHANNEL_ID, SEASON)], (
        f"the upgrade did not leave every board running and intact: {rows!r}"
    )
    assert messages == 1, "the upgrade dropped a board's messages"
