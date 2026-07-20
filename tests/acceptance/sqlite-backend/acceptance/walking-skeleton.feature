@walking_skeleton @real_io @driving_port
Feature: Walking skeleton — operator migrates a JSON cluster tree to SQLite and renders the same leaderboards
  The thinnest end-to-end slice proving the backend swap is feasible:
  seed a JSON cluster tree → run the JSON→SQLite migration (subprocess) →
  construct the SQLite-backed repository → load guilds, the player list, and
  battle hits through the ClusterRepository ABC → render via the existing
  embeds.build_*_messages → assert the render matches the JSON-backed render.

  This is Strategy C (Real local). Adapters exercised with real I/O:
  real JSON in tmp_path, real subprocess migration, real SQLite file,
  real Fernet round-trip, real alembic upgrade head (clean env only).

  The with-stale-config variant asserts the startup probe REFUSES the
  stale-alembic-version DB (ADR-006 D8 step 2) — the bot does NOT start on
  a half-migrated DB.

  Scenario Outline: Operator migrates a JSON cluster tree to SQLite and renders the same leaderboards
    Given a JSON <env> cluster tree in a temporary working directory
      And the ScrapCode SQLite environment is configured for "<env>"
    When the operator runs the JSON-to-SQLite migration as a subprocess against the copy
      And the operator constructs the SQLite-backed repository against the resulting database
      And the operator loads the cluster's guilds, player list, and battle hits through the repository
      And the operator renders the Battle and Bomb leaderboards via the existing embeds builders
    Then the rendered leaderboards are non-empty
      And the rendered leaderboards match the JSON-backed render byte-for-byte
      And the startup probe succeeds on the resulting database
    Examples:
      | env                       |
      | clean                     |
      | with-existing-json-data   |

  @walking_skeleton @real_io @driving_port @infrastructure-failure
  Scenario: Operator migration refuses to start against a stale-config database
    Given a JSON with-existing-json-data cluster tree in a temporary working directory
      And the SQLite database is stamped at an older alembic revision than the compiled head
      And the ScrapCode SQLite environment is configured for "with-stale-config"
    When the operator constructs the SQLite-backed repository and runs the startup probe
    Then the probe refuses with a health.startup.refused event
      And the bot does not start on the half-migrated database