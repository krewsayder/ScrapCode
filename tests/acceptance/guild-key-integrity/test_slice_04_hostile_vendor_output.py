"""Slice 04 — survive hostile vendor output. Implements
`acceptance/slice-04-survive-hostile-vendor-output.feature`.

AUTHORED IN DISTILL, 2026-08-02, AFTER the adversarial re-review. Every
scenario here is expected to FAIL against production as it stands; the
failures ARE the deliverable, and `docs/feature/guild-key-integrity/distill/
red-classification.md` records the classification of each one. DELIVER makes
them green — do not adjust an assertion to meet the current behaviour.

Why this file did not exist before is more important than what is in it. The
DELIVER wave shipped with a payload builder that could render exactly one
shape: a well-formed `{"guild": {...}}` whose `guildId` was one of two
canonical constants. Every defect below was therefore not merely untested but
UNWRITABLE — no scenario expressible against that double could reach any of
them. The double was extended first (`conftest.GuildServiceResponse`,
`domain_types.VendorBody` / `GuildIdVariant`); these scenarios are what the
extension is for.

The transport double goes in BELOW `bot.services.tacticus.guild_client`, at
the httpx boundary, so every classification asserted here is made by
production code reading a real `httpx.Response` — including one whose
`.json()` raises.
"""
from __future__ import annotations

import pytest

from domain_types import (
    DARK_MECHANICUM,
    WORD_BEARERS,
    GuildIdentity,
    GuildIdVariant,
    KeyStatus,
    ProbeOutcome,
    VendorBody,
)
from conftest import (
    GUILD_DM,
    GUILD_WB,
    PROD_SERVER_ID,
    SEASON,
    FakeChannel,
    GuildServiceResponse,
)

pytestmark = pytest.mark.slice_04


# ===========================================================================
# AC-007.1 / AC-007.2 — the KPI-4 false-positive set
# ===========================================================================

@pytest.mark.kpi
@pytest.mark.driving_port
@pytest.mark.error
@pytest.mark.parametrize(
    "variant",
    [v for v in GuildIdVariant if v.names_the_bound_guild],
    ids=lambda v: v.value,
)
async def test_the_same_guild_written_differently_is_not_drift(
    sqlite_repo, bound_cluster, fake_guild_service, update_channel, key_events,
    variant: GuildIdVariant,
):
    """AC-007.1 / AC-007.2 / KPI-4 / DISCUSS D3.

    `matches()` compares `self.uuid == other.uuid` — a raw string compare
    against a value the vendor is free to re-case at any time, through a
    proxy chain free to prepend a BOM. Each variant below is the SAME guild.
    A single vendor formatting change flips every guild in the cluster to
    quarantined inside one cycle, and then REFUSES the operator's correct key
    for the same reason, because `install_guild_key` compares the same way.
    That is D3's trap arriving from the direction nobody guarded.

    `canonical` is in the set as the control: if it ever fails, the harness
    is broken rather than the classifier.
    """
    from bot.guilds import load_guild_binding

    before = load_guild_binding(PROD_SERVER_ID, GUILD_WB)
    _program(fake_guild_service, "wb-key", identity=WORD_BEARERS, guild_id=variant)

    await _run_hourly_cycle(fake_guild_service, update_channel)

    after = load_guild_binding(PROD_SERVER_ID, GUILD_WB)
    assert after.key_status == KeyStatus.ACTIVE.value, (
        f"a guildId written as {variant.value} quarantined a healthy guild — "
        "a vendor formatting change is now indistinguishable from the "
        "2026-07-28 incident"
    )
    assert after.tacticus_guild_id == before.tacticus_guild_id, (
        "the stored identity moved on a probe that should have agreed"
    )
    assert not key_events.named("guild.key.mismatch"), (
        f"{variant.value} was reported as drift — KPI-4's false-positive "
        "count is no longer zero"
    )


# ===========================================================================
# AC-007.3 / AC-007.4 — values no identity can be built from
# ===========================================================================

@pytest.mark.error
@pytest.mark.driving_port
@pytest.mark.parametrize(
    "variant",
    [
        GuildIdVariant.WHITESPACE_ONLY,
        GuildIdVariant.EMPTY_STRING,
        GuildIdVariant.JSON_NUMBER,
        GuildIdVariant.JSON_BOOL,
        GuildIdVariant.JSON_NULL,
        GuildIdVariant.NOT_A_UUID,
    ],
    ids=lambda v: v.value,
)
async def test_an_unusable_guild_identifier_is_unverifiable_never_drift(
    sqlite_repo, bound_cluster, fake_guild_service, update_channel, key_events,
    variant: GuildIdVariant,
):
    """AC-007.3 / AC-007.4 / DDD-6.

    `parse_guild_snapshot` guards with `if not uuid`, which catches `None`,
    `""` and `False` — and lets `"   "`, `12345`, `True` and
    `"not-a-uuid-at-all"` straight through into `GuildIdentity(uuid=...)`.
    A `GuildIdentity` holding `12345` then compares unequal to every stored
    binding, so the policy layer reads it as MISMATCH and quarantines. The
    key worked; only the CHECK did not — which is the definition of
    UNVERIFIABLE, and DDD-6 requires UNVERIFIABLE to leave `key_status`
    byte-identical.

    `12345` also breaks `GuildIdentity.short` (`self.uuid[:8]` on an int),
    so the operator-facing rendering raises while reporting the drift.
    """
    from bot.guilds import load_guild_binding

    before = load_guild_binding(PROD_SERVER_ID, GUILD_WB)
    _program(fake_guild_service, "wb-key", identity=WORD_BEARERS, guild_id=variant)

    await _run_hourly_cycle(fake_guild_service, update_channel)

    after = load_guild_binding(PROD_SERVER_ID, GUILD_WB)
    assert after == before, (
        f"a guildId of {variant.value} changed the stored binding — DDD-6 "
        "requires UNVERIFIABLE to leave it byte-identical"
    )
    assert key_events.named("guild.key.unverifiable"), (
        f"{variant.value} was not classified UNVERIFIABLE; events seen: "
        f"{key_events.all_events()}"
    )
    assert not key_events.named("guild.key.mismatch")


# ===========================================================================
# AC-007.5 / AC-007.6 — the availability regression
# ===========================================================================

@pytest.mark.error
@pytest.mark.driving_port
@pytest.mark.parametrize(
    "body",
    [
        VendorBody.NOT_JSON_HTML,
        VendorBody.EMPTY,
        VendorBody.TRUNCATED_JSON,
        VendorBody.JSON_NULL,
        VendorBody.JSON_LIST,
        VendorBody.JSON_STRING,
        VendorBody.JSON_BOOL,
        VendorBody.GUILD_NOT_A_DICT,
        VendorBody.GUILD_NULL,
    ],
    ids=lambda b: b.value,
)
async def test_a_body_that_is_not_a_guild_object_never_ends_the_cycle(
    sqlite_repo, bound_cluster, fake_guild_service, update_channel, key_events,
    body: VendorBody,
):
    """AC-007.5 / AC-007.6 — the most severe defect found in the re-review.

    `fetch_guild_snapshot` documents itself as "Never raises for an expected
    failure", and then calls `response.json()` outside any guard
    (`guild_client.py:183`). A 200 carrying an nginx 502 page raises
    `JSONDecodeError` there; the payload walk raises `AttributeError` on a
    non-dict `guild`; a bare list raises on `payload.get`. None of those is
    caught by `verify_and_resolve`, none by `_update_one_guild` (whose only
    `except` is `GuildQuarantined`), none by `_update_one_server`, and
    `discord.ext.tasks.Loop` STOPS on an unhandled exception.

    So a single malformed 200 from the vendor permanently ends hourly
    ingestion for every server on the bot until someone restarts the process
    — and nothing announces it. UNVERIFIABLE is the correct classification:
    the key worked, the check did not, retry next cycle, change nothing.
    """
    from bot.guilds import load_guild_binding

    before = load_guild_binding(PROD_SERVER_ID, GUILD_WB)
    _program(fake_guild_service, "wb-key", identity=WORD_BEARERS, body=body)

    # No `pytest.raises`: the assertion IS that nothing escapes. A scenario
    # that wrapped this would be asserting the defect rather than the fix.
    await _run_hourly_cycle(fake_guild_service, update_channel)

    after = load_guild_binding(PROD_SERVER_ID, GUILD_WB)
    assert after == before, (
        f"a {body.value} body changed the stored binding — DDD-6 requires "
        "UNVERIFIABLE to leave key_status byte-identical"
    )
    assert key_events.named("guild.key.unverifiable"), (
        f"{body.value} was not classified UNVERIFIABLE; events seen: "
        f"{key_events.all_events()}"
    )


@pytest.mark.kpi
@pytest.mark.error
@pytest.mark.driving_port
async def test_one_unreadable_answer_does_not_stop_the_other_guilds(
    sqlite_repo, bound_cluster, fake_guild_service, update_channel, key_events
):
    """AC-007.9 / KPI-5 — containment, stated as the thing operators feel.

    The preceding scenario proves the cycle survives. This one proves the
    survival is worth something: the SIBLING guild, whose key answers
    perfectly, must still be probed and still have its data written in the
    SAME cycle. A guard that catches the exception and then abandons the
    remaining guilds converts a crash into a silent stall, which is strictly
    worse — it looks healthy.

    Word Bearers is first in iteration order (the `bound_cluster` fixture
    pins it), so the unreadable answer is encountered BEFORE the healthy
    sibling. A fixture that happened to order them the other way would pass
    against a cycle that aborts on the first failure.
    """
    _program(fake_guild_service, "wb-key", identity=WORD_BEARERS,
             body=VendorBody.NOT_JSON_HTML)
    _program(fake_guild_service, "dm-key", identity=DARK_MECHANICUM,
             members=["dm1", "dm2"])

    await _run_hourly_cycle(fake_guild_service, update_channel)

    assert fake_guild_service.was_called_with("dm-key"), (
        "the healthy sibling was never probed — one guild's malformed "
        "response ended the cycle for the rest of the cluster (KPI-5 = 0%)"
    )
    probe_ok = [
        r for r in key_events.named("guild.key.probe.ok")
        if getattr(r, "guild_id", None) == GUILD_DM
    ]
    assert probe_ok, (
        "the sibling guild produced no successful probe record, so nothing "
        "was ingested for it this cycle"
    )


# ===========================================================================
# AC-007.7 — a partially-sent roster
# ===========================================================================

@pytest.mark.error
@pytest.mark.driving_port
async def test_a_partially_sent_roster_degrades_instead_of_ending_the_cycle(
    sqlite_repo, bound_cluster, fake_guild_service, update_channel, key_events
):
    """AC-007.7.

    `frozenset(m["userId"] for m in guild.get("members") or [])` is eager and
    subscripts every entry. One member the vendor sent without a `userId`
    raises `KeyError` for a roster that is otherwise entirely usable, and the
    raise lands in the same unguarded path as AC-007.5.

    The identity is present and agrees, so the outcome is MATCH: this is a
    roster-quality problem, not an identity problem, and conflating them
    would let a vendor serialisation hiccup quarantine a guild.
    """
    from bot.guilds import load_guild_binding

    _program(
        fake_guild_service, "wb-key", identity=WORD_BEARERS,
        members=["u1", "u2", "u3"], body=VendorBody.MEMBER_WITHOUT_USER_ID,
    )

    await _run_hourly_cycle(fake_guild_service, update_channel)

    binding = load_guild_binding(PROD_SERVER_ID, GUILD_WB)
    assert binding.key_status == KeyStatus.ACTIVE.value, (
        "a malformed member entry quarantined the guild — a roster problem "
        "was read as an identity problem"
    )
    assert key_events.named("guild.key.probe.ok"), (
        "the probe did not succeed even though the identity was present and "
        f"agreed; events seen: {key_events.all_events()}"
    )


# ===========================================================================
# AC-007.8 — the regression guard on the whole slice
# ===========================================================================

@pytest.mark.kpi
@pytest.mark.driving_port
async def test_a_genuinely_different_guild_still_quarantines(
    sqlite_repo, bound_cluster, fake_guild_service, update_channel, key_events
):
    """AC-007.8 — the control, and the slice's acceptance gate.

    Canonicalisation makes the comparison strictly MORE permissive. A sloppy
    implementation — normalising away hyphens, or casefolding a non-uuid
    string that then satisfies `UUID_PATTERN` — could make two genuinely
    different guilds compare equal, which is the original incident restored
    by the fix meant to prevent it. This scenario must stay green through
    every change slice 04 makes; if it ever reds, the canonicalisation is
    over-normalising and the slice is not acceptable.
    """
    from bot.guilds import load_guild_binding

    _program(fake_guild_service, "wb-key", identity=DARK_MECHANICUM,
             members=["x1", "x2"])

    await _run_hourly_cycle(fake_guild_service, update_channel)

    binding = load_guild_binding(PROD_SERVER_ID, GUILD_WB)
    assert binding.key_status == KeyStatus.QUARANTINED.value, (
        "real drift no longer quarantines — canonicalisation made two "
        "different guilds compare equal, which is the incident itself"
    )
    assert WORD_BEARERS.tag in binding.quarantine_reason
    assert DARK_MECHANICUM.tag in binding.quarantine_reason


# ===========================================================================
# AC-007.10 — the same canonicalisation on the recovery path
# ===========================================================================

@pytest.mark.error
@pytest.mark.driving_port
async def test_a_poisoned_binding_still_accepts_the_operators_correct_key(
    sqlite_repo, registered_guilds, fake_guild_service
):
    """AC-007.10 / DISCUSS D3 — quarantine is never a trap.

    Canonicalising the hourly comparison alone is not enough.
    `install_guild_key` builds `GuildIdentity(uuid=binding.tacticus_guild_id)`
    and calls the same raw `matches()`, so a binding whose stored uuid was
    adopted in a non-canonical form refuses the operator's correct key
    forever. The guild is quarantined, the only exit is `/update_guild_key`,
    and `/update_guild_key` says no — which is exactly the SSH session this
    feature exists to retire.

    The `Given` is a binding stored in upper case, which is reachable today:
    trust-on-first-use (DDD-8) writes `observed.uuid` verbatim, so whatever
    casing the vendor sent on adoption day is what is stored.
    """
    from bot.guilds import load_guild_binding, save_guild_binding
    from bot.repository import GuildBinding
    import bot.guild_keys as guild_keys

    save_guild_binding(PROD_SERVER_ID, GUILD_WB, GuildBinding(
        tacticus_guild_id=WORD_BEARERS.uuid.upper(),
        tacticus_guild_tag=WORD_BEARERS.tag,
        tacticus_guild_name=WORD_BEARERS.name,
        identity_bound_at="2026-07-31T04:00:00Z",
        key_status=KeyStatus.QUARANTINED.value,
        quarantine_reason="key drift (recorded before this slice)",
        quarantined_at="2026-07-31T04:00:00Z",
    ))
    _program(fake_guild_service, "the-correct-key", identity=WORD_BEARERS)

    with _tacticus_answered_by(fake_guild_service):
        result = await guild_keys.install_guild_key(
            PROD_SERVER_ID, GUILD_WB, "the-correct-key", force=False
        )

    assert result.outcome is ProbeOutcome.MATCH, (
        "the operator's correct key was refused because the stored uuid was "
        "written in a different case — quarantine is a trap"
    )
    assert load_guild_binding(PROD_SERVER_ID, GUILD_WB).key_status == \
        KeyStatus.ACTIVE.value


# ===========================================================================
# AC-007.11 — the production-data criterion
# ===========================================================================

@pytest.mark.real_io
@pytest.mark.adapter_integration
def test_the_recorded_vendor_response_still_matches_after_recasing(
    recorded_guild_response,
):
    """AC-007.11 — the slice's production-data criterion, not synthetic.

    Replays the RECORDED Tacticus body with its `guildId` re-cased and
    BOM-prefixed. A hand-written stub would let this pass against an
    implementation reading a field name the vendor does not use; the recorded
    file is the one whose header records that `guildId` is undocumented,
    which is the argument for totality in the first place.
    """
    from bot.services.tacticus.guild_client import parse_guild_snapshot

    recorded_uuid = recorded_guild_response["guild"]["guildId"]
    poisoned = {
        **recorded_guild_response,
        "guild": {
            **recorded_guild_response["guild"],
            "guildId": f"﻿{recorded_uuid.upper()}",
        },
    }

    snapshot = parse_guild_snapshot(poisoned)

    assert snapshot.outcome is ProbeOutcome.MATCH
    assert snapshot.identity is not None
    assert snapshot.identity.matches(GuildIdentity(uuid=recorded_uuid)), (
        "the recorded response stopped matching its own guild once the "
        "vendor re-cased the identifier and a proxy prepended a BOM"
    )


# ===========================================================================
# Helpers — wiring only
# ===========================================================================
from contextlib import contextmanager  # noqa: E402 — helpers-only dependency


def _program(service, api_key: str, *, identity, guild_id=GuildIdVariant.CANONICAL,
             body=VendorBody.WELL_FORMED, members=None) -> None:
    """Program one answer, naming both knobs explicitly at every call site.

    `guild_id` chooses the VALUE of the identifier, `body` the SHAPE of the
    envelope around it; they are orthogonal and a scenario that means to vary
    one should not silently vary the other.
    """
    service.program(api_key, GuildServiceResponse(
        identity=identity,
        members=members if members is not None else ["u1", "u2"],
        guild_id=guild_id,
        body=body,
    ))


async def _run_hourly_cycle(service, channel) -> None:
    """Drive one `auto_update` tick through the production composition root.

    Same shape as slice 01/03 (UD-10: replicated, never cross-imported), with
    ONE difference that is the point of this module: the transport double
    renders through `GuildServiceResponse.as_httpx_response`, so a scenario
    can put a body on the wire that `response.json()` refuses to parse. The
    slice-01/03 copies build `httpx.Response(status, json=...)` and are
    structurally incapable of it.
    """
    _register_the_bound_cluster(service)
    cog = _tasks_cog_posting_to(channel)
    with _tacticus_answered_by(service):
        await cog.auto_update()


def _register_the_bound_cluster(service) -> None:
    """Ensure both guilds are registered, Word Bearers FIRST, both answering.

    Ordering is pinned for the same reason slice 03 pins it: the containment
    scenario only distinguishes "the cycle survived and continued" from "the
    cycle survived by luck" when the failing guild is met first.

    The sibling's healthy answer is programmed here rather than in every
    scenario because it is the Background's `And the guild service is
    answering`, not a scenario's subject — every scenario in this module
    varies WORD BEARERS' answer and holds the cluster around it constant.
    Programmed with `setdefault` semantics so the one scenario that DOES
    care about the sibling (`test_one_unreadable_answer_does_not_stop_the_
    other_guilds`) keeps its own explicit programming.
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
    if "dm-key" not in service._by_key:
        _program(service, "dm-key", identity=DARK_MECHANICUM, members=["dm1", "dm2"])


@contextmanager
def _tacticus_answered_by(guild_service):
    import httpx

    real_client = httpx.AsyncClient
    httpx.AsyncClient = lambda *args, **kwargs: _HostileTacticus(guild_service)
    try:
        yield
    finally:
        httpx.AsyncClient = real_client


class _HostileTacticus:
    """An `httpx.AsyncClient` that can serve a body production cannot parse.

    The guild endpoint is rendered by the programmed answer itself
    (`as_httpx_response`), so the bytes production reads are the bytes the
    scenario declared — including none at all. The season and raid endpoints
    answer normally: this slice is about the guild endpoint, and a scenario
    that also broke the raid feed could not tell which failure it had caught.
    """

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

        if url == TACTICUS_GUILD_URL:
            answer = self._guild_service.answer_for((headers or {}).get("X-API-KEY", ""))
            return answer.as_httpx_response(url)
        request = httpx.Request("GET", url)
        if url == TACTICUS_RAID_URL.format(season=SEASON):
            return httpx.Response(200, json={"entries": _raid_entries()}, request=request)
        if url == TACTICUS_CURRENT_RAID:
            return httpx.Response(200, json={"season": SEASON}, request=request)
        raise AssertionError(f"the loop called an endpoint no scenario declared: {url}")


def _raid_entries() -> list[dict]:
    """One battle hit and one bomb hit, in raw vendor shape (verbatim from
    slice 03)."""
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


def _tasks_cog_posting_to(channel):
    """The real cog, minus the scheduler (verbatim from slice 01/03)."""
    from bot.cogs.tasks_cog import TasksCog
    from bot.services.chronicl3r.player_service import PlayerService

    cog = TasksCog.__new__(TasksCog)
    cog.bot = _FakeBot(channel)
    cog.player_service = PlayerService(_FakeChroniclerClient())
    return cog


class _FakeBot:
    def __init__(self, channel) -> None:
        self._channel = channel

    def get_channel(self, channel_id: int):
        return self._channel


class _FakeChroniclerClient:
    """Chronicler is a paid external service — faked (verbatim from slice 01)."""

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
