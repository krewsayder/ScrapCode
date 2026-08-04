"""Property tests for the guild-identifier canonicaliser (slice 04, 06-01).

WHY-NEW-FILE: tests/unit/test_guild_identifier_canonicalisation.py
  CLOSEST-EXISTING: tests/unit/test_guild_keys_policy.py
  EXTENSION-COST: that module drives `bot/guild_keys.py` with a stubbed
    `guild_client` and asserts policy branches; these properties drive
    `guild_client` itself and would have to stub nothing, so they would sit
    inside a module whose every fixture they bypass.
  PARALLEL-RATIONALE: different unit under test (the vendor-facing parser vs
    the policy layer above it) and an incompatible dependency set — these
    tests need `hypothesis` and no repository at all, while every test in
    the policy module needs the storage fakes.

WHY PROPERTIES AND NOT EXAMPLES. The acceptance suite pins six ways a vendor
might re-write the SAME uuid. The vendor is not limited to six, and the
dangerous direction is not "did we handle the six" but "does any pair of
DISTINCT uuids now read as equal" — a claim over the whole input space that
no enumerated example can establish. Canonicalisation makes the comparison
strictly MORE permissive, so the regression it can cause is exactly the
2026-07-28 incident, and the guard has to be quantified.

DECLARED UNIVERSE. Each property below asserts over the FULL observable
surface a `guildId` value can be read through, not a single slot:

    outcome            — the classification `parse_guild_snapshot` returns
    identity           — present exactly when the identifier was usable
    identity.uuid      — the canonical form the rest of the system compares
    identity.matches   — the comparison every binding decision runs through
    identity.short     — the operator-facing rendering that used to raise
    members            — dropped on UNVERIFIABLE, kept otherwise

`_surface()` captures all six; the equivalence property compares the whole
dict, so a canonicalisation that fixed the comparison while silently moving
`members` or `short` would fail here rather than in production.
"""
from __future__ import annotations

import pytest

from bot.services.tacticus.guild_client import (
    UUID_PATTERN,
    GuildIdentity,
    ProbeOutcome,
    canonical_guild_id,
    parse_guild_snapshot,
)

hypothesis = pytest.importorskip(
    "hypothesis",
    reason="hypothesis is not installed — DISTILL pins it into requirements.txt",
)

import hypothesis.strategies as st  # noqa: E402
from hypothesis import assume, given, settings  # noqa: E402

# Deselected from the 250-test baseline for the same reason the slice-04
# acceptance module is: they belong to the remediation slice, and the
# baseline command is the "nothing that shipped has regressed" gate.
pytestmark = [pytest.mark.property, pytest.mark.slice_04]

BOM = "\ufeff"

# Every way a vendor can re-case a uuid without changing which guild it names.
# `_alternating` rather than only `.upper()`: an implementation that
# normalises one direction only passes `upper` and fails here.
_CASINGS = (
    lambda uuid: uuid,
    lambda uuid: uuid.upper(),
    lambda uuid: uuid.lower(),
    lambda uuid: "".join(
        c.upper() if i % 2 == 0 else c.lower() for i, c in enumerate(uuid)
    ),
)

# Whitespace and BOM, at either end, in either order — what a proxy, a CDN or
# a copy-paste into a config file adds around a value in transit.
_SURROUNDINGS = st.sampled_from(
    ["", " ", "  ", "\t", "\n", "\r\n", BOM, BOM + " ", " " + BOM, " \t\n"]
)


@st.composite
def same_uuid_written_two_ways(draw) -> tuple[str, str]:
    """A canonical uuid, and the same uuid as a hostile vendor might send it."""
    canonical = str(draw(st.uuids()))
    written = draw(st.sampled_from(_CASINGS))(canonical)
    return canonical, f"{draw(_SURROUNDINGS)}{written}{draw(_SURROUNDINGS)}"


# Values no identity can be built from. Text lengths are bounded AWAY from a
# uuid's 36 characters rather than filtered through the function under test —
# a generator that asks the SUT which inputs are invalid proves nothing.
unusable_identifiers = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.lists(st.text(max_size=4), max_size=2),
    st.dictionaries(st.text(max_size=2), st.text(max_size=2), max_size=1),
    st.text(alphabet=" \t\r\n" + BOM, max_size=8),
    st.text(max_size=20),
    st.text(min_size=40, max_size=60),
)


def _surface(raw_guild_id: object, members: list[str]) -> dict:
    """The full observable surface of one parsed vendor body (see UNIVERSE)."""
    snapshot = parse_guild_snapshot({
        "guild": {
            "guildId": raw_guild_id,
            "guildTag": "EUVQZ",
            "name": "Word Bearers",
            "members": [{"userId": m} for m in members],
        }
    })
    identity = snapshot.identity
    return {
        "outcome": snapshot.outcome,
        "identity_present": identity is not None,
        "uuid": identity.uuid if identity else None,
        "short": identity.short if identity else None,
        "members": snapshot.members,
    }


@given(pair=same_uuid_written_two_ways(), members=st.lists(st.text(min_size=1), max_size=4))
@settings(max_examples=200, deadline=None)
def test_how_the_vendor_writes_a_guild_id_never_changes_what_is_read(pair, members):
    """The KPI-4 invariant: noise around a uuid is not a different guild.

    Asserts the WHOLE universe, not just the outcome — a fix that classified
    correctly but handed back a differently-cased `uuid`, an unusable `short`
    or a smaller member set would still have moved something downstream.
    """
    canonical, as_written = pair

    assert _surface(as_written, members) == _surface(canonical, members)
    assert GuildIdentity(uuid=as_written).matches(GuildIdentity(uuid=canonical))
    assert GuildIdentity(uuid=canonical).matches(GuildIdentity(uuid=as_written))


@given(left=same_uuid_written_two_ways(), right=same_uuid_written_two_ways())
@settings(max_examples=200, deadline=None)
def test_two_different_guilds_never_read_as_one(left, right):
    """The regression guard, quantified — AC-007.8's property form.

    Canonicalisation is only safe if it is injective on real identifiers. If
    ANY pair of distinct uuids collapses under it, the drift detection this
    feature exists to provide is silently off for that pair.
    """
    canonical_left, written_left = left
    canonical_right, written_right = right
    assume(canonical_left != canonical_right)

    assert canonical_guild_id(written_left) != canonical_guild_id(written_right)
    assert not GuildIdentity(uuid=written_left).matches(GuildIdentity(uuid=written_right))
    assert _surface(written_left, [])["uuid"] != _surface(written_right, [])["uuid"]


@given(raw=unusable_identifiers, members=st.lists(st.text(min_size=1), max_size=4))
@settings(max_examples=200, deadline=None)
def test_an_identifier_no_guild_can_be_built_from_is_unverifiable(raw, members):
    """DDD-6: the key worked, only the check did not — so change nothing.

    The universe assertion carries the `short` fix: `identity_present` is
    False for every one of these, which is WHY the operator-facing rendering
    can no longer be handed a value it cannot subscript.
    """
    # No `assume` guard: the strategy is unusable BY CONSTRUCTION (non-str, or
    # text bounded away from a uuid's 36 characters). Filtering the generator
    # through the function under test would let a bug in it define the test's
    # own input space.
    assert _surface(raw, members) == {
        "outcome": ProbeOutcome.UNVERIFIABLE,
        "identity_present": False,
        "uuid": None,
        "short": None,
        "members": frozenset(),
    }
    assert not GuildIdentity(uuid=raw).matches(GuildIdentity(uuid=raw))


@given(uuid=st.uuids())
@settings(max_examples=100, deadline=None)
def test_canonicalisation_only_ever_removes_noise(uuid):
    """The four operations, and the absence of a fifth.

    Stripping hyphens or applying unicode normalisation would each merge
    identifiers that name different guilds, so the canonical form is asserted
    to still BE a uuid, and the two normalisations nobody may add are
    asserted to be rejected rather than accepted.
    """
    canonical = str(uuid)

    assert canonical_guild_id(canonical) == canonical.casefold()
    assert UUID_PATTERN.match(canonical_guild_id(canonical))
    assert canonical_guild_id(canonical.replace("-", "")) is None, (
        "hyphens were stripped — a 32-character digest is not this uuid"
    )
    assert canonical_guild_id(f"{canonical[:-1]}{BOM}{canonical[-1]}") is None, (
        "a BOM was removed from INSIDE the value, not just from its ends"
    )
