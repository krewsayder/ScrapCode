"""Domain vocabulary for the `guild-key-integrity` acceptance suite.

Mandate-12 criterion (1): every domain noun that appears in the `.feature`
files has a typed representation reachable from here, so the scenario text,
the executable specs and the Tier B state machine all name the same things
the same way.

`ProbeOutcome`, `KeyStatus` and `GuildIdentity` are RE-EXPORTED from
production, not re-declared. A test-side copy of an enum compares unequal to
the production one under `is`, and the copy that drifts is always the one
nobody is running in production — so there is exactly one definition and the
suite imports it.

Everything defined locally below is genuinely test-only: environment names,
the call-site inventory, and the two identities from the incident.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# SSOT: defined in production, reused here (Mandate-12 criterion 2).
from bot.services.tacticus.guild_client import (  # noqa: F401
    DEAD_KEY_STATUSES,
    GuildIdentity,
    KeyStatus,
    ProbeOutcome,
)


class Environment(Enum):
    """The eight target environments from `environments.yaml` (DEVOPS wave).

    `test_environment_matrix.py::test_environment_names_match_devops_artifact`
    asserts this enum and that file cannot drift apart.
    """

    CLEAN = "clean"
    BOUND_MATCHING = "bound-matching"
    BOUND_DRIFTED = "bound-drifted"
    UNVERIFIABLE = "unverifiable"
    TACTICUS_UNREACHABLE = "tacticus-unreachable"
    DEAD_KEY = "dead-key"
    MIXED_CLUSTER = "mixed-cluster"
    JSON_BACKEND_ROLLBACK = "json-backend-rollback"


@dataclass(frozen=True)
class SiteCoordinates:
    """Where a key-consumption site actually lives in production.

    `reader` is the enclosing function whose body calls the chokepoint —
    that is the coordinate the AST scan can verify. `driving_port` is what a
    human invokes to reach it, and is the grain AC-004.6 parametrizes over,
    because "the site refuses" is only observable by driving the port.

    Two members may share one `reader` (`/update_leaderboard` and
    `/update_all` both enter through `update_cog._verified_key`) — the
    inventory test compares READER sets, so that is not a duplicate.
    """

    module: str
    reader: str
    driving_port: str


class KeyConsumptionSite(Enum):
    """Every production path that reaches a STORED guild `api_key`
    (ADR-008 D3 / DDD-3).

    AC-004.6 is parametrized over this enum. A guard on all-but-one is the
    silent contamination path the chokepoint exists to close, so the enum is
    the test's definition of "all of them" — and, unlike before, that claim
    is now EXECUTED rather than asserted in prose:
    `test_architecture_chokepoint.py::test_the_key_consumption_inventory_matches_production`
    AST-scans `bot/` for every call to `active_key` / `verify_and_resolve` /
    `install_guild_key` and fails when the set of enclosing functions differs
    from the `reader` coordinates declared here. Adding a site without adding
    it here now reds the build; so does deleting one.

    CORRECTED 2026-08-02 (adversarial re-review). The previous version of
    this enum named seven sites, three of which were wrong:

      * `PLAYER_SERVICE_REFRESH` / `PLAYER_SERVICE_STALE` are NOT key
        consumption sites. DDD-2 moved the fetch out; both methods are HANDED
        a `GuildSnapshot` and never see a key
        (`player_service.py:132,160` — the parameter is `snapshot`). The
        two branches that claimed to drive them called `active_key` in a
        vacuum and raised, so their `assert call_count == 0` was vacuously
        true: no production entry point ran, so of course nothing was
        fetched. Their real key-consumption points are the CALLERS that
        produce the snapshot — `admin_cog.register_guild` (now
        `REGISTER_GUILD`) and `tasks_cog._validate_roster` (covered by
        `AUTO_UPDATE_ROSTER`) — so removing them strictly increases coverage.
      * The enum OMITTED `admin_cog.register_guild`,
        `admin_cog.set_live_leaderboard` and
        `admin_cog.set_live_cluster_leaderboard` — the three sites where the
        confirmed slice-04/05 defects live. The one AC that was supposed to
        prove "all sites are blocked" was parametrized over a set that
        excluded every site that is not.

    The count is deliberately no longer stated as a number here. "Seven" was
    itself inherited from ADR-008 D3 and was never true of this repository;
    a docstring that pins a count invites the next reader to make the set fit
    the number. The invariant is the set equality, not its cardinality.
    """

    AUTO_UPDATE_SEASON = SiteCoordinates(
        "bot/cogs/tasks_cog.py", "_current_season",
        "auto_update (hourly loop) — season discovery",
    )
    AUTO_UPDATE_RAID = SiteCoordinates(
        "bot/cogs/tasks_cog.py", "_update_one_guild",
        "auto_update (hourly loop) — raid ingest",
    )
    AUTO_UPDATE_ROSTER = SiteCoordinates(
        "bot/cogs/tasks_cog.py", "_update_one_guild",
        "auto_update (hourly loop) — roster validation",
    )
    UPDATE_LEADERBOARD = SiteCoordinates(
        "bot/cogs/update_cog.py", "_verified_key", "/update_leaderboard",
    )
    UPDATE_ALL = SiteCoordinates(
        "bot/cogs/update_cog.py", "_verified_key", "/update_all",
    )
    REGISTER_GUILD = SiteCoordinates(
        "bot/cogs/admin_cog.py", "register_guild", "/register_guild",
    )
    SET_LIVE_LEADERBOARD = SiteCoordinates(
        "bot/cogs/admin_cog.py", "set_live_leaderboard", "/set_live_leaderboard",
    )
    SET_LIVE_CLUSTER_LEADERBOARD = SiteCoordinates(
        "bot/cogs/admin_cog.py", "set_live_cluster_leaderboard",
        "/set_live_cluster_leaderboard",
    )

    @property
    def module(self) -> str:
        return self.value.module

    @property
    def reader(self) -> str:
        return self.value.reader

    @property
    def driving_port(self) -> str:
        return self.value.driving_port


# The chokepoint call that must NOT refuse a quarantined guild.
#
# `/update_guild_key` probes a SUBMITTED key, never the stored one, and is the
# only exit from quarantine (DISCUSS D3 — quarantine is never a trap). It is
# declared here rather than omitted so the inventory scan can account for
# every `bot/guild_keys.py` entry-point call in production: an unaccounted
# call site is a failure, and "this one is deliberate" has to be written down
# somewhere a reviewer reads.
RECOVERY_ENTRY_POINTS: dict[str, str] = {
    "bot/cogs/admin_cog.py::update_guild_key":
        "the recovery path — probes the SUBMITTED key and must work WHILE "
        "quarantined (DISCUSS D3). Refusing here would make quarantine a trap.",
}


class VendorBody(Enum):
    """Every SHAPE a Tacticus 200 response body can arrive in.

    The vendor's `guildId` is undocumented (see the header of
    `fixtures/guild_response_recorded.json`), so the payload is unversioned
    output from a service that has already changed shape once inside this
    feature's lifetime. "Well-formed dict" is an assumption, not a contract,
    and every member below is a body a real HTTP 200 can carry.

    Added 2026-08-02. The suite previously had no way to express any member
    of this enum except `WELL_FORMED`: the payload builder could only render
    `{"guild": {...}}`. Every slice-04 defect was therefore unreachable from
    the suite by construction — not missed, but structurally unwritable.
    `NOT_JSON_HTML` is the most severe of them: it is the nginx 502 page that
    stops hourly ingestion for every server until the process restarts.
    """

    WELL_FORMED = "well_formed"
    NOT_JSON_HTML = "not_json_html"          # an nginx/CDN error page
    EMPTY = "empty"                          # 200 with a zero-length body
    TRUNCATED_JSON = "truncated_json"        # connection cut mid-serialise
    JSON_NULL = "json_null"                  # literal `null`
    JSON_LIST = "json_list"                  # a bare array
    JSON_STRING = "json_string"              # a bare quoted string
    JSON_BOOL = "json_bool"                  # a bare `true`
    GUILD_NOT_A_DICT = "guild_not_a_dict"    # {"guild": "unavailable"} — truthy
    GUILD_NULL = "guild_null"                # {"guild": null} — falsy
    MEMBER_WITHOUT_USER_ID = "member_without_user_id"


class GuildIdVariant(Enum):
    """Every VALUE the `guildId` field can carry on a well-formed body.

    Split from `VendorBody` because these are orthogonal: any variant here
    can arrive inside any dict-shaped body. The first six are the SAME guild
    written differently — a vendor that re-cases a uuid, or a proxy that
    prepends a BOM, must not read as drift (KPI-4, DISCUSS D3). The rest are
    values `GuildIdentity` cannot be built from and must classify
    UNVERIFIABLE rather than raise or compare.

    `MISMATCHED_UUID` is the control: it is the ONLY member that may
    quarantine, and AC-007.8 is the regression guard proving canonicalisation
    did not make two different guilds compare equal.
    """

    CANONICAL = "canonical"
    UPPERCASE = "uppercase"
    MIXED_CASE = "mixed_case"
    SURROUNDING_WHITESPACE = "surrounding_whitespace"
    BOM_PREFIXED = "bom_prefixed"
    TRAILING_NEWLINE = "trailing_newline"
    WHITESPACE_ONLY = "whitespace_only"
    EMPTY_STRING = "empty_string"
    JSON_NUMBER = "json_number"
    JSON_BOOL = "json_bool"
    JSON_NULL = "json_null"
    NOT_A_UUID = "not_a_uuid"
    ABSENT = "absent"
    MISMATCHED_UUID = "mismatched_uuid"

    @property
    def names_the_bound_guild(self) -> bool:
        """True when this value IS the bound guild, however it is written.

        The six members that answer True are the KPI-4 false-positive set:
        each must classify MATCH and leave `key_status` byte-identical. Any
        of them reading as MISMATCH quarantines a healthy guild on a vendor
        formatting change, which is the incident this feature exists to
        prevent, arriving from the opposite direction.
        """
        return self in {
            GuildIdVariant.CANONICAL,
            GuildIdVariant.UPPERCASE,
            GuildIdVariant.MIXED_CASE,
            GuildIdVariant.SURROUNDING_WHITESPACE,
            GuildIdVariant.BOM_PREFIXED,
            GuildIdVariant.TRAILING_NEWLINE,
        }


class TransportFailure(Enum):
    """Ways the Tacticus call fails without producing an identity.

    Parametrized into the `tacticus-unreachable` environment. All four must
    classify UNREACHABLE; none may classify MISMATCH.
    """

    TIMEOUT = "timeout"
    CONNECT_ERROR = "connect_error"
    SERVER_ERROR_500 = "http_500"
    SERVER_ERROR_503 = "http_503"


class DeadKeyStatus(Enum):
    """HTTP statuses Tacticus returns for a revoked key.

    Built FROM the production constant rather than beside it, so a change to
    the taxonomy cannot leave the suite testing the old one. The Reuse
    Analysis classifies `_DEAD_KEY_STATUSES` EXTEND (reuse verbatim), and
    this is that reuse.
    """

    UNAUTHORIZED = 401
    FORBIDDEN = 403


assert {s.value for s in DeadKeyStatus} == set(DEAD_KEY_STATUSES), (
    "the dead-key taxonomy changed in production but not in the suite"
)


# ---------------------------------------------------------------------------
# The two real identities from the 2026-07-28 incident.
#
# These are guild identifiers, not credentials: they are returned by the
# Tacticus API to anyone holding a key for the guild and grant nothing.
# They are hard-coded because the incident replay is only a replay if it
# uses the values that actually drifted.
# ---------------------------------------------------------------------------

WORD_BEARERS = GuildIdentity(
    uuid="b64bdba4-36ac-4229-bd29-4b7b6ce7f44f",
    tag="EUVQZ",
    name="【UNDV】Word Bearers",
)

DARK_MECHANICUM = GuildIdentity(
    uuid="d71d583f-c970-4493-936f-178c21ab844c",
    tag="PXGQW",
    name="【UNDV】Dark Mechanicum",
)

# A retag of Word Bearers: same uuid, different display fields. AC-002.5's
# input. If this ever reports MISMATCH the lock is on the wrong field.
WORD_BEARERS_RETAGGED = GuildIdentity(
    uuid=WORD_BEARERS.uuid,
    tag="WBRRS",
    name="【UNDV】Word Bearers Reborn",
)
