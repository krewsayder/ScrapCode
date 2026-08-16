@slice-03 @us-005
Feature: The always-on board grows a message for a new tier
  Slice 03 — live board reconciliation. Today the refresh skips any tier with
  no stored message, while the season-rollover path sends a full set. So a new
  tier appears weeks later, on its own, with no action taken — which reads as a
  bug that fixed itself and teaches the operator nothing about when to expect
  it.

  The failure mode of this slice is visible to every member of the guild, not
  just the operator. The idempotence and rollover-race scenarios below should
  be the FIRST written, not the last.

  Background:
    Given a registered guild with a healthy key
    And the current season is 107
    And an always-on leaderboard channel covering every tier except the third mythic one

  # -------------------------------------------------------------------
  # The behaviour being added
  # -------------------------------------------------------------------

  @us-005 @driving_port @kpi
  Scenario: The board gains a message for the tier it was missing
    Given the season on the board is the current season
    When the board refreshes
    Then one new message is posted for the missing tier
    And its identifier is remembered
    And the record of the refresh names the tier that was added

  @us-005 @driving_port
  Scenario: A second refresh adds nothing
    Given the board has already been brought up to date
    When the board refreshes again
    Then no message is posted
    And nothing is recorded about adding a tier
    # Idempotence is the single property that makes this safe to run hourly.
    # It is keyed on the stored tier, so running twice in one hour, or an hour
    # apart, are the same thing.

  @us-005 @driving_port
  Scenario: The new message sits in tier order relative to the ones already there
    Given the season on the board is the current season
    When the board refreshes
    Then the tier that was added appears after the tiers that sort before it

  @us-005 @driving_port
  Scenario Outline: Both kinds of always-on board are brought up to date
    Given the board is configured "<shape>"
    When the board refreshes
    Then one new message is posted for the missing tier

    Examples:
      | shape           |
      | for one guild   |
      | for the cluster |
    # Two branches of the same loop. Fixing one and missing the other is the
    # realistic failure.

  # -------------------------------------------------------------------
  # Error paths — where the public damage lives
  # -------------------------------------------------------------------

  @us-005 @error @driving_port
  Scenario Outline: A refused post leaves the board exactly as it was
    Given posting a message fails with <failure>
    When the board refreshes
    Then the remembered message identifiers are unchanged
    And the failure is recorded without the underlying response text
    And the next refresh adds the tier once, not twice

    Examples:
      | failure                            |
      | a permissions refusal              |
      | a rate limit                       |
      | a post that succeeded then was lost |
    # Retaining unchanged is the requirement, NOT writing back what we got. A
    # partial write that omits an already-posted message is exactly what
    # produces the duplicate on the following cycle.
    #
    # The third case is the honest one: a real post SUCCEEDS after local state
    # has already concluded it failed.

  @us-005 @error @driving_port
  Scenario: A rollover and a new tier in the same cycle produce one set of messages
    Given the season on the board is not the current season
    And the registry holds a tier the board has never had a message for
    When the board refreshes
    Then a full set of messages is posted exactly once
    And no tier ends up with two messages
    # THE RACE. The rollover path rewrites the whole set of remembered
    # identifiers. A reconciliation that runs beside it duplicates the entire
    # board, in front of everyone.

  @us-005 @error @driving_port
  Scenario: A tier that leaves the registry keeps its board
    Given the board has a message for a tier the registry no longer holds
    When the board refreshes
    Then that message is left alone
    And nothing is deleted
    # Additive only. Deleting boards automatically on a vendor-shaped input
    # contradicts the operator's stated anti-goal: a problem should stop and
    # wait for a human, not resolve itself destructively.

  @us-005 @error @driving_port
  Scenario: A board whose channel has gone is still handled the way it always was
    Given the channel behind an always-on board no longer exists
    When the board refreshes
    Then that board's configuration is removed as before
    And the other boards are still brought up to date
    # Reconciliation must not become a new way for one broken board to stop
    # every other one — the blast-radius rule this project already applies to
    # guilds within a server.
