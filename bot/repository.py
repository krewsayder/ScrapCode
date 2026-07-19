import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from bot.models import Cluster, Guild
from bot.migrations.player_list_migrations import PlayerListMigrator

# ADR-007: the ABC carries 4 storage-medium-agnostic season-hit read/write
# methods. `get_guild_data_path` (JSON-specific — returned a filesystem dir)
# was removed from the ABC in Slice 04 once the 4 cog read sites +
# `embeds.load_leaderboard_file` were rewired to `load_battle_hits` /
# `load_bomb_hits` (US-008 / 04-02). The dict shape returned by the load_*
# methods is the existing `{"boss_hits": ...}` shape that
# `bot/embeds.build_battle_messages` / `build_bomb_messages` and
# `bot/tracker.process_api_response` consume today (data-dictionary §2.7/§2.9).


class ClusterRepository(ABC):
    @abstractmethod
    def load(self, discord_server_id: int) -> Cluster: ...

    @abstractmethod
    def save(self, cluster: Cluster) -> None: ...

    @abstractmethod
    def load_player_registrations(self, discord_server_id: int) -> dict: ...

    @abstractmethod
    def save_player_registrations(self, discord_server_id: int, data: dict) -> None: ...

    @abstractmethod
    def load_capped_state(self, discord_server_id: int) -> dict: ...

    @abstractmethod
    def save_capped_state(self, discord_server_id: int, data: dict) -> None: ...

    @abstractmethod
    def load_player_list(self, discord_server_id: int, guild_id: str) -> dict: ...

    @abstractmethod
    def save_player_list(self, discord_server_id: int, guild_id: str, data: dict) -> None: ...

    @abstractmethod
    def load_live_leaderboards(self, discord_server_id: int) -> dict: ...

    @abstractmethod
    def save_live_leaderboards(self, discord_server_id: int, data: dict) -> None: ...

    @abstractmethod
    def list_server_ids(self) -> list[int]: ...

    # --- ADR-007: storage-medium-agnostic season-hit read/write methods ---

    @abstractmethod
    def load_battle_hits(self, discord_server_id: int, guild_id: str, season: int) -> dict:
        """Return `{"boss_hits": {boss_id: {encounter_index: {tier_key: [entries]}}}}`
        — the exact shape `bot/embeds.build_battle_messages` and
        `bot/tracker.process_api_response` consume today (data-dictionary §2.7).
        """

    @abstractmethod
    def load_bomb_hits(self, discord_server_id: int, guild_id: str, season: int) -> dict:
        """Return `{"boss_hits": ...}` with the bomb entry shape (data-dictionary §2.9)."""

    @abstractmethod
    def upsert_battle_hits(self, discord_server_id: int, guild_id: str, season: int,
                           entries: list[dict]) -> None:
        """Upsert Battle hit entries with per-player-per-roster dedup
        (keep-max(damage)). Replaces `bot/tracker.try_insert(check_roster=True)`
        + `save_json` (ADR-006 D4 / ADR-007 / US-006)."""

    @abstractmethod
    def upsert_bomb_hits(self, discord_server_id: int, guild_id: str, season: int,
                         entries: list[dict]) -> None:
        """Upsert Bomb hit entries with plain top-N (no roster dedup)
        (data-dictionary §2.9 / US-006)."""

    # --- ADR-007-pattern replay methods (added in 04-03; ADR-006 D10/D11) ---
    # The replay cog routes through these instead of replay_index.json +
    # hardcoded FORUM_CHANNELS/MAP_THREADS. Per-tenant URL uniqueness is
    # enforced on (discord_server_id, boss, map_name, url) — the global
    # uniqueness leak (ADR-004 §3) is closed. Thread IDs come from
    # replay_threads (seeded in 03-03), closing the hardcoded-thread-ID leak.

    @abstractmethod
    def load_replay_entries(self, discord_server_id: int, boss: str, map_name: str) -> list[dict]:
        """Return the replay entries for (server, boss, map_name) in insertion
        order — the shape `build_index_message` consumes (data-dictionary §2.10)."""

    @abstractmethod
    def upsert_replay_entry(self, discord_server_id: int, boss: str, map_name: str,
                            entry: dict) -> None:
        """Insert one replay entry. Raises `DuplicateReplayUrlError` when the
        URL already exists for (server, boss, map_name) (ADR-006 D11)."""

    @abstractmethod
    def delete_replay_entry(self, discord_server_id: int, boss: str, map_name: str,
                            url: str) -> bool:
        """Delete the entry with matching URL. Return True if a row was removed,
        False if no matching entry existed."""

    @abstractmethod
    def get_replay_thread(self, discord_server_id: int, boss: str, map_name: str) -> dict | None:
        """Return `{"forum_channel_id", "thread_id", "index_message_id"}` for
        the (server, boss, map_name) thread, or None if no such thread is
        registered (ADR-006 D10)."""

    @abstractmethod
    def set_replay_thread_index_message(self, discord_server_id: int, boss: str,
                                         map_name: str, index_message_id: int) -> None:
        """Record the Discord message id of the forum-thread index message for
        (server, boss, map_name) so subsequent renders edit it in place."""

    @abstractmethod
    def list_replay_threads(self, discord_server_id: int) -> dict:
        """Return `{boss: {map_name: {"forum_channel_id", "thread_id"}}}` for
        every registered (boss, map_name) — drives boss/map autocomplete."""


class DuplicateReplayUrlError(Exception):
    """Raised by `upsert_replay_entry` when (server, boss, map_name, url) is
    already present (ADR-006 D11 per-tenant URL uniqueness). Carries the
    (boss, map_name) so the cog can render the byte-for-byte duplicate reply."""
    def __init__(self, boss: str, map_name: str, url: str):
        self.boss = boss
        self.map_name = map_name
        self.url = url
        super().__init__(f"Duplicate replay URL for {boss!r}/{map_name!r}: {url!r}")


class JsonClusterRepository(ClusterRepository):
    def __init__(self, base_path: Path = Path("clusters")):
        self._base = base_path

    def _server_path(self, discord_server_id: int) -> Path:
        path = self._base / str(discord_server_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _guild_path(self, discord_server_id: int, guild_id: str) -> Path:
        path = self._server_path(discord_server_id) / guild_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _read_json(self, path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_json(self, path: Path, data: dict) -> None:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def load(self, discord_server_id: int) -> Cluster:
        guilds_file = self._server_path(discord_server_id) / "guilds.json"
        raw = self._read_json(guilds_file)
        if not raw:
            return Cluster(discord_server_id=discord_server_id)

        guilds = {
            guild_id: Guild(
                id=guild_id,
                name=data["name"],
                api_key=data.get("api_key", ""),
                role_id=data.get("role_id", 0),
                notification_channel_id=data.get("notification_channel_id"),
                member_role_ids=data.get("member_role_ids", []),
            )
            for guild_id, data in raw.get("guilds", {}).items()
        }

        return Cluster(
            discord_server_id=discord_server_id,
            guilds=guilds,
            update_channel_id=raw.get("update_channel_id"),
            role_tiers=raw.get("role_tiers", {}),
        )

    def save(self, cluster: Cluster) -> None:
        guilds_file = self._server_path(cluster.discord_server_id) / "guilds.json"
        self._write_json(guilds_file, {
            "update_channel_id": cluster.update_channel_id,
            "role_tiers":        cluster.role_tiers,
            "guilds": {
                guild_id: {
                    "name":                    g.name,
                    "api_key":                 g.api_key,
                    "role_id":                 g.role_id,
                    "notification_channel_id": g.notification_channel_id,
                    "member_role_ids":         g.member_role_ids,
                }
                for guild_id, g in cluster.guilds.items()
            },
        })

    def load_player_registrations(self, discord_server_id: int) -> dict:
        path = self._server_path(discord_server_id) / "player_registrations.json"
        return self._read_json(path)

    def save_player_registrations(self, discord_server_id: int, data: dict) -> None:
        path = self._server_path(discord_server_id) / "player_registrations.json"
        self._write_json(path, data)

    def load_capped_state(self, discord_server_id: int) -> dict:
        path = self._server_path(discord_server_id) / "capped_state.json"
        return self._read_json(path)

    def save_capped_state(self, discord_server_id: int, data: dict) -> None:
        path = self._server_path(discord_server_id) / "capped_state.json"
        self._write_json(path, data)

    def load_player_list(self, discord_server_id: int, guild_id: str) -> dict:
        path = self._guild_path(discord_server_id, guild_id) / "player_list.json"
        if not path.exists():
            return {"__meta__": {"version": PlayerListMigrator.CURRENT_VERSION}, "players": {}}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            data, was_migrated = PlayerListMigrator.migrate(raw)
            if was_migrated:
                path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            return data
        except Exception:
            return {"__meta__": {"version": PlayerListMigrator.CURRENT_VERSION}, "players": {}}

    def save_player_list(self, discord_server_id: int, guild_id: str, data: dict) -> None:
        path = self._guild_path(discord_server_id, guild_id) / "player_list.json"
        self._write_json(path, data)

    def load_live_leaderboards(self, discord_server_id: int) -> dict:
        path = self._server_path(discord_server_id) / "live_leaderboards.json"
        return self._read_json(path)

    def save_live_leaderboards(self, discord_server_id: int, data: dict) -> None:
        path = self._server_path(discord_server_id) / "live_leaderboards.json"
        self._write_json(path, data)

    def list_server_ids(self) -> list[int]:
        if not self._base.exists():
            return []
        return [
            int(d.name)
            for d in self._base.iterdir()
            if d.is_dir() and d.name.isdigit()
        ]

    # --- ADR-007: JSON-backed impls of the 4 new ABC methods ---
    # These keep the parametrized contract tests green against the JSON impl
    # (rollback path / `SCRAPCODE_REPO_BACKEND=json`) and preserve the
    # existing on-disk shape (data-dictionary §2.7 / §2.9).
    # `get_guild_data_path` was removed from the ABC in Slice 04 (ADR-007 §2);
    # the JSON impl inlines the data-dir path here instead.

    def _season_file(self, discord_server_id: int, guild_id: str, season: int,
                     kind: str) -> Path:
        data_dir = self._guild_path(discord_server_id, guild_id) / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        if kind == "battle":
            return data_dir / f"highest_hits_season_{season}.json"
        if kind == "bomb":
            return data_dir / f"highest_bombs_season_{season}.json"
        raise ValueError(f"unknown season-file kind: {kind}")

    def load_battle_hits(self, discord_server_id: int, guild_id: str, season: int) -> dict:
        return self._read_json(self._season_file(discord_server_id, guild_id, season, "battle")) \
            or {"boss_hits": {}}

    def load_bomb_hits(self, discord_server_id: int, guild_id: str, season: int) -> dict:
        return self._read_json(self._season_file(discord_server_id, guild_id, season, "bomb")) \
            or {"boss_hits": {}}

    def upsert_battle_hits(self, discord_server_id: int, guild_id: str, season: int,
                           entries: list[dict]) -> None:
        # Lazy import to avoid a circular dependency at module import time
        # (tracker imports repository; repository does not import tracker).
        from bot.tracker import try_insert
        path = self._season_file(discord_server_id, guild_id, season, "battle")
        data = self._read_json(path) or {"boss_hits": {}}
        boss_hits = data.setdefault("boss_hits", {})
        for entry in entries:
            boss_id = str(entry["unitId"])
            e_index = str(entry.get("encounterIndex", 0))
            tier_key = entry["tier_key"]
            detailed = {
                "encounterType":   entry.get("encounterType"),
                "damage":           entry["damage"],
                "user_id":          entry["userId"],
                "completed_on":      entry["completedOn"],
                "hero_details":      entry.get("heroDetails", []),
                "machine_of_war":   entry.get("machineOfWarDetails"),
            }
            tier_list = (boss_hits.setdefault(boss_id, {})
                         .setdefault(e_index, {})
                         .setdefault(tier_key, []))
            try_insert(tier_list, detailed, check_roster=True)
        self._write_json(path, {"boss_hits": boss_hits})

    def upsert_bomb_hits(self, discord_server_id: int, guild_id: str, season: int,
                         entries: list[dict]) -> None:
        from bot.tracker import try_insert
        path = self._season_file(discord_server_id, guild_id, season, "bomb")
        data = self._read_json(path) or {"boss_hits": {}}
        boss_hits = data.setdefault("boss_hits", {})
        for entry in entries:
            boss_id = str(entry["unitId"])
            e_index = str(entry.get("encounterIndex", 0))
            tier_key = entry["tier_key"]
            bomb_entry = {
                "encounterType": entry.get("encounterType"),
                "damage":         entry["damage"],
                "user_id":         entry["userId"],
                "completed_on":    entry["completedOn"],
            }
            tier_list = (boss_hits.setdefault(boss_id, {})
                         .setdefault(e_index, {})
                         .setdefault(tier_key, []))
            try_insert(tier_list, bomb_entry, check_roster=False)
        self._write_json(path, {"boss_hits": boss_hits})

    # --- ADR-007-pattern replay impls (04-03). The JSON impl reads/writes
    # the existing `replay_index.json` at the project root (`self._base.parent`)
    # so the rollback path stays real. Thread IDs are NOT in the JSON shape
    # (they were hardcoded constants pre-cutover); the JSON impl returns None
    # for forum_channel_id/thread_id — the SQLite impl returns replay_threads
    # rows. This is an acceptable rollback degradation (ADR-006 D9).
    def _replay_index_file(self) -> Path:
        return self._base.parent / "replay_index.json"

    def load_replay_entries(self, discord_server_id: int, boss: str, map_name: str) -> list[dict]:
        data = self._read_json(self._replay_index_file())
        return list(data.get(boss, {}).get(map_name, {}).get("entries", []))

    def upsert_replay_entry(self, discord_server_id: int, boss: str, map_name: str,
                            entry: dict) -> None:
        path = self._replay_index_file()
        data = self._read_json(path)
        # Global duplicate-URL scan (pre-cutover semantics for rollback fidelity).
        for b, maps in data.items():
            for m, mdata in maps.items():
                for existing in mdata.get("entries", []):
                    if existing.get("url") == entry.get("url"):
                        raise DuplicateReplayUrlError(b, m, entry["url"])
        map_data = (data.setdefault(boss, {})
                    .setdefault(map_name, {"index_message_id": None, "entries": []}))
        map_data["entries"].append(entry)
        self._write_json(path, data)

    def delete_replay_entry(self, discord_server_id: int, boss: str, map_name: str,
                            url: str) -> bool:
        path = self._replay_index_file()
        data = self._read_json(path)
        map_data = data.get(boss, {}).get(map_name)
        if not map_data:
            return False
        entries = map_data.get("entries", [])
        for i, e in enumerate(entries):
            if e.get("url") == url:
                del entries[i]
                self._write_json(path, data)
                return True
        return False

    def get_replay_thread(self, discord_server_id: int, boss: str, map_name: str) -> dict | None:
        data = self._read_json(self._replay_index_file())
        map_data = data.get(boss, {}).get(map_name)
        if map_data is None:
            return None
        return {
            "forum_channel_id": None,
            "thread_id": None,
            "index_message_id": map_data.get("index_message_id"),
        }

    def set_replay_thread_index_message(self, discord_server_id: int, boss: str,
                                         map_name: str, index_message_id: int) -> None:
        path = self._replay_index_file()
        data = self._read_json(path)
        map_data = (data.setdefault(boss, {})
                    .setdefault(map_name, {"index_message_id": None, "entries": []}))
        map_data["index_message_id"] = index_message_id
        self._write_json(path, data)

    def list_replay_threads(self, discord_server_id: int) -> dict:
        data = self._read_json(self._replay_index_file())
        return {
            boss: {
                map_name: {"forum_channel_id": None, "thread_id": None}
                for map_name in maps
            }
            for boss, maps in data.items()
        }