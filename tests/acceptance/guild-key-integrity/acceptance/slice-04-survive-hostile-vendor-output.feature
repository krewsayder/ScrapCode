# Slice 04 — Survive hostile vendor output
#
# Scenario SSOT for `test_slice_04_hostile_vendor_output.py`.
# Remediates US-001, KPI-4, DISCUSS D3, plus the availability regression.
#
# The `guildId` field is UNDOCUMENTED by Tacticus (see the header of
# fixtures/guild_response_recorded.json). The whole payload is unversioned
# output from a service that has already changed shape once inside this
# feature's lifetime, so "the vendor sends a well-formed object" is an
# assumption the classifier may not make.

Feature: The classifier is total over anything Tacticus can return
  As the operator of a cluster whose ingestion runs unattended every hour
  I want every possible vendor response to land in one of the five outcomes
  So that no vendor change can quarantine a healthy guild or stop the loop

  Background:
    Given a cluster where every guild is bound to the identity its key resolves to
    And the guild service is answering

  @kpi @driving_port @error
  Scenario Outline: The same guild written differently is still the same guild
    Given the guild service names the bound guild as <variant>
    When the hourly cycle runs
    Then the guild is not quarantined
    And the stored binding is byte-identical to what it was before
    And no key-mismatch is reported

    Examples: the six ways one uuid can be written
      | variant               |
      | canonical             |
      | uppercase             |
      | mixed_case            |
      | surrounding_whitespace|
      | bom_prefixed          |
      | trailing_newline      |

  @error @driving_port
  Scenario Outline: A guild identifier that is not usable is unverifiable, never drift
    Given the guild service answers with a guild identifier of <variant>
    When the hourly cycle runs
    Then the outcome is reported as unverifiable
    And the guild is not quarantined
    And the stored binding is byte-identical to what it was before

    Examples: values no identity can be built from
      | variant          |
      | whitespace_only  |
      | empty_string     |
      | json_number      |
      | json_bool        |
      | json_null        |
      | not_a_uuid       |

  @error @driving_port
  Scenario Outline: A body that is not a guild object never stops the cycle
    Given the guild service answers 200 with a body that is <body>
    When the hourly cycle runs
    Then the cycle completes without raising
    And the outcome is reported as unverifiable
    And the stored binding is byte-identical to what it was before

    Examples: bodies a real 200 can carry
      | body               |
      | not_json_html      |
      | empty              |
      | truncated_json     |
      | json_null          |
      | json_list          |
      | json_string        |
      | json_bool          |
      | guild_not_a_dict   |
      | guild_null         |

  @error @driving_port
  Scenario: A partially-sent roster degrades instead of ending the cycle
    Given the guild service names the bound guild
    And one member entry arrives without a member identifier
    When the hourly cycle runs
    Then the cycle completes without raising
    And the guild is not quarantined
    And the members that did arrive are still usable

  @kpi @driving_port
  Scenario: A genuinely different guild still quarantines
    Given the guild service names a different guild entirely
    When the hourly cycle runs
    Then the guild is quarantined
    And the reason names both guilds

  @kpi @error @driving_port
  Scenario: One guild's unreadable answer does not stop the other guilds
    Given one guild's key returns a body that is not a guild object
    And a sibling guild's key answers normally
    When the hourly cycle runs
    Then the sibling guild is still processed
    And the sibling guild's data is written

  @error @driving_port
  Scenario: A recovery key is accepted against a differently-written stored identity
    Given the stored identity was recorded in a different letter case
    And the operator submits the correct key for that guild
    When the operator replaces the guild's key
    Then the key is accepted
    And the guild is no longer quarantined

  @real-io @adapter-integration
  Scenario: The recorded vendor response still matches after re-casing
    Given the recorded Tacticus response with its guild identifier re-cased and BOM-prefixed
    When that response is classified against the real stored binding
    Then the outcome is a match
