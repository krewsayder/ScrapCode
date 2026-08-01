@slice-02 @us-003
Feature: An admin replaces a guild's key from Discord
  Slice 02 — the recovery path. It ships BEFORE enforcement (ADR-008 D3 /
  DISCUSS D3) because it is the only exit from quarantine; enforcing first
  makes the first quarantine unrecoverable without a console session.

  Covers US-003. KPI-3 (wall-clock to replace a key) is measured from the
  record these scenarios assert.

  @driving_port @walking_skeleton @real-io
  Scenario: An admin installs a new key and is told which guild it belongs to
    Given a registered guild bound to "【UNDV】Word Bearers"
      And a replacement key that resolves to the same guild
    When an admin asks to update that guild's key
    Then the guild's stored key is replaced
      And the reply names the guild the new key resolves to
      And the reply is visible only to the admin who ran it
      And the elapsed time of the replacement is recorded

  @kpi
  Scenario: Replacing a key destroys nothing
    Given a registered guild with recorded players, battle hits and bomb hits
    When an admin updates that guild's key with a key for the same guild
    Then the guild's player records are unchanged in number
      And the guild's battle-hit records are unchanged in number
      And the guild's bomb-hit records are unchanged in number

  @error @kpi
  Scenario: A key for the wrong guild is refused and nothing is stored
    Given a registered guild bound to "【UNDV】Word Bearers"
      And a submitted key that resolves to "【UNDV】Dark Mechanicum"
    When an admin asks to update that guild's key
    Then the update is refused
      And the reply names both the bound guild and the guild the key resolves to
      And the stored key is unchanged

  Scenario: The same key is accepted when the admin says the re-bind is deliberate
    Given a registered guild bound to "【UNDV】Word Bearers"
      And a submitted key that resolves to "【UNDV】Dark Mechanicum"
    When an admin asks to update that guild's key and confirms the re-bind is intended
    Then the key is installed
      And the guild is now bound to "【UNDV】Dark Mechanicum"
      And the re-bind is recorded with both the old and the new identity

  @error
  Scenario Outline: A rejected key is never installed
    Given a registered guild with a working stored key
      And a submitted key that is rejected with status <status>
    When an admin asks to update that guild's key
    Then the update is refused with a dead-key message
      And the stored key is unchanged

    Examples:
      | status |
      | 401    |
      | 403    |

  @error
  Scenario Outline: A key that cannot be checked is never installed
    Given a registered guild with a working stored key
      And the guild service is unreachable because of <failure>
    When an admin asks to update that guild's key
    Then the update is refused with a could-not-verify message
      And the stored key is unchanged
      And no binding is written

    Examples:
      | failure              |
      | a timeout            |
      | a refused connection |
      | a 503 response       |

  @error @driving_port
  Scenario: An officer cannot replace a guild's key
    Given a user who holds the officer tier but not the admin tier
    When that user asks to update a guild's key
    Then the request is refused on permissions
      And the stored key is unchanged

  @error @kpi
  Scenario: No key value is ever shown or written down
    Given any outcome of the key-update request
    When the reply is sent
    Then the reply is visible only to the person who ran the command
      And the submitted key value does not appear in the reply
      And the submitted key value does not appear in the bot's log
      And the stored key value does not appear in the bot's log

  Scenario: Installing a matching key releases a quarantined guild
    Given a guild that is quarantined because its key resolved to another guild
      And a replacement key that resolves to the guild it is bound to
    When an admin asks to update that guild's key
    Then the guild is no longer quarantined
      And the reason it was quarantined is cleared
      And the guild resumes ingesting on the next cycle

  @error @driving_port
  Scenario: An unknown guild name is refused with the list of the ones that exist
    Given a cluster with registered guilds
    When an admin asks to update the key of a guild that is not registered
    Then the request fails naming the guilds that are registered
      And nothing is written
