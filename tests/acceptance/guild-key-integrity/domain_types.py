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


class KeyConsumptionSite(Enum):
    """The seven places a guild `api_key` is read (ADR-008 D3 / DDD-3).

    AC-004.6 is parametrized over this enum. A guard on six of seven is the
    silent contamination path the chokepoint exists to close, so the enum is
    the test's definition of "all of them" — adding an eighth site without
    adding it here is the mistake this type is shaped to make visible.
    """

    AUTO_UPDATE_SEASON = "tasks_cog.auto_update season discovery"
    AUTO_UPDATE_RAID = "tasks_cog.auto_update raid fetch"
    AUTO_UPDATE_ROSTER = "tasks_cog.auto_update roster validation"
    UPDATE_LEADERBOARD = "update_cog.update_leaderboard"
    UPDATE_ALL = "update_cog.update_all"
    PLAYER_SERVICE_REFRESH = "player_service.refresh_guild"
    PLAYER_SERVICE_STALE = "player_service.validate_if_stale"


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
