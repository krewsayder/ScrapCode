# Slice 07 — Composition-root integrity
#
# Scenario SSOT for `test_slice_07_composition_root_integrity.py`.
# Remediates ADR-006 D8, ADR-006 D9, DDD-3.
#
# Operator decision, 2026-08-02: a bad .env takes the bot down. Hard stop,
# not degraded-but-running. "Boot but disable auto_update" was considered and
# rejected — a bot that is up but not ingesting looks healthy to everyone
# except the person reading the log, which is the same reassuring-but-false
# signal this feature exists to remove.

Feature: The bot refuses to run in a configuration where quarantine is inert
  As the operator who will be paged when ingestion stops
  I want a broken deploy to fail loudly at startup instead of quietly at runtime
  So that "the bot is up" and "the bot is protecting the data" mean the same thing

  @error @driving_port
  Scenario: A missing encryption key stops the bot and names itself
    Given the storage backend is configured as the database
    And the database encryption key is not set
    When the bot builds its storage
    Then the bot refuses to start
    And the failure names the missing setting

  @error @driving_port
  Scenario: A malformed encryption key stops the bot at startup, not mid-cycle
    Given the storage backend is configured as the database
    And the database encryption key has a trailing carriage return
    When the bot builds its storage
    Then the bot refuses to start
    And the failure names the malformed setting

  @error @driving_port
  Scenario: A missing database file stops the bot
    Given the storage backend is configured as the database
    And the database file is gone from a directory that exists
    When the bot builds its storage
    Then the bot refuses to start

  @driving_port
  Scenario: A deliberate rollback still starts, and says what it gives up
    Given the storage backend is deliberately configured as files
    When the bot builds its storage
    Then the bot starts
    And it announces that the guild key guard is inert

  @error @driving_port
  Scenario: The startup health check is actually run
    Given the bot's startup sequence
    Then the storage health check has a production caller

  @error
  Scenario: The direct-key-read rule covers every module, not two directories
    Given every module under the bot package
    Then no module outside the sanctioned readers reads a guild key directly
