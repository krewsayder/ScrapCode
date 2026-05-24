import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from models import Cluster, Guild
from migrations.player_list_migrations import PlayerListMigrator


class ClusterRepository(ABC):
    @abstractmethod
    def load(self, discord_server_id: int) -> Cluster: ...

    @abstractmethod
    def save(self, cluster: Cluster) -> None: ...

    @abstractmethod
    def load_player_apis(self, discord_server_id: int, guild_id: str) -> dict: ...

    @abstractmethod
    def save_player_apis(self, discord_server_id: int, guild_id: str, data: dict) -> None: ...

    @abstractmethod
    def load_capped_state(self, discord_server_id: int, guild_id: str) -> dict: ...

    @abstractmethod
    def save_capped_state(self, discord_server_id: int, guild_id: str, data: dict) -> None: ...

    @abstractmethod
    def load_player_list(self, discord_server_id: int, guild_id: str) -> dict: ...

    @abstractmethod
    def save_player_list(self, discord_server_id: int, guild_id: str, data: dict) -> None: ...

    @abstractmethod
    def load_live_leaderboards(self, discord_server_id: int) -> dict: ...

    @abstractmethod
    def save_live_leaderboards(self, discord_server_id: int, data: dict) -> None: ...

    @abstractmethod
    def get_guild_data_path(self, discord_server_id: int, guild_id: str) -> Path: ...

    @abstractmethod
    def list_server_ids(self) -> list[int]: ...


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

    def load(self, discord_server_id: int) -> Cluster:
        guilds_file = self._server_path(discord_server_id) / "guilds.json"
        if not guilds_file.exists():
            return Cluster(discord_server_id=discord_server_id)
        try:
            raw = json.loads(guilds_file.read_text(encoding="utf-8"))
        except Exception:
            return Cluster(discord_server_id=discord_server_id)

        guilds = {}
        for guild_id, data in raw.get("guilds", {}).items():
            guilds[guild_id] = Guild(
                id=guild_id,
                name=data["name"],
                api_key=data.get("api_key", ""),
                role_id=data.get("role_id", 0),
                notification_channel_id=data.get("notification_channel_id"),
            )

        return Cluster(
            discord_server_id=discord_server_id,
            guilds=guilds,
            update_channel_id=raw.get("update_channel_id"),
        )

    def save(self, cluster: Cluster) -> None:
        guilds_file = self._server_path(cluster.discord_server_id) / "guilds.json"
        raw = {
            "update_channel_id": cluster.update_channel_id,
            "guilds": {
                guild_id: {
                    "name":                    g.name,
                    "api_key":                 g.api_key,
                    "role_id":                 g.role_id,
                    "notification_channel_id": g.notification_channel_id,
                }
                for guild_id, g in cluster.guilds.items()
            },
        }
        guilds_file.write_text(json.dumps(raw, indent=2), encoding="utf-8")

    def load_player_apis(self, discord_server_id: int, guild_id: str) -> dict:
        path = self._guild_path(discord_server_id, guild_id) / "player_api_list.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save_player_apis(self, discord_server_id: int, guild_id: str, data: dict) -> None:
        path = self._guild_path(discord_server_id, guild_id) / "player_api_list.json"
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def load_capped_state(self, discord_server_id: int, guild_id: str) -> dict:
        path = self._guild_path(discord_server_id, guild_id) / "capped_state.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save_capped_state(self, discord_server_id: int, guild_id: str, data: dict) -> None:
        path = self._guild_path(discord_server_id, guild_id) / "capped_state.json"
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

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
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def load_live_leaderboards(self, discord_server_id: int) -> dict:
        path = self._server_path(discord_server_id) / "live_leaderboards.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save_live_leaderboards(self, discord_server_id: int, data: dict) -> None:
        path = self._server_path(discord_server_id) / "live_leaderboards.json"
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def get_guild_data_path(self, discord_server_id: int, guild_id: str) -> Path:
        path = self._guild_path(discord_server_id, guild_id) / "data"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def list_server_ids(self) -> list[int]:
        if not self._base.exists():
            return []
        return [
            int(d.name)
            for d in self._base.iterdir()
            if d.is_dir() and d.name.isdigit()
        ]