@slice-04 @us-006
Feature: The tier picker discovers tiers instead of being told about them
  Slice 04 — the "dynamic" in the feature name. Slices 01-03 make the third
  mythic tier work; they do not make the fourth one work, because a
  registry-derived list is still fixed when commands are synced. The picker
  cannot offer a tier the running process did not know at startup.

  Placed last, and abandonable. If its hypothesis fails, Slices 01-03 have
  already delivered the whole outcome, and the fallback — a derived list needing
  one file edit per tier — is strictly better than the two files with an
  undocumented skew between them that this feature started from.

  Background:
    Given a registered guild with a healthy key
    And the current season is 107

  # -------------------------------------------------------------------
  # The union: what the picker knows
  # -------------------------------------------------------------------

  @us-006 @driving_port @real_io @kpi
  Scenario: A tier present only in the stored data is offered
    Given hits are stored at a tier the registry does not hold
    When the operator opens the tier picker
    Then that tier is offered
    # Without this the slice has achieved nothing Slice 02 did not. The tier
    # being offered on the strength of stored rows alone is the whole proof.

  @us-006 @driving_port
  Scenario: A tier in the registry with no hits yet is still offered
    Given the registry holds a tier no hit has ever been stored at
    When the operator opens the tier picker
    Then that tier is offered
    # The picker is the registry AND the stored data, never the stored data
    # alone — so one malformed row cannot define the tier list, and a tier can
    # be chosen before its first hit lands.

  @us-006 @driving_port
  Scenario: Typing narrows the offered tiers
    Given more tiers exist than a picker is allowed to offer at once
    When the operator types the beginning of a tier's name
    Then only matching tiers are offered
    And no more are offered than the picker allows
    # Handled by filtering on what was typed, not by cutting an unfiltered list
    # short. A truncated list silently omits exactly the tier being looked for.

  # -------------------------------------------------------------------
  # The submitted value
  # -------------------------------------------------------------------

  @us-006 @driving_port @real_io
  Scenario Outline: A chosen tier finds its stored hits
    Given hits are stored at the third mythic tier
    When the operator runs "<command>" and chooses that tier from the picker
    Then the board shows that tier's hits

    Examples:
      | command                   |
      | /view_leaderboard         |
      | /view_bomb_leaderboard    |
      | /view_cluster_leaderboard |

  @us-006 @driving_port @error
  Scenario: Text matching no tier is refused by name
    Given the operator submits a tier that does not exist
    When the command runs
    Then the response says the tier is not recognised
    And it names the tiers that are
    And no board is shown
    # An empty board is indistinguishable from a tier with no hits, which is
    # the exact ambiguity this feature exists to remove. Answering it with an
    # empty board would reintroduce the defect at the last step.

  @us-006 @driving_port @real_io @error
  Scenario: A replay submitted through the picker can be found again afterwards
    Given the operator uploads a replay and chooses a tier from the picker
    When an officer later asks for the replays of that map
    Then the replay is returned
    # The one surface where the tier's LABEL is what gets stored. A round trip,
    # not just the picker: choosing correctly and storing something unfindable
    # would pass a picker-only assertion.

  # -------------------------------------------------------------------
  # AC-006.3 — the hypothesis, as an assertion
  # -------------------------------------------------------------------

  @us-006 @architecture @kpi
  Scenario Outline: The modules that read a tier are untouched by this change
    Given the modules that read a tier's stored value or its label
    When this slice's changes are applied
    Then "<module>" is not modified

    Examples:
      | module                  |
      | bot/cogs/tasks_cog.py   |
      | bot/cogs/view_cog.py    |
      | bot/cogs/admin_cog.py   |
      | bot/embeds.py           |
      | bot/cogs/replay_cog.py  |
    # FIVE modules, not one. DISCUSS framed this as "the three call sites in
    # the embed builder"; the count is 26 reads across these five. The design
    # is unaffected and arguably vindicated — a tier record shaped like the
    # object those sites already read is what makes 26 sites tractable — but
    # the verification surface is five modules wide, and this is where that is
    # asserted rather than asserted in prose.

  @us-006 @architecture @error
  Scenario: The places that use "tier" for a permission level are untouched
    Given the commands that show a permission tier or set one
    When this slice's changes are applied
    Then those commands behave exactly as before
    # A mechanical rename across everything called "tier" would break both of
    # them, and both would still type-check. This is the assertion that stands
    # in for a type the language will not give us.

  @us-006 @error @real_io
  Scenario: The rollback storage backend can still answer what tiers exist
    Given the bot is running on the file-based storage backend
    When the operator opens the tier picker
    Then the tiers are offered without failure
    # The shared storage interface gains a method this slice. Adding it to the
    # interface without adding it to BOTH implementations makes the file-based
    # one impossible to construct — which breaks the documented rollback path
    # at startup, exactly when someone is reaching for it.
