"""Slice 05 — close the write holes. Implements
`acceptance/slice-05-close-the-write-holes.feature`.

AUTHORED IN DISTILL, 2026-08-02. Expected RED; the failures are the
deliverable. See `docs/feature/guild-key-integrity/distill/red-classification.md`.

DEPENDENCY, and the reason this module exists at all: the `KeyConsumptionSite`
correction landed first. AC-004.6 — the criterion that certifies "every site
refuses" — was parametrized over an enum that omitted all three sites this
slice fixes and substituted two that consume no key. Verifying slice 05
against that enum would have re-certified the same hole.

Two of the scenarios here are the strong halves of pairs whose weak halves
live in `test_slice_03_quarantine_enforcement.py::test_every_key_consumption_
site_refuses_a_quarantined_guild`. The slice-03 branch drives the port and
observes a refusal; these drive the port in the state where the refusal does
not happen.
"""
from __future__ import annotations

import pytest

from domain_types import (
    DARK_MECHANICUM,
    WORD_BEARERS,
    KeyStatus,
    ProbeOutcome,
)
from conftest import (
    GUILD_DM,
    GUILD_WB,
    PROD_SERVER_ID,
    SEASON,

    GuildServiceResponse,
)

pytestmark = pytest.mark.slice_05


# ===========================================================================
# AC-008.3 — the structural fix, and the one the other five rest on
# ===========================================================================

@pytest.mark.kpi
@pytest.mark.driving_port
async def test_the_chokepoint_refuses_without_being_asked_twice(
    sqlite_repo, registered_guilds, bound_guild, fake_guild_service
):
    """AC-008.3 / DDD-3 — safety must not depend on call order.

    `_is_quarantined` has exactly one caller in the whole codebase, inside
    `active_key`. `verify_and_resolve` — whose own docstring says "This is
    what an ingestion path calls" — reads the key at `guild_keys.py:112` with
    no status check at all. Every current caller is safe only because it
    happens to call `active_key` first and bail; `admin_cog.register_guild`
    is the caller that does not, and it is the one that corrupts rosters.

    Stated as a property of the function rather than of today's call sites,
    because "all our callers happen to do the right thing" is a convention,
    and this feature exists because a convention was mistaken for a
    guarantee. Moving the gate inside makes a NEW caller safe by default —
    which is the only version of this that survives the next contributor.

    `call_count == 0` is the sharp half: refusing after fetching would still
    pull the other guild's roster into memory and into the logs.
    """
    import bot.guild_keys as guild_keys

    _quarantine(GUILD_WB, bound=WORD_BEARERS, observed=DARK_MECHANICUM)
    fake_guild_service.program(
        "wb-key", GuildServiceResponse(identity=DARK_MECHANICUM, members=["x1"])
    )

    with pytest.raises(guild_keys.GuildQuarantined):
        with _tacticus_answered_by(fake_guild_service):
            await guild_keys.verify_and_resolve(
                PROD_SERVER_ID, GUILD_WB, enforce=True
            )

    assert fake_guild_service.call_count == 0, (
        "verify_and_resolve fetched the quarantined guild's data before "
        "refusing — the other guild's roster reached memory, and the refusal "
        "depends on the caller having checked active_key first"
    )


# ===========================================================================
# AC-008.1 / AC-008.2 — /register_guild
# ===========================================================================

@pytest.mark.kpi
@pytest.mark.error
@pytest.mark.driving_port
async def test_registering_over_a_quarantined_guild_names_the_way_out(
    sqlite_repo, registered_guilds, guild_with_recorded_rows, fake_guild_service
):
    """AC-008.1 / KPI-2 — RE-AUTHORED 2026-08-03. The reachable half.

    WHY THIS SCENARIO EXISTS IN THIS SHAPE. Its first version drove the same
    command against a quarantined binding with NO guild row, reached by
    calling `_rollback_data`. AC-009.6 closes that hole by adding
    `guild_key_bindings` to the rollback's delete order — after which the
    same `Given` produces an UNBOUND guild, `/register_guild` correctly
    adopts it under trust-on-first-use (DDD-8), and the scenario reds forever
    no matter how well slice 05 is implemented. The two ACs could not both
    hold. AC-009.6 is a confirmed defect and wins; this scenario moved to the
    state that survives it. Full reasoning in `distill/upstream-issues.md`
    UI-13.

    THE STATE THAT SURVIVES is the ordinary one: a REGISTERED guild whose
    binding says quarantined. That is where every officer whose key drifted
    actually is, and where the recovery journey starts. Post-AC-009.6 it is
    the ONLY reachable state a quarantined binding can be in, because the FK
    CASCADE and the corrected delete order between them make an orphan
    unconstructible through any sanctioned path.

    WHAT `/register_guild` DOES WITH IT TODAY. `admin_cog.py:83` refuses the
    already-registered id and replies "Choose a different ID or contact an
    admin to remove the existing entry." Zero rows are written, so the
    write-hole half of AC-008.1 is already closed here — and the two write
    assertions below are kept as GUARDS, not as the reproduction, because a
    gate that "handles quarantine" by re-probing would break them.

    THE DEFECT IS THE ROUTING. "Remove the existing entry" is
    `/deregister_guild`, which per AC-009.4 destroys the guild's entire raid
    history, and re-registering afterwards launders the quarantine per
    AC-009.5. So the reply hands an officer one command away from
    `/update_guild_key` a route through the two most destructive commands in
    the cog. That is the same defect shape as AC-008.5b — a refusal that
    reaches the operator as the wrong KIND of refusal and routes them into
    the destructive path — which is why it belongs in this slice.

    A ROLE THE CLUSTER DOES NOT ALREADY USE, deliberately: `admin_cog.py:91`
    refuses a role already linked to another guild, and a scenario that
    tripped that branch would be about role collisions rather than about
    quarantine.
    """
    _quarantine(GUILD_WB, bound=WORD_BEARERS, observed=DARK_MECHANICUM)
    fake_guild_service.program(
        "wb-key",
        GuildServiceResponse(identity=DARK_MECHANICUM, members=["dm1", "dm2", "dm3"]),
    )

    before = _roster(GUILD_WB)
    assert before, "fixture precondition lost — there is no roster to protect"

    interaction = _admin_interaction()
    with _tacticus_answered_by(fake_guild_service):
        await _invoke_admin_command(
            "register_guild", interaction,
            name="Word Bearers", guild_id=GUILD_WB,
            api_key="wb-key", role=_FakeRole(role_id=99),
        )

    assert _roster(GUILD_WB) == before, (
        "registering over a quarantined guild changed its roster: "
        f"{sorted(_roster(GUILD_WB))} (was {sorted(before)})"
    )
    assert fake_guild_service.call_count == 0, (
        "the refusal probed the quarantined guild's key first — the other "
        "guild's roster reached memory and the logs before anything was "
        "refused. Quarantine is a stored fact; reading it costs no request"
    )

    reply = interaction.all_replies
    assert "quarantin" in reply.lower(), (
        "an officer whose guild is quarantined was told only that the id is "
        f"taken, so the actual problem was never named: {reply!r}"
    )
    assert "/update_guild_key" in reply, (
        "the refusal did not name the only exit from quarantine, so the "
        f"officer has no way to act on it: {reply!r}"
    )
    assert "remove the existing entry" not in reply, (
        "a quarantined guild's officer was routed to deregistration — the "
        "command that destroys the guild's entire raid history (AC-009.4) "
        f"and launders the quarantine on re-registration (AC-009.5): {reply!r}"
    )


@pytest.mark.kpi
@pytest.mark.error
@pytest.mark.driving_port
async def test_registering_over_an_orphaned_quarantined_binding_writes_nothing(
    sqlite_repo, sqlite_db_path, fake_guild_service
):
    """AC-008.1c / KPI-2 — the confirmed roster-corruption reproduction,
    carried over from AC-008.1's first version with its assertions verbatim
    and only its `Given` re-built. RE-AUTHORED 2026-08-03; see UI-13.

    THE STATE IS RESIDUE, AND RESIDUE IS NOT THE SAME AS UNREACHABLE.
    AC-009.6 stops `_rollback_data` from CREATING orphaned bindings. It is a
    forward-only fix: it adds a table to a delete order, and it does not
    remove the rows an earlier rollback already left behind. Any database
    that went through a parity rollback during the SQLite cutover is carrying
    those rows right now, and `/register_guild` on such a slug walks the
    contamination path below. So the `Given` is built to reproduce that
    residue directly — `PRAGMA foreign_keys=OFF`, delete `players`, delete
    `guilds` — which is precisely what the pre-fix `_rollback_data` did to
    this table set.

    IT DELIBERATELY NO LONGER CALLS `_rollback_data`. Calling it was the
    right choice while the orphan leak was open, and it is the wrong one now:
    a `Given` assembled from the very function AC-009.6 changes collapses
    into a DIFFERENT state the moment that fix lands, and collapses silently
    — the guild becomes UNBOUND, trust-on-first-use adopts it correctly, and
    the scenario reds while reporting nothing true. That is exactly what
    happened, and `_leave_an_orphaned_quarantined_binding` now asserts its own
    postcondition so it can never happen quietly again.

    WHAT PRODUCTION DOES FROM HERE, unchanged from the original write-up.
    `register_guild` writes the guild row, calls
    `verify_and_resolve(enforce=False)` — which reads the quarantined
    binding, sees the mismatch, reports it, and because enforce is False
    hands back the OTHER GUILD'S SNAPSHOT anyway — and passes it to
    `refresh_guild`, whose own guard deliberately does not refuse a MISMATCH
    snapshot because the policy layer is supposed to have blocked first.
    Nothing in that chain checks `key_status`, so the other guild's members
    are written into this guild's roster. Measured today: `['dm1','dm2','dm3']`,
    and a reply reading "Bound to: 【UNDV】Dark Mechanicum" — the drifted
    identity adopted, announced as a success.

    THIS IS THE SCENARIO THAT KEEPS THE UNBOUND GUARD HONEST. Satisfying it
    requires reading `load_guild_binding(...).key_status` before adopting, so
    the gate must tell QUARANTINED from UNBOUND rather than refusing every
    unverified guild — which is what
    `test_registering_a_never_bound_guild_still_adopts_normally` forbids.
    Neither scenario alone forces that distinction; the pair does.

    ON THE ROSTER. There is nothing to flip here: `players` CASCADEs from
    `guilds` and every route to "no guild row" also empties the roster, so
    the `is_former` clause the brief bundled into AC-008.1 is unobservable in
    this state and is asserted where it IS observable, in AC-008.1b.

    NOT PINNED: whether the guild row should be created at all. An admin who
    registers a slug carrying stale residue could reasonably be refused
    outright, or registered and left quarantined (which lands them in
    AC-008.1's state, where the reply names the way out). Both are defensible
    and it is a product call, not a defect — same class as UI-9's open
    question. The scenario asserts only that no roster is written and that
    the operator is told how to recover.
    """
    _leave_an_orphaned_quarantined_binding(
        sqlite_db_path, GUILD_WB, bound=WORD_BEARERS
    )
    fake_guild_service.program(
        "the-dark-mechanicum-key",
        GuildServiceResponse(identity=DARK_MECHANICUM, members=["dm1", "dm2", "dm3"]),
    )

    interaction = _admin_interaction()
    with _tacticus_answered_by(fake_guild_service):
        await _invoke_admin_command(
            "register_guild", interaction,
            name="Word Bearers", guild_id=GUILD_WB,
            api_key="the-dark-mechanicum-key", role=_FakeRole(role_id=1),
        )

    assert _roster(GUILD_WB) == {}, (
        "another guild's members were written into a guild whose binding "
        f"says quarantined: {sorted(_roster(GUILD_WB))}"
    )
    assert "/update_guild_key" in interaction.all_replies, (
        "the operator was not told how to recover. The refusal must name the "
        "only exit from quarantine, or the command is a dead end reported as "
        f"a fetch failure. Replies were: {interaction.all_replies!r}"
    )


@pytest.mark.kpi
@pytest.mark.error
async def test_the_registration_sequence_does_not_flip_real_members_to_departed(
    sqlite_repo, registered_guilds, guild_with_recorded_rows, fake_guild_service
):
    """AC-008.1b — the incident's larger half, reproduced where it is
    observable.

    THE ROSTER INVERSION IS THE WORSE DAMAGE. The 2026-07-28 incident put 30
    battle rows and 20 bomb rows on the wrong guild — and corrupted 60 of 67
    `players` rows. Hit rows are additive noise; an `is_former` flip is
    destructive, because the bot tells the officers a real member has left.

    WHY THIS DRIVES A SEQUENCE AND NOT THE SLASH COMMAND. The reproduction
    in `remediation-plan.md` describes a guild that is registered — it has
    "the real Dark Mechanicum key installed" — and quarantined, with five
    real members. `/register_guild` refuses an already-registered guild_id at
    `admin_cog.py:83`, before any probe, so the measured result cannot have
    come through the command. It came through the two calls the command makes
    at `admin_cog.py:121-124`, which is what this scenario drives:
    `verify_and_resolve(enforce=False)` and then `refresh_guild` with
    whatever snapshot came back.

    That is the honest home for the `is_former` clause. AC-008.1's own
    `Given` (quarantined binding, NO guild row, reached via `_rollback_data`)
    cannot carry a roster, because `players` CASCADEs from `guilds` and the
    rollback deletes it — so the clause is unobservable there and observable
    here. The two scenarios are the same defect at two different depths: this
    one shows what the ungated chokepoint destroys, AC-008.1 shows the
    command walking into it.

    `enforce=False` is deliberate and is exactly what `register_guild`
    passes. A gate that only fires under `enforce=True` would leave this path
    fully open, which is the shape of the original defect: enforcement that
    depends on the caller asking for it.
    """
    import bot.guild_keys as guild_keys
    from bot.services.chronicl3r.player_service import PlayerService

    before = _roster(GUILD_WB)
    assert before, "fixture precondition lost — there are no members to flip"
    assert not any(p.get("is_former") for p in before.values()), (
        "fixture precondition lost — every seeded member should start active"
    )

    _quarantine(GUILD_WB, bound=WORD_BEARERS, observed=DARK_MECHANICUM)
    fake_guild_service.program(
        "wb-key",
        GuildServiceResponse(identity=DARK_MECHANICUM, members=["dm1", "dm2", "dm3"]),
    )
    service = PlayerService(_FakeChroniclerClient())

    with _tacticus_answered_by(fake_guild_service):
        try:
            snapshot = await guild_keys.verify_and_resolve(
                PROD_SERVER_ID, GUILD_WB, enforce=False
            )
        except guild_keys.GuildQuarantined:
            # The refusal AC-008.3 adds. Reached here, the roster was never
            # touched and the assertions below hold trivially — which is the
            # correct outcome, not a skipped test.
            snapshot = None
        if snapshot is not None:
            await service.refresh_guild(PROD_SERVER_ID, GUILD_WB, snapshot)

    after = _roster(GUILD_WB)
    departed = sorted(
        member_id for member_id, p in after.items()
        if p.get("is_former") and not before.get(member_id, {}).get("is_former")
    )
    assert not departed, (
        "real members were marked as departed because another guild's roster "
        f"was treated as the truth about who is in this one: {departed}"
    )
    assert set(after) == set(before), (
        "another guild's members were written into a quarantined guild's "
        f"roster: {sorted(set(after) - set(before))}"
    )


@pytest.mark.driving_port
async def test_registering_a_never_bound_guild_still_adopts_normally(
    sqlite_repo, fake_guild_service
):
    """AC-008.2 / DDD-8 — the regression guard on trust-on-first-use.

    The gate added for AC-008.1c must distinguish QUARANTINED from UNBOUND.
    Refusing both would break the only reason the probe is in
    `/register_guild` at all: the operator learns what the key resolves to at
    registration time instead of waiting up to an hour. A slice that closes
    the write hole by refusing every unverified guild would pass AC-008.1c
    and make the command useless.

    ITS PAIR IS AC-008.1c, NOT AC-008.1. Both this scenario and AC-008.1c
    enter `/register_guild` on a guild that has no row yet, so both take the
    same branch and are separated only by what
    `load_guild_binding(...).key_status` says. That is the whole discrimination
    the gate has to make, and neither scenario forces it alone: this one alone
    is satisfied by an ungated command, AC-008.1c alone is satisfied by one
    that refuses everything. AC-008.1 lives in the already-registered branch
    and cannot substitute for either.
    """
    from bot.guilds import load_guild_binding

    fake_guild_service.program(
        "fresh-key",
        GuildServiceResponse(identity=WORD_BEARERS, members=["u1", "u2", "u3"]),
    )

    interaction = _admin_interaction()
    with _tacticus_answered_by(fake_guild_service):
        await _invoke_admin_command(
            "register_guild", interaction,
            name="Word Bearers", guild_id="brand_new_guild",
            api_key="fresh-key", role=_FakeRole(role_id=9),
        )

    binding = load_guild_binding(PROD_SERVER_ID, "brand_new_guild")
    assert binding.tacticus_guild_id == WORD_BEARERS.uuid, (
        "trust-on-first-use stopped adopting — the quarantine gate is "
        "refusing unbound guilds too"
    )
    assert _roster("brand_new_guild"), "the roster was never populated"


# ===========================================================================
# AC-008.4 / AC-008.5 — the leaderboard SPOF
# ===========================================================================

@pytest.mark.kpi
@pytest.mark.driving_port
async def test_a_quarantined_guild_first_does_not_disable_the_cluster_leaderboard(
    sqlite_repo, fake_guild_service, update_channel
):
    """AC-008.4 / KPI-5 / DDD-7 — the SPOF AC-004.7 fixed once and left twice.

    `set_live_cluster_leaderboard` reads `next(iter(guilds))` and aborts the
    ENTIRE cluster when that one guild's key is unusable — the identical
    shape `_current_season` had before AC-004.7 replaced it with a
    fall-through, never applied to the siblings. KPI-5 says 100% of unrelated
    guilds are unaffected by one guild's quarantine; measured for this
    command it is 0%.

    The season is a CLUSTER fact, not a guild fact: any healthy key can
    answer it. Coupling it to whichever guild happens to sort first is the
    accident, and the quarantined guild being first is the only ordering in
    which the accident is visible — which is why the fixture pins it.
    """
    _register_two_guilds_quarantined_first()
    fake_guild_service.program(
        "dm-key", GuildServiceResponse(identity=DARK_MECHANICUM, members=["dm1"])
    )

    interaction = _admin_interaction()
    with _tacticus_answered_by(fake_guild_service):
        await _invoke_admin_command(
            "set_live_cluster_leaderboard", interaction,
            channel=_FakeLeaderboardChannel(channel_id=7),
        )

    assert _guild_ids_in_order()[0] == GUILD_WB, "fixture ordering precondition lost"
    assert not interaction.reply_text.startswith("❌"), (
        "one quarantined guild disabled the cluster-wide leaderboard for "
        f"every healthy sibling: {interaction.reply_text!r}"
    )
    assert _live_leaderboards(), "no cluster leaderboard was created"


@pytest.mark.kpi
@pytest.mark.driving_port
async def test_a_quarantined_sibling_does_not_disable_a_healthy_guilds_leaderboard(
    sqlite_repo, fake_guild_service, update_channel
):
    """AC-008.5 — REGRESSION GUARD, green today, and deliberately so.

    THE PROPOSED AC IS WRONG AND THIS TEST IS THE EVIDENCE. Slice 05's brief
    says of this command only "Same for `/set_live_leaderboard`", grouping it
    with the cluster-leaderboard SPOF. It is not the same defect.
    `set_live_cluster_leaderboard` reads `next(iter(guilds))` — an arbitrary
    guild, unrelated to what the officer asked for — and aborts the whole
    cluster on it. `set_live_leaderboard` (`admin_cog.py:404`) takes
    `active_key(server_id, guild_id)` for the guild NAMED IN THE COMMAND.
    There is no arbitrary pick and no cross-guild blast radius, so there is
    no fall-through to apply.

    Run against production as it stands, this scenario PASSES. That is the
    finding, not a failure of the scenario: a healthy guild's leaderboard is
    already unaffected by a quarantined sibling. It is kept as a regression
    guard because the fall-through added for AC-008.4 touches shared
    leaderboard code, and the cheapest way for that change to go wrong is to
    start resolving the season from the wrong guild here.

    Read literally, the proposed AC would instead require a QUARANTINED
    guild's own leaderboard to be built from a sibling's key. That is a
    different and more arguable claim — it means publishing a live board over
    data the bot has stopped updating — and it is a product decision, not a
    defect. Raised in `distill/upstream-issues.md`; not decided here.

    The defect `/set_live_leaderboard` DOES have is what it says when it
    refuses, which is the next scenario.
    """
    _register_two_guilds_quarantined_first()
    fake_guild_service.program(
        "dm-key", GuildServiceResponse(identity=DARK_MECHANICUM, members=["dm1"])
    )

    interaction = _admin_interaction()
    with _tacticus_answered_by(fake_guild_service):
        await _invoke_admin_command(
            "set_live_leaderboard", interaction,
            guild_id=GUILD_DM, channel=_FakeLeaderboardChannel(channel_id=7),
        )

    assert not interaction.reply_text.startswith("❌"), (
        "a healthy guild could not get a live leaderboard while an unrelated "
        f"guild was quarantined: {interaction.reply_text!r}"
    )
    assert _live_leaderboards(), "no live leaderboard was created"


@pytest.mark.error
@pytest.mark.driving_port
async def test_a_quarantined_guild_is_not_reported_as_having_no_key(
    sqlite_repo, fake_guild_service
):
    """AC-008.5b — the real `/set_live_leaderboard` defect, substituted for
    the proposed AC-008.5 above.

    A quarantined guild HAS a key. It has a perfectly valid key that resolves
    to the wrong guild, which is a completely different problem with a
    completely different fix. `admin_cog.py:406` tells the officer
    "❌ Guild `x` has no API key set", and an officer who reads that goes and
    sets one — with `/register_guild`, because that is the command whose
    description says it registers a guild with its API key.

    `/register_guild` is the command that, per AC-008.1, writes the other
    guild's roster in. So the misleading message is not cosmetic: it is the
    step that routes an officer from a contained failure into the
    destructive one. The reply must name quarantine and name
    `/update_guild_key`, which is the actual exit.

    Same defect shape as slice 05's "narrow the swallow" item for
    `/register_guild` — a refusal that reaches the operator as the wrong
    kind of refusal — which is why it belongs in this slice rather than in
    the deferred list.
    """
    _register_two_guilds_quarantined_first()

    interaction = _admin_interaction()
    with _tacticus_answered_by(fake_guild_service):
        await _invoke_admin_command(
            "set_live_leaderboard", interaction,
            guild_id=GUILD_WB, channel=_FakeLeaderboardChannel(channel_id=7),
        )

    reply = interaction.reply_text
    assert "no API key" not in reply, (
        "a quarantined guild was reported as having no key. The officer is "
        f"sent to /register_guild, which overwrites the roster: {reply!r}"
    )
    assert "/update_guild_key" in reply, (
        f"the refusal did not name the only exit from quarantine: {reply!r}"
    )


@pytest.mark.error
@pytest.mark.driving_port
async def test_a_fully_quarantined_cluster_is_refused_for_a_stated_reason(
    sqlite_repo, fake_guild_service
):
    """AC-008.6 — the regression guard on AC-004.8.

    The fall-through must end in a clean, explained refusal, not in a silent
    skip and not in an empty leaderboard. And the reason must say
    QUARANTINED: today `set_live_cluster_leaderboard` reports "has no usable
    key", which reads to an officer as "someone forgot to set the key" and
    sends them to `/register_guild` — the command that, per AC-008.1,
    corrupts the roster.
    """
    _quarantine_every_guild()

    interaction = _admin_interaction()
    with _tacticus_answered_by(fake_guild_service):
        await _invoke_admin_command(
            "set_live_cluster_leaderboard", interaction,
            channel=_FakeLeaderboardChannel(channel_id=7),
        )

    assert interaction.reply_text.startswith("❌")
    assert "quarantin" in interaction.reply_text.lower(), (
        "an all-quarantined cluster was reported as a missing-key problem. "
        "The officer is sent to /register_guild, which is the command that "
        f"overwrites the roster: {interaction.reply_text!r}"
    )
    assert fake_guild_service.call_count == 0


# ===========================================================================
# Helpers — wiring only
# ===========================================================================
from contextlib import contextmanager  # noqa: E402 — helpers-only dependency


def _quarantine(guild_id: str, *, bound, observed) -> None:
    """`Given a quarantined guild`, bound to `bound`, drifted to `observed`."""
    from bot.guilds import load_guild_binding, save_guild_binding
    from bot.repository import GuildBinding

    save_guild_binding(PROD_SERVER_ID, guild_id, GuildBinding(
        tacticus_guild_id=bound.uuid,
        tacticus_guild_tag=bound.tag,
        tacticus_guild_name=bound.name,
        identity_bound_at="2026-07-31T04:00:00Z",
        key_status=KeyStatus.QUARANTINED.value,
        quarantine_reason=(
            f"key drift: bound 【{bound.tag}】 but resolves to 【{observed.tag}】 "
            f"— observed={observed.uuid}"
        ),
        quarantined_at="2026-07-31T04:00:00.000Z",
    ))


def _leave_an_orphaned_quarantined_binding(db_path, guild_id: str, *, bound) -> None:
    """`Given a database still carrying the residue of a parity rollback`.

    REBUILT 2026-08-03. This helper used to call
    `migrations_json_to_sqlite._rollback_data`, on the reasoning that a
    `Given` assembled from the production function that actually produces a
    state is more honest than one that edits the database behind its back.
    That reasoning was right and the choice still turned out to be wrong,
    for a reason worth writing down: AC-009.6 CHANGES that function, and a
    `Given` built from a function another slice is about does not merely
    break when that slice lands — it silently becomes a different `Given`.
    With `guild_key_bindings` added to the delete order the binding is
    deleted too, the guild comes back UNBOUND, trust-on-first-use adopts it
    exactly as DDD-8 says it should, and the scenario reds while reporting
    something that is no longer true. Measured before rewriting this.

    So the residue is now reproduced directly, and the three statements below
    are what the PRE-FIX `_rollback_data` did to these two tables: FK
    enforcement off, `players` deleted, `guilds` deleted, bindings left
    standing with no parent row. That is not a contrivance — it is the state
    sitting in any database that went through a parity rollback during the
    cutover, and AC-009.6 does not clean those rows up (it is forward-only:
    it stops NEW orphans, it does not remove old ones). Recorded as UI-14.

    THE POSTCONDITION IS ASSERTED HERE, not left to the scenario. The failure
    this helper is being rewritten to fix was a `Given` that collapsed into a
    different state without saying so. A `Given` that can silently degrade
    into the state whose OPPOSITE the scenario is about has to check itself,
    or the next change to any of these tables reproduces the same trap.
    """
    import sqlite3

    from bot.guilds import load_guild_binding, load_guilds, save_guilds

    save_guilds(PROD_SERVER_ID, {
        guild_id: {
            "name": "Word Bearers", "api_key": "wb-key", "role_id": 1,
            "notification_channel_id": None, "member_role_ids": [],
        },
    })
    _quarantine(guild_id, bound=bound, observed=DARK_MECHANICUM)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        for table in ("players", "guilds"):
            conn.execute(f"DELETE FROM {table}")  # noqa: S608 — literal names
        conn.execute("PRAGMA foreign_keys=ON")
        conn.commit()
    finally:
        conn.close()

    assert guild_id not in load_guilds(PROD_SERVER_ID), (
        f"the Given did not take: `{guild_id}` still has a guild row, so "
        "/register_guild will refuse it as already-registered and this "
        "scenario is exercising AC-008.1 rather than AC-008.1c"
    )
    binding = load_guild_binding(PROD_SERVER_ID, guild_id)
    assert binding.key_status == KeyStatus.QUARANTINED.value, (
        "the Given collapsed: the binding this scenario is about is gone, so "
        f"`{guild_id}` is UNBOUND and registration will correctly adopt it "
        "under trust-on-first-use. The scenario would then red for the "
        "absence of the state it needs rather than for the defect it names"
    )


def _roster(guild_id: str) -> dict:
    from bot.guilds import load_player_list
    return load_player_list(PROD_SERVER_ID, guild_id).get("players", {})


def _register_two_guilds_quarantined_first() -> None:
    """`Given the quarantined guild is FIRST in iteration order`.

    Word Bearers is inserted before Dark Mechanicum and then quarantined, so
    `next(iter(guilds))` meets the unusable key first. A fixture that ordered
    them the other way would pass while the SPOF was fully present — the same
    trap AC-004.7's fixture was shaped to avoid.
    """
    from bot.guilds import save_guilds

    save_guilds(PROD_SERVER_ID, {
        GUILD_WB: {
            "name": "Word Bearers", "api_key": "wb-key", "role_id": 1,
            "notification_channel_id": None, "member_role_ids": [],
        },
        GUILD_DM: {
            "name": "Dark Mechanicum", "api_key": "dm-key", "role_id": 2,
            "notification_channel_id": None, "member_role_ids": [],
        },
    })
    _quarantine(GUILD_WB, bound=WORD_BEARERS, observed=DARK_MECHANICUM)


def _quarantine_every_guild() -> None:
    from bot.guilds import load_guilds

    _register_two_guilds_quarantined_first()
    for guild_id in load_guilds(PROD_SERVER_ID):
        _quarantine(guild_id, bound=WORD_BEARERS, observed=DARK_MECHANICUM)


def _guild_ids_in_order() -> list[str]:
    from bot.guilds import load_guilds
    return list(load_guilds(PROD_SERVER_ID))


def _live_leaderboards() -> dict:
    from bot.guilds import load_live_leaderboards
    return load_live_leaderboards(PROD_SERVER_ID)


@contextmanager
def _tacticus_answered_by(guild_service):
    """Answer every Tacticus call from the scenario's programmed doubles.

    Replicated from slice 01/03 (UD-10: never cross-import test modules).
    """
    import httpx

    real_client = httpx.AsyncClient
    httpx.AsyncClient = lambda *args, **kwargs: _RecordedTacticus(guild_service)
    try:
        yield
    finally:
        httpx.AsyncClient = real_client


class _RecordedTacticus:
    def __init__(self, guild_service) -> None:
        self._guild_service = guild_service

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url: str, headers: dict | None = None, **kwargs):
        import httpx
        from bot.cogs.tasks_cog import TACTICUS_CURRENT_RAID, TACTICUS_RAID_URL
        from bot.services.tacticus.guild_client import TACTICUS_GUILD_URL

        credential = (headers or {}).get("X-API-KEY", "")
        request = httpx.Request("GET", url)
        if url == TACTICUS_GUILD_URL:
            answer = self._guild_service.answer_for(credential)
            return answer.as_httpx_response(url)
        if url == "https://api.tacticusgame.com/api/v1/guildRaid":
            # The leaderboard commands read the CURRENT season from this
            # endpoint directly rather than through `tasks_cog`'s constant.
            self._guild_service.calls.append(credential)
            return httpx.Response(200, json={"season": SEASON}, request=request)
        if url == TACTICUS_RAID_URL.format(season=SEASON):
            return httpx.Response(200, json={"entries": []}, request=request)
        if url == TACTICUS_CURRENT_RAID:
            return httpx.Response(200, json={"season": SEASON}, request=request)
        raise AssertionError(f"a command called an endpoint no scenario declared: {url}")


# ---------------------------------------------------------------------------
# Driving the real AdminCog slash commands (replicated from slice 02/03).
# ---------------------------------------------------------------------------

async def _invoke_admin_command(command_name: str, interaction, **kwargs) -> None:
    from bot.cogs.admin_cog import AdminCog
    from bot.services.chronicl3r.player_service import PlayerService

    cmd = _find_admin_command(command_name)
    for chk in cmd.checks:
        predicate = chk.predicate if hasattr(chk, "predicate") else chk
        if not await predicate(interaction):
            await interaction.response.send_message(
                "❌ You don't have permission to use this command.", ephemeral=True
            )
            return
    cog = AdminCog.__new__(AdminCog)
    cog.player_service = PlayerService(_FakeChroniclerClient())
    await cmd.callback(cog, interaction, **kwargs)


def _find_admin_command(name: str):
    from bot.cogs.admin_cog import AdminCog

    for cmd in AdminCog.__cog_app_commands__:
        if cmd.name == name:
            return cmd
    raise AssertionError(
        f"no `{name}` command is registered on AdminCog — delete the command "
        "method and this helper errors, which is the port-to-port litmus test"
    )


class _FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id
        self.mention = f"<@&{role_id}>"


class _FakeLeaderboardChannel:
    """A channel whose `send` returns a message, because these commands use it.

    `conftest.FakeChannel.send` returns None, which is right for the alert
    channels it was built for — nothing reads the result. Both leaderboard
    commands do: `message_ids[tier.value] = msg.id` on the very next line
    (`admin_cog.py:429`). Using the alert double here made the scenario fail
    with `AttributeError: 'NoneType' has no attribute 'id'` — a harness bug,
    the WRONG-reason RED the pre-DELIVER gate exists to catch, and one that
    would have gone green the moment a crafter "fixed" it without the
    feature ever being exercised.
    """

    def __init__(self, channel_id: int) -> None:
        self.id = channel_id
        self.mention = f"<#{channel_id}>"
        self.messages: list[str] = []

    async def send(self, content: str = "", **kwargs):
        self.messages.append(content)
        return _FakeMessage(message_id=1000 + len(self.messages))

    @property
    def text(self) -> str:
        return "\n".join(self.messages)


class _FakeMessage:
    def __init__(self, message_id: int) -> None:
        self.id = message_id


class _FakeResponse:
    def __init__(self, interaction) -> None:
        self._interaction = interaction

    async def send_message(self, content="", *, embed=None, ephemeral=False, **kwargs):
        self._interaction._record(content or (getattr(embed, "description", "") or ""))
        self._interaction._replied = True

    async def defer(self, *, ephemeral=False, **kwargs):
        return None

    def is_done(self):
        return self._interaction._replied


class _FakeFollowup:
    def __init__(self, interaction) -> None:
        self._interaction = interaction

    async def send(self, content="", *, embed=None, ephemeral=False, **kwargs):
        self._interaction._record(content or (getattr(embed, "description", "") or ""))


class _FakeInteraction:
    """Captures EVERY reply. `/register_guild` replies up to three times, so a
    last-wins double would let a success line overwrite a refusal."""

    def __init__(self, *, administrator: bool = True) -> None:
        self.guild_id = PROD_SERVER_ID
        self.replies: list[str] = []
        self._replied = False
        self.user = _FakeUser(administrator=administrator)
        self.response = _FakeResponse(self)
        self.followup = _FakeFollowup(self)

    def _record(self, content: str) -> None:
        self.replies.append(content)

    @property
    def reply_text(self) -> str:
        return self.replies[0] if self.replies else ""

    @property
    def all_replies(self) -> str:
        return "\n".join(self.replies)


class _FakePermissions:
    def __init__(self, *, administrator: bool) -> None:
        self.administrator = administrator


class _FakeUser:
    def __init__(self, *, administrator: bool) -> None:
        self.guild_permissions = _FakePermissions(administrator=administrator)
        self.roles = []


def _admin_interaction():
    return _FakeInteraction(administrator=True)


class _FakeChroniclerClient:
    def authenticate(self) -> None:
        return None

    def register_user(self, tacticus_user_id: str) -> dict:
        return self.get_profile(tacticus_user_id)

    def get_profile(self, tacticus_user_id: str) -> dict:
        assert tacticus_user_id, "chronicl3r rejects an empty tacticus_user_id"
        return {
            "tacticus_user_id": tacticus_user_id,
            "tacticus_display_nm": f"player-{tacticus_user_id}",
        }
