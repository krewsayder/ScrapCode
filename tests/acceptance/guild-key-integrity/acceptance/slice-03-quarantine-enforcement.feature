@slice-03 @us-004 @us-005
Feature: A guild whose key has drifted stops ingesting entirely
  Slice 03 — enforcement. Deploys only after Slice 02 exists (the exit) and
  after the 7-day soak of Slice 01 (DEVOPS D11) has shown the binding is
  reliable.

  Covers US-004 (enforce) and US-005 (show it). KPI-2 (contaminated rows
  after detection) and KPI-5 (guilds unaffected by another guild's
  quarantine) are measured from these scenarios.

  # -------------------------------------------------------------------
  # US-004 — enforce
  # -------------------------------------------------------------------

  @us-004 @kpi @driving_port
  Scenario: A drifted guild is quarantined with both identities on the record
    Given a guild bound to "【UNDV】Word Bearers" whose key now resolves to "【UNDV】Dark Mechanicum"
    When the hourly update runs for that guild
    Then the guild is marked quarantined
      And the reason records both the bound guild and the resolved guild
      And the moment it was quarantined is stamped

  @us-004 @kpi
  Scenario: A quarantined guild writes not one raid record
    Given a quarantined guild with existing raid records
    When the hourly update runs for that guild
    Then no battle-hit record is written for that guild
      And no bomb-hit record is written for that guild
      And the number of raid records is the same as before the cycle

  @us-004 @kpi
  Scenario: A quarantined guild writes not one roster record
    Given a quarantined guild with existing player records
    When the hourly update runs for that guild
    Then no player record is inserted for that guild
      And no player record is updated for that guild
      And no player is marked as a former member
      And the number of player records is the same as before the cycle

  @us-004 @driving_port
  Scenario: Entering quarantine alerts both the update channel and the guild
    Given a guild with a ping channel configured
    When that guild enters quarantine
    Then an alert naming both identities is posted to the update channel
      And an alert naming both identities is posted to the guild's ping channel

  @us-004 @error
  Scenario: A guild that stays quarantined is not alerted about hourly
    Given a guild that has been quarantined and already alerted
    When the hourly update runs repeatedly over the following day
    Then at most one alert for that guild is posted in twenty-four hours
      And the suppressed alerts are recorded as suppressed rather than dropped silently

  @us-004 @kpi
  Scenario Outline: Every place that reads a guild's key refuses a quarantined guild
    Given a quarantined guild
    When <site> asks for that guild's key
    Then it is refused
      And no request for that guild's data is made

    Examples:
      | site                                     |
      | the hourly update's season discovery     |
      | the hourly update's raid fetch           |
      | the hourly update's roster validation    |
      | the update-leaderboard command           |
      | the update-all command                   |
      | the roster refresh                       |
      | the staleness check                      |

  @us-004 @kpi
  Scenario: A quarantined guild listed first does not stop the rest of the server
    Given a server with two guilds where the quarantined one is listed first
    When the hourly update determines the season for that server
    Then it skips the quarantined guild's key
      And it uses the next usable key
      And the server is not skipped

  @us-004 @error
  Scenario: A server with no usable key is skipped for a stated reason
    Given a server where every guild is quarantined
    When the hourly update runs
    Then the server is skipped
      And the reason given is that every guild is quarantined
      And the skip is recorded rather than passed over in silence

  @us-004 @kpi
  Scenario: A healthy guild beside a quarantined one updates normally
    Given a server with one quarantined guild and one healthy guild
    When the hourly update runs
    Then the healthy guild's raid data is written
      And the healthy guild appears as successful in the summary
      And the quarantined guild appears as blocked in the summary
      And the cycle records how many guilds it processed and how many it skipped

  @us-004 @error @kpi
  Scenario Outline: A service outage never quarantines anything
    Given an active guild with a stored binding
      And the guild service is unreachable because of <failure>
    When the hourly update runs
    Then the guild is still active
      And nothing is quarantined anywhere in the cluster

    Examples:
      | failure              |
      | a timeout            |
      | a refused connection |
      | a 500 response       |
      | a 503 response       |

  @us-004 @error @kpi
  Scenario: A missing guild identifier never quarantines anything
    Given every guild in the cluster is active and bound
      And the guild service stops returning guild identifiers
    When the hourly update runs
    Then no guild is quarantined
      And every guild still ingests its data
      And a loud alert states that identity verification is offline

  # -------------------------------------------------------------------
  # US-005 — show it
  # -------------------------------------------------------------------

  @us-005 @driving_port
  Scenario: The guild list shows that a guild is quarantined and why
    Given a quarantined guild bound to "【UNDV】Word Bearers" whose key resolves to "【UNDV】Dark Mechanicum"
    When an officer asks to see the guild configuration
    Then that guild's key entry shows it is quarantined
      And it shows the tag the key resolves to and the tag that was expected
      And it shows the date the quarantine began

  @us-005 @driving_port
  Scenario: The guild list shows a healthy guild as verified
    Given an active guild with a stored binding
    When an officer asks to see the guild configuration
    Then that guild's key entry shows it is healthy
      And it shows the bound tag
      And it shows the date the binding was last verified

  @us-005 @error
  Scenario: A guild with no key still reads exactly as it did before
    Given a registered guild with no key at all
    When an officer asks to see the guild configuration
    Then that guild's key entry reads as missing, exactly as it did before this feature

  @us-005 @error @kpi
  Scenario Outline: No key value and no full identifier is ever shown
    Given a guild in the <state> state
    When an officer asks to see the guild configuration
    Then no key value appears anywhere in the response
      And no identifier longer than eight characters appears in the response

    Examples:
      | state        |
      | healthy      |
      | quarantined  |
      | unbound      |
      | keyless      |
