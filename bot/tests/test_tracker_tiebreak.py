"""Regression tests for the leaderboard tie-break bug.

Bug: when two players tie on damage (e.g. both 100% of a boss's HP — the max,
so neither can exceed the other), the sort used only `damage` with
`reverse=True` and no tiebreaker. Python's stable sort then fell back to
insertion order, which follows API/guild-iteration order rather than actual
hit time — so a later hitter could take the first hitter's place.

Fix: sort by `(-damage, completed_on)` so equal damage breaks by earliest
hit first. These tests pin that behavior.
"""
from bot.tracker import try_insert, TOP_N


def _entry(user_id, damage, completed_on, *, mow=None):
    """Build a minimal entry matching the shape try_insert / get_roster_key expect."""
    return {
        "damage": damage,
        "user_id": user_id,
        "completed_on": completed_on,
        "hero_details": [],
        "machine_of_war": mow,
    }


# ==========================================
# The reported bug: tie at max damage
# ==========================================

def test_tie_keeps_earliest_hit_first_battle():
    """Two Battle hits, both 100% (max). The earlier hit must keep #1 even
    when the later hit is inserted first (API returns it first)."""
    entries = []
    later   = _entry("B", 100, "2026-07-18T11:00:00Z")
    earlier = _entry("A", 100, "2026-07-18T10:00:00Z")

    assert try_insert(entries, later,   check_roster=True) is True
    assert try_insert(entries, earlier, check_roster=True) is True

    assert entries[0]["user_id"] == "A"  # earliest hit holds #1
    assert entries[1]["user_id"] == "B"

def test_tie_keeps_earliest_hit_first_bomb():
    """Same regression on the Bomb path (check_roster=False)."""
    entries = []
    later   = _entry("B", 100, "2026-07-18T11:00:00Z")
    earlier = _entry("A", 100, "2026-07-18T10:00:00Z")

    assert try_insert(entries, later,   check_roster=False) is True
    assert try_insert(entries, earlier, check_roster=False) is True

    assert entries[0]["user_id"] == "A"
    assert entries[1]["user_id"] == "B"

def test_tie_order_follows_completed_on_not_insertion():
    """Insert three equal-damage hits in reverse chronological order; the
    final order must be chronological (earliest first), not insertion order."""
    entries = []
    for uid, ts in [("C", "2026-07-18T12:00:00Z"),
                   ("A", "2026-07-18T10:00:00Z"),
                   ("B", "2026-07-18T11:00:00Z")]:
        assert try_insert(entries, _entry(uid, 100, ts), check_roster=True) is True

    assert [e["user_id"] for e in entries] == ["A", "B", "C"]


# ==========================================
# Primary sort still works (regression guard)
# ==========================================

def test_higher_damage_wins_over_earlier_lower_hit():
    """A higher-damage hit ranks above a lower one even if the lower hit
    happened earlier and was inserted first."""
    entries = []
    assert try_insert(entries, _entry("A", 80, "2026-07-18T09:00:00Z"), check_roster=True) is True
    assert try_insert(entries, _entry("B", 100, "2026-07-18T11:00:00Z"), check_roster=True) is True

    assert entries[0]["user_id"] == "B"  # 100 > 80 regardless of time

def test_same_player_same_roster_equal_damage_keeps_first():
    """Same player + roster, equal damage: the existing (first) entry is kept,
    the new one is skipped (returns False). This was already correct and must
    stay correct after the tiebreak change."""
    entries = []
    first  = _entry("A", 100, "2026-07-18T10:00:00Z")
    second = _entry("A", 100, "2026-07-18T11:00:00Z")  # same user_id, same empty roster

    assert try_insert(entries, first,  check_roster=True) is True
    assert try_insert(entries, second, check_roster=True) is False  # equal damage -> skip

    assert len(entries) == 1
    assert entries[0]["completed_on"] == "2026-07-18T10:00:00Z"  # first hit retained