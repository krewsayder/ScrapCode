"""Tacticus guild endpoint — identity + roster from a single read.

The only module in the codebase that issues `GET /api/v1/guild`.

This module owns the vocabulary the rest of the feature classifies against:
`ProbeOutcome`, `KeyStatus` and `GuildIdentity` are defined HERE and imported
everywhere else, including by the acceptance suite's `domain_types.py`
(Mandate-12: one definition, reused, never re-declared per layer).

DDD-2: the identity probe is folded into the roster fetch. One call, one
response — a probe and a roster from two calls could disagree, and the
per-hour Tacticus call volume does not rise.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

TACTICUS_GUILD_URL = "https://api.tacticusgame.com/api/v1/guild"

# MOVED here from `bot/guilds.py` (where it had no remaining caller) rather
# than copied. `guild_client` is the guild-identity vocabulary's home and is
# import-light — `re` is stdlib and costs nothing, whereas importing
# `bot.guilds` from here would drag the composition root (and the repository
# it builds at import time) into every module that wants the vocabulary, and
# importing THIS module from `bot/guilds.py` would put `httpx` on the wrapper
# layer's transitive import graph, which `test_archon_rules_hold` forbids.
# One definition, in the only place both constraints allow (Mandate-12).
UUID_PATTERN = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE
)

# U+FEFF. A proxy or CDN that re-encodes a JSON body is free to prepend one,
# and it is NOT whitespace to `str.strip()` (category Cf), so it has to be
# named explicitly or it survives into the comparison.
BYTE_ORDER_MARK = "\ufeff"

# Reused verbatim from `PlayerService._fetch_roster`, the call this module
# replaces. The endpoint, the header shape and this timeout are the same read
# that produced every roster written so far; keeping them identical is what
# makes the member set in a snapshot the set the old reader produced.
_REQUEST_TIMEOUT_SECONDS = 20.0

# HTTP statuses Tacticus returns for a revoked or invalid key. Verified
# against Tacticus 2026-07-25. Lifted here from
# bot/cogs/registration_cog.py::_DEAD_KEY_STATUSES so the two paths share one
# taxonomy rather than drifting (Reuse Analysis: EXTEND, reuse verbatim).
DEAD_KEY_STATUSES = (401, 403)


class ProbeOutcome(Enum):
    """The five classifications one verification can produce (ADR-008 D6).

    Collapsing any two of these is a documented failure mode:
      MISMATCH vs UNREACHABLE  → a Tacticus outage quarantines the cluster
      MISMATCH vs UNVERIFIABLE → a vendor change quarantines the cluster
      MISMATCH vs DEAD         → a revoked key needs recovery it does not need
    """

    MATCH = "match"
    MISMATCH = "mismatch"
    UNVERIFIABLE = "unverifiable"
    UNREACHABLE = "unreachable"
    DEAD = "dead"


class KeyStatus(Enum):
    """Persisted lifecycle state of a guild's key (ADR-008 D5)."""

    ACTIVE = "active"
    QUARANTINED = "quarantined"


def canonical_guild_id(value: object) -> str | None:
    """The one canonical form of a `guildId`, or None when there isn't one.

    EXACTLY four operations, in this order, and nothing more:

      1. reject anything that is not a `str` — an int, a bool or None is not
         an identifier, it is the absence of one;
      2. strip surrounding whitespace and the BOM (either order, either end);
      3. validate what is left against `UUID_PATTERN`;
      4. casefold.

    The order is the safety property. Casefolding BEFORE validating would
    admit strings that are only uuid-shaped once folded, and two different
    guilds that both fold onto the same shape is the 2026-07-28 incident
    restored by the fix meant to prevent it. Hyphens are NOT stripped and
    unicode is NOT normalised for the same reason: every operation here has
    to be one that cannot merge two distinct identifiers.

    Returning None rather than raising is deliberate — an identifier no
    identity can be built from is UNVERIFIABLE (the key worked, only the check
    did not), and DDD-6 requires that to leave the binding byte-identical.
    """
    if not isinstance(value, str):
        return None
    candidate = _without_surrounding_noise(value)
    if not UUID_PATTERN.match(candidate):
        return None
    return candidate.casefold()


def _without_surrounding_noise(value: str) -> str:
    """Whitespace and BOM removed from BOTH ends, in either order.

    Three passes rather than one: `str.strip()` does not treat U+FEFF as
    whitespace, so a space-then-BOM prefix needs the space gone before the
    BOM sits at an end, and a BOM-then-space prefix needs the BOM gone
    before the space does. Only the ENDS are touched — a BOM in the MIDDLE
    of a value stays there, fails `UUID_PATTERN`, and the value is refused,
    which is the correct answer for a value that arrived corrupted.
    """
    return value.strip().strip(BYTE_ORDER_MARK).strip()


@dataclass(frozen=True)
class GuildIdentity:
    """The guild a key resolves to.

    `uuid` is the ONLY field compared (ADR-008 D1). `tag` and `name` are
    display-only and may be absent — a guild that retags or renames must not
    trip the lock, so they are deliberately not part of any equality check
    used for the binding decision.
    """

    uuid: str
    tag: str | None = None
    name: str | None = None

    def matches(self, other: "GuildIdentity") -> bool:
        """Compare the two identifiers canonically, BOTH sides.

        Both sides, not just the observed one: a binding adopted before this
        existed holds whatever casing the vendor sent on adoption day
        (trust-on-first-use writes `observed.uuid` verbatim, DDD-8), and
        `install_guild_key` compares the operator's correct key against it.
        Canonicalising only the incoming side would leave that guild
        quarantined with its only exit refusing the right key — quarantine as
        a trap, which DISCUSS D3 forbids.

        An identifier with no canonical form matches nothing, INCLUDING
        another identifier with no canonical form: two values neither of which
        names a guild do not name the same guild.
        """
        mine = canonical_guild_id(self.uuid)
        return mine is not None and mine == canonical_guild_id(other.uuid)

    @property
    def short(self) -> str:
        """First 8 characters — the form shown to operators (AC-005.4)."""
        return self.uuid[:8]


@dataclass(frozen=True)
class GuildSnapshot:
    """One read of `/api/v1/guild`: who the key belongs to, and who is in it.

    `identity` is None exactly when `outcome` is UNVERIFIABLE, UNREACHABLE or
    DEAD. `raw` is retained so the contract test can diff the live response
    against the recorded fixture.
    """

    outcome: ProbeOutcome
    identity: GuildIdentity | None = None
    members: frozenset[str] = frozenset()
    status: int | None = None
    error: str | None = None
    raw: dict | None = None


def parse_guild_snapshot(payload: object) -> GuildSnapshot:
    """Classify a decoded 200-OK `/api/v1/guild` body into a snapshot.

    TOTAL over every value a JSON decoder can produce, by construction: the
    parameter is `object`, not `dict`, because a 200 is a statement about the
    KEY and says nothing whatsoever about the shape of what follows it. The
    vendor's `guildId` is undocumented and the payload is unversioned, so
    "well-formed object" is an assumption; every step below therefore checks
    the shape it is about to walk instead of asserting it. A step that raised
    would leave `discord.ext.tasks.Loop` stopped and hourly ingestion dead for
    every server on the bot until someone restarted the process (AC-007.5).

    Every refusal is UNVERIFIABLE and never UNREACHABLE: the key WORKED — the
    vendor answered 200 — and only the check did not, so DDD-6 requires the
    binding left byte-identical and the next cycle to retry. There is NO
    fallback to comparing `guildTag` when the identifier cannot be read: a
    quiet downgrade to a weaker check is the same failure shape as the
    incident this feature exists to prevent (DDD-10).

    A `GuildIdentity` is constructed ONLY from a value that canonicalised, so
    every identity in the system holds a validated canonical uuid: `matches`
    cannot be fooled by casing and `short` cannot raise on a value it can no
    longer be handed.

    A resolved identity is reported as MATCH: this function was given no
    binding to compare against, so it reports "a guild resolved". Deciding
    MATCH vs MISMATCH is the policy layer's call, and it is the only caller
    that holds the expected identity.
    """
    if not isinstance(payload, dict):
        # A bare list, string, bool or `null` is valid JSON and not a guild.
        # `raw` stays None rather than carrying the value: the field exists so
        # the contract test can diff a body against the recorded fixture, and
        # a non-object has nothing to diff.
        return _unverifiable_body(_not_a_guild_object("the response body", payload))

    guild = payload.get("guild") or {}
    if not isinstance(guild, dict):
        # A TRUTHY non-dict — `{"guild": "unavailable"}`. The falsy cases
        # (`null`, `{}`, `[]`, `""`) are already absorbed by the `or {}` above
        # and reported as an absent identifier, which is what they are.
        return _unverifiable_body(
            _not_a_guild_object("the response's guild field", guild), raw=payload,
        )

    raw_uuid = guild.get("guildId")
    uuid = canonical_guild_id(raw_uuid)

    if uuid is None:
        # The members are deliberately dropped as well: a roster whose owner
        # cannot be established is one nobody may write, and handing it back
        # anyway is the invitation to write it.
        return _unverifiable_body(_no_usable_guild_id(raw_uuid), raw=payload)

    return GuildSnapshot(
        outcome=ProbeOutcome.MATCH,
        identity=GuildIdentity(
            uuid=uuid,
            tag=guild.get("guildTag"),
            name=guild.get("name"),
        ),
        members=_member_ids(guild.get("members")),
        status=200,
        raw=payload,
    )


def _unverifiable_body(reason: str, *, raw: dict | None = None) -> GuildSnapshot:
    """A 200 whose body could not be read — the key worked, the check did not.

    One constructor for every body-shape refusal so the three call sites
    cannot drift into three different outcomes. `status` is 200 because that
    is what the vendor said, and it is the field that distinguishes this from
    UNREACHABLE for whoever reads the record afterwards.
    """
    return GuildSnapshot(
        outcome=ProbeOutcome.UNVERIFIABLE,
        status=200,
        error=reason,
        raw=raw,
    )


def _not_a_guild_object(what: str, value: object) -> str:
    """Why the body could not be walked, naming the TYPE and never the value.

    Same rule as `_no_usable_guild_id`, for the same reason and one level up:
    the body is unvalidated vendor output on the same connection the key was
    sent over, and an error string is copied into logs, alerts and pastebins
    by people trying to help. Only the type name crosses that boundary
    (KPI-6).
    """
    return f"{what} is not a guild object ({type(value).__name__})"


def _member_ids(members: object) -> frozenset[str]:
    """The member ids that actually arrived — and only those (AC-007.7).

    A roster DEGRADES where an identity cannot. One entry the vendor sent
    without a usable `userId` is dropped and the entries that did arrive stay
    usable, because a partially-serialised roster is a roster-QUALITY problem
    and treating it as an identity problem would let a vendor hiccup
    quarantine a healthy guild. The downstream guard that refuses to write an
    EMPTY member set (`player_service._roster_write_refusal`) is what stops
    the degradation going too far.

    Non-list `members` yields nothing rather than raising: iterating a string
    would silently produce characters, which is worse than reporting none.
    """
    if not isinstance(members, (list, tuple)):
        return frozenset()
    arrived = (_user_id_of(entry) for entry in members)
    return frozenset(user_id for user_id in arrived if user_id)


def _user_id_of(entry: object) -> str:
    """The member id this entry carries, or `""` when it carries none.

    The ONE place that knows the field is spelled `userId`, so a vendor rename
    is one edit rather than a predicate and an extractor that can disagree.

    `str` and non-empty, not merely present: `members` is declared
    `frozenset[str]` and every reader downstream treats its contents as player
    identifiers, so a number or a blank admitted here would surface as a
    corrupt row rather than as a dropped one. `""` carries both "no field" and
    "unusable field" because the caller's answer to them is the same — drop
    this entry, keep the rest — and it keeps the collected set `frozenset[str]`
    as declared rather than one holding a sentinel.
    """
    if not isinstance(entry, dict):
        return ""
    user_id = entry.get("userId")
    return user_id if isinstance(user_id, str) else ""


def _no_usable_guild_id(raw_uuid: object) -> str:
    """Why the identifier could not be used, in the words of the operator.

    "absent" and "unusable" are different vendor faults with different fixes,
    and a single message for both is what makes an operator investigate the
    wrong one. The VALUE is not echoed — only its type — because the body is
    unvalidated vendor output and a log line is not the place to find out what
    it can contain.
    """
    if raw_uuid is None:
        return "the response carries no guildId"
    return (
        f"the response carries a guildId that is not a uuid "
        f"({type(raw_uuid).__name__})"
    )


async def fetch_guild_snapshot(api_key: str) -> GuildSnapshot:
    """Read `/api/v1/guild` with `api_key` and classify the result.

    Never raises for an expected failure — transport errors, 5xx, dead-key
    statuses AND a 200 whose body will not decode or will not walk all come
    back as a classified snapshot, because the caller's job is to distinguish
    them and an exception erases the distinction. The caller here is the
    hourly `discord.ext.tasks.Loop`, which STOPS on an unhandled exception:
    anything escaping this function ends ingestion for every server on the bot
    until the process is restarted, and announces nothing while it does.

    THREE guards, kept separate on purpose. The REQUEST is guarded for
    transport failure (UNREACHABLE), the DECODE and the WALK for body failure
    (UNVERIFIABLE). Widening the transport `except` to cover the body would
    make a Tacticus OUTAGE and a Tacticus SCHEMA CHANGE indistinguishable to
    whoever is paged — one is waited out and the other is worked on — and
    DDD-6 lists that collapse as a documented failure mode.
    """
    # Imported here, not at module scope, so importing this module costs
    # `dataclasses` and `enum` only. `domain_types.py` and the policy layer
    # import it for the vocabulary alone and must not pay for httpx.
    import httpx

    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.get(
                TACTICUS_GUILD_URL,
                headers={"accept": "application/json", "X-API-KEY": api_key},
            )
    except httpx.HTTPError as transport_error:
        # Timeout, DNS failure, connection refused, protocol error. UNREACHABLE
        # means "no state change, retry next cycle" — collapsing it into
        # MISMATCH would quarantine every guild during a Tacticus outage
        # (DDD-6).
        return GuildSnapshot(
            outcome=ProbeOutcome.UNREACHABLE,
            error=f"{type(transport_error).__name__}: {transport_error}",
        )

    if response.status_code in DEAD_KEY_STATUSES:
        # A revoked key returns no data, so there is nothing to contaminate:
        # report it, never quarantine on it.
        return GuildSnapshot(
            outcome=ProbeOutcome.DEAD,
            status=response.status_code,
            error=f"the key was refused with HTTP {response.status_code}",
        )

    if response.status_code != 200:
        # 5xx and everything else unexpected. Deliberately UNREACHABLE rather
        # than a narrower 5xx test: an unrecognised status is a reason to
        # retry, never a reason to act on a guild's binding.
        return GuildSnapshot(
            outcome=ProbeOutcome.UNREACHABLE,
            status=response.status_code,
            error=f"the guild service answered HTTP {response.status_code}",
        )

    try:
        payload = response.json()
    except ValueError as decode_error:
        # An nginx/CDN error page served as 200, a zero-length body, a body
        # cut mid-serialise, or bytes that are not the encoding they claim.
        # `json.JSONDecodeError` and `UnicodeDecodeError` are both `ValueError`
        # subclasses, so the taxonomy is named without importing `json` and
        # this module stays import-light. Neither the exception's message nor
        # the body is echoed — only the type name (KPI-6): the body is
        # unvalidated bytes from the connection the key was sent over.
        return _unverifiable_body(
            f"the response body is not JSON ({type(decode_error).__name__})"
        )

    return parse_guild_snapshot(payload)
