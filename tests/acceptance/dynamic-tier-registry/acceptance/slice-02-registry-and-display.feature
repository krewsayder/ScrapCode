@slice-02 @us-003 @us-004
Feature: One place knows what a tier is, and every view command shows the new one
  Slice 02 — the registry, and Mythic 3 on every view command. US-004 is
  infrastructure and has no output of its own; it lands as the PRECURSOR COMMIT
  of this slice rather than as a slice, because a slice containing only
  infrastructure is a structural failure. US-003 is what makes the slice
  releasable.

  The user-visible outcome of this slice is that NOTHING CHANGES: the same
  eight tiers, the same labels, the same order. That is how the derivation is
  known to be right.

  Background:
    Given a registered guild with a healthy key
    And the current season is 107

  # -------------------------------------------------------------------
  # US-004 — one place that knows what a tier is (precursor commit)
  # -------------------------------------------------------------------

  @us-004
  Scenario: The registry owns all four rules
    Given the tier registry
    Then it can work out a tier from a result
    And it can work out a label from a tier
    And it can put tiers in order
    And it accepts an override for a tier the game names irregularly

  @us-004 @architecture
  Scenario: The picker's list is derived, never written by hand
    Given the list of tiers offered in slash commands
    Then it is built from the registry
    And it contains no tier written out by hand

  @us-004 @architecture
  Scenario: The ingest parser asks the registry rather than knowing the answer
    Given the ingest parser
    Then it defers to the registry's rule
    And it names no tier of its own

  @us-004 @architecture @kpi
  Scenario: Tier names appear in exactly one place in the source
    Given the whole source tree
    When it is searched for tier names
    Then every match is inside the registry or its own tests
    And the places that use the word "tier" for a permission level are exempt by name
    # This is TK-4's instrument, not a style rule (DEVOPS D11). If tier names
    # live in one file, adding a tier is one edit — measured on every test run
    # rather than reviewed a year from now when the next tier ships.
    #
    # The exemption is load-bearing. "Tier" means two unrelated things here,
    # and a rule that fires on the permission-tier code either gets ignored or
    # gets loosened until it stops catching what it was written for.

  @us-004
  Scenario: An override wins over the derived label
    Given the registry has an override naming a tier differently
    When that tier's label is worked out
    Then the override is used
    # So a future tier the game names irregularly is a data edit rather than a
    # change to the shape of the rule.

  @us-004
  Scenario: Tiers sort by rarity then by index
    Given the registry's ordering
    Then legendary tiers come before mythic tiers
    And within a rarity the indexes ascend
    # Read by the replay grouping, the live-board message order and the picker
    # order, so all three stay in step.

  # -------------------------------------------------------------------
  # US-003 — read a Mythic 3 leaderboard on demand
  # -------------------------------------------------------------------

  @us-003 @kpi
  Scenario: The derived list reproduces the old one exactly, and adds the new tier
    Given the list of tiers offered in slash commands
    Then the seven tiers that existed before appear in the same order with the same names and the same stored values
    And the third mythic tier appears after them
    # AC-003.2, widened from seven to eight (devops/upstream-changes.md item 2).
    # The eighth entry is the one Slice 02 REPLACES — the operator has been
    # using it since Slice 01 shipped — so it is the only one with a live
    # regression surface, and it was the one outside the pin.

  @us-003
  Scenario Outline: A label is worked out from the stored tier
    Given the stored tier "<key>"
    When its label is worked out
    Then the label is "<label>"

    Examples:
      | key         | label       |
      | Legendary_0 | Legendary 1 |
      | Legendary_4 | Legendary 5 |
      | Mythic      | Mythic 1    |
      | Mythic_1    | Mythic 2    |
      | Mythic_2    | Mythic 3    |
    # The stored name and the displayed name are off by one, in both rarities,
    # and that is FROZEN. Correcting it would rewrite the live board's message
    # keys and orphan every historical replay, which is stored under the label.

  @us-003 @driving_port @real_io
  Scenario Outline: Every view command offers and renders the new tier
    Given hits are stored at the third mythic tier
    When an officer runs "<command>" for the season and that tier
    Then the board is titled with the tier's label
    And the ranked entries are that tier's hits

    Examples:
      | command                   |
      | /view_leaderboard         |
      | /view_bomb_leaderboard    |
      | /view_cluster_leaderboard |
    # Parametrized because fixing one surface and missing the others is the
    # realistic failure, not a hypothetical one.

  @us-003 @driving_port @error
  Scenario: Choosing a tier with no hits gives an empty board, not an error
    Given no hits are stored at the third mythic tier
    When an officer asks for that tier's leaderboard
    Then the usual no-data response is given, naming the tier
    And nothing fails

  @us-003 @error @real_io
  Scenario: A stored tier nobody has named still renders, under its own name
    Given hits are stored at a tier the registry does not know
    When the leaderboard is rendered
    Then the rows are shown
    And the tier is labelled with its stored name
    # A row is never hidden because its name could not be worked out. Hiding it
    # is how this feature's original defect looks from the read side.

  @us-003 @error
  Scenario: More tiers than a slash command can offer stops the bot at startup
    Given the registry holds more tiers than a slash command can offer
    When the bot starts
    Then it refuses to start and says why
    # Discord REJECTS an oversized command sync rather than failing locally.
    # The result would be the old list live in front of new code, with nothing
    # anywhere saying the two disagree.

  @us-003 @driving_port @real_io @error
  Scenario: Replays recorded under the old labels are still found
    Given replays were recorded under the tier labels used before this change
    When an officer asks for the replays of a map
    Then the previously recorded replays are still returned
    # The cheapest possible check that labels did not shift. Replays are stored
    # under the LABEL and filtered on it, so a label that drifts drops them out
    # of the command while leaving them in the database.
