@contract @adapter-integration
Feature: The guild service returns an identity we can bind on
  DESIGN Open Question 3, and ADR-006 §H's "highest-risk boundary". The
  identifier this whole feature binds on is UNDOCUMENTED by the vendor. If
  it disappears or changes shape, every scenario in the other feature files
  still passes against a fixture while production is blind.

  These scenarios are the only ones that assert anything about the vendor's
  response shape. Everything else is written against the classification, not
  the payload.

  @real-io
  Scenario: A recorded response yields an identity and a roster from one read
    Given a recorded response captured from the live guild service
    When the response is read
    Then a guild identifier is present
      And a guild tag is present
      And a guild name is present
      And the member list is present
      And the identity and the roster come from that single read

  @real-io @error
  Scenario: A recorded response with the identifier removed is unverifiable, not a mismatch
    Given a recorded response with the guild identifier removed
    When the response is read
    Then the outcome is unverifiable
      And it is not a mismatch
      And no comparison against the guild tag is attempted

  @real-io @error
  Scenario Outline: A recorded response with a display field removed still yields an identity
    Given a recorded response with the <field> removed
    When the response is read
    Then a guild identifier is still present
      And the missing field is reported as absent rather than raising

    Examples:
      | field      |
      | guild tag  |
      | guild name |

  @real-io
  Scenario: The recorded response is the shape the roster reader used to fetch for itself
    Given the recorded response
    When the member identifiers are extracted from it
    Then they are the same set the previous roster reader would have produced

  @requires_external @kpi
  Scenario: The live guild service still returns an identifier for a real key
    Given a real guild key for a registered guild
    When the live guild service is asked for that guild
    Then a guild identifier is present in the response
      And it is stable across two consecutive reads
      And it equals the identity the guild is bound to

  @requires_external @error
  Scenario: The live identifier has not changed shape since it was recorded
    Given a real guild key and the recorded response captured earlier
    When the live guild service is asked for that guild
    Then the live response carries every field the recorded response carries
