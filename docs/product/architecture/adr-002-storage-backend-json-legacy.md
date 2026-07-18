# ADR-002: Storage backend is per-server JSON today; SQLite is the accepted successor

- **Status:** Accepted — as-built, retroactive
- **Date:** 2026-07-18 (recorded; decision predates this baseline)
- **Closes:** Gap 8 (seed ADR); Gap 1 (data layout) in decision form
- **Related:** [brief.md §4](brief.md#4-data-layout--storage-_closes-gap-1--decision-form-in-adr-002)

## Context

ScrapCode persists all cluster state as flat JSON files under
`clusters/{discord_server_id}/`, accessed through `JsonClusterRepository` in
`bot/repository.py` and the wrappers in `bot/guilds.py`. The layout is documented
exhaustively in [brief.md §4](brief.md#4-data-layout--storage-_closes-gap-1--decision-form-in-adr-002).

Two properties of the current backend make it a standing data-loss hazard:

1. **Writes are non-atomic** — `_write_json` does `path.write_text(json.dumps(...))`
   with no temp-file + `os.replace`.
2. **Reads are silent-empty-on-corruption** — `_read_json` (and `load_player_list`,
   and `tracker.load_json`) swallow all exceptions and return an empty dict, so a
   truncated file is indistinguishable from a freshly-initialized one.

A SQLite backend has been accepted as the successor that resolves both.

## Decision

1. **Current state = per-server JSON.** The `clusters/{id}/` layout, the
   `JsonClusterRepository` ABC, and the file schemas in brief §4 are the as-built
   storage layer. Nothing in this baseline changes them.
2. **JSON is legacy / a migration source.** The JSON files are the canonical input
   to a future SQLite migration. The schema and access patterns (per-server
   partitioning; the `guild_id` slug keys; the `player_list` `__meta__.version`
   scheme; the season-file shape) are what SQLite must preserve or explicitly
   supersede.
3. **Do not extend the JSON layer.** New persistence features should not grow new
   top-level JSON files or new silent-empty read helpers. The standing
   non-atomic + silent-empty trap is documented, **not fixed**, because the fix
   belongs to the SQLite migration, not to incremental doc-baseline work.
4. **The repository is already abstract.** `ClusterRepository` is an ABC; the
   concrete `JsonClusterRepository` is the only implementation. The successor
   lands as a second implementation behind the same interface.

## Consequences

- The data-loss trap (non-atomic writes + silent-empty reads) remains live until
  the SQLite migration ships. It is tracked separately and is **out of scope** for
  this documentation baseline.
- `bot/migrations/` already contains one structural migration
  (`to_cluster_layout.py`) and one runtime schema migrator
  (`player_list_migrations.py`); the SQLite migration joins this lineage.
- `update_channel_id` is written into `guilds.json` but is **unused at runtime**
  (the live update channel comes from `config.UPDATE_CHANNEL_ID`, not the stored
  field). A SQLite schema should drop or repurpose it deliberately.

## Alternatives considered

- **Fix the JSON atomicity now (write-temp + replace, surface corruption).**
  Rejected for this baseline: it is a behavior change, and the scope is
  documentation-only with zero behavior changes. The trap is documented instead.
- **A different document database (e.g. embedded Mongo-style).** Not warranted at
  this scale; SQLite matches the single-process, local-file deployment and the
  relational shape of the registry + registrations + season data.