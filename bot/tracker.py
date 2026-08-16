from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

TRACKED_RARITIES = {"Legendary", "Mythic"}
TOP_N = 5


# ---------------------------------------------------------------------------
# Ingest reporting — RED scaffold (created by DISTILL, feature
# `dynamic-tier-registry`). DDD-7: `process_api_response` returns an
# `IngestReport` instead of None, so the caller can count what was thrown away.
#
# __SCAFFOLD__: the SHAPE below is real and the tests are written against it;
# nothing populates it yet. `process_api_response` still returns None until
# DELIVER. Additive — no existing caller reads a return value, so this breaks
# nothing while it waits.
#
# DESIGN Open Question 2 left the field NAMES to DISTILL; they are pinned here.
# The one field DEVOPS fixed rather than left open is `tier_keys_written`:
# TK-2 measures capture latency as `MIN(completed_on)` against the first cycle
# record carrying the key, and no wording of an AC recovers that after the
# fact. See devops/upstream-changes.md item 3.
# ---------------------------------------------------------------------------

__INGEST_REPORT_SCAFFOLD__ = True


class SkipReason(Enum):
    """Why one entry was not written.

    Three reasons, NEVER collapsed into one total (ADR-009 D2). They share an
    outcome — nothing is stored — and differ in the only thing TK-5 measures.
    A single counter reading "7 skipped" is a number nobody can act on, which
    is the defect this feature was opened to fix.

    `UNTRACKED_RARITY` is expected to be non-zero forever: the allow-list is
    closed on purpose, so every Epic hit the API returns is a deliberate and
    correct discard. It is watched for NOVELTY (a rarity string nobody has
    seen), not for volume — do not write an assertion against its count.
    """

    UNTRACKED_RARITY = "untracked_rarity"
    MALFORMED_SET = "malformed_set"
    UNPARSEABLE = "unparseable"


@dataclass
class IngestReport:
    """What one call to `process_api_response` saw, stored and discarded.

    Folded into `_CycleReport` by the caller and rendered onto the
    update-channel post. The counters live here rather than in the cog because
    `bot/tracker.py` is where the decision is made, and a count produced
    anywhere other than the place that decides is a count that can drift from
    it.
    """

    entries_total: int = 0
    entries_written: int = 0
    skip_counts: dict[SkipReason, int] = field(default_factory=dict)
    unrecognised_rarities: set[str] = field(default_factory=set)
    tier_keys_written: set[str] = field(default_factory=set)

    @property
    def entries_skipped(self) -> int:
        raise AssertionError("Not yet implemented — RED scaffold (IngestReport)")

    def counts_by_name(self) -> dict[str, int]:
        """`{"untracked_rarity": 7, "malformed_set": 0, "unparseable": 0}`.

        ALL THREE KEYS, ALWAYS, even at zero. An absent key is
        indistinguishable from an unimplemented counter — and the whole feature
        exists because something that left no trace was assumed not to be
        happening. It also makes TK-5's equality checkable without a schema
        lookup. See `docs/product/kpi-contracts.yaml`.
        """
        raise AssertionError("Not yet implemented — RED scaffold (IngestReport)")


def get_tier_key(entry: dict) -> str | None:
    rarity = entry.get("rarity")
    if rarity not in TRACKED_RARITIES:
        return None
    if rarity == "Mythic":
        try:
            tier = int(entry.get("set"))
            if tier == 0:
                return "Mythic"
            if tier == 1:
                return "Mythic_1"
        except (TypeError, ValueError):
            pass
        return None
    try:
        tier = int(entry.get("set"))
        if 0 <= tier <= 4:
            return f"Legendary_{tier}"
    except (TypeError, ValueError):
        pass
    return None


def get_roster_key(entry: dict) -> tuple:
    """Returns a hashable key representing a player + roster combination.
    Heroes are sorted so order doesn't matter. MoW is included."""
    user_id = entry.get("user_id", "")
    heroes = tuple(sorted(h.get("unitId", "") for h in entry.get("hero_details", [])))
    mow = entry.get("machine_of_war") or {}
    mow_id = mow.get("unitId", "") if mow else ""
    return (user_id, heroes, mow_id)


def try_insert(entries: list, new_entry: dict, check_roster: bool = False) -> bool:
    """Insert new_entry into entries if it qualifies.

    If check_roster is True (Battle hits):
      - Same player + same roster: only keep the higher damage hit.
      - Same player + different roster: allow as a separate entry.
    If check_roster is False (Bomb hits): original top-N logic, no deduplication.

    Retained for the JSON rollback impl (`bot.repository.JsonClusterRepository`
    imports it) and for the tiebreak contract pin (RC14 /
    `bot/tests/test_tracker_tiebreak.py`). The SQLite write path
    (`process_api_response`) no longer calls this — the SQL upsert enforces
    keep-max(damage) (RC15). Removed from `bot.tracker` once the JSON impl's
    `try_insert` import is retired (04-04 / later cleanup).
    """
    damage = new_entry["damage"]

    if check_roster:
        new_key = get_roster_key(new_entry)

        # Check if this exact player+roster is already in the list
        for i, existing in enumerate(entries):
            if get_roster_key(existing) == new_key:
                # Same player, same roster — only keep the higher damage
                if damage > existing["damage"]:
                    entries[i] = new_entry
                    entries.sort(key=lambda e: (-e["damage"], e.get("completed_on", "")))
                    return True
                else:
                    return False  # Lower damage with same roster — skip

        # Different roster (or new player) — insert if it qualifies for top N
        if len(entries) < TOP_N or damage > entries[-1]["damage"]:
            entries.append(new_entry)
            entries.sort(key=lambda e: (-e["damage"], e.get("completed_on", "")))
            del entries[TOP_N:]
            return True
        return False

    else:
        # Original logic for Bombs — no roster deduplication
        if len(entries) < TOP_N or damage > entries[-1]["damage"]:
            entries.append(new_entry)
            entries.sort(key=lambda e: (-e["damage"], e.get("completed_on", "")))
            del entries[TOP_N:]
            return True
        return False


def process_api_response(api_data: dict, season: int,
                          discord_server_id: int, guild_id: str) -> None:
    """Upsert Tacticus API entries into battle_hits / bomb_hits via the repo.

    Replaces the JSON season-file write path (ADR-006 D4 / ADR-007 / US-008).
    The `data_dir` parameter is gone — the SQL partition key
    `(season, discord_server_id, guild_id)` replaces it. Entries are filtered
    by tracked rarity (`get_tier_key`) and routed by `damageType` to the
    repo's `upsert_guild_hits` (one transaction per guild — ADR-006 D6).
    The in-memory `try_insert` dedup is retired from this path — the SQL
    upsert enforces keep-max(damage) (RC15). No `highest_*_season_*.json`
    file is written.
    """
    repo = _get_write_repo()
    battle_entries: list[dict] = []
    bomb_entries: list[dict] = []
    for entry in api_data.get("entries", []):
        # The repo upsert contract reads entry["tier_key"] (both
        # ClusterRepository impls); the JSON era used get_tier_key's return as
        # the outer dict key, so entries never carried the field. The SQL
        # upsert takes a flat list, so stamp it here. See conftest.py
        # make_tacticus_entry / make_entry for the documented contract.
        tier_key = get_tier_key(entry)
        if tier_key is None:
            continue
        # The repo upsert contract reads two normalized fields that raw
        # Tacticus API entries do not carry: `tier_key` (derived from
        # rarity + set) and `damage` (the API field is `damageDealt`). The
        # JSON era built new entry dicts with these normalized; the SQL
        # upsert takes the raw entry, so stamp them here. See conftest.py
        # make_tacticus_entry / make_entry for the documented contract.
        entry["tier_key"] = tier_key
        entry["damage"] = entry["damageDealt"]
        damage_type = entry.get("damageType")
        if damage_type == "Battle":
            battle_entries.append(entry)
        elif damage_type == "Bomb":
            bomb_entries.append(entry)
    # One transaction per guild (ADR-006 D6): battle + bomb upserts share a
    # single session_scope; a mid-guild failure rolls back that guild's whole
    # write batch. Cross-guild isolation comes from separate calls per
    # guild_id. The crash-injection assertion (AP7) lands in 04-05.
    repo.upsert_guild_hits(discord_server_id, guild_id, season, battle_entries, bomb_entries)


def _get_write_repo():
    """Resolve the write-side ClusterRepository from the current env.

    04-04: delegates to `bot.guilds.build_repo()` so the write path re-reads
    SCRAPCODE_REPO_BACKEND (and the missing-key/file safety net) at call
    time — the same factory the composition root uses. The hourly
    `auto_update` loop fires once per guild per hour, so the per-call
    construction cost is negligible; the benefit is that test fixtures
    (monkeypatch.setenv) and the operator's rolling-config changes are
    honored without a process restart. ADR-006 D9.
    """
    from bot.guilds import build_repo
    return build_repo()