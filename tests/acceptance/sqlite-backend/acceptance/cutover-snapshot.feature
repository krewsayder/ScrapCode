@driving_port @kpi
Feature: Cutover render snapshot — existing commands are byte-identical pre- and post-cutover
  KPI-4: 0 baseline-snapshot diffs across all command groups. The closest
  testable proxy for the Discord commands (which are too heavy to drive
  live) is `embeds.build_battle_messages` / `build_bomb_messages` driven
  against the JSON-backed repo vs the SQLite-backed repo — byte-identical
  output for the same input (US-011, US-009, ADR-007 §3).

  @driving_port @kpi @real-io
  Scenario: Battle leaderboard render is byte-identical pre- and post-cutover
    Given a pre-cutover baseline render of the Battle leaderboard for guild "neuro", season 94, boss "Avatar"
      And the same data is loaded through the SQLite-backed repository
    When the operator renders the Battle leaderboard through the existing embeds builder against the SQLite-backed repo
    Then the rendered messages match the JSON-backed baseline byte-for-byte
      And the player display names and "(former)" suffixes are preserved

  @kpi @real-io
  Scenario: Bomb leaderboard render is byte-identical pre- and post-cutover
    Given a pre-cutover baseline render of the Bomb leaderboard for guild "neuro", season 94, boss "Avatar"
      And the same data is loaded through the SQLite-backed repository
    When the operator renders the Bomb leaderboard through the existing embeds builder against the SQLite-backed repo
    Then the rendered messages match the JSON-backed baseline byte-for-byte

  @kpi @real-io
  Scenario: Replay index render is byte-identical pre- and post-cutover
    Given a pre-cutover baseline render of the replay index for boss "Avatar", map "GB_Khaine_01"
      And the same replay entries are loaded through the SQLite-backed repository
    When the operator renders the replay index against the SQLite-backed repo
    Then the rendered index message matches the JSON-backed baseline byte-for-byte

  @edge @real-io
  Scenario: An empty leaderboard renders the same no-entries message pre- and post-cutover
    Given a guild with no battle hits for season 94, boss "Avatar"
    When the operator renders the leaderboard through the JSON-backed repo and the SQLite-backed repo
    Then both renders produce the same no-entries message
      And neither render raises on the empty state

  @edge @real-io
  Scenario: A player marked is_former renders the same "(former)" suffix pre- and post-cutover
    Given a player "Jonas Klein" with is_former true in the player list
    When the operator renders the Battle leaderboard through each repo
    Then both renders show the player as "Jonas Klein (former)" in the same position

  @driving_port @real-io
  Scenario: upload_replay writes a replay_entries row, not replay_index.json
    Given a Discord user in server 1458181638453203099 runs /upload_replay for boss "Avatar", map "GB_Khaine_01", url "https://replay.example/abc"
      And the SQLite-backed repository is the live repo
    When the command completes
    Then a replay_entries row exists with discord_server_id 1458181638453203099 and the given boss, map, and url
      And no write to replay_index.json occurred

  @infrastructure-failure
  Scenario: Duplicate upload URL in the same server, boss, and map is rejected
    Given a replay_entries row for the production server, boss "Avatar", map "GB_Khaine_01", url "https://replay.example/abc"
    When the same user runs /upload_replay again with the same url
    Then the user receives the existing duplicate-URL rejection reply
      And no new row is inserted

  @driving_port
  Scenario: delete_replay removes the row from replay_entries and re-renders the index
    Given a replay_entries row exists for the production server, boss "Avatar", map "GB_Khaine_01", url "https://replay.example/abc"
    When a user runs /delete_replay for that boss, map, and url
    Then the row is removed from replay_entries
      And the index message is re-rendered with the remaining entries

  @kpi
  Scenario: replay_index.json helpers and hardcoded forum constants are removed from replay_cog.py
    Given the cutover commit is applied
    When the operator greps bot/cogs/replay_cog.py for REPLAY_INDEX_FILE, load_replay_index, save_replay_index, replay_index.json, FORUM_CHANNELS, and MAP_THREADS
    Then every grep returns zero matches
      And the thread ids are looked up from the replay_threads table instead

  @property @real-io
  Scenario: The JSON tree is not modified after a successful cutover cycle
    Given a successful cutover cycle has run against the SQLite backend
      And the modification times of the clusters/ tree were captured before the cycle
    When the operator inspects the clusters/ tree modification times after the cycle
    Then no JSON file was modified during or after the cutover cycle
      And the JSON tree remains intact as the read-only rollback fallback