"""Property tests for the vendor-body walk being TOTAL (slice 04, 06-02).

WHY-NEW-FILE: tests/unit/test_guild_body_totality.py
  CLOSEST-EXISTING: tests/unit/test_guild_identifier_canonicalisation.py
  EXTENSION-COST: every property in that module is quantified over the
    `guildId` VALUE inside a body it builds well-formed and holds constant —
    its `_surface()` helper hard-codes `{"guild": {...}}` and would have to
    become a second, differently-shaped generator for these properties to
    live there, at which point the module's own invariant ("only the
    identifier varies") stops holding for half its tests.
  PARALLEL-RATIONALE: the two modules quantify over ORTHOGONAL axes that the
    suite's own vocabulary already separates — `GuildIdVariant` (the value)
    versus `VendorBody` (the envelope) — and the acceptance suite's
    `_program(..., guild_id=..., body=...)` names them as two knobs precisely
    so a scenario meaning to vary one cannot silently vary the other. Merging
    them here would re-couple at unit level what DISTILL deliberately split.

WHY PROPERTIES AND NOT EXAMPLES. The acceptance suite pins nine body shapes
that a real HTTP 200 can carry. The vendor is not limited to nine, and the
claim this slice makes is not "those nine are handled" but "NO decoded JSON
value makes this function raise" — a universal statement over the whole input
space, which is exactly what no enumerated example can establish. The failure
being guarded is also unbounded in blast radius rather than local: an escaping
exception stops `discord.ext.tasks.Loop` and ends hourly ingestion for every
server on the bot, silently, until someone restarts the process.

DECLARED UNIVERSE. `_surface()` captures every observable slot one parse
produces, and each property asserts the WHOLE dict rather than one slot, so a
fix that classified correctly while quietly moving `members`, `status` or
`identity` fails here rather than in production:

    outcome            — the classification, asserted to stay inside the
                         five-member `ProbeOutcome` partition (DDD-6)
    identity_present   — present exactly when the identifier was usable
    uuid               — the canonical form the rest of the system compares
    short              — the operator-facing rendering that used to raise
    members            — dropped on UNVERIFIABLE, degraded on MATCH
    status             — 200 on every body-shaped refusal, which is what
                         distinguishes UNVERIFIABLE from UNREACHABLE
    error_present      — a refusal always says why; a MATCH never does
"""
from __future__ import annotations

import asyncio
import re
from contextlib import contextmanager

import pytest

from bot.services.tacticus.guild_client import (
    TACTICUS_GUILD_URL,
    GuildSnapshot,
    ProbeOutcome,
    fetch_guild_snapshot,
    parse_guild_snapshot,
)

hypothesis = pytest.importorskip(
    "hypothesis",
    reason="hypothesis is not installed — DISTILL pins it into requirements.txt",
)

import hypothesis.strategies as st  # noqa: E402
from hypothesis import given, settings  # noqa: E402

# Deselected from the 250-test baseline for the same reason the slice-04
# acceptance module is: they belong to the remediation slice, and the baseline
# command is the "nothing that shipped has regressed" gate.
pytestmark = [pytest.mark.property, pytest.mark.slice_04]

WORD_BEARERS_UUID = "b64bdba4-36ac-4229-bd29-4b7b6ce7f44f"

# The CLOSED vocabulary every body-shaped refusal is allowed to speak (KPI-6).
#
# Stated as a whole-string match rather than as "the body is not a substring of
# the error": a one-byte body is a substring of almost any sentence, so the
# substring form reports a false violation on `b"("` while a message that
# appended a LONG body would pass whenever the decoded bytes differed by one
# character. Anchoring the permitted set at both ends is both stronger and
# decidable — the message may name a fixed phrase and a TYPE, and there is no
# slot in the grammar for vendor content to occupy.
BODY_INDEPENDENT_REFUSAL = re.compile(
    r"^(?:"
    r"the response body is not JSON \(\w+\)"
    r"|the response body is not a guild object \(\w+\)"
    r"|the response's guild field is not a guild object \(\w+\)"
    r"|the response carries no guildId"
    r"|the response carries a guildId that is not a uuid \(\w+\)"
    r")$"
)

# Everything `json.loads` can hand back, nested to the depth a real payload
# reaches. Deliberately NOT filtered to "shapes we expect": the whole claim is
# that the function is total, and a generator that excluded the shapes the
# author did not think of would test the author's imagination instead.
json_values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(max_size=8),
    lambda children: st.lists(children, max_size=3)
    | st.dictionaries(st.text(max_size=6), children, max_size=3),
    max_leaves=8,
)

# Entries a vendor can put in `members` that no roster row can be built from.
# `{"userId": <non-str>}` and `{"userId": ""}` are in the set because a
# `frozenset[str]` that admits an int or a blank is corrupt rather than
# degraded — the caller cannot tell the difference at the point it breaks.
unusable_member_entries = st.one_of(
    st.just({}),
    st.just({"displayName": "a member the vendor sent partially"}),
    st.just({"userId": None}),
    st.just({"userId": ""}),
    st.just({"userId": 12345}),
    st.none(),
    st.text(max_size=4),
    st.integers(),
    st.lists(st.text(max_size=2), max_size=2),
)


def _surface(payload: object) -> dict:
    """The full observable surface of one parsed vendor body (see UNIVERSE)."""
    snapshot = parse_guild_snapshot(payload)
    identity = snapshot.identity
    return {
        "outcome": snapshot.outcome,
        "identity_present": identity is not None,
        "uuid": identity.uuid if identity else None,
        "short": identity.short if identity else None,
        "members": snapshot.members,
        "status": snapshot.status,
        "error_present": snapshot.error is not None,
    }


def _well_formed_body(members: list) -> dict:
    """The body Tacticus sends when nothing has gone wrong."""
    return {
        "guild": {
            "guildId": WORD_BEARERS_UUID,
            "guildTag": "EUVQZ",
            "name": "Word Bearers",
            "members": members,
        }
    }


@given(payload=json_values)
@settings(max_examples=400, deadline=None)
def test_no_decoded_body_can_stop_the_hourly_cycle(payload):
    """The totality invariant — the whole point of the slice.

    For EVERY value a JSON decoder can produce: a `GuildSnapshot` comes back,
    nothing is raised, and the classification stays inside the five-member
    partition DDD-6 declares. `parse_guild_snapshot` returning at all is half
    the assertion; the other half is that it did not return by inventing a
    sixth outcome.
    """
    snapshot = parse_guild_snapshot(payload)

    assert isinstance(snapshot, GuildSnapshot)
    assert snapshot.outcome in set(ProbeOutcome), (
        "the walk produced a classification outside the declared partition"
    )
    assert snapshot.outcome in {ProbeOutcome.MATCH, ProbeOutcome.UNVERIFIABLE}, (
        f"a decoded 200 body classified {snapshot.outcome} — MISMATCH is the "
        "policy layer's call and UNREACHABLE/DEAD are transport verdicts, "
        "so a body walk may reach neither (DDD-6)"
    )


@given(payload=json_values)
@settings(max_examples=400, deadline=None)
def test_a_body_that_is_not_a_guild_object_yields_nothing_to_write(payload):
    """AC-007.5 / AC-007.6 — refusal is whole, not partial.

    Asserts the ENTIRE universe for every body that is not a guild object, so
    an implementation that classified UNVERIFIABLE while still handing back a
    member set — a roster whose owner was never established, which is the
    invitation to write it — fails here.
    """
    if _names_a_guild(payload):
        return  # the MATCH half is asserted by the two properties below

    assert _surface(payload) == {
        "outcome": ProbeOutcome.UNVERIFIABLE,
        "identity_present": False,
        "uuid": None,
        "short": None,
        "members": frozenset(),
        "status": 200,
        "error_present": True,
    }


@given(
    arrived=st.lists(st.text(min_size=1, max_size=6), min_size=1, max_size=4, unique=True),
    lost=st.lists(unusable_member_entries, min_size=1, max_size=4),
)
@settings(max_examples=200, deadline=None)
def test_a_partially_sent_roster_moves_only_the_roster(arrived, lost):
    """AC-007.7, as a state delta against the same body sent whole.

    The declared delta is: `members` shrinks to exactly the entries that
    arrived, and EVERY other slot in the universe is unchanged from the clean
    parse. That is the property the acceptance scenario states in prose — a
    partially-sent roster is a roster-quality problem, never an identity one —
    and asserting it as a delta is what forbids the outcome, the identity or
    the status moving as a side effect of dropping an entry.
    """
    clean = _surface(_well_formed_body([{"userId": m} for m in arrived]))
    degraded = _surface(_well_formed_body(
        [{"userId": m} for m in arrived] + list(lost)
    ))

    assert degraded == clean, (
        "a member entry the vendor sent without a usable userId moved "
        "something other than the member set"
    )
    assert degraded["outcome"] is ProbeOutcome.MATCH
    assert degraded["members"] == frozenset(arrived)


@given(members=st.lists(unusable_member_entries, max_size=4))
@settings(max_examples=100, deadline=None)
def test_a_roster_of_only_unusable_entries_is_empty_and_not_an_error(members):
    """The far end of the degradation, kept on the roster side of the line.

    Every entry unusable is still MATCH: the identity was present and read
    cleanly, and only the roster is empty. The refusal to WRITE an empty
    roster belongs to `player_service._roster_write_refusal`, one layer up —
    reclassifying here would put a roster verdict inside the identity
    vocabulary and quarantine on a serialisation hiccup.
    """
    surface = _surface(_well_formed_body(members))

    assert surface["outcome"] is ProbeOutcome.MATCH
    assert surface["members"] == frozenset()
    assert surface["identity_present"] is True


@given(body=st.binary(max_size=64))
@settings(max_examples=150, deadline=None)
def test_arbitrary_bytes_at_200_are_unverifiable_and_never_unreachable(body):
    """The DECODE guard, and the taxonomy it must NOT collapse into.

    Drives the real `fetch_guild_snapshot` with a real `httpx.Response`
    carrying arbitrary bytes, so the guard is exercised through the production
    call path rather than around it. UNREACHABLE would mean "Tacticus is
    down"; the vendor answered 200, so the key worked and only the check did
    not. Collapsing the two makes an outage and a schema change
    indistinguishable to whoever is paged (DDD-6).
    """
    with _tacticus_answering(200, body):
        snapshot = asyncio.run(fetch_guild_snapshot("a-key"))

    assert snapshot.outcome is ProbeOutcome.UNVERIFIABLE
    assert snapshot.status == 200
    assert snapshot.identity is None
    assert snapshot.members == frozenset()
    assert BODY_INDEPENDENT_REFUSAL.match(snapshot.error or ""), (
        f"the refusal {snapshot.error!r} is not in the module's declared "
        "vocabulary — the message is anchored at both ends precisely so an "
        "implementation that appended the vendor body fails here. That "
        "string is copied into logs and pastebins, and the body arrived on "
        "the connection the key was sent over (KPI-6)"
    )


# ---------------------------------------------------------------------------
# Helpers — wiring only
# ---------------------------------------------------------------------------

def _names_a_guild(payload: object) -> bool:
    """Whether this body carries a readable identifier, decided WITHOUT the
    parser — a helper that asked the function under test which inputs it
    accepts would let a bug in it define the test's own expectations."""
    if not isinstance(payload, dict):
        return False
    guild = payload.get("guild") or {}
    if not isinstance(guild, dict):
        return False
    return guild.get("guildId") == WORD_BEARERS_UUID


@contextmanager
def _tacticus_answering(status: int, body: bytes):
    """Swap the httpx client production builds for one serving `body` verbatim.

    Same seam the acceptance suite uses (below `guild_client`, at the httpx
    boundary) so the bytes production reads are the bytes the property
    generated — including none at all, which is the zero-length 200 an
    exhausted upstream returns.
    """
    import httpx

    real_client = httpx.AsyncClient
    httpx.AsyncClient = lambda *args, **kwargs: _RawBodyTacticus(status, body)
    try:
        yield
    finally:
        httpx.AsyncClient = real_client


class _RawBodyTacticus:
    def __init__(self, status: int, body: bytes) -> None:
        self._status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url: str, headers: dict | None = None, **kwargs):
        import httpx

        assert url == TACTICUS_GUILD_URL, f"unexpected endpoint: {url}"
        return httpx.Response(
            self._status, content=self._body,
            headers={"content-type": "application/json"},
            request=httpx.Request("GET", url),
        )
