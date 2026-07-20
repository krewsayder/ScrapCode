@driving_port @kpi
Feature: JSON→SQLite data migration — row-count parity is the cutover gate
  KPI-2: 100% row-count parity across all easy-entity tables +
  battle_hits + bomb_hits + replay_entries + replay_threads. The parity
  report is the artifact Slice 04's cutover depends on (US-005, US-007,
  ADR-006 D10/D11/D12). The migration ALWAYS runs against a COPY of the
  production clusters/ tree (DEVOPS constraint); it never reads
  /opt/discord-bot/clusters/ directly.

  @kpi @driving_port @real-io
  Scenario: Easy-entity row counts match JSON-derived counts exactly
    Given a copy of a realistic clusters/ tree in a temporary working directory
    When the operator runs the JSON-to-SQLite migration as a subprocess
    Then every easy-entity table's SQL row count equals its JSON-derived count
      And the parity report is written with overall status "PASS"
      And the migration subprocess exits 0

  @real-io
  Scenario: v1 player_list files are inverted to v2 exactly once
    Given the copied clusters tree contains a v1 player_list.json keyed by display name
    When the operator runs the migration
    Then the players table rows for that guild are keyed by tacticus_user_id
      And the rows have last_validated "1970-01-01T00:00:00Z" and is_former false
      And re-running the migration is a noop

  @real-io @adapter-integration
  Scenario: api_key values are encrypted on insert, not stored as plaintext
    Given the copied clusters tree has a guilds.json with api_key "tacticus-neuro-key"
    When the operator runs the migration
    Then the guilds.api_key column for that guild contains Fernet ciphertext
      And the plaintext "tacticus-neuro-key" does not appear in the database file

  @infrastructure-failure
  Scenario: Parity mismatch fails the migration loudly
    Given a synthetic guilds.json with two guilds whose slugs collide after normalization
    When the operator runs the migration
    Then the migration exits non-zero
      And the parity report shows guilds as "MISMATCH"
      And the overall status is "FAIL"
      And the SQLite file is left in a rolled-back uncommitted state

  @property
  Scenario: The migration is idempotent
    Given a clusters tree copy and a successfully completed first migration run
    When the operator runs the migration a second time against the same source and database
    Then the per-table row counts are unchanged
      And the parity report still shows overall "PASS"
      And the subprocess still exits 0

  @kpi @real-io
  Scenario: All existing replay entries are assigned to the production server
    Given the copied replay_index.json has entries across multiple bosses and maps
    When the operator runs the migration
    Then every replay_entries row has discord_server_id 1458181638453203099
      And the replay_entries row count equals the JSON entry count

  @infrastructure-failure @property
  Scenario: URL uniqueness is scoped per server, boss, and map
    Given a replay_entries row for server 1458181638453203099, boss "Avatar", map "GB_Khaine_01", url "https://replay.example/abc"
    When the operator inserts a second row with the same server, boss, map, and url
    Then the insert fails with a unique-constraint violation
      But a row with the same url under a different discord_server_id inserts successfully

  @real-io
  Scenario: replay_threads rows are seeded from the hardcoded forum and map constants
    Given the cutover commit is applied and the migration has run
    When the operator inspects the replay_threads table
    Then there is one row per (boss, map_name) previously hardcoded in replay_cog.py
      And each row carries the production server id and the original index_message_id where present

  @infrastructure-failure
  Scenario: Migration against a missing source directory fails loudly
    Given a --source path that does not exist
    When the operator runs the migration
    Then the migration exits non-zero
      And the error output names the missing source path
      And no SQLite database file is created at the --db path

  @edge
  Scenario: Migration with an empty clusters tree produces an empty SQLite file with the schema
    Given a --source path that exists but contains no server directories
    When the operator runs the migration
    Then the migration exits 0
      And the parity report shows every table at JSON=0 SQL=0 PASS
      And the SQLite file has the full schema applied (alembic_version populated)