@slice-01 @us-001 @us-002 @us-003 @us-004
Feature: An officer can stop the cluster leaderboard publishing, and start it again
  Slice 01 — the operator's off switch, with its conformance precursor.

  The cluster board previously had two states and neither was chosen: it
  refreshed every hour, or it froze silently because no key in the cluster
  could answer the season. This adds the deliberate, human-set state.

  Turning a board off leaves its posted messages exactly as they are. That is
  the whole design (DISCUSS D2) and it has a price: a frozen board and a live
  board look identical in the channel. Every scenario under US-003 exists
  because of that price.

  KPI-1 (zero Discord calls against a paused board), KPI-2 (the pause survives
  a restart), KPI-3 (a pause is visible without reading logs), KPI-4 (the
  upgrade pauses nothing) and KPI-5 (the two adapters agree) are measured from
  these scenarios.

  # -------------------------------------------------------------------
  # US-001 — turn it off
  # -------------------------------------------------------------------

  @us-001 @driving_port @real-io
  Scenario: An officer turns the cluster board off and is told what survived
    Given a configured cluster leaderboard
    When an officer turns the cluster leaderboard off
    Then the board is recorded as turned off
      And the reply names the channel the board lives in
      And the reply says the posted messages are left as they are
      And the reply names the command that turns it back on

  @us-001 @kpi @real-io
  Scenario: A board that is off is never touched by the hourly cycle
    Given a cluster leaderboard that is turned off
    When the hourly cycle runs
    Then not one message is edited
      And not one message is sent
      And the board's configuration is still there

  @us-001 @kpi @real-io
  Scenario: Turning a board off changes nothing except the switch
    Given a configured cluster leaderboard
    When an officer turns the cluster leaderboard off
    Then the channel it posts to is unchanged
      And the messages it tracks are unchanged
      And the season it was built for is unchanged

  @us-001 @kpi @real-io
  Scenario: A pause outlives the process
    Given a cluster leaderboard that is turned off
    When the bot is restarted
    Then the board is still turned off

  @us-001 @error @driving_port
  Scenario: Turning off a board that was never set up is refused
    Given no cluster leaderboard has been set up
    When an officer turns the cluster leaderboard off
    Then the request is refused
      And the reply names the command that sets a board up
      And nothing is written to storage

  @us-001 @error @driving_port
  Scenario Outline: Asking for the state a board is already in writes nothing
    Given a cluster leaderboard that is <starting state>
    When an officer <command> the cluster leaderboard
    Then the reply says the board is already in that state
      And nothing is written to storage

    Examples:
      | starting state | command             |
      | turned off     | turns off           |
      | running        | turns on            |

  @us-001 @error @driving_port
  Scenario Outline: Only an officer may change whether the board publishes
    Given a configured cluster leaderboard
      And a member who does not hold the officer tier
    When that member <command> the cluster leaderboard
    Then the request is refused for lack of permission
      And the board's state is unchanged

    Examples:
      | command   |
      | turns off |
      | turns on  |

  # -------------------------------------------------------------------
  # US-002 — turn it back on
  # -------------------------------------------------------------------

  @us-002 @driving_port @real-io
  Scenario: An officer turns the cluster board back on
    Given a cluster leaderboard that is turned off
    When an officer turns the cluster leaderboard on
    Then the board is recorded as running
      And the reply names the channel the board lives in
      And the reply says updates resume on the next cycle

  @us-002 @kpi @real-io
  Scenario: Resuming within the same season edits the board that is already there
    Given a cluster leaderboard that was turned off during the current season
    When an officer turns the cluster leaderboard on
      And the hourly cycle runs
    Then the messages already posted are edited
      And no new message is sent

  @us-002 @real-io
  Scenario: Resuming after the season rolled over starts a fresh board
    Given a cluster leaderboard that was turned off during an earlier season
    When an officer turns the cluster leaderboard on
      And the hourly cycle runs
    Then a fresh set of messages is posted
      And the messages from the earlier season are left untouched

  @us-002 @driving_port @real-io
  Scenario: Setting a board up again brings it back on
    Given a cluster leaderboard that is turned off
    When an officer sets up the cluster leaderboard again
    Then the board is recorded as running
      And the hourly cycle updates it

  # -------------------------------------------------------------------
  # US-003 — see which boards are off
  #
  # Load-bearing, not cosmetic. Because a pause leaves the posted messages
  # alone, this is the ONLY place an officer can tell a frozen board from a
  # live one.
  # -------------------------------------------------------------------

  @us-003 @driving_port @kpi
  Scenario: The configuration view says a board is turned off
    Given a cluster leaderboard that is turned off
    When an officer asks to see the leaderboard configuration
    Then that board's entry says it is turned off
      And it says the messages are frozen at the last update

  @us-003 @driving_port @kpi
  Scenario: The configuration view says a running board is running
    Given a configured cluster leaderboard
    When an officer asks to see the leaderboard configuration
    Then that board's entry says it is updating hourly

  @us-003 @driving_port @kpi
  Scenario Outline: A board's state is always stated, never left to be inferred
    Given a cluster leaderboard that is <state>
    When an officer asks to see the leaderboard configuration
    Then that board's entry states its publishing state
      And the channel and the number of tiers are still shown

    Examples:
      | state      |
      | running    |
      | turned off |

  # -------------------------------------------------------------------
  # US-004 — @infrastructure — the conformance precursor
  #
  # No user-visible change. These scenarios exist because the shipped
  # representation diverged from the one this codebase already uses for a
  # guild key's on/off state, and the divergence is invisible from every
  # driving port — so only tests at this level can hold it.
  # -------------------------------------------------------------------

  @us-004 @real-io
  Scenario: A board's state arrives as a described value, not a bare record
    Given a configured cluster leaderboard
    When the stored leaderboards are read
    Then each board arrives as a described configuration
      And its publishing state can be read from it directly

  @us-004 @real-io
  Scenario: A board's state is one of the named states
    Given a configured cluster leaderboard
    When the stored leaderboards are read
    Then the publishing state is one of the named publishing states

  @us-004 @kpi @real-io
  Scenario: A board stored before the switch existed reads as running
    Given a cluster leaderboard stored with no publishing state recorded
    When the stored leaderboards are read
    Then the board reads as running
      And no reader has to assume it

  @us-004 @kpi @adapter-integration @real-io
  Scenario Outline: Both ways of storing a board agree about its state
    Given a cluster leaderboard that is <state>
    When it is saved and read back through the file store
      And it is saved and read back through the database
    Then both give the same configuration

    Examples:
      | state      |
      | running    |
      | turned off |

  @us-004 @real-io
  Scenario: There is exactly one place that decides whether a board publishes
    Given the parts of the bot that decide whether to refresh a board
    When they are examined
    Then the decision is read from the board's own configuration
      And no other part of the bot compares the state itself

  @us-004 @kpi @real-io
  Scenario Outline: Any board can be turned off, not only the cluster one
    Given a <kind> leaderboard that is turned off
    When the hourly cycle runs
    Then not one message is edited
      And not one message is sent

    Examples:
      | kind    |
      | cluster |
      | guild   |

  # -------------------------------------------------------------------
  # The upgrade — KPI-4. A schema change that pauses every live board on the
  # cluster is the outage this feature exists to prevent.
  # -------------------------------------------------------------------

  @us-004 @kpi @real-io @adapter-integration
  Scenario: Upgrading the database pauses nothing
    Given a database holding configured leaderboards from before the switch existed
    When the database is upgraded
    Then every board reads as running
      And every board keeps its channel, its messages and its season
