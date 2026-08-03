# Slice 05 — Close the write holes
#
# Scenario SSOT for `test_slice_05_close_the_write_holes.py`.
# Remediates US-003, KPI-2, KPI-5, DDD-3.
#
# Slice 03 claimed one chokepoint gated all key-consumption sites and shipped
# with three sites ungated behind an enum that named the wrong seven. The
# claim itself is what is under test here, which is why the first scenario is
# about the chokepoint's structure rather than about any one command.

Feature: A quarantined guild writes zero rows at every site
  As an officer whose roster is the record of who is in my guild
  I want a quarantined key to be refused wherever it is reached from
  So that no command can overwrite my members with another guild's

  @kpi @driving_port
  Scenario: The chokepoint refuses without being asked twice
    Given a quarantined guild
    When the ingestion path verifies and resolves that guild directly
    Then the guild is refused
    And no request is made to the guild service

  # RE-AUTHORED 2026-08-03 (upstream-issues UI-13). The single scenario that
  # stood here drove a quarantined binding with no guild row, a state reached
  # by the parity rollback — which AC-009.6 closes. Once closed, that Given
  # yields an UNBOUND guild and registration correctly adopts it, so the two
  # ACs could not both hold. AC-009.6 is a confirmed defect and stands; this
  # split follows the state into the two places it can still be found.
  #
  # The proposed AC also asked for "and no existing member is marked as
  # departed" in the same breath. That step is deliberately absent from the
  # orphan scenario: `players` CASCADEs from `guilds`, so every route to
  # "quarantined binding with no guild row" also empties the roster and there
  # is no member left to flip. It is asserted where it IS observable, in the
  # registration-sequence scenario (AC-008.1b).
  @kpi @driving_port @error
  Scenario: Registering over a quarantined guild names the way out
    Given a registered guild whose binding is quarantined
    And a roster of real members recorded against it
    When an admin registers that guild again
    Then not one player row changes
    And no request is made to the guild service
    And the reply names the quarantine
    And the reply names the command that installs a correct key
    And the reply does not send the officer to remove the guild

  # The residue of a parity rollback taken before AC-009.6 shipped. That fix
  # is forward-only — it stops new orphans, it does not delete the rows an
  # earlier rollback already left — so this is a state real databases are in.
  @kpi @driving_port @error
  Scenario: Registering over an orphaned quarantined binding writes nothing
    Given a database still carrying a quarantined binding whose guild is gone
    And that guild's key now resolves to a different guild
    When an admin registers the guild
    Then no player rows are written
    And the reply refuses and names the command that installs a correct key

  # The pair to the orphan scenario above, not to the one before it: both
  # enter on a guild with no row, and only the binding's status separates
  # them. Neither forces that distinction alone.
  @driving_port
  Scenario: Registering a guild that was never bound still adopts normally
    Given a guild that has never been bound to any identity
    When an admin registers the guild
    Then the guild is adopted
    And its player roster is populated

  @kpi @driving_port
  Scenario: A quarantined guild first in order does not disable the cluster leaderboard
    Given a cluster where the quarantined guild is first in iteration order
    And a healthy sibling guild
    When an officer sets up the live cluster leaderboard
    Then the cluster leaderboard is created
    And the season was determined from the healthy sibling

  # Green today. The proposed AC grouped this command with the cluster SPOF;
  # it does not share it (it reads the key of the guild the officer named,
  # not an arbitrary first guild). Kept as a regression guard because the
  # AC-008.4 fall-through touches shared leaderboard code.
  @kpi @driving_port
  Scenario: A quarantined guild first in order does not disable a guild leaderboard
    Given a cluster where the quarantined guild is first in iteration order
    And a healthy sibling guild
    When an officer sets up the live leaderboard for the healthy sibling
    Then that guild's live leaderboard is created

  # Substituted for the proposed AC-008.5: this is the defect the command
  # actually has. "No API key set" sends the officer to /register_guild —
  # the command that overwrites the roster.
  @error @driving_port
  Scenario: A quarantined guild is refused as quarantined, not as keyless
    Given a quarantined guild
    When an officer sets up the live leaderboard for that guild
    Then the officer is not told the guild has no API key
    And the refusal names the command that installs a correct key

  @error @driving_port
  Scenario: A cluster with no usable key at all is refused for a stated reason
    Given a cluster where every guild is quarantined
    When an officer sets up the live cluster leaderboard
    Then the officer is told no guild has a usable key
    And the reason names quarantine rather than a missing key
