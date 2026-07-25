@driving_port @kpi
Feature: Atomicity and startup probe — the data-loss trap is retired
  KPI-3: 100% of hourly auto_update writes transactional; 0 partial writes
  observable after a crash; 0 silent-empty reads; 0 JSON writes post-cutover
  from migrated paths. Covers US-008, US-010, ADR-006 D6/D8/D9.

  The startup probe (ADR-006 D8) is the Earned-Trust gate: it refuses to
  start the bot on a corrupted DB, a wrong Fernet key, a stale alembic
  version, or a read-only filesystem. The probe is SKIPPED when
  SCRAPCODE_REPO_BACKEND=json (the rollback path).

  @driving_port @real-io
  Scenario: Probe asserts WAL mode and the alembic version matches the compiled head
    Given a fresh SQLite database with the schema applied via alembic upgrade head
    When the operator runs the startup probe
    Then the probe asserts the journal mode is WAL
      And the probe asserts the alembic_version row matches the compiled head revision
      And the probe returns successfully

  @real-io @adapter-integration
  Scenario: Probe round-trips a known plaintext through Fernet
    Given a SCRAPCODE_DB_KEY configured in the environment
    When the operator runs the startup probe
    Then the probe round-trips a known plaintext through Fernet with the configured key
      And the round-trip succeeds before any real api_key is touched

  @infrastructure-failure @kpi
  Scenario: Probe refuses on a stale alembic version
    Given a SQLite database stamped at an older alembic revision than the compiled head
      And the ScrapCode environment is configured for "with-stale-config"
    When the operator runs the startup probe
    Then the probe refuses with a health.startup.refused event
      And the bot does not start on the half-migrated database
      And a structured log record names the stale-version step as the failure

  @infrastructure-failure
  Scenario: Probe refuses on a wrong Fernet key
    Given a SQLite database whose api_key columns were encrypted with a different SCRAPCODE_DB_KEY
    When the operator runs the startup probe with the current SCRAPCODE_DB_KEY
    Then the probe refuses at the Fernet round-trip step
      And the refusal is emitted as a health.startup.refused event before any real api_key is decrypted

  @infrastructure-failure
  Scenario: Probe refuses on a corrupted non-SQLite database file
    Given a path that points at a non-SQLite file containing the bytes "not a database"
    When the operator runs the startup probe
    Then the probe refuses with a health.startup.refused event
      And the refusal is emitted before the bot starts

  @infrastructure-failure
  Scenario: Probe refuses on a read-only filesystem
    Given a SQLite database on a read-only filesystem path
    When the operator runs the startup probe
    Then the probe refuses at the write-and-rollback step
      And the refusal names the read-only filesystem as the failure reason

  @infrastructure-failure @kpi @real-io
  Scenario: A crash mid-transaction leaves the database in the pre-cycle state
    Given a populated SQLite database with a captured pre-cycle row-count baseline
      And an hourly auto_update cycle is mid-transaction when the process is killed hard
    When the bot restarts and the operator re-reads the affected tables
    Then none of the partial upserts from that cycle are present
      And the row counts match the pre-cycle baseline
      And the data-loss trap is retired

  @property @real-io
  Scenario: The hourly auto_update write is one transaction per guild
    Given a running bot on the SQLite backend with multiple registered guilds
    When the hourly auto_update writes updates for two guilds in one cycle
    Then each guild's writes commit as a single transaction
      And a failure in guild B's writes does not roll back guild A's already-committed writes

  @driving_port
  Scenario: SCRAPCODE_REPO_BACKEND selects the live repository implementation
    Given the composition root reads SCRAPCODE_REPO_BACKEND from the environment
    When the operator starts the bot with SCRAPCODE_REPO_BACKEND=sqlite
    Then the live repo is the SQLite-backed repository and the probe runs
      But when the operator starts the bot with SCRAPCODE_REPO_BACKEND=json
    Then the live repo is the JSON-backed repository and the probe is skipped

  @infrastructure-failure
  Scenario: Missing SQLite file on startup falls back to JSON for one cycle
    Given SCRAPCODE_REPO_BACKEND=sqlite and the configured SQLite file does not exist
    When the operator starts the bot
    Then the bot logs a loud SQLite-missing warning
      And the live repo falls back to the JSON-backed repository for one cycle

  @kpi
  Scenario: Post-cutover grep finds zero JSON-write helpers in the retired modules
    Given the cutover commit is applied
    When the operator greps bot/tracker.py, bot/embeds.py, and bot/cogs/replay_cog.py for path.write_text, load_json, save_json, try_insert, and replay_index.json
    Then every grep returns zero matches in the migrated modules

  @driving_port @real-io
  Scenario: process_api_response writes to battle_hits and bomb_hits via the repository
    Given the SQLite singleton is active and 25 Tacticus entries arrive for an hourly update
    When the operator calls process_api_response with the entries
    Then battle_hits and bomb_hits rows are upserted for that server, guild, and season
      And no highest_hits_season_*.json or highest_bombs_season_*.json file is written to disk