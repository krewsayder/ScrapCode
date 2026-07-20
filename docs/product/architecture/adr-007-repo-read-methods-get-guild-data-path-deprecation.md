# ADR-007: Extend ClusterRepository ABC with season-hit read methods; deprecate `get_guild_data_path`

- **Status:** Accepted — DESIGN wave, feature `sqlite-backend`
- **Date:** 2026-07-18
- **Depends on:** [ADR-006](adr-006-sqlite-storage-backend.md) (this ADR
  records a contradiction the DESIGN wave surfaced and that ADR-006 depends
  on being resolved)
- **Related:** [brief.md §4](brief.md#4-data-layout--storage-_closes-gap-1--decision-form-in-adr-002),
  [data-dictionary.md §2.7](data-dictionary.md#27-battle-detailed--clustersidguildiddatahighest_hits_season_njson),
  [data-dictionary.md §2.9](data-dictionary.md#29-bombs--clustersidguildiddatahighest_bombs_season_njson)

## Context

The DESIGN-wave codebase audit (grep `get_guild_data_path|load_leaderboard_file|data_dir`
across `bot/`) surfaced a contradiction in the architecture baseline:

> **brief.md §4** says: "*`JsonClusterRepository` is the only reader/writer
> of the per-server tree except `bot/tracker.py`, which reads/writes the
> season files directly under `{guild_id}/data/`. `replay_cog.py` is the
> only reader/writer of `replay_index.json`.*"

> **Reality:** the season files are ALSO read directly by
> `bot/embeds.py::load_leaderboard_file`, called from `bot/cogs/view_cog.py`
> (lines 54, 104, 153), `bot/cogs/admin_cog.py` (lines 330, 408), and
> `bot/cogs/tasks_cog.py` (lines 292, 312). Each caller obtains the data
> directory via `repo.get_guild_data_path(server_id, guild_id)` and then
> reads the JSON file directly. Five call sites bypass the repo for season-
> file reads — not one.

The data-dictionary §2.7 / §2.9 ARE correct ("Readers: `tracker.load_json`,
embeds"). The contradiction is between brief §4's prose and the
data-dictionary + the code; brief §4 undercounts the bypass.

**Consequence for the feature:** DISCUSS US-008 only addresses the
`tracker.py` WRITE side of the season-file bypass. The READ side (cogs →
`get_guild_data_path` → `embeds.load_leaderboard_file`) is undocumented in
DISCUSS and, if left untouched, would keep reading JSON files off the
filesystem after the singleton flip — the cutover (US-010 / US-011) would
NOT be complete and the data-loss trap would persist on the read path
(`load_leaderboard_file` returns `None, "Leaderboard file is corrupted."`
on a truncated file — a milder variant of the silent-empty trap, but still
a filesystem-coupled read that the SQLite migration must retire).

The orchestrator's framing of deferred decision #3 ("`get_guild_data_path`
… returns a filesystem dir used only by `tracker.py`'s direct JSON I/O
(which Slice 04 retires)") inherits the brief §4 undercount and is
therefore incorrect. This ADR records the corrected disposition.

## Decision

### 1. Extend the `ClusterRepository` ABC with season-hit read + write methods

Four new methods on `bot/repository.py::ClusterRepository`:

| Method | Returns | Notes |
|--------|---------|-------|
| `load_battle_hits(discord_server_id, guild_id, season) -> dict` | `{"boss_hits": {boss_id: {encounter_index: {tier_key: [entries]}}}}` — the exact shape `bot/embeds.build_battle_messages` and `bot/tracker.process_api_response` consume today. | Read path for battle detailed. |
| `load_bomb_hits(discord_server_id, guild_id, season) -> dict` | Same outer shape; entries are the bomb form (no roster). | Read path for bombs. |
| `upsert_battle_hits(discord_server_id, guild_id, season, entries) -> None` | None. | Write path; replaces `tracker.process_api_response`'s in-memory `try_insert` + `save_json`. Enforces the `(server, guild, season, boss, encounter, tier, roster_key, user_id)` unique constraint with upsert-keep-max(damage) (data-dictionary §2.7; US-006). |
| `upsert_bomb_hits(discord_server_id, guild_id, season, entries) -> None` | None. | Write path; plain top-N (no roster dedup) per data-dictionary §2.9. The top-N limit is enforced on READ (`ORDER BY damage DESC, completed_on ASC LIMIT 5` per partition), not on write. |

The dict shape is the existing `boss_hits` shape — `build_battle_messages` /
`build_bomb_messages` are unchanged; only the source of the dict changes
(repo vs `load_leaderboard_file`). This keeps cogs untouched.

`JsonClusterRepository` implements these by reading/writing the existing
JSON files (so the contract tests stay parametrized against both impls and
the rollback path is real). `SqlAlchemyClusterRepository` implements them
against `battle_hits` / `bomb_hits` (ADR-006 D3).

### 2. Deprecate then remove `get_guild_data_path`

`get_guild_data_path` returns a filesystem directory — a JSON-specific
concept the SQLite impl has no analog for. Disposition:

- **Slice 02:** `SqlAlchemyClusterRepository.get_guild_data_path` RAISES
  `NotImplementedError("get_guild_data_path is JSON-only; use
  load_battle_hits / load_bomb_hits")`. No caller reaches it through the
  SQLite impl yet (the singleton is still JSON).
- **Slice 04 (alongside US-008 / US-009):** the 4 cog read sites
  (`view_cog`, `admin_cog`, `tasks_cog`) and `embeds.load_leaderboard_file`
  are rewired to call `repo.load_battle_hits` / `repo.load_bomb_hits`. The
  `data_dir` parameter is removed from `tracker.process_api_response`
  (US-008 already requires this). Then `get_guild_data_path` is removed
  from the ABC and from `JsonClusterRepository`.
- **Post-cutover:** grep for `get_guild_data_path` in `bot/` returns 0
  matches. The Slice-01 contract test that pinned `get_guild_data_path` is
  updated to assert the method is gone (or the test is removed with a
  `wave-decisions.md` note).

This is an interface change to a backend-agnostic ABC. It is justified
because the existing method is JSON-specific (it leaks the storage medium
through the port — a violation of ports-and-adapters), and because the
SQLite impl cannot honor it without inventing a filesystem layout that
serves no purpose. Keeping it as a deprecated JSON-only method would leave
a footgun in the port.

### 3. Remove `embeds.load_leaderboard_file`

`load_leaderboard_file` reads JSON from a `pathlib.Path`. Once cogs read
season data from the repo, this helper has no callers. It is removed in
Slice 04. Its per-failure-mode error strings (`"No data file found."`,
`"Leaderboard file is corrupted."`) are retired: the repo's
`load_battle_hits` / `load_bomb_hits` return an empty `{"boss_hits": {}}`
when the partition has no rows (matching the cog's existing "no entries"
message), and a corrupted DB raises (ADR-006 D8 probe) instead of returning
a "corrupted" string. The "corrupted" string was only reachable on the
JSON path; it has no SQLite analog.

### 4. Correct brief §4

The brief §4 prose is corrected in the `## Application Architecture —
sqlite-backend` section appended to `brief.md` (this DESIGN wave). The
as-built baseline text in brief §4 is left intact (it is an as-built
snapshot; per the baseline's status banner, contradictions are flagged,
not rewritten); the new section records the corrected count and points
here.

## Consequences

- **Positive:** the read-side bypass is closed in the same slice as the
  write-side bypass (Slice 04). The cutover is complete: no cog reads or
  writes season files via `pathlib.Path` after Slice 04. The data-loss
  trap is retired on both sides.
- **Positive:** the ABC's surface area shrinks (one JSON-specific method
  removed) and grows by 4 storage-medium-agnostic methods. The port is
  cleaner post-migration.
- **Negative:** the ABC change is a DISCUSS-unscooped interface expansion.
  `JsonClusterRepository` must implement 4 new methods (the JSON read/write
  equivalents of the SQL upsert) so the parametrized contract tests stay
  green on both impls. This is real Slice-02 work in addition to
  `SqlAlchemyClusterRepository`. Recorded as an upstream change in
  `wave-decisions.md`.
- **Negative:** US-008's scope expands. US-008 today says "Rewrite
  `process_api_response` to read/write via the repo" — it now ALSO must
  rewire the 4 cog read sites + `embeds.load_leaderboard_file`. Recorded
  in `wave-decisions.md` so the orchestrator can update US-008's AC if
  desired.
- **Trade-off:** keeping `load_leaderboard_file` as a thin shim that
  calls the repo would be a smaller diff but would leave a redundant
  helper. Removal is the cleaner end state; the cog rewires are small.

## Alternatives considered

- **Keep `get_guild_data_path` on the ABC, SQLite impl returns a sentinel.
  ** Rejected: the method is JSON-specific (returns a filesystem dir); a
  sentinel / `NotImplementedError` in the SQLite impl is a footgun that
  violates the Liskov substitutability the ABC is supposed to guarantee.
  The whole point of the ABC swap (US-004) is that both impls satisfy the
  same contract.
- **Make `SqlAlchemyClusterRepository.get_guild_data_path` return a
  synthesized dir.** Rejected: inventing a filesystem layout to satisfy a
  leaky port is architectural dishonesty. Fix the port.
- **Leave the cog read-side bypass on JSON after the cutover.** Rejected:
  the data-loss trap persists on the read path (`load_leaderboard_file`
  returns a "corrupted" string on a truncated file — a milder silent-fail
  than the repo's empty dict, but still filesystem-coupled). The trap is
  only retired when the read path is also transactional.
- **Defer this ADR to a post-migration refactor.** Rejected: the
  contradiction is discovered now, and the cutover (Slice 04) is the
  cheapest moment to fix it — the cogs are already being rewired for
  US-008 / US-009. A second pass later would re-touch the same code.