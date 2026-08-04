"""Properties of `/register_guild`'s quarantine refusal (step 07-02).

WHY-NEW-FILE: tests/unit/test_admin_cog_quarantine_refusal.py
  CLOSEST-EXISTING: tests/unit/test_guild_keys_quarantine_gate.py
  EXTENSION-COST: every property there takes the module-scoped
    `policy_under_test` fixture, which runs an alembic migration, swaps
    `bot.guilds.repo` for a real SQLite repository and monkeypatches
    `fetch_guild_snapshot`. Adding these properties to it would attach that
    entire database lifecycle to a function that reads one frozen dataclass
    and returns a string.
  PARALLEL-RATIONALE: incompatible dependency set. The claim below has to hold
    for `key_status` values no migration ever wrote — that is what makes it an
    "if and only if" rather than a "not active" — and those states cannot be
    constructed through the repository that module drives every example
    through. Its universe is stored state; this one's observable is the text
    an operator reads.

WHY A PROPERTY HERE AT ALL. The acceptance scenario that owns this behaviour
(`test_registering_over_a_quarantined_guild_names_the_way_out`) asserts an
EXACT operator-facing string — `/update_guild_key` must appear, "remove the
existing entry" must not. That is a golden assertion, correctly so: the defect
is that an officer was routed into `/deregister_guild`, and routing is made of
literal words. The paradigm exemption is taken there and compensated here, on
the adjacent slot the golden assertion cannot reach: WHICH BINDING STATES take
the refusal branch at all.

That distinction is the whole risk in this step. `/register_guild` exists to
probe an unproven key at registration time (DDD-8, trust-on-first-use); a gate
that refused every unverified guild would satisfy both write assertions and
make the command useless. So the branch must key on `key_status ==
quarantined` and on nothing else — not on "unbound", not on "never verified",
not on "not active". Property 1 quantifies exactly that, over generated
statuses including ones no migration ever produced, so "iff" is tested as iff.

DELTA-FIRST BYPASS: `_quarantine_refusal` is a pure function of one frozen
dataclass with a single return value and no side effects — a named exempt
category in the delta-first paradigm. There is no universe to declare; the
return value IS the whole observable surface.
"""
from __future__ import annotations

import os

# `bot.guilds` evaluates `repo = build_repo()` at import time and reads the
# environment at that moment. Pin a harmless backend before any `bot.*` import
# so collection cannot construct a repository pointed at a live tree. Same
# precedent as `tests/unit/test_guild_keys_quarantine_gate.py`.
os.environ.setdefault("SCRAPCODE_REPO_BACKEND", "json")

import re  # noqa: E402

import pytest  # noqa: E402

from bot.repository import GuildBinding  # noqa: E402
from bot.services.tacticus.guild_client import KeyStatus  # noqa: E402

hypothesis = pytest.importorskip(
    "hypothesis",
    reason="hypothesis is not installed — DISTILL pins it into requirements.txt",
)

import hypothesis.strategies as st  # noqa: E402
from hypothesis import given, settings  # noqa: E402

# Deselected from the 250-test baseline for the same reason the slice-05
# acceptance module is: these belong to the remediation slice, and the baseline
# command is the "nothing that shipped has regressed" gate.
pytestmark = [pytest.mark.property, pytest.mark.slice_05]

# KPI-6 is 0 leaks. `quarantine_reason` embeds the FULL observed uuid for drift
# re-reporting, so anything rendered from it is checked against this.
_UUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# `/deregister_guild`'s route, quoted from the reply this step is replacing. It
# destroys the guild's raid history (AC-009.4) and launders the quarantine on
# re-registration (AC-009.5), so it must never be what a quarantined guild's
# officer is handed.
_THE_DESTRUCTIVE_ROUTE = "remove the existing entry"


# ---------------------------------------------------------------------------
# Strategies — every binding state, including ones storage cannot produce
# ---------------------------------------------------------------------------

_GUILD_IDS = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789_", min_size=1, max_size=24
)
# Tag alphabet deliberately cannot spell a uuid: the bound tag is rendered
# verbatim, so a generated tag that happened to BE an identifier would make the
# KPI-6 assertion fail for the strategy's reason rather than production's.
_TAGS = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ", min_size=3, max_size=5)
_NAMES = st.text(min_size=1, max_size=24).filter(lambda s: s.strip())
_UUIDS = st.uuids().map(str)
_INSTANTS = st.just("2026-07-31T04:00:00.000Z")

_KEY_STATUSES = st.one_of(
    st.just(KeyStatus.ACTIVE.value),
    st.just(KeyStatus.QUARANTINED.value),
    # A column value no migration ever wrote. Without this axis the property
    # would only say "refuses when not active", which a gate that refused
    # UNBOUND guilds would also satisfy — and that gate is the one AC-008.2
    # forbids.
    st.text(max_size=12),
)


@st.composite
def _quarantine_reasons(draw):
    """Both shapes production stores, and the uuid each one carries.

    The long shape is what `bot.guild_keys._quarantine_reason` writes, marker
    and all. The short shape has no `resolves to 【TAG】` marker — the state a
    hand-written or legacy reason is in — and reaches the renderer's uuid
    stripper instead of its extractor. Both are generated because the two
    paths render through different code and KPI-6 binds to both.
    """
    observed_uuid = draw(_UUIDS)
    if draw(st.booleans()):
        return (
            f"key drift: bound 【{draw(_TAGS)}】 {draw(_NAMES)} but resolves to "
            f"【{draw(_TAGS)}】 {draw(_NAMES)} — observed={observed_uuid}"
        )
    prose = draw(st.text(max_size=40).filter(lambda s: "【" not in s))
    return f"{prose} observed={observed_uuid}"


@st.composite
def _bindings(draw, *, key_status=_KEY_STATUSES):
    """A `GuildBinding` in an arbitrary state, bound or unbound.

    `tacticus_guild_id` varies independently of `key_status` on purpose: the
    two are separate columns, and a gate that keyed on "has an identity" rather
    than on the status would pass a property that always paired them.
    """
    return GuildBinding(
        tacticus_guild_id=draw(st.one_of(st.none(), _UUIDS)),
        tacticus_guild_tag=draw(st.one_of(st.none(), _TAGS)),
        tacticus_guild_name=draw(st.one_of(st.none(), _NAMES)),
        identity_bound_at=draw(st.one_of(st.none(), _INSTANTS)),
        key_status=draw(key_status),
        quarantine_reason=draw(st.one_of(st.none(), _quarantine_reasons())),
        quarantined_at=draw(st.one_of(st.none(), _INSTANTS)),
        last_alerted_at=draw(st.one_of(st.none(), _INSTANTS)),
    )


_SETTINGS = settings(max_examples=100, deadline=None)


def _refusal_under_test():
    """Import `bot.cogs.admin_cog` LATE, and never at module scope.

    `config.py` reads UPDATE_CHANNEL_ID / REPLAY_INDEX_CHANNEL_ID at import
    time via `int(os.getenv(...))` and raises TypeError when either is unset,
    and importing any cog imports config — the wrong-reason RED the pre-DELIVER
    gate exists to catch, and the one this module hit on its first run.

    More importantly, importing the cog imports `bot.guilds`, which builds the
    process-wide `ClusterRepository` singleton from whatever environment exists
    AT THAT MOMENT. At collection time no fixture has run. Precedent:
    `tests/unit/test_auto_update_cycle_containment.py::_tasks_cog`.
    """
    os.environ.setdefault("UPDATE_CHANNEL_ID", "0")
    os.environ.setdefault("REPLAY_INDEX_CHANNEL_ID", "0")
    from bot.cogs.admin_cog import _quarantine_refusal

    return _quarantine_refusal


# ===========================================================================
# Property 1 — AC-008.1 / AC-008.2. The branch condition, stated as an iff.
# ===========================================================================

@given(binding=_bindings(), guild_id=_GUILD_IDS)
@_SETTINGS
def test_the_refusal_branch_is_taken_exactly_when_the_key_is_quarantined(
    binding, guild_id: str
):
    """The one discrimination the gate has to make, quantified.

    Read in one direction it is AC-008.1: a quarantined guild must be refused
    with the exit named. Read in the other it is AC-008.2 / DDD-8: everything
    else must fall through to trust-on-first-use, or `/register_guild` stops
    being the command that tells an operator what their key resolves to.

    The acceptance pair (`..._names_the_way_out` and `..._still_adopts_
    normally`) pins the two ENDS of that iff with one example each. This pins
    the middle — including `key_status` values storage never produced, so the
    condition cannot be satisfied by a `!= "active"` test that would refuse an
    unbound guild the day someone adds a third status.
    """
    refused = _refusal_under_test()(binding, guild_id) is not None

    assert refused == (binding.key_status == KeyStatus.QUARANTINED.value), (
        f"a binding with key_status={binding.key_status!r} was "
        f"{'refused' if refused else 'let through'} — the refusal must key on "
        "quarantine and on nothing else, or the probe that makes "
        "/register_guild worth running is either skipped or never skipped"
    )


# ===========================================================================
# Property 2 — the compensation for the golden assertion upstream.
# ===========================================================================

@given(
    binding=_bindings(key_status=st.just(KeyStatus.QUARANTINED.value)),
    guild_id=_GUILD_IDS,
)
@_SETTINGS
def test_every_refusal_names_the_exit_and_never_the_destructive_route(
    binding, guild_id: str
):
    """AC-008.1 / KPI-6 — over every quarantined state, not one fixture.

    The acceptance scenario asserts these three strings for a single binding
    whose reason, tags and dates are pinned. An officer's binding is not
    pinned: the tag may be missing, the reason may predate the marker, the
    quarantine date may be absent. The route out has to survive all of them,
    because a refusal that renders correctly only for the shape a test author
    chose is a dead end for everyone else.

    The uuid clause is KPI-6 and it is not incidental: the refusal is built
    from `quarantine_reason`, and that column carries the full observed
    identifier by design so drift can be re-reported.
    """
    refusal = _refusal_under_test()(binding, guild_id)

    assert "/update_guild_key" in refusal, (
        f"the refusal did not name the only exit from quarantine: {refusal!r}"
    )
    assert "quarantin" in refusal.lower(), (
        f"the refusal never named the actual problem: {refusal!r}"
    )
    assert _THE_DESTRUCTIVE_ROUTE not in refusal, (
        "a quarantined guild's officer was routed to deregistration, which "
        f"destroys the raid history and launders the quarantine: {refusal!r}"
    )
    assert not _UUID.search(refusal), (
        f"a full identifier reached an operator-facing reply (KPI-6): {refusal!r}"
    )
