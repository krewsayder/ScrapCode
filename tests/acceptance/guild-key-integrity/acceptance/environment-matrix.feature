@environment-matrix
Feature: The feature behaves correctly in every environment it will meet
  Mandate 4 — Environmental Realism. One scenario per target environment in
  `docs/feature/guild-key-integrity/environments.yaml`, each asserting that
  environment's headline invariant rather than re-testing the slices.

  The environments are not OS installs. They are the eight states of the
  relationship between (a) the stored binding, (b) what the guild service
  returns, and (c) whether the call succeeded at all.

  @env-clean @real-io
  Scenario: clean — every guild adopts its identity on the first cycle and says so once
    Given a freshly upgraded cluster with no bindings and three registered guilds
    When the hourly update runs once
    Then all three guilds are bound
      And exactly three adoption messages are posted
    When the hourly update runs a second time
    Then no further adoption message is posted

  @env-bound-matching @kpi
  Scenario: bound-matching — the steady state is completely silent
    Given a cluster where every guild is bound to the identity its key resolves to
    When the hourly update runs
    Then no alert of any kind is raised
      And every guild ingests its data
      And a successful-verification record is written for each guild

  @env-bound-drifted @kpi
  Scenario: bound-drifted — the incident replay writes nothing once enforcement is on
    Given a guild bound to "【UNDV】Word Bearers" whose key resolves to "【UNDV】Dark Mechanicum"
      And a recorded count of that guild's players, battle hits and bomb hits
    When the hourly update runs with enforcement active
    Then the guild is quarantined
      And the counts of players, battle hits and bomb hits are unchanged

  @env-unverifiable @error @kpi
  Scenario: unverifiable — a vendor change alerts loudly and blocks nothing
    Given a cluster where the guild service has stopped returning guild identifiers for every guild
    When the hourly update runs
    Then no guild is quarantined
      And every guild still ingests its data
      And an alert states that identity verification is offline
      And no guild's tag is compared as a substitute for its identifier

  @env-tacticus-unreachable @error @kpi
  Scenario: tacticus-unreachable — an outage leaves every binding byte-identical
    Given a cluster of bound, active guilds
      And a recorded copy of every binding
    When the guild service becomes unreachable and the hourly update runs
    Then no guild is quarantined
      And every binding is byte-identical to the recorded copy

  @env-dead-key @error
  Scenario: dead-key — a revoked key is reported, not quarantined
    Given a bound guild whose key has been revoked
    When the hourly update runs
    Then the guild is reported as having a dead key
      And the guild is not quarantined
      And the operator is told to install a new key

  @env-mixed-cluster @kpi
  Scenario: mixed-cluster — a quarantined guild listed first does not take the server down
    Given a server whose first-listed guild is quarantined and whose second guild is healthy
    When the hourly update runs
    Then the season is determined from the healthy guild's key
      And the healthy guild is updated
      And the cycle records one guild processed and one guild skipped with a stated reason

  @env-json-backend-rollback @error @real-io @adapter-integration
  Scenario: json-backend-rollback — the feature goes inert without raising
    Given a cluster running on the file-based storage backend
    When the hourly update runs
    Then every guild reports as unbound
      And no binding is stored
      And no guild is quarantined
      And ingestion behaves exactly as it did before this feature existed
      And nothing raises

  @traceability
  Scenario: The suite and the platform artifact cannot drift apart
    Given the environment list recorded by the platform wave
    When the suite's environment vocabulary is compared against it
    Then the two lists are identical
