"""Domain vocabulary for the `dynamic-tier-registry` acceptance suite.

NAMED `tier_types`, NOT `domain_types`. Every acceptance suite in this project
sits in its own directory with no `__init__.py`, so pytest's rootdir-prepend
import mode puts each directory on `sys.path` and a bare module name is shared
across all of them: `sys.modules["domain_types"]` holds whichever suite was
collected FIRST. This file was originally `domain_types.py`, and because
`dynamic-tier-registry` sorts before `guild-key-integrity`, a combined
`pytest tests/unit tests/acceptance` run resolved THAT suite's
`from domain_types import DARK_MECHANICUM` to this module and failed its entire
collection with an ImportError.

That is the same hazard `guild-key-integrity`'s conftest already documents for
`sys.modules["conftest"]`, arriving through a second door. It is invisible in a
standalone run and only appears in the combined invocation — which is the one
an operator and any future CI actually type.

For the same reason this module also owns the suite's CONSTANTS, and the test
modules import them from here rather than from `conftest`. `SEASON` is 107 here
and 106 in `guild-key-integrity`; a test module doing `from conftest import
SEASON` in a combined run binds whichever conftest won, silently, and asserts
against the wrong season.

Mandate-12 criterion (1): every domain noun that appears in the `.feature`
files has a typed representation reachable from here, so the scenario text, the
executable specs and the Tier B state machine name the same things the same
way.

`Tier`, `SkipReason` and `TRACKED_RARITIES` are RE-EXPORTED from production,
never re-declared. A test-side copy of an enum compares unequal to the
production one under `is`, and the copy that drifts is always the one nobody
runs in production — so there is exactly one definition and the suite imports
it. Same discipline as `guild-key-integrity/domain_types.py`.

Everything defined locally below is genuinely test-only: environment names, the
frozen pre-feature key set, and the tier-reader inventory AC-006.3 is
parametrized over.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

# SSOT: defined in production, reused here (Mandate-12 criterion 2).
from bot.tiers import (  # noqa: F401
    LABEL_OVERRIDES,
    MAX_CHOICES,
    TRACKED_RARITIES,
    Tier,
)
from bot.tracker import IngestReport, SkipReason  # noqa: F401

# ---------------------------------------------------------------------------
# Suite constants. Here rather than in conftest — see the module docstring.
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]
ENVIRONMENTS_YAML = (
    REPO_ROOT / "docs" / "feature" / "dynamic-tier-registry" / "environments.yaml"
)

PROD_SERVER_ID = 1458181638453203099
GUILD_WB = "word_bearers"
GUILD_DM = "dark_mechanicum"
SEASON = 107


def environment_names_from_devops_artifact() -> list[str]:
    """Parse `target_environments[].name` out of environments.yaml.

    Deliberately a regex and not PyYAML: `requirements.txt` has no YAML parser,
    and this suite must not add a runtime dependency to read eleven strings. If
    the file's shape changes enough to break this regex, the traceability test
    fails loudly, which is the correct outcome. Same choice, same reasoning as
    `guild-key-integrity`.
    """
    text = ENVIRONMENTS_YAML.read_text("utf-8")
    body = text.split("target_environments:", 1)[1].split("\ncoexistence_matrix:", 1)[0]
    return re.findall(r"^\s*-\s*name:\s*(\S+)\s*$", body, flags=re.MULTILINE)


class Environment(Enum):
    """The eleven target environments from `environments.yaml` (DEVOPS wave).

    `test_environment_matrix.py::test_environment_names_match_devops_artifact`
    asserts this enum and that file cannot drift apart.
    """

    KNOWN_TIERS_ONLY = "known-tiers-only"
    MYTHIC_3_LIVE = "mythic-3-live"
    TIER_BEYOND_THE_REGISTRY = "tier-beyond-the-registry"
    UNTRACKED_RARITY = "untracked-rarity"
    MALFORMED_SET = "malformed-set"
    LIVE_BOARD_INCOMPLETE = "live-board-incomplete"
    LIVE_BOARD_ROLLOVER_RACE = "live-board-rollover-race"
    DISCORD_SEND_REFUSED = "discord-send-refused"
    HISTORICAL_REPLAY_LABELS = "historical-replay-labels"
    PICKER_AT_THE_CAP = "picker-at-the-cap"
    JSON_BACKEND_ROLLBACK = "json-backend-rollback"


# ---------------------------------------------------------------------------
# The frozen key set — ADR-009 D4
# ---------------------------------------------------------------------------

# Every tier key the parser could produce BEFORE this feature, in the order
# `config.TIER_CHOICES` listed them, paired with the label that list gave them.
#
# This is the regression pin behind AC-001.4 and AC-003.2, and it is written as
# LITERALS on purpose. Deriving it from the registry under test would make both
# assertions circular — a derivation that is wrong in the same way twice would
# agree with itself and pass. The values are copied from `config.py:22-30` as
# it stood at the start of this feature.
#
# Note the skew: key `Mythic_1` displays as "Mythic 2", key `Legendary_0` as
# "Legendary 1". It is off by one in both rarities and it is FROZEN. Fixing it
# would rewrite `live_leaderboards.messages` keys and orphan every historical
# replay row, which is keyed by the LABEL.
PRE_FEATURE_TIERS: tuple[tuple[str, str], ...] = (
    ("Legendary_0", "Legendary 1"),
    ("Legendary_1", "Legendary 2"),
    ("Legendary_2", "Legendary 3"),
    ("Legendary_3", "Legendary 4"),
    ("Legendary_4", "Legendary 5"),
    ("Mythic", "Mythic 1"),
    ("Mythic_1", "Mythic 2"),
)

# The tier this feature exists for. `rarity="Mythic", set=2` — the payload
# shape the operator confirmed on 2026-08-15, not an inferred one.
MYTHIC_3_KEY = "Mythic_2"
MYTHIC_3_LABEL = "Mythic 3"

# The tier AFTER the one that broke things. Used for `tier-beyond-the-registry`
# and for AC-006.1, where a key must be present in stored data and absent from
# the registry.
MYTHIC_4_KEY = "Mythic_3"
MYTHIC_4_LABEL = "Mythic 4"


class UntrackedRarity(Enum):
    """Rarities outside the allow-list, parametrized into `untracked-rarity`.

    `EPIC` is the routine, high-volume case and `DIVINE` the novel one, and the
    distinction is the whole of AC-007.3: a report that emits per entry lets
    routine Epic volume bury a rarity nobody has seen before.
    """

    EPIC = "Epic"
    RARE = "Rare"
    UNCOMMON = "Uncommon"
    COMMON = "Common"
    DIVINE = "Divine"


class MalformedSet(Enum):
    """Every way the `set` field arrives unusable.

    Split from `UntrackedRarity` deliberately. They share an outcome — nothing
    is written — and differ in the stated reason, which is the only thing TK-5
    measures. An enum that merged them would pass against an implementation
    with one counter and no reasons.

    `NEGATIVE` is the boundary case: `set = -1` parses as an integer perfectly
    well, so a parser that only removed the upper bound produces the key
    `"Mythic_-1"` and writes a row nobody can ever select.
    """

    ABSENT = "absent"
    NULL = "null"
    NON_NUMERIC = "non_numeric"
    NEGATIVE = "negative"

    @property
    def expected_reason(self) -> SkipReason:
        """Which counter this case increments.

        ABSENT and NULL are the field not being there; NON_NUMERIC and NEGATIVE
        are the field being there and wrong. The split is not cosmetic — a
        vendor that drops `set` entirely is a schema change, and a vendor that
        sends `"two"` is a serialisation change, and an operator wants to know
        which one is happening.
        """
        if self in (MalformedSet.ABSENT, MalformedSet.NULL):
            return SkipReason.UNPARSEABLE
        return SkipReason.MALFORMED_SET


class LiveConfigShape(Enum):
    """The two key shapes a live-leaderboard config row can take.

    AC-005.7 parametrizes over both. Fixing reconciliation for one shape and
    missing the other is the realistic failure — they are handled by two
    branches of the same loop (`tasks_cog._refresh_live_leaderboards`).
    """

    PER_GUILD = "guild:{id}"
    CLUSTER = "cluster"


class SendFailure(Enum):
    """How a reconciliation message send fails.

    Under rate limiting the second is the NORMAL case, not an edge case.

    `SENT_THEN_PERSIST_FAILED` is the honest one and the reason a mocked
    channel cannot substitute for the production run: the failure shape is a
    real Discord send SUCCEEDING after local state has already concluded it
    failed. A partial write of the `messages` map that omits an already-sent
    message is what produces the public duplicate.
    """

    FORBIDDEN = "forbidden"
    RATE_LIMITED = "rate_limited"
    SENT_THEN_PERSIST_FAILED = "sent_then_persist_failed"


# ---------------------------------------------------------------------------
# The tier-reader inventory — AC-006.3
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReaderModule:
    """A production module that reads `.name` / `.value` off a raid tier."""

    path: str
    raid_tier_reads: int
    note: str


class TierReader(Enum):
    """Every module reading a RAID tier's `.name` or `.value` (DESIGN SPIKE).

    AC-006.3 is parametrized over this enum, and the count is why. DISCUSS
    framed the dependency as "the three `embeds.py` call sites"; the SPIKE run
    during DESIGN returned 26 reads across five modules — wrong by an order of
    magnitude.

    The design is unaffected and arguably vindicated: `Tier` is structurally
    compatible with `app_commands.Choice[str]`, so all 26 sites keep working
    unmodified. With three sites a plain refactor would have been viable; with
    26 the structural compatibility is the only tractable option.

    What DOES change is the verification surface. AC-006.3 must assert that
    FIVE modules are untouched by the Slice 04 diff, not one. See
    `design/upstream-changes.md` §3 and `devops/upstream-changes.md`.
    """

    TASKS_COG = ReaderModule("bot/cogs/tasks_cog.py", 11, "live boards + rollover")
    VIEW_COG = ReaderModule("bot/cogs/view_cog.py", 6, "the three view commands")
    ADMIN_COG = ReaderModule(
        "bot/cogs/admin_cog.py", 6,
        "6 of 8 `tier.` reads — the other 2 are PERMISSION tiers, exempt",
    )
    EMBEDS = ReaderModule("bot/embeds.py", 5, "the three renderers")
    REPLAY_COG = ReaderModule("bot/cogs/replay_cog.py", 1, "tier_order")

    @property
    def path(self) -> str:
        return self.value.path


# ---------------------------------------------------------------------------
# The permission-tier exemption — ADR-009 D10
# ---------------------------------------------------------------------------

# `tier` names two unrelated concepts in this codebase. These paths read
# `tier.value` as a PERMISSION tier (`member` / `officer` / `admin`), not a raid
# tier, and the tier-literal architecture rule must exempt them BY NAME.
#
# A rule written without the exemption either fires on correct code — after
# which the operator learns to ignore it, which is worse than having no rule —
# or gets loosened until it stops catching what it was written for. A global
# `tier.value` refactor would silently break `/scrapcode_help` and
# `/config_role_tier`, and both would still type-check.
PERMISSION_TIER_PATHS: dict[str, str] = {
    "bot/cogs/fun_cog.py":
        "4 sites — /scrapcode_help renders the permission tier of each command",
    "bot/cogs/admin_cog.py::config_role_tier":
        "admin_cog.py:734,736 — /config_role_tier maps a Discord role to a "
        "permission tier",
}
