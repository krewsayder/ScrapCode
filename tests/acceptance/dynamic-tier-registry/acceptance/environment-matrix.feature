@environment-matrix
Feature: The feature behaves correctly in every state the operator can be in
  Mandate 4 — Environmental Realism. One scenario per target environment in
  docs/feature/dynamic-tier-registry/environments.yaml, so the scenarios are
  parametrized over the states a real deployment is actually in rather than
  over the one state a fixture happens to build.

  The distinguishing variables are four: what the results carry, whether the
  resulting tier is known to the registry, what the always-on board already
  holds, and which storage backend is bound.

  @traceability
  Scenario: The environments the suite knows are the environments DEVOPS declared
    Given the environment list in the platform artifact
    Then the suite covers every one of them
    And it invents none of its own
    # A matrix that drifts from the artifact is a matrix nobody is maintaining.

  @real_io @kpi
  Scenario: known-tiers-only — the steady state is completely silent
    Given results only at tiers the bot already supported
    When the hourly update runs
    Then every hit is stored as before
    And nothing is reported as discarded
    And every reason count reads zero
    # THE REGRESSION ENVIRONMENT. A suite that only covers the new tier will
    # not catch an implementation that reports a discard on every cycle.

  @real_io @kpi
  Scenario: mythic-3-live — the incident replay
    Given real results at the third mythic tier
    When the hourly update runs
    Then hits are stored at that tier
    And the tier can be chosen and its board rendered

  @error
  Scenario: tier-beyond-the-registry — captured, reported, not hidden
    Given results at a tier beyond anything the registry holds
    When the hourly update runs
    Then the hits are stored
    And the post says they were captured but cannot yet be displayed

  @error
  Scenario: untracked-rarity — counted and named, never adopted
    Given results at a rarity outside the allow-list
    When the hourly update runs
    Then none of them are stored
    And the rarity is named exactly as it arrived

  @error @kpi
  Scenario: malformed-set — each unusable tier index has its own reason
    Given results whose tier index is missing, empty, not a number, or negative
    When the hourly update runs
    Then each is counted under its own reason
    And the number discarded equals the sum of the counts per reason

  @driving_port
  Scenario: live-board-incomplete — the board is brought up to date, once
    Given an always-on board missing a message for a tier that has hits
    When the board refreshes twice
    Then exactly one message is added

  @driving_port @error
  Scenario: live-board-rollover-race — one set of messages, not two
    Given a season rollover in the same cycle as a tier with no message
    When the board refreshes
    Then each tier has exactly one message

  @driving_port @error
  Scenario: discord-send-refused — nothing is remembered that was not posted
    Given posting to the always-on board is refused
    When the board refreshes
    Then the remembered identifiers are unchanged
    And the following refresh adds the tier once

  @real_io @error
  Scenario: historical-replay-labels — nothing recorded before goes missing
    Given replays recorded under the labels used before this change
    When the registry is in use
    Then those replays are still returned

  @error
  Scenario: picker-at-the-cap — refuses loudly rather than syncing badly
    Given more tiers than a slash command can offer
    When the bot starts
    Then it refuses to start

  @real_io @error
  Scenario: json-backend-rollback — the rollback path still works
    Given the bot is running on the file-based storage backend
    When the hourly update runs
    Then hits are stored exactly as they are on the primary backend
    And the bot starts cleanly
    # The environment an operator lands in when a slice has to come out under
    # time pressure. "Does not crash" is the whole requirement.
