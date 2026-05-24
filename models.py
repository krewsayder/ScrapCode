from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Guild:
    id: str
    name: str
    api_key: str
    role_id: int
    notification_channel_id: Optional[int] = None
    member_role_ids: list[int] = field(default_factory=list)


@dataclass
class Cluster:
    discord_server_id: int
    guilds: dict[str, Guild] = field(default_factory=dict)
    update_channel_id: Optional[int] = None
    role_tiers: dict[str, list[int]] = field(default_factory=dict)