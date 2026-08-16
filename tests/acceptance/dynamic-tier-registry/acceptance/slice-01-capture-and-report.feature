@slice-01 @us-001 @us-002 @us-007
Feature: Every tier the guild clears is captured, and everything discarded is named
  Slice 01 — capture and report. Ships alone and first (DEVOPS D12): every
  hourly cycle that runs before it permanently destroys that hour's hardest-tier
  hits, because the guild-raid endpoint serves a rolling window and cannot be
  asked for yesterday.

  Covers US-001 (capture), US-002 (report what was thrown away) and US-007
  (a new rarity is reported, never silently adopted). TK-1, TK-2 and TK-5 are
  measured from the records these scenarios assert.

  Background:
    Given a registered guild with a healthy key
    And the current season is 107

  # -------------------------------------------------------------------
  # US-001 — capture a tier the bot has never seen
  # -------------------------------------------------------------------

  @us-001 @driving_port @real_io @kpi
  Scenario: A hit at a tier the bot has never seen is stored
    Given the guild's results include a battle hit at the third mythic tier
    When the hourly update runs
    Then the hit is stored against that tier
    And the tier appears in the cycle's record of what was written

  @us-001
  Scenario Outline: Every tier index within a tracked rarity is understood
    Given a result at rarity "<rarity>" and tier index <index>
    When the tier is worked out
    Then it is recorded as "<key>"

    Examples:
      | rarity    | index | key         |
      | Mythic    | 0     | Mythic      |
      | Mythic    | 1     | Mythic_1    |
      | Mythic    | 2     | Mythic_2    |
      | Mythic    | 3     | Mythic_3    |
      | Mythic    | 7     | Mythic_7    |
      | Legendary | 0     | Legendary_0 |
      | Legendary | 4     | Legendary_4 |
      | Legendary | 5     | Legendary_5 |
      | Legendary | 9     | Legendary_9 |

  @us-001 @kpi
  Scenario: Every tier the bot understood before is understood identically
    Given results at every tier the bot supported before this change
    When their tiers are worked out
    Then each one is recorded exactly as it was recorded before
    # The regression pin. A subtle change here orphans historical rows: a
    # hit stored under a key nobody derives any more is a hit nobody can find.

  @us-001 @driving_port @real_io
  Scenario: A bomb hit at a new tier is filed as a bomb hit
    Given the guild's results include a bomb hit at the third mythic tier
    When the hourly update runs
    Then the hit is filed with the bomb results
    And it is not filed with the battle results
    # Generalising the tier must not disturb how a hit is routed by its kind.

  @us-001 @error
  Scenario Outline: A rarity outside the allow-list is never stored
    Given a result at rarity "<rarity>"
    When the tier is worked out
    Then no tier is recorded for it

    Examples:
      | rarity   |
      | Epic     |
      | Rare     |
      | Uncommon |
      | Common   |
      | Divine   |

  @us-001 @error
  Scenario: A negative tier index is refused rather than turned into a tier
    Given a result at rarity "Mythic" and tier index -1
    When the tier is worked out
    Then no tier is recorded for it
    # The boundary a partial fix misses. Removing only the upper bound leaves
    # -1 parsing perfectly well as an integer, which writes a row under a name
    # nobody can ever select.

  # -------------------------------------------------------------------
  # US-002 — see what ingest threw away
  # -------------------------------------------------------------------

  @us-002 @driving_port @kpi
  Scenario: Discarded results are counted separately by reason
    Given a cycle in which three results are refused for an untracked rarity
    And one result is refused for an unusable tier index
    When the hourly update runs
    Then the cycle's record carries both counts under their own reasons
    And neither count is folded into the other

  @us-002 @driving_port @kpi
  Scenario: Every count of discarded results carries a reason
    Given a cycle in which at least one result is refused
    When the hourly update runs
    Then the number discarded equals the sum of the counts per reason
    # TK-5 as an assertion. A total that exceeds the sum of the reasons means a
    # path exists that discards a result without naming itself — the original
    # defect in a new location.

  @us-002 @driving_port
  Scenario: The update post names both the count and the reason
    Given a cycle in which three results are refused for an untracked rarity
    When the hourly update runs
    Then the post to the update channel states how many were discarded
    And it states why
    # A count with no reason is a number nobody can act on — the same rule the
    # cycle report already applies to whole guilds it skips.

  @us-002 @driving_port
  Scenario: A clean cycle says nothing at all
    Given a cycle in which every result is stored
    When the hourly update runs
    Then the post to the update channel carries no discarded line
    And every reason count is reported as zero
    # Silence must mean clean, or the operator learns to scroll past the
    # warning. The counts are still emitted at zero: an absent count is
    # indistinguishable from a counter nobody built.

  @us-002 @driving_port @error
  Scenario: A tier that is captured but cannot be offered says so, every cycle
    Given the guild's results include a hit at a tier no picker can offer
    When the hourly update runs
    Then the hit is stored
    And the post states that results were captured but cannot yet be displayed
    And it names the tier
    # Supersedes AC-002.4/AC-002.5 per ADR-009 D5. A standing condition,
    # re-derived each cycle, not a first-sighting announcement — announcing
    # once needs stored state to de-duplicate something that clears itself.

  @us-002 @driving_port @error
  Scenario: The condition keeps being reported while it is still true, and stops when it is not
    Given a tier was reported as captured-but-not-displayable last cycle
    When the hourly update runs again with more hits at that tier
    Then the post reports the condition again
    And when the tier becomes offerable the post stops reporting it

  @us-002 @error
  Scenario: The report is about shapes, never about players
    Given a cycle in which results are refused
    When the cycle's record is written
    Then it carries the rarity and the tier index of what was refused
    And it carries no player name, no player identifier and no damage figure

  # -------------------------------------------------------------------
  # US-007 — a new rarity is reported, never silently adopted
  # -------------------------------------------------------------------

  @us-007 @driving_port
  Scenario: An unrecognised rarity is named exactly as it arrived
    Given twelve results arrive at a rarity called "Divine"
    When the hourly update runs
    Then none of them are stored
    And the report names "Divine" exactly as it arrived
    # So a brand-new rarity is identifiable without opening a shell.

  @us-007 @driving_port
  Scenario: A routine unrecognised rarity cannot bury a novel one
    Given four hundred results arrive at the routine unrecognised rarity
    And one result arrives at a rarity nobody has seen before
    When the hourly update runs
    Then each distinct rarity is named once in the cycle's record
    And the rarity nobody has seen before is named
    # The rate limit is structural: the record carries a SET of rarities per
    # cycle, so it cannot repeat within a cycle and nothing has to be reset.

  @us-007
  Scenario: Adding a rarity to the allow-list needs no other change
    Given a rarity is added to the allow-list
    When results arrive at several tier indexes of that rarity
    Then every one of them is captured and named
    # The tier dimension was already generalised, so the rarity decision stays
    # a one-line product decision rather than a code-shape change.

  # -------------------------------------------------------------------
  # US-001 + the picker — the day-one readable board
  # -------------------------------------------------------------------

  @us-001 @driving_port @real_io
  Scenario: The captured tier is readable from Discord the same day
    Given hits are stored at the third mythic tier
    When an officer asks for that tier's leaderboard for the season
    Then the board is rendered with that tier's hits
    # Slice 01 adds ONE hand-written picker entry so the board is readable on
    # day one; Slice 02 deletes it when the derived list lands. Without it the
    # slice captures data nobody can look at, which was the operator decision
    # recorded in design/upstream-changes.md §2.
