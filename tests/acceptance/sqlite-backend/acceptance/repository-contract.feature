@driving_port
Feature: Repository contract — the ClusterRepository ABC is the swap seam
  KPI-1: ≥17 contract tests, all green against BOTH JsonClusterRepository
  and SqlAlchemyClusterRepository (parametrized). The JSON impl defines the
  behavior; the SQLite impl must satisfy the same contract unchanged
  (ADR-002 / ADR-006 D2). Covers US-001, US-002, US-003, US-004, US-006.

  The ABC grows 4 storage-agnostic season-hit methods (ADR-007); both impls
  implement them so the contract tests stay parametrized. The JSON impl's
  silent-empty-on-corruption read is pinned as the trap Slice 02 retires.

  @kpi @driving_port
  Scenario: Every ClusterRepository ABC method round-trips through the repository
    Given a synthetic cluster with two guilds, registrations, capped state, a v2 player list, and live leaderboard config
    When the operator saves and loads each entity through the repository
    Then each method's return value equals the input that produced it
      And the round-trip holds for both the JSON-backed and the SQLite-backed repository

  @driving_port
  Scenario: The four new ABC season-hit methods round-trip battle and bomb hits
    Given a guild with one Battle hit and one Bomb hit for season 94
    When the operator upserts the hits and loads them back through the repository
    Then the loaded battle hits match the {"boss_hits": ...} shape the embeds consume
      And the loaded bomb hits match the same outer shape with bomb entries
      And both impls return the same dict for the same input

  Scenario: Player list v2 round-trips without invoking the migrator
    Given a player_list.json already at version 2 with one player
    When the operator loads the player list through the repository
    Then the returned dict equals the file content exactly
      And no migrator rewrite occurred

  @driving_port
  Scenario: load_player_list returns the v2 dict shape cogs expect
    Given a players table with one player in guild "neuro"
    When the operator loads the player list through the SQLite-backed repository
    Then the returned dict has the {"__meta__": {"version": 2}, "players": {...}} shape
      And cog code calling .get("players", {}) works unchanged

  Scenario: list_server_ids enumerates only the registered servers
    Given a storage backing with two registered servers and a stray non-numeric entry
    When the operator lists the server ids
    Then the return is the two numeric server ids, as ints

  @infrastructure-failure
  Scenario: Silent-empty-on-corruption is pinned as the trap to retire
    Given a guilds.json file containing the truncated bytes "{broken"
    When the operator loads the cluster through the JSON-backed repository
    Then the call returns an empty Cluster with no guilds and no exception
      And the scenario is annotated as the behavior Slice 02 retires

  @infrastructure-failure @real-io
  Scenario: Corrupted SQLite database raises instead of silently returning empty
    Given a path that points at a non-SQLite file containing the bytes "not a database"
    When the operator constructs the SQLite-backed repository and loads a cluster
    Then the call raises rather than silently returning an empty Cluster
      And the silent-empty-on-corruption trap is retired on the SQLite path

  @edge
  Scenario: An empty SQLite database returns empty dicts without raising
    Given a fresh SQLite database with the schema applied and no rows
    When the operator loads each entity through the SQLite-backed repository
    Then every load returns the empty-shaped dict or empty Cluster
      And no call raises on the legit-empty state

  @real-io @adapter-integration
  Scenario: api_key is encrypted at rest and decrypted on read
    Given a SCRAPCODE_DB_KEY configured in the environment
      And a guild saved through the SQLite-backed repository with api_key "tacticus-neuro-key"
    When the operator queries the guilds.api_key column directly via sqlite3
    Then the stored value is Fernet ciphertext, not the plaintext
      And loading the guild through the repository returns the plaintext "tacticus-neuro-key"

  @infrastructure-failure
  Scenario: player_registrations api_key uniqueness is enforced
    Given a player_registrations row for Discord user "123456789" with api_key "shared-key"
    When a second row for Discord user "987654321" is inserted with the same api_key
    Then the insert fails with a unique-constraint violation on the api_key binding

  @infrastructure-failure
  Scenario: role_tiers check constraint rejects an invalid tier
    Given the role_tiers table created by the baseline migration
    When the operator inserts a row with tier "superuser"
    Then the insert fails with a check-constraint violation
      And only "admin" and "officer" are valid tiers

  @edge
  Scenario: A guild with an empty api_key is allowed and round-trips
    Given a cluster with a guild whose api_key is the empty string
    When the operator saves and loads the cluster through the repository
    Then the round-tripped guild has api_key ""
      And both impls preserve the empty-string value

  @property
  Scenario: PlayerListMigrator v1→v2 inverts with the epoch sentinel; v2 is a noop
    Given a v1 player list mapping display names to tacticus ids
    When the operator runs the migrator on the v1 input
    Then the result is keyed by tacticus_user_id with last_validated "1970-01-01T00:00:00Z" and is_former false
      And re-running the migrator on a v2 input is a noop that returns was_migrated false

  @property
  Scenario: try_insert dedup branches are pinned as the contract the SQL upsert must preserve
    Given the four try_insert branches (same-roster-equal, same-roster-higher, different-roster, top-N-truncate)
    When the operator exercises each branch against the in-memory dedup
    Then same-roster-equal keeps the first entry
      And same-roster-higher replaces with the higher damage
      And different-roster inserts as a separate entry
      And top-N truncation drops the lowest when a higher hit arrives

  @property @real-io
  Scenario: Upsert keep-max on battle_hits preserves the try_insert contract in SQL
    Given battle_hits rows for season 94, boss "Avatar", encounter 0, tier "Legendary_0", player uid-001
    When the operator upserts a same-roster higher-damage entry, a same-roster lower-damage entry, and a different-roster entry
    Then the same-roster higher-damage row replaces the existing row to the higher value
      And the same-roster lower-damage row leaves the row at the higher value
      And the different-roster row is inserted as a separate row
      And the read query orders by damage desc then completed_on asc, limited to TOP_N
      And bomb_hits has no roster dedup and reads as a plain top-N

  @kpi
  Scenario: battle_hits_simple is dropped from the schema and from tracker.py
    Given the cutover commit is applied
    When the operator greps bot/tracker.py for BATTLE_SIMPLE_FILE and the schema for battle_hits_simple
    Then there is no battle_hits_simple table in the SQLite schema
      And there is no save_json(BATTLE_SIMPLE_FILE, ...) line in bot/tracker.py