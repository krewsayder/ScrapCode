"""The single source of raid-tier truth — RED scaffold (created by DISTILL).

ADR-009 / DDD-1. Third instance of the single-source chokepoint pattern after
`bot/permissions.py` (ADR-001) and `bot/guild_keys.py` (ADR-008). Owns four
rules and nothing else:

  * the PARSE rule    — a Tacticus entry to a stored tier key
  * the LABEL rule    — a stored tier key to a display label
  * the ORDERING rule — how tiers sort, everywhere they are listed
  * the OVERRIDE table — the escape hatch for a tier the game names irregularly

DDD-2 — THIS MODULE IMPORTS NOTHING BUT THE STANDARD LIBRARY, and specifically
not `discord`, not `config`, not `bot.guilds`, not `bot.repository*`, not
`bot.db`. Enforced by an `import-linter` contract (DEVOPS D10) in the same
shape as the one guarding `bot/obs.py`. The direction runs one way: `config.py`
imports THIS module to derive `TIER_CHOICES`, never the reverse. That is what
keeps the rule table testable without an event loop and keeps a parse rule out
of a config module.

DDD-4 — STORED KEYS ARE FROZEN, LABELS ARE DERIVED. Nothing here may change the
key any historical row was written under. `battle_hits.tier_key` and
`bomb_hits.tier_key` keep every value they hold; the whole feature happens on
the read path. Two things depend on that and would break silently otherwise:
`live_leaderboards.messages` is keyed by tier VALUE, and replay rows are keyed
by display LABEL (`replay_cog.py:208`) with rendering filtered on it
(`replay_cog.py:54`) — so a label that drifts drops historical replays out of
`/get_replay` while leaving them in the database.

__SCAFFOLD__: every callable below raises. DELIVER replaces the bodies one
acceptance test at a time; the signatures are the contract DISTILL wrote the
tests against.
"""
from __future__ import annotations

from dataclasses import dataclass

__SCAFFOLD__ = True

_RED = "Not yet implemented — RED scaffold (bot/tiers.py)"


# ---------------------------------------------------------------------------
# The rarity allow-list — CLOSED on purpose (ADR-009 D1 / DDD-3)
# ---------------------------------------------------------------------------

TRACKED_RARITIES: frozenset[str] = frozenset({"Legendary", "Mythic"})
"""Which rarities produce leaderboard rows.

Unbounding `set` fixes a bug: the game shipped a tier the parser could not
name, and the entries were discarded. Unbounding `rarity` would change what
the leaderboard MEANS — it is a product decision, and an ingest parser does not
get to make it as a side effect of a fix. AC-001.7 pins this closed.
"""

MAX_CHOICES = 25
"""Discord's hard cap on `app_commands.Choice` entries per option.

Named here rather than in `config.py` because the registry is what can now
grow past it. A sync that exceeds the cap is rejected BY DISCORD, leaving the
old choice list live in front of new code with nothing anywhere saying so —
hence AC-003.6's loud startup refusal rather than a warning.
"""


# ---------------------------------------------------------------------------
# The tier record
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Tier:
    """One raid tier: its stored key and its display label.

    DDD-5. The attribute names are `value` and `name` — deliberately the two
    `app_commands.Choice[str]` exposes — because 26 raid-tier reads across five
    modules (`tasks_cog` 11, `view_cog` 6, `admin_cog` 6, `embeds` 5,
    `replay_cog` 1) read exactly those. Structural compatibility is what makes
    Slice 04 tractable: every one of those sites keeps working unmodified, and
    AC-006.3 asserts that none of the five modules is touched.

    `value` is the STORED key (`"Mythic_1"`). `name` is the DISPLAY label
    (`"Mythic 2"`). They are off by one and always have been; freezing that
    skew rather than fixing it is ADR-009 D4.
    """

    value: str
    name: str


# ---------------------------------------------------------------------------
# The override table
# ---------------------------------------------------------------------------

LABEL_OVERRIDES: dict[str, str] = {}
"""Stored key to display label, for tiers the derivation rule gets wrong.

Empty today: every current key derives correctly. It exists so that a future
tier the game names irregularly ("Mythic Prime") is a one-line data edit rather
than a change to the shape of the rule (AC-004.5).

An override WINS over derivation. It does not affect ordering — DESIGN Open
Question 1 leaves order overrides undesigned until a tier actually needs one.
"""


def registered() -> tuple[Tier, ...]:
    """Every tier the registry knows, in display order.

    The source `config.TIER_CHOICES` is derived from (AC-004.2), and the left
    half of Slice 04's `registry ∪ observed` union (AC-006.6) — so a tier can be
    selected before its first hit lands.
    """
    raise AssertionError(_RED)


def parse(entry: dict) -> str | None:
    """A raw Tacticus entry to its stored tier key, or None if not tracked.

    The rule `tracker.get_tier_key` delegates to (AC-004.3). Any `set >= 0`
    within a tracked rarity produces a key; the `set <= 1` (Mythic) and
    `set <= 4` (Legendary) bounds that discarded Mythic 3 are gone, removed
    symmetrically so the same bug cannot recur one rarity over (AC-001.3).

    Returns None for an untracked rarity, a missing or non-integer `set`, and a
    negative `set`. Returning None is not the same as being silent — the caller
    counts it by reason (AC-002.1); this function only decides.
    """
    raise AssertionError(_RED)


def label(tier_key: str) -> str:
    """A stored tier key to its display label.

    `Legendary_0` to `Legendary 1`, `Mythic` to `Mythic 1`, `Mythic_1` to
    `Mythic 2`, `Mythic_2` to `Mythic 3` (AC-003.6). Overrides win.

    NEVER raises and never returns an empty string. A key with no derivable
    label renders as ITSELF (AC-003.7) — the read-path form of ADR-009 D2: a
    row is never hidden because its name could not be worked out.
    """
    raise AssertionError(_RED)


def order_key(tier_key: str) -> tuple[int, int]:
    """Sort key: Legendary before Mythic, numeric suffix ascending (AC-004.6).

    Read by `replay_cog.tier_order`, the live-board message order and the
    picker order, so all three stay in step. Unregistered keys sort last rather
    than raising — they are still rows somebody ran.
    """
    raise AssertionError(_RED)


def tier_for(tier_key: str) -> Tier:
    """The `Tier` for any stored key, registered or not.

    Total by construction: an unregistered key comes back as
    `Tier(value=key, name=key)` rather than None, which is what lets the
    renderers stay unmodified (AC-006.3) while still displaying a row nobody
    has named yet.
    """
    raise AssertionError(_RED)


def resolve(submitted: str) -> Tier | None:
    """A submitted picker value to a `Tier`, or None when nothing matches.

    Slice 04. Autocomplete hands back a plain `str` and the user may type
    something else entirely, so this accepts either a display label or a stored
    key. None means "no such tier" and MUST produce an explicit message naming
    the valid tiers (AC-006.4) — an empty board is indistinguishable from a
    tier with no hits, which is the ambiguity the feature exists to remove.
    """
    raise AssertionError(_RED)
