@slice-01 @us-001 @us-002 @us-006
Feature: A guild's key carries the identity of the guild it belongs to
  Slice 01 — bind and report. Non-blocking by design (ADR-008 D3): this
  slice learns whether the identity is reliable before Slice 03 makes it
  enforcing. Nothing here refuses a write.

  Covers US-006 (the binding store), US-001 (bind and show), US-002 (report
  drift without blocking). KPI-1 is measured from the records these
  scenarios assert.

  # -------------------------------------------------------------------
  # US-006 — the binding store
  # -------------------------------------------------------------------

  @us-006 @real-io @adapter-integration
  Scenario: Adding the binding store leaves every existing guild record untouched
    Given a copy of a cluster whose guilds were registered before this feature existed
    When the operator applies the binding-store upgrade
    Then a binding store exists and holds no records yet
      And every guild record is byte-identical to how it was before the upgrade
      And no guild record gained a field

  @us-006 @real-io @error
  Scenario: The binding store can be removed and the cluster returns to its prior shape
    Given a cluster with the binding-store upgrade applied
    When the operator reverses the upgrade
    Then the binding store is gone
      And the cluster's shape matches the state it had before the upgrade exactly

  @us-006 @error
  Scenario: Loading a guild and saving it back unchanged preserves every field
    Given a guild with a stored binding
    When the guild is loaded and saved back with no modification
    Then every field of the guild record is unchanged
      And the guild's binding is unchanged

  # -------------------------------------------------------------------
  # US-001 — bind the identity and show it
  # -------------------------------------------------------------------

  @us-001 @driving_port @kpi
  Scenario: The first successful verification adopts the guild's identity and says so once
    Given a registered guild with a working key and no stored binding
    When the hourly update verifies the guild's key
    Then the guild is bound to the identity the key resolves to
      And the date of verification is recorded
      And exactly one message naming the guild and the adopted identity is posted to the update channel

  @us-001
  Scenario: A later verification refreshes the date without announcing again
    Given a guild already bound to the identity its key resolves to
    When the hourly update verifies the guild's key a second time
    Then the date of verification is refreshed
      And no adoption message is posted

  @us-001 @driving_port
  Scenario: The guild list shows which guild each key belongs to and when it was checked
    Given a guild bound to "【UNDV】Word Bearers" with tag "EUVQZ"
    When an officer asks to see the guild configuration
    Then the guild's entry shows the bound tag
      And it shows the first eight characters of the bound identifier
      And it shows the date the binding was last verified

  @us-001 @error @kpi
  Scenario: A response with no guild identifier is called unverifiable and never falls back to the tag
    Given a registered guild whose key returns a guild without an identifier
    When the hourly update verifies the guild's key
    Then the outcome is reported as unverifiable
      And the guild is not quarantined
      And the guild's data is still ingested
      And an alert states that identity verification is offline
      And the guild's tag is not compared as a substitute

  @us-001 @error
  Scenario Outline: A missing display field does not stop the guild from being bound
    Given a registered guild whose key returns a guild with no <missing_field>
    When the hourly update verifies the guild's key
    Then the guild is still bound on its identifier alone
      And the missing display field is shown as a dash

    Examples:
      | missing_field |
      | tag           |
      | name          |
      | tag or name   |

  @us-001 @error @driving_port
  Scenario: Changing a guild's ping channel leaves its binding untouched
    Given a guild with a stored binding
    When an admin changes that guild's ping channel
    Then the guild's binding is unchanged
      And the date of verification is unchanged

  @us-001 @error
  Scenario Outline: A rejected key is reported as dead and is never quarantined
    Given a registered guild whose key is rejected with status <status>
    When the hourly update verifies the guild's key
    Then the outcome is reported as a dead key
      And no binding is written
      And the guild is not quarantined

    Examples:
      | status |
      | 401    |
      | 403    |

  @us-001 @error @kpi
  Scenario Outline: An unreachable service leaves the stored binding exactly as it was
    Given a guild with a stored binding
      And the guild service is unreachable because of <failure>
    When the hourly update verifies the guild's key
    Then the outcome is reported as unreachable
      And the stored binding is byte-identical to what it was before
      And the guild is not quarantined
      And the check is retried on the next cycle

    Examples:
      | failure           |
      | a timeout         |
      | a refused connection |
      | a 500 response    |
      | a 503 response    |

  # -------------------------------------------------------------------
  # US-002 — report drift, block nothing
  # -------------------------------------------------------------------

  @us-002 @kpi
  Scenario: A drifted key is reported naming both the bound and the resolved guild
    Given a guild bound to "【UNDV】Word Bearers" whose key now resolves to "【UNDV】Dark Mechanicum"
    When the hourly update verifies the guild's key
    Then a mismatch is reported
      And the report names both guilds by name and tag
      And the report shows the first eight characters of both identifiers

  @us-002
  Scenario: In this slice a mismatch still ingests the data
    Given a guild whose key resolves to a different guild than the one it is bound to
    When the hourly update runs for that guild
    Then the guild's raid data is still written
      And the guild is not quarantined

  @us-002 @driving_port
  Scenario: The mismatch appears in the hourly summary beside the other guilds
    Given a cluster with one drifted guild and one healthy guild
    When the hourly update completes
    Then the summary posted to the update channel contains the mismatch line for the drifted guild
      And it contains the normal success line for the healthy guild

  @us-002 @kpi
  Scenario: A guild whose key resolves to the guild it is bound to produces no message anywhere
    Given a guild bound to the identity its key resolves to
    When the hourly update verifies the guild's key
    Then no mismatch message is produced in the update channel
      And no mismatch message is produced in the guild's ping channel
      And no alert record is written

  @us-002 @error
  Scenario Outline: A retag or a rename is not a mismatch
    Given a guild bound to "【UNDV】Word Bearers" with tag "EUVQZ"
      And its key now resolves to the same guild with a changed <display_field>
    When the hourly update verifies the guild's key
    Then no mismatch is reported
      And no alert is raised
      And the stored display fields are refreshed to the new values

    Examples:
      | display_field |
      | tag           |
      | name          |
      | tag and name  |

  @us-002 @kpi
  Scenario: A mismatch that persists is reported on every cycle in this slice
    Given a guild whose key resolves to a different guild than the one it is bound to
    When the hourly update runs twice in succession
    Then a mismatch is reported on both cycles
      And the repetition is recorded for later review

  @us-002 @kpi
  Scenario: The time from the last agreeing check to the alert can be measured
    Given a guild that verified successfully on one cycle
      And whose key resolves to a different guild on the next cycle
    When the alert is raised
    Then the moment of the last agreeing check is recoverable
      And the moment of the alert is recoverable
      And the gap between them is a positive span no longer than one cycle
