"""Slice 01 — bind and report identity. Implements
`acceptance/slice-01-bind-and-report.feature`.

Covers US-006 (binding store), US-001 (bind + display), US-002 (report drift
without blocking). Every test is a RED scaffold: DELIVER unskips them one at
a time.

Driving ports used here, per the port-to-port principle:
  * `bot.cogs.tasks_cog.TasksCog.auto_update`  — the hourly loop
  * `bot.cogs.admin_cog.AdminCog._config_guilds` — the /view_config read side
  * `bot.guilds.save_guild_binding` / `load_guild_binding` — the storage seam
Nothing enters through `bot.guild_keys` internals; the chokepoint is
exercised through the surfaces that call it.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from domain_types import (
    DARK_MECHANICUM,
    WORD_BEARERS,
    WORD_BEARERS_RETAGGED,
    DeadKeyStatus,
    GuildIdentity,
    ProbeOutcome,
    TransportFailure,
)
from conftest import GUILD_WB, PROD_SERVER_ID, GuildServiceResponse, alembic_config

RED = pytest.mark.skip(reason="RED scaffold — enable one at a time in DELIVER")


# ===========================================================================
# US-006 — the binding store
# ===========================================================================

@pytest.mark.real_io
@pytest.mark.adapter_integration
def test_upgrade_creates_the_binding_store_and_touches_no_guild_record(
    db_at_previous_head: Path,
):
    """AC-006.1 (restated per DDD-4).

    DISCUSS wrote this AC against columns added to `guilds`. DESIGN's DDD-4
    moved binding state into its own `guild_key_bindings` table precisely so
    that `save_guilds` cannot clobber it, which makes the original wording
    unreachable. The INTENT — an additive migration that alters no existing
    row — is what is asserted. See distill/upstream-issues.md UI-1.

    Do NOT reintroduce the `sqlite_repo` parameter. It was requested here but
    never used in the body, and it silently defeated the test: `sqlite_repo`
    depends on `migrated_db`, which upgrades THIS SAME `sqlite_db_path` to
    head. Both `before` and `after` were then read from an already-migrated
    database, so the column-list assertion — the one guarding DDD-4's whole
    reason for existing — would have passed even if the revision DID add a
    column to `guilds`. Found during DELIVER 02-01; see the feature-delta
    `## Wave: DELIVER / [WHY] Upstream Issues`, UD-2.
    """
    from alembic import command

    with sqlite3.connect(db_at_previous_head) as conn:
        before = conn.execute("SELECT * FROM guilds ORDER BY guild_id").fetchall()
        guild_cols_before = [r[1] for r in conn.execute("PRAGMA table_info(guilds)")]

    command.upgrade(alembic_config(db_at_previous_head), "head")

    with sqlite3.connect(db_at_previous_head) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert "guild_key_bindings" in tables
        assert conn.execute("SELECT COUNT(*) FROM guild_key_bindings").fetchone()[0] == 0

        after = conn.execute("SELECT * FROM guilds ORDER BY guild_id").fetchall()
        guild_cols_after = [r[1] for r in conn.execute("PRAGMA table_info(guilds)")]

    assert after == before, "the upgrade altered an existing guild row"
    assert guild_cols_after == guild_cols_before, (
        "the upgrade added a column to `guilds` — DDD-4 puts binding state in "
        "its own table so that save_guilds cannot overwrite it"
    )


@pytest.mark.real_io
@pytest.mark.error
def test_downgrade_restores_the_prior_shape_exactly(db_at_previous_head: Path):
    """AC-006.2. The rollback half of the DEVOPS ordering constraint: the
    probe refuses on `alembic_version != head` in BOTH directions, so a
    downgrade that does not clean up leaves the unit unable to start."""
    from alembic import command

    with sqlite3.connect(db_at_previous_head) as conn:
        before = sorted(r[0] for r in conn.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"
        ))

    cfg = alembic_config(db_at_previous_head)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "-1")

    with sqlite3.connect(db_at_previous_head) as conn:
        after = sorted(r[0] for r in conn.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"
        ))

    assert after == before


@pytest.mark.error
def test_load_and_save_unchanged_preserves_every_field(bound_guild, env_vars):
    """AC-006.3 + AC-001.7 — the `bot/guilds.py:83-97` round-trip trap.

    `save_guilds` rebuilds each `Guild` from a five-key dict. DESIGN's
    correction established this is harmless on SQLite TODAY, and becomes a
    live clobber the moment anyone threads a binding field through the
    `Guild` dataclass. This test is the tripwire on that future mistake, so
    it must keep running even though it currently cannot fail.

    Takes `bound_guild`, not a bare repository. Without a registered guild AND
    a stored binding this compared an unbound placeholder against an unbound
    placeholder and an empty dict against an empty dict — green while
    asserting nothing about a round trip. A tripwire with no wire is worse
    than no tripwire, because it reports coverage it does not have.
    """
    from bot.guilds import load_guilds, save_guilds, load_guild_binding

    binding_before = load_guild_binding(PROD_SERVER_ID, GUILD_WB)
    guilds_before = load_guilds(PROD_SERVER_ID)

    save_guilds(PROD_SERVER_ID, load_guilds(PROD_SERVER_ID))

    assert load_guilds(PROD_SERVER_ID) == guilds_before
    assert load_guild_binding(PROD_SERVER_ID, GUILD_WB) == binding_before


# ===========================================================================
# US-001 — bind the identity and show it
# ===========================================================================

@pytest.mark.driving_port
@pytest.mark.kpi
async def test_first_verification_adopts_the_identity_and_announces_once(
    sqlite_repo, matching_guild, update_channel, key_events
):
    """AC-001.1 + AC-001.3 — trust-on-first-use (DDD-8).

    The announcement IS the verification step. There is no historical record
    to reconstruct a binding from, so a silent adoption would bind whatever
    the key happened to resolve to on deploy day — including, if the deploy
    had happened on 2026-07-29, Dark Mechanicum.
    """
    from bot.guilds import load_guild_binding

    await _run_hourly_cycle(matching_guild, update_channel)

    binding = load_guild_binding(PROD_SERVER_ID, GUILD_WB)
    assert binding.tacticus_guild_id == WORD_BEARERS.uuid
    assert binding.tacticus_guild_tag == WORD_BEARERS.tag
    assert binding.tacticus_guild_name == WORD_BEARERS.name
    assert binding.identity_bound_at is not None

    bound = key_events.named("guild.key.bound")
    assert len(bound) == 1
    assert WORD_BEARERS.name in update_channel.text
    assert update_channel.text.count(WORD_BEARERS.name) == 1


@RED
async def test_second_verification_refreshes_the_date_without_announcing(
    sqlite_repo, matching_guild, update_channel, key_events
):
    """AC-001.4. An announcement on every cycle is alert fatigue by
    construction, and would bury the one announcement that matters."""
    from bot.guilds import load_guild_binding

    await _run_hourly_cycle(matching_guild, update_channel)
    first = load_guild_binding(PROD_SERVER_ID, GUILD_WB).identity_bound_at
    update_channel.messages.clear()

    await _run_hourly_cycle(matching_guild, update_channel)
    second = load_guild_binding(PROD_SERVER_ID, GUILD_WB).identity_bound_at

    assert second >= first
    assert key_events.named("guild.key.bound") == []
    assert WORD_BEARERS.name not in update_channel.text


@RED
@pytest.mark.driving_port
def test_guild_list_shows_the_bound_guild_and_when_it_was_checked(sqlite_repo, env_vars):
    """AC-001.2. Entered through the read side of /view_config, not through
    a formatter helper — the AC is about what the officer sees."""
    embed = _config_guilds_embed()
    field = _guild_field(embed, GUILD_WB)

    assert WORD_BEARERS.tag in field
    assert WORD_BEARERS.short in field
    assert "verified" in field.lower()


@pytest.mark.error
@pytest.mark.kpi
async def test_missing_identifier_is_unverifiable_and_never_falls_back_to_the_tag(
    sqlite_repo, unverifiable_guild, update_channel, key_events
):
    """AC-001.5 / DDD-10 — the load-bearing negative.

    The failure this guards is not "we got it wrong", it is "we quietly got
    weaker". A fallback to comparing `guildTag` would leave every alert green
    while the actual guarantee had evaporated — the same shape as the
    original incident, where the system looked healthy for three days.

    Both guilds in the incident share the 【UNDV】 alliance prefix, so a tag
    comparison is exactly the check that would have looked reassuring and
    proved nothing.
    """
    from bot.guilds import load_guild_binding

    await _run_hourly_cycle(unverifiable_guild, update_channel)

    assert key_events.named("guild.key.unverifiable")
    assert load_guild_binding(PROD_SERVER_ID, GUILD_WB).key_status == "active"
    assert not key_events.any_named("guild.key.mismatch", "guild.key.quarantined")
    assert "verification is offline" in update_channel.text.lower()

    # The tag was present in the response and MUST NOT have been used.
    assert not key_events.any_named("guild.key.bound"), (
        "a binding was written from a response with no identifier — the only "
        "field left to bind on was the tag"
    )


@RED
@pytest.mark.error
@pytest.mark.parametrize(
    "drop_fields",
    [("guildTag",), ("name",), ("guildTag", "name")],
    ids=["tag", "name", "tag-and-name"],
)
async def test_missing_display_field_still_binds_on_the_identifier(
    sqlite_repo, fake_guild_service, update_channel, drop_fields
):
    """AC-001.6. Display fields are never load-bearing (D1). A guild that
    has not set a tag must still be protected."""
    from bot.guilds import load_guild_binding

    fake_guild_service.program(
        "wb-key",
        GuildServiceResponse(
            identity=WORD_BEARERS, members=["u1"], drop_fields=drop_fields
        ),
    )
    await _run_hourly_cycle(fake_guild_service, update_channel)

    binding = load_guild_binding(PROD_SERVER_ID, GUILD_WB)
    assert binding.tacticus_guild_id == WORD_BEARERS.uuid

    field = _guild_field(_config_guilds_embed(), GUILD_WB)
    assert "—" in field


@pytest.mark.error
@pytest.mark.driving_port
def test_changing_the_ping_channel_leaves_the_binding_untouched(bound_guild, env_vars):
    """AC-001.7. The round-trip trap approached from the user's side: an
    unrelated admin command must not be able to erase identity state.

    `bound_guild` supplies the `Given a guild with a stored binding` the
    Gherkin declares. Without it `load_guilds` returned `{}` and this raised
    KeyError before reaching its assertion.
    """
    from bot.guilds import load_guild_binding, save_guilds, load_guilds

    before = load_guild_binding(PROD_SERVER_ID, GUILD_WB)

    guilds = load_guilds(PROD_SERVER_ID)
    guilds[GUILD_WB]["notification_channel_id"] = 999888777
    save_guilds(PROD_SERVER_ID, guilds)

    assert load_guild_binding(PROD_SERVER_ID, GUILD_WB) == before


@pytest.mark.error
@pytest.mark.parametrize("status", list(DeadKeyStatus), ids=lambda s: str(s.value))
async def test_rejected_key_is_dead_not_quarantined(
    sqlite_repo, fake_guild_service, update_channel, key_events, status: DeadKeyStatus
):
    """AC-001.8 / D4. A dead key returns no data, so there is nothing to
    contaminate. Quarantining it would add a recovery step for zero safety."""
    from bot.guilds import load_guild_binding

    fake_guild_service.program("wb-key", GuildServiceResponse(status=status.value))
    await _run_hourly_cycle(fake_guild_service, update_channel)

    assert key_events.named("guild.key.dead")
    assert not key_events.any_named("guild.key.quarantined", "guild.key.bound")
    assert load_guild_binding(PROD_SERVER_ID, GUILD_WB).key_status == "active"


@pytest.mark.error
@pytest.mark.kpi
@pytest.mark.parametrize("failure", list(TransportFailure), ids=lambda f: f.value)
async def test_unreachable_leaves_the_binding_byte_identical(
    sqlite_repo, fake_guild_service, update_channel, key_events,
    failure: TransportFailure,
):
    """AC-001.9 / D6 — the decision that keeps a Tacticus outage from
    quarantining the entire cluster.

    Byte-identical, not "still active": an implementation that rewrites
    `identity_bound_at` on a failed probe would report a fresh verification
    date for a check that never happened, which is worse than no date.
    """
    from bot.guilds import load_guild_binding

    before = load_guild_binding(PROD_SERVER_ID, GUILD_WB)
    fake_guild_service.program("wb-key", _transport_failure(failure))

    await _run_hourly_cycle(fake_guild_service, update_channel)

    assert key_events.named("guild.key.unreachable")
    assert load_guild_binding(PROD_SERVER_ID, GUILD_WB) == before
    assert not key_events.any_named("guild.key.quarantined")


# ===========================================================================
# US-002 — report drift, block nothing
# ===========================================================================

@pytest.mark.kpi
async def test_drift_is_reported_naming_both_guilds(
    sqlite_repo, drifted_guild, update_channel, key_events
):
    """AC-002.1 + AC-002.7 — the incident replay, reporting only.

    The comparison is on uuid; the MESSAGE must carry names and tags, because
    a uuid pair tells an operator nothing about what to do next.
    """
    await _run_hourly_cycle(drifted_guild, update_channel)

    (record,) = key_events.named("guild.key.mismatch")
    assert record.bound_id == WORD_BEARERS.uuid
    assert record.observed_id == DARK_MECHANICUM.uuid

    text = update_channel.text
    for token in (
        WORD_BEARERS.name, WORD_BEARERS.tag, WORD_BEARERS.short,
        DARK_MECHANICUM.name, DARK_MECHANICUM.tag, DARK_MECHANICUM.short,
    ):
        assert token in text, f"the mismatch report omits {token!r}"


async def test_slice_01_still_ingests_on_a_mismatch(
    sqlite_repo, drifted_guild, update_channel
):
    """AC-002.2. Deliberately asserts the non-blocking intermediate state.

    This test is expected to be INVERTED by Slice 03, not deleted — the
    inversion is the visible record that enforcement turned on. Deleting it
    instead would leave no evidence the intermediate state ever shipped.
    """
    before = _hit_counts(sqlite_repo)
    await _run_hourly_cycle(drifted_guild, update_channel)

    assert _hit_counts(sqlite_repo) != before
    assert not _is_quarantined(GUILD_WB)


@pytest.mark.driving_port
async def test_mismatch_appears_in_the_hourly_summary(
    sqlite_repo, fake_guild_service, update_channel
):
    """AC-002.3. The mismatch has to appear where the operator already
    looks; a line in `discord.log` nobody greps is not detection."""
    fake_guild_service.program("wb-key", GuildServiceResponse(identity=DARK_MECHANICUM))
    fake_guild_service.program("dm-key", GuildServiceResponse(identity=DARK_MECHANICUM))

    await _run_hourly_cycle(fake_guild_service, update_channel)

    text = update_channel.text
    assert "⚠️" in text or "mismatch" in text.lower()
    assert "✅" in text


@pytest.mark.kpi
async def test_a_matching_guild_is_completely_silent(
    sqlite_repo, matching_guild, update_channel, ping_channel, key_events
):
    """AC-002.4 — the silence test.

    A suite that only covers drift passes against an implementation that
    alerts on every cycle. This is the scenario that fails it, and it is the
    empirical basis for KPI-4's zero-false-positive target.
    """
    await _run_hourly_cycle(matching_guild, update_channel)

    assert not key_events.any_named(
        "guild.key.mismatch", "guild.key.quarantined",
        "guild.key.unverifiable", "guild.key.alert.sent",
    )
    assert "mismatch" not in update_channel.text.lower()
    assert ping_channel.messages == []


@RED
@pytest.mark.error
@pytest.mark.parametrize(
    "resolved",
    [
        GuildIdentity(WORD_BEARERS.uuid, "WBRRS", WORD_BEARERS.name),
        GuildIdentity(WORD_BEARERS.uuid, WORD_BEARERS.tag, "【UNDV】Word Bearers Reborn"),
        WORD_BEARERS_RETAGGED,
    ],
    ids=["tag-changed", "name-changed", "both-changed"],
)
async def test_a_retag_or_rename_is_not_a_mismatch(
    sqlite_repo, fake_guild_service, update_channel, key_events,
    resolved: GuildIdentity,
):
    """AC-002.5 — the direct consequence of binding on uuid (D1).

    Guilds retag and rename routinely. If either tripped the lock, Slice 03
    would quarantine healthy guilds on a cosmetic change, and the operator
    would learn to ignore the alert.
    """
    from bot.guilds import load_guild_binding

    fake_guild_service.program("wb-key", GuildServiceResponse(identity=resolved))
    await _run_hourly_cycle(fake_guild_service, update_channel)

    assert not key_events.any_named("guild.key.mismatch", "guild.key.alert.sent")

    binding = load_guild_binding(PROD_SERVER_ID, GUILD_WB)
    assert binding.tacticus_guild_id == WORD_BEARERS.uuid
    assert binding.tacticus_guild_tag == resolved.tag
    assert binding.tacticus_guild_name == resolved.name


@pytest.mark.kpi
async def test_a_persistent_mismatch_is_reported_every_cycle_in_this_slice(
    sqlite_repo, drifted_guild, update_channel, key_events
):
    """AC-002.6. No suppression in Slice 01 — suppression arrives with
    quarantine in Slice 03, where the state persists by design. Here a
    repeat means the operator has not acted yet, and that is worth saying."""
    await _run_hourly_cycle(drifted_guild, update_channel)
    await _run_hourly_cycle(drifted_guild, update_channel)

    assert len(key_events.named("guild.key.mismatch")) == 2


@pytest.mark.kpi
async def test_the_detection_latency_is_computable_from_the_records(
    sqlite_repo, fake_guild_service, update_channel, key_events
):
    """KPI-1's formula, asserted at the point it is actually derived.

    Added 2026-08-01 after the Final Wave Review Gate. Every other KPI-1
    scenario asserts that probe.ok, mismatch and alert.sent are EMITTED.
    None of them asserted that `alerted_at − last_probe_ok_at` can be
    computed from what was emitted — which is the whole metric.

    That gap matters because KPI-1 was already replaced once (DEVOPS U2) for
    being unfalsifiable. Emitting three correctly-named records with an
    unparseable or missing timestamp would reproduce the same failure in a
    new place: a dashboard that renders and means nothing.

    This does NOT claim anything about production latency — the clock here is
    ours. It claims the formula has operands.
    """
    from datetime import datetime, timedelta

    fake_guild_service.program("wb-key", GuildServiceResponse(identity=WORD_BEARERS))
    await _run_hourly_cycle(fake_guild_service, update_channel)

    fake_guild_service.program("wb-key", GuildServiceResponse(identity=DARK_MECHANICUM))
    await _run_hourly_cycle(fake_guild_service, update_channel)

    (probe_ok,) = key_events.named("guild.key.probe.ok")
    (alert,) = key_events.named("guild.key.alert.sent")

    last_probe_ok_at = datetime.fromisoformat(probe_ok.ts)
    alerted_at = datetime.fromisoformat(alert.ts)

    latency = alerted_at - last_probe_ok_at
    assert latency > timedelta(0), (
        "the alert is not after the last agreeing probe — the operands are "
        "reversed or one timestamp is not real"
    )
    assert latency <= timedelta(hours=1, minutes=5), (
        f"detection_latency={latency} exceeds one loop interval plus alert "
        "latency; a cycle was missed or the loop is throttled"
    )


# ===========================================================================
# Helpers — thin, no branching (Mandate-12 criterion 3 keeps logic out of
# the test bodies above; these are wiring, not policy).
# ===========================================================================
from contextlib import contextmanager  # noqa: E402 — helpers-only dependency


async def _run_hourly_cycle(guild_service, channel) -> None:
    """Drive one `auto_update` tick through the production composition root.

    The double goes in at the Tacticus transport, BELOW
    `bot.services.tacticus.guild_client` — so every classification a scenario
    asserts on (MATCH / MISMATCH / UNVERIFIABLE / UNREACHABLE / DEAD) is made
    by production code reading a real vendor body, not by this helper. Wire it
    any shallower and the scenarios stop proving the loop is wired and start
    proving the helper is.

    The litmus test the port-to-port principle asks for: delete the
    `verify_and_resolve` call site in `tasks_cog` and every scenario here goes
    red, because no `guild.key.*` record is emitted at all.
    """
    _register_the_guilds_the_scenario_programmed(guild_service)
    _restore_the_binding_the_environment_declares(guild_service)
    cog = _tasks_cog_posting_to(channel)
    with _tacticus_answered_by(guild_service):
        await cog.auto_update()


def _register_the_guilds_the_scenario_programmed(guild_service) -> None:
    """Register exactly the guilds whose key the scenario programmed an answer for.

    Not both guilds unconditionally: `FakeGuildService.answer_for` refuses an
    unprogrammed key precisely so a scenario cannot silently exercise a path it
    did not declare, and registering a guild nobody programmed would trip that
    guard on every single-guild scenario.

    Idempotent — several scenarios run two cycles, and `save_guilds` leaves
    binding state alone by construction (DDD-4 keeps it in its own table).
    """
    from bot.guilds import save_guilds

    programmed = set(guild_service._by_key)
    if guild_service._default is not None:
        programmed = set(_GUILD_REGISTRY)

    save_guilds(PROD_SERVER_ID, {
        _GUILD_REGISTRY[key][0]: _GUILD_REGISTRY[key][1]
        for key in _GUILD_REGISTRY
        if key in programmed
    })


# Word Bearers FIRST: `auto_update` derives the season from the first guild
# that can answer, and the season SPOF only misbehaves in that ordering.
_GUILD_REGISTRY: dict[str, tuple[str, dict]] = {
    "wb-key": ("word_bearers", {
        "name": "Word Bearers", "api_key": "wb-key", "role_id": 1,
        "notification_channel_id": 4242, "member_role_ids": [],
    }),
    "dm-key": ("dark_mechanicum", {
        "name": "Dark Mechanicum", "api_key": "dm-key", "role_id": 2,
        "notification_channel_id": None, "member_role_ids": [],
    }),
}

# Which Tacticus guild each registered guild IS. Both identities are the real
# ones from the 2026-07-28 incident, so a failure prints the values in the
# postmortem.
_CANONICAL_IDENTITY = {"word_bearers": WORD_BEARERS, "dark_mechanicum": DARK_MECHANICUM}


def _restore_the_binding_the_environment_declares(guild_service) -> None:
    """Write the historical half of a `bound-drifted` environment.

    `environments.yaml` defines `bound-drifted` as TWO facts: the guild is
    bound to Word Bearers, AND its key now resolves to Dark Mechanicum. The
    `drifted_guild` fixture programs the second. The first is history — the
    guild was verified for weeks before the key-holder changed guilds — and no
    fixture writes it. Without it the drift scenarios would quietly become
    trust-on-first-use scenarios and pass against an implementation that never
    compares anything, which is the exact failure shape this feature exists to
    prevent.

    A guild whose key still resolves to its own identity is left UNBOUND, so
    the production TOFU path (DDD-8) is what writes its binding and never this
    helper. `_by_key` is read rather than `answer_for` so peeking does not
    register a call the scenarios count.

    UPSTREAM GAP (reported at DELIVER 03-03): this inference belongs in a
    fixture that states the Given outright. See the report for step 03-03.
    """
    from bot.guilds import load_guild_binding, save_guild_binding
    from bot.repository import GuildBinding

    for key, (guild_id, _) in _GUILD_REGISTRY.items():
        answer = guild_service._by_key.get(key)
        canonical = _CANONICAL_IDENTITY[guild_id]
        if answer is None or answer.identity is None or answer.identity.uuid == canonical.uuid:
            continue
        if not load_guild_binding(PROD_SERVER_ID, guild_id).is_unbound:
            continue
        save_guild_binding(PROD_SERVER_ID, guild_id, GuildBinding(
            tacticus_guild_id=canonical.uuid,
            tacticus_guild_tag=canonical.tag,
            tacticus_guild_name=canonical.name,
            identity_bound_at="2026-07-27T04:00:00.000Z",
        ))


def _tasks_cog_posting_to(channel):
    """The real cog, minus the scheduler.

    `TasksCog.__init__` calls `.start()` on both `@tasks.loop`s, which would
    hand the hourly cycle to the event loop and race the assertions. `__new__`
    plus the two collaborators the composition root supplies gives the real
    object with the real loop body and no scheduler.
    """
    from bot.cogs.tasks_cog import TasksCog
    from bot.services.chronicl3r.player_service import PlayerService

    cog = TasksCog.__new__(TasksCog)
    cog.bot = _FakeBot(channel)
    cog.player_service = PlayerService(_FakeChroniclerClient())
    return cog


class _FakeBot:
    """`UPDATE_CHANNEL_ID` is 0 under test (conftest sets it before collection),
    so the single global update channel is whatever the scenario passed in."""

    def __init__(self, channel) -> None:
        self._channel = channel

    def get_channel(self, channel_id: int):
        return self._channel


class _FakeChroniclerClient:
    """Chronicler is a paid external service — faked, per Mandate 5 strategy B.

    Validates its inputs the way the real client's HTTP layer would: a test
    double that accepts a user id the real one would 4xx on is a double that
    lies, and the wiring bug it hides only shows up in production.
    """

    def register_user(self, tacticus_user_id: str) -> dict:
        return self.get_profile(tacticus_user_id)

    def get_profile(self, tacticus_user_id: str) -> dict:
        assert tacticus_user_id, "chronicl3r rejects an empty tacticus_user_id"
        return {
            "tacticus_user_id": tacticus_user_id,
            "tacticus_display_nm": f"player-{tacticus_user_id}",
        }


@contextmanager
def _tacticus_answered_by(guild_service):
    """Answer every Tacticus call from the scenario's programmed doubles.

    Swapping `httpx.AsyncClient` rather than a production function keeps ALL
    the classification in production code: a 401 becomes DEAD, a 500 becomes
    UNREACHABLE and a body with no `guildId` becomes UNVERIFIABLE because
    `guild_client` decided so, not because this helper did.
    """
    import httpx

    real_client = httpx.AsyncClient
    httpx.AsyncClient = lambda *args, **kwargs: _RecordedTacticus(guild_service)
    try:
        yield
    finally:
        httpx.AsyncClient = real_client


class _RecordedTacticus:
    """An `httpx.AsyncClient` that answers from the programmed doubles.

    Returns a REAL `httpx.Response`, so `raise_for_status()`, `.json()` and
    `.status_code` behave exactly as they do against the live service.
    """

    def __init__(self, guild_service) -> None:
        self._guild_service = guild_service

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url: str, headers: dict | None = None, **kwargs):
        import httpx
        status, body = _tacticus_body(
            self._guild_service, url, (headers or {}).get("X-API-KEY", "")
        )
        return httpx.Response(status, json=body, request=httpx.Request("GET", url))


def _tacticus_body(guild_service, url: str, credential: str) -> tuple[int, dict]:
    from bot.cogs.tasks_cog import TACTICUS_CURRENT_RAID, TACTICUS_RAID_URL
    from bot.services.tacticus.guild_client import TACTICUS_GUILD_URL
    from conftest import SEASON

    if url == TACTICUS_GUILD_URL:
        answer = guild_service.answer_for(credential)
        return answer.status, answer.payload()
    if url == TACTICUS_RAID_URL.format(season=SEASON):
        return 200, {"entries": _raid_entries()}
    if url == TACTICUS_CURRENT_RAID:
        return 200, {"season": SEASON}
    raise AssertionError(f"the loop called an endpoint no scenario declared: {url}")


def _raid_entries() -> list[dict]:
    """One battle hit and one bomb hit, in raw vendor shape.

    Raw on purpose: `process_api_response` derives `tier_key` and `damage`
    itself, and pre-normalising them here would let the loop pass against an
    implementation that stopped doing so.
    """
    common = {
        "unitId": "Avatar", "encounterIndex": 0, "rarity": "Legendary",
        "set": 0, "userId": "u1", "heroDetails": [{"unitId": "Aethana"}],
        "machineOfWarDetails": None,
    }
    return [
        {**common, "damageType": "Battle", "encounterType": "Battle",
         "damageDealt": 12000, "completedOn": "2026-08-01T10:00:00Z"},
        {**common, "damageType": "Bomb", "encounterType": "Bomb",
         "damageDealt": 3400, "completedOn": "2026-08-01T10:05:00Z"},
    ]


def _config_guilds_embed():
    """Render the /view_config guilds embed through the admin cog."""
    raise AssertionError("Not yet implemented — RED scaffold")


def _guild_field(embed, guild_id: str) -> str:
    raise AssertionError("Not yet implemented — RED scaffold")


def _hit_counts(repo) -> tuple[int, int]:
    """(battle, bomb) hit rows recorded for Word Bearers this season."""
    from conftest import SEASON
    return (
        _entry_total(repo.load_battle_hits(PROD_SERVER_ID, GUILD_WB, SEASON)),
        _entry_total(repo.load_bomb_hits(PROD_SERVER_ID, GUILD_WB, SEASON)),
    )


def _entry_total(hits: dict) -> int:
    return sum(
        len(entries)
        for encounters in hits.get("boss_hits", {}).values()
        for tiers in encounters.values()
        for entries in tiers.values()
    )


def _is_quarantined(guild_id: str) -> bool:
    from bot.guilds import load_guild_binding
    from bot.services.tacticus.guild_client import KeyStatus
    return load_guild_binding(PROD_SERVER_ID, guild_id).key_status == KeyStatus.QUARANTINED.value


def _transport_failure(failure: TransportFailure) -> GuildServiceResponse:
    """Map the enum onto a programmed failure at the httpx boundary."""
    import httpx
    if failure is TransportFailure.TIMEOUT:
        return GuildServiceResponse(raises=httpx.TimeoutException("timed out"))
    if failure is TransportFailure.CONNECT_ERROR:
        return GuildServiceResponse(raises=httpx.ConnectError("connection refused"))
    return GuildServiceResponse(status=int(failure.value.removeprefix("http_")))
