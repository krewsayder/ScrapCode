# Slice 06 — Admin command safety
#
# Scenario SSOT for `test_slice_06_admin_command_safety.py`.
# Remediates KPI-6, AC-003.4, ADR-008 DDD-4.
#
# KPI-6's baseline incident was a replacement key left in a temp file — an
# error-handling artefact, not a feature. Every scenario here is on an error
# path for that reason: "by construction" has to mean the FAILURE paths were
# designed, not only the success ones.

Feature: No admin command leaks a secret or destroys history quietly
  As a cluster admin working through an incident
  I want the commands to refuse in plain language and tell the truth about
    what they delete
  So that recovering from one mistake does not create a worse one

  @kpi @error @driving_port
  Scenario: A key already held by another guild is refused in plain language
    Given a guild whose key is already registered to a sibling guild
    When an admin installs that same key on this guild
    Then the reply names the guild that already holds it
    And no key material appears in the reply
    And no key material appears in the log

  @kpi @error @driving_port
  Scenario: The same refusal holds when the admin forces the rebind
    Given a guild whose key is already registered to a sibling guild
    When an admin installs that same key with force
    Then the reply names the guild that already holds it
    And no key material appears in the reply

  @driving_port
  Scenario: A legitimate forced rebind still succeeds
    Given a guild bound to one identity whose new key resolves to another
    And no other guild holds that key
    When an admin installs the new key with force
    Then the key is installed
    And the guild is rebound to the new identity

  @error @driving_port
  Scenario: Deregistering states what it will destroy and waits
    Given a registered guild with players, battle hits and bomb hits
    When an admin deregisters the guild
    Then the reply states how many players and hits will be destroyed
    And nothing has been deleted yet

  @error @driving_port
  Scenario: Re-registering a previously quarantined slug does not adopt silently
    Given a guild that was quarantined and then deregistered
    When an admin registers the same guild id again
    Then the reply surfaces that this guild id was previously quarantined

  @error @real-io
  Scenario: A parity rollback leaves no orphaned bindings
    Given a migrated database with a quarantined guild binding
    When the migration data is rolled back
    Then no guild key bindings remain

  @error
  Scenario: Blanking a guild key is refused
    Given a registered guild with a key
    When the repository is asked to replace the key with an empty value
    Then the write is refused
    And the stored key is unchanged
