"""Property tests for the quarantine gate inside `verify_and_resolve` (07-01).

WHY-NEW-FILE: tests/unit/test_guild_keys_quarantine_gate.py
  CLOSEST-EXISTING: tests/unit/test_guild_keys_policy.py
  EXTENSION-COST: every test there takes the function-scoped `sqlite_repo` /
    `guild_service` / `key_events` fixtures, and Hypothesis refuses a
    function-scoped fixture under `@given` (HealthCheck.function_scoped_
    fixture). Extending that module means either suppressing that health
    check — forbidden here without operator approval — or converting fixtures
    every one of its ~20 example tests depends on.
  PARALLEL-RATIONALE: incompatible fixture lifecycle. These properties need
    ONE migrated database reused across every generated example and a probe
    recorder reset per example; that module needs a fresh database per test.
    The two lifecycles cannot share a fixture set.

WHAT IS BEING QUANTIFIED, and why an example could not do it. The defect this
step closes is not "one call site forgot to check". It is that the CHECK
lived outside the function, so safety was a property of the call graph rather
than of the chokepoint. A claim about the call graph is refuted by the next
caller; the claim that survives is a claim about the function, over every
state it can be entered in. So the properties below quantify over the full
cross product the roadmap names — the three binding states (UNBOUND, ACTIVE,
QUARANTINED) crossed with `enforce` in {True, False} — plus generated
identifiers, rather than pinning the four states somebody thought of.

The `enforce` axis is the sharp one. `enforce` governs whether a NEWLY
OBSERVED mismatch quarantines. It has never governed whether an ALREADY
quarantined guild may be probed again, and `admin_cog.register_guild` passes
`enforce=False` — which is how five real Word Bearers members were flipped to
`is_former`. A gate that fired only under `enforce=True` would reproduce the
original defect exactly: enforcement that depends on the caller asking for it.
Property 1 therefore ranges over BOTH values and expects the same refusal.

DECLARED UNIVERSE. Every property asserts a state-delta over the FULL
observable surface of one `verify_and_resolve` call, not a single slot:

    binding.tacticus_guild_id     — the lock
    binding.tacticus_guild_tag    — display, refreshed by an agreeing probe
    binding.tacticus_guild_name   — display, refreshed by an agreeing probe
    binding.identity_bound_at     — the verification date
    binding.key_status            — active / quarantined
    binding.quarantine_reason     — both identities, for the operator
    binding.quarantined_at        — KPI-2's operand
    binding.last_alerted_at       — the 24h suppression clock
    probe.calls                   — requests issued to Tacticus
    refusal.raised                — the exception class, or None

All ten slots are compared on every example (`_assert_state_delta` is strict:
a slot with no declared predicate must be byte-identical). A gate that
refused correctly while quietly stamping `identity_bound_at`, or that skipped
the probe while resetting the alert clock, fails here rather than in
production.
"""
from __future__ import annotations

import os

# `bot.guilds` evaluates `repo = build_repo()` at import time and reads the
# environment at that moment. Pin a harmless backend before any `bot.*`
# import so collection cannot construct a repository pointed at a live tree.
# Same precedent as `tests/unit/test_guild_keys_policy.py`.
os.environ.setdefault("SCRAPCODE_REPO_BACKEND", "json")

import base64  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from bot.services.tacticus.guild_client import (  # noqa: E402
    GuildIdentity,
    KeyStatus,
    ProbeOutcome,
    parse_guild_snapshot,
)

hypothesis = pytest.importorskip(
    "hypothesis",
    reason="hypothesis is not installed — DISTILL pins it into requirements.txt",
)

import hypothesis.strategies as st  # noqa: E402
from hypothesis import HealthCheck, given, settings  # noqa: E402

# Deselected from the 250-test baseline for the same reason the slice-05
# acceptance module is: these belong to the remediation slice, and the
# baseline command is the "nothing that shipped has regressed" gate.
pytestmark = [pytest.mark.property, pytest.mark.slice_05]

SERVER_ID = 1458181638453203099
GUILD_WB = "word_bearers"
WB_KEY = "wb-key"

FERNET_KEY = base64.urlsafe_b64encode(b"guild-key-integrity-unit-tests!!"[:32]).decode()

UNBOUND = "unbound"
ACTIVE = "active"
QUARANTINED = "quarantined"

# The universe, named once. Every capture and every assertion reads it from
# here, so a slot added to `GuildBinding` is added in exactly one place.
UNIVERSE = (
    "binding.tacticus_guild_id",
    "binding.tacticus_guild_tag",
    "binding.tacticus_guild_name",
    "binding.identity_bound_at",
    "binding.key_status",
    "binding.quarantine_reason",
    "binding.quarantined_at",
    "binding.last_alerted_at",
    "probe.calls",
    "refusal.raised",
)


# ===========================================================================
# Storage + probe — built ONCE, reset per generated example
# ===========================================================================

@pytest.fixture(scope="module")
def policy_under_test(tmp_path_factory):
    """One migrated database and one probe recorder for the whole module.

    Module-scoped deliberately: Hypothesis rejects function-scoped fixtures
    under `@given`, and running alembic per generated example would put a
    schema migration inside the inner loop of a property test. Every example
    rewrites the guild row and the binding before it acts, so the state each
    one enters on is fully determined by that example rather than inherited
    from the previous one.
    """
    from alembic import command
    from alembic.config import Config

    import bot.db
    import bot.guilds as guilds_mod
    from bot.repository_sqlalchemy import SqlAlchemyClusterRepository
    from bot.services.tacticus import guild_client

    db_path = tmp_path_factory.mktemp("quarantine-gate") / "data" / "scrapcode.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    db_pkg = Path(bot.db.__file__).parent
    cfg = Config(str(db_pkg / "alembic.ini"))
    cfg.set_main_option("script_location", str(db_pkg / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "head")

    probe = _RecordingProbe()
    original_repo = guilds_mod.repo
    original_probe = guild_client.fetch_guild_snapshot
    guilds_mod.repo = SqlAlchemyClusterRepository(
        db_path=str(db_path), fernet_key=FERNET_KEY
    )
    guild_client.fetch_guild_snapshot = probe
    try:
        yield probe
    finally:
        guilds_mod.repo = original_repo
        guild_client.fetch_guild_snapshot = original_probe


class _RecordingProbe:
    """Stands in for `fetch_guild_snapshot`, counting every request.

    The count is the actual observable for "no network happened". Asserting
    only on stored state would pass an implementation that fetched the drifted
    guild's roster, pulled it into memory and into a traceback, and then threw
    it away — which is precisely the behaviour AC-008.3 forbids.

    Answers are built by the REAL `parse_guild_snapshot`, so no example can
    assert against a classification production would never produce.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._identity: GuildIdentity | None = None

    def answers_with(self, identity: GuildIdentity) -> None:
        self._identity = identity
        self.calls.clear()

    async def __call__(self, api_key: str):
        self.calls.append(api_key)
        return parse_guild_snapshot({
            "guild": {
                "guildId": self._identity.uuid,
                "guildTag": self._identity.tag,
                "name": self._identity.name,
                "members": [{"userId": "u1"}, {"userId": "u2"}],
            },
        })


# ===========================================================================
# State-delta helpers — strict universe, predicates per slot
# ===========================================================================

class _Unchanged:
    def __repr__(self) -> str:
        return "unchanged()"


def unchanged() -> _Unchanged:
    return _Unchanged()


def set_to(value):
    """The slot holds exactly `value` afterwards."""
    return ("set_to", value)


def now_present():
    """The slot was empty and now holds something (a timestamp, a reason)."""
    return ("now_present", None)


def _assert_state_delta(before: dict, after: dict, expected: dict) -> None:
    """Strict: every universe slot without a declared predicate is unchanged.

    Strict is the whole point. The bug class this guards is "the code did the
    right thing to the slot the test looked at, and something else to the one
    it did not" — an implicit-unchanged assertion over the declared universe
    is what turns that from invisible into a failure.
    """
    assert set(before) == set(UNIVERSE) == set(after), (
        "the capture drifted from the declared universe — a slot was added to "
        "GuildBinding without being declared here, so nothing asserts on it"
    )
    for slot in UNIVERSE:
        predicate = expected.get(slot, unchanged())
        if isinstance(predicate, _Unchanged):
            assert after[slot] == before[slot], (
                f"{slot} changed from {before[slot]!r} to {after[slot]!r} and "
                "no property declared that it should"
            )
            continue
        kind, value = predicate
        if kind == "set_to":
            assert after[slot] == value, (
                f"{slot} is {after[slot]!r}, expected {value!r}"
            )
            continue
        assert after[slot], f"{slot} is {after[slot]!r}, expected it to be set"


def _capture(calls: int, raised: str | None) -> dict:
    from bot.guilds import load_guild_binding

    binding = load_guild_binding(SERVER_ID, GUILD_WB)
    return {
        "binding.tacticus_guild_id": binding.tacticus_guild_id,
        "binding.tacticus_guild_tag": binding.tacticus_guild_tag,
        "binding.tacticus_guild_name": binding.tacticus_guild_name,
        "binding.identity_bound_at": binding.identity_bound_at,
        "binding.key_status": binding.key_status,
        "binding.quarantine_reason": binding.quarantine_reason,
        "binding.quarantined_at": binding.quarantined_at,
        "binding.last_alerted_at": binding.last_alerted_at,
        "probe.calls": calls,
        "refusal.raised": raised,
    }


# ===========================================================================
# Given-state construction
# ===========================================================================

def _arrange(state: str, *, bound: GuildIdentity, observed: GuildIdentity) -> None:
    """Put the guild into one of the three binding states.

    `save_guilds` first and the binding second, always: `guild_key_bindings`
    CASCADEs from `guilds`, so writing the guild row after the binding erases
    the very state the example is about.

    UNBOUND is written EXPLICITLY rather than left implicit. The database is
    module-scoped, `save_guilds` on an already-present slug updates the row
    instead of replacing it, and the CASCADE therefore does not fire — so
    "write nothing and inherit an empty table" silently became "inherit the
    previous example's binding". A default `GuildBinding` is observationally
    the unbound state (`load_guild_binding` returns exactly that for a guild
    with no row at all), so this is the same Given, stated rather than
    assumed.
    """
    from bot.guilds import save_guild_binding, save_guilds
    from bot.repository import GuildBinding

    save_guilds(SERVER_ID, {
        GUILD_WB: {
            "name": "Word Bearers",
            "api_key": WB_KEY,
            "role_id": 1,
            "notification_channel_id": None,
            "member_role_ids": [],
        },
    })
    if state == UNBOUND:
        save_guild_binding(SERVER_ID, GUILD_WB, GuildBinding())
        return
    if state == ACTIVE:
        save_guild_binding(SERVER_ID, GUILD_WB, GuildBinding(
            tacticus_guild_id=bound.uuid,
            tacticus_guild_tag=bound.tag,
            tacticus_guild_name=bound.name,
            identity_bound_at="2026-07-31T04:00:00.000Z",
        ))
        return
    save_guild_binding(SERVER_ID, GUILD_WB, GuildBinding(
        tacticus_guild_id=bound.uuid,
        tacticus_guild_tag=bound.tag,
        tacticus_guild_name=bound.name,
        identity_bound_at="2026-07-31T04:00:00.000Z",
        key_status=KeyStatus.QUARANTINED.value,
        quarantine_reason=(
            f"key drift: bound 【{bound.tag}】 {bound.name} but resolves to "
            f"【{observed.tag}】 {observed.name} — observed={observed.uuid}"
        ),
        quarantined_at="2026-07-31T04:00:00.000Z",
        last_alerted_at="2026-07-31T04:00:00.000Z",
    ))


async def _drive(enforce: bool) -> Exception | None:
    """Enter through the driving port, returning the refusal if there was one.

    `verify_and_resolve` IS the driving port for an ingestion path — its own
    docstring says so — which is why every property here calls it directly
    rather than reaching for a cog.
    """
    import bot.guild_keys as guild_keys

    try:
        await guild_keys.verify_and_resolve(SERVER_ID, GUILD_WB, enforce=enforce)
    except guild_keys.GuildQuarantined as exc:
        return exc
    return None


# ---------------------------------------------------------------------------
# Identifier strategies — distinct guilds, arbitrary display fields
# ---------------------------------------------------------------------------

_UUIDS = st.uuids().map(str)
_TAGS = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ", min_size=3, max_size=5)
_NAMES = st.text(min_size=1, max_size=24).filter(lambda s: s.strip())


@st.composite
def _two_distinct_guilds(draw):
    bound_uuid, observed_uuid = draw(
        st.lists(_UUIDS, min_size=2, max_size=2, unique=True)
    )
    return (
        GuildIdentity(uuid=bound_uuid, tag=draw(_TAGS), name=draw(_NAMES)),
        GuildIdentity(uuid=observed_uuid, tag=draw(_TAGS), name=draw(_NAMES)),
    )


_SETTINGS = settings(
    max_examples=40,
    deadline=None,
    # The database and the probe recorder are module-scoped BY DESIGN (see
    # `policy_under_test`), not by accident. Suppressing the check for a
    # function-scoped fixture would be the forbidden bypass; this suppression
    # is for `too_slow`, because every example does real SQLite I/O through a
    # real repository and that is the point of driving the real port.
    suppress_health_check=[HealthCheck.too_slow],
)


# ===========================================================================
# Property 1 — AC-008.3. The gate does not read `enforce`.
# ===========================================================================

@given(guilds=_two_distinct_guilds(), enforce=st.booleans())
@_SETTINGS
async def test_a_quarantined_guild_is_refused_before_any_request_whatever_enforce_says(
    policy_under_test, guilds, enforce: bool
):
    """AC-008.3 / DDD-3/5 — safety must not depend on the argument or the caller.

    The delta is EMPTY: a refusal that stamped `identity_bound_at`, reset the
    alert clock or re-wrote `quarantine_reason` would be doing work on behalf
    of a key the cluster has stopped trusting. And `probe.calls == 0` is the
    sharp half — refusing after fetching still puts the drifted guild's roster
    in memory and in the traceback.

    Quantified over `enforce` because that is the axis the original defect ran
    down: `register_guild` passes `enforce=False`, and a gate that consulted
    the flag would leave that path exactly as open as it was on 2026-07-28.
    """
    bound, observed = guilds
    _arrange(QUARANTINED, bound=bound, observed=observed)
    policy_under_test.answers_with(observed)
    before = _capture(calls=0, raised=None)

    raised = await _drive(enforce)

    after = _capture(calls=len(policy_under_test.calls), raised=type(raised).__name__)
    _assert_state_delta(before, after, expected={
        "probe.calls": set_to(0),
        "refusal.raised": set_to("GuildQuarantined"),
    })
    assert raised.bound.uuid == bound.uuid, (
        "the refusal did not carry the identity the key is bound to, so the "
        "caller cannot name what went wrong without re-reading the binding"
    )
    assert raised.observed.uuid == observed.uuid, (
        "the refusal did not carry the drifted identity — it must be "
        "reconstructed from the stored quarantine_reason, never re-probed"
    )


# ===========================================================================
# Property 2 — AC-008.2 / DDD-8. Everything else is byte-identical.
# ===========================================================================

@given(guilds=_two_distinct_guilds(), enforce=st.booleans())
@_SETTINGS
async def test_an_unbound_guild_still_probes_and_adopts_whatever_enforce_says(
    policy_under_test, guilds, enforce: bool
):
    """AC-008.2 / DDD-8 — the gate tells QUARANTINED from UNBOUND.

    An unbound guild has no stored identity to be wrong about. Refusing it
    would close the write hole by making `/register_guild`'s probe useless —
    the operator would go back to learning what the key resolves to up to an
    hour later, which is the whole reason the probe is in the command.

    `key_status` is the whole discrimination, and this property is what pins
    it: both states enter with no usable prior identity and are separated only
    by that one column.
    """
    bound, observed = guilds
    _arrange(UNBOUND, bound=bound, observed=observed)
    policy_under_test.answers_with(observed)
    before = _capture(calls=0, raised=None)
    assert before["binding.tacticus_guild_id"] is None, "the Given did not take"

    raised = await _drive(enforce)

    after = _capture(calls=len(policy_under_test.calls), raised=None)
    assert raised is None, "trust-on-first-use was refused — the gate refuses too much"
    _assert_state_delta(before, after, expected={
        "binding.tacticus_guild_id": set_to(observed.uuid),
        "binding.tacticus_guild_tag": set_to(observed.tag),
        "binding.tacticus_guild_name": set_to(observed.name),
        "binding.identity_bound_at": now_present(),
        "probe.calls": set_to(1),
    })


@given(guilds=_two_distinct_guilds(), enforce=st.booleans())
@_SETTINGS
async def test_an_active_guild_whose_key_still_agrees_is_unaffected_by_the_gate(
    policy_under_test, guilds, enforce: bool
):
    """The regression half — an ACTIVE binding behaves exactly as it did.

    The lock is untouched and the display fields are refreshed from the
    agreeing probe (DDD-1: a retag or a rename must update what the operator
    sees without going near `tacticus_guild_id`).
    """
    bound, _observed = guilds
    _arrange(ACTIVE, bound=bound, observed=_observed)
    retagged = GuildIdentity(uuid=bound.uuid, tag="RETAG", name="renamed guild")
    policy_under_test.answers_with(retagged)
    before = _capture(calls=0, raised=None)

    raised = await _drive(enforce)

    after = _capture(calls=len(policy_under_test.calls), raised=None)
    assert raised is None, "a healthy guild was refused"
    _assert_state_delta(before, after, expected={
        "binding.tacticus_guild_tag": set_to(retagged.tag),
        "binding.tacticus_guild_name": set_to(retagged.name),
        "binding.identity_bound_at": now_present(),
        "probe.calls": set_to(1),
    })
    assert after["binding.tacticus_guild_id"] == bound.uuid, (
        "an agreeing probe moved the lock — only adoption may set it (DDD-8)"
    )


@given(guilds=_two_distinct_guilds(), enforce=st.booleans())
@_SETTINGS
async def test_a_newly_observed_mismatch_is_still_the_only_thing_enforce_governs(
    policy_under_test, guilds, enforce: bool
):
    """`enforce` keeps its meaning, and only its meaning.

    This is the orthogonality claim the roadmap makes explicit: moving the
    already-quarantined gate inside must not turn `enforce` into a second
    quarantine switch, and must not turn `enforce=False` into a block. Under
    `enforce=False` the drift is REPORTED and the caller still receives the
    snapshot (Slice 01's shipped behaviour); under `enforce=True` it
    quarantines and raises.
    """
    import bot.guild_keys as guild_keys

    bound, observed = guilds
    _arrange(ACTIVE, bound=bound, observed=observed)
    policy_under_test.answers_with(observed)
    before = _capture(calls=0, raised=None)

    raised: Exception | None = None
    snapshot = None
    try:
        snapshot = await guild_keys.verify_and_resolve(
            SERVER_ID, GUILD_WB, enforce=enforce
        )
    except guild_keys.GuildQuarantined as exc:
        raised = exc

    after = _capture(
        calls=len(policy_under_test.calls),
        raised=type(raised).__name__ if raised else None,
    )
    if not enforce:
        assert snapshot.outcome is ProbeOutcome.MISMATCH, (
            "the drift was not reported to a caller that asked not to be "
            f"blocked: {snapshot.outcome}"
        )
        _assert_state_delta(before, after, expected={"probe.calls": set_to(1)})
        return

    _assert_state_delta(before, after, expected={
        "binding.key_status": set_to(KeyStatus.QUARANTINED.value),
        "binding.quarantine_reason": now_present(),
        "binding.quarantined_at": now_present(),
        "probe.calls": set_to(1),
        "refusal.raised": set_to("GuildQuarantined"),
    })
