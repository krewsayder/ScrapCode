"""SQLAlchemy 2.0 declarative ORM models for the SQLite backend.

Per ADR-006 D3 + data-dictionary §4. Tables:

  clusters                       (PK discord_server_id)
  role_tiers                     (server_id, tier, role_id)  CHECK tier IN ('admin','officer')
  guilds                         (server_id, guild_id)      api_key_hmac UNIQUE
  guild_member_roles             (server_id, guild_id, role_id)
  player_registrations           (PK discord_id)            api_key_hmac UNIQUE NOT NULL, is_capped
  players                        (PK tacticus_user_id)
  battle_hits                    surrogate PK, unique (server,guild,season,boss,encounter,tier,roster_key,user_id)
  bomb_hits                      surrogate PK, unique (server,guild,season,boss,encounter,tier,user_id,completed_on)
  replay_threads                 (server_id, boss, map_name)
  replay_entries                 surrogate PK, unique (server_id, boss, map_name, url)
  live_leaderboards              surrogate PK
  live_lb_messages               (config_id, tier_value)

D4: no `battle_hits_simple` table. D5: `capped_state` is the `is_capped`
column on `player_registrations`. D7: `api_key` columns are Fernet
ciphertext at rest; uniqueness is enforced on the deterministic
`api_key_hmac` column (HMAC-SHA256, key derived from `SCRAPCODE_DB_KEY`
via HKDF). The Fernet encrypt/decrypt + HKDF helper lands in 02-03; this
module declares only the column + the UNIQUE constraint so the schema
can enforce the 1:1 binding. D10: `replay_threads` + `replay_entries`
replace `replay_index.json`. D11: `replay_entries.discord_server_id` is
new; URL uniqueness is scoped per `(discord_server_id, boss, map_name)`.
D12: no `update_channel_id` column anywhere.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base shared by every ORM model in the SQLite backend."""


class ClusterRow(Base):
    __tablename__ = "clusters"

    discord_server_id: Mapped[int] = mapped_column(Integer, primary_key=True)


class RoleTierRow(Base):
    __tablename__ = "role_tiers"

    discord_server_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("clusters.discord_server_id", ondelete="CASCADE"),
        primary_key=True,
    )
    tier: Mapped[str] = mapped_column(String(32), primary_key=True)
    role_id: Mapped[int] = mapped_column(Integer, primary_key=True)

    __table_args__ = (
        CheckConstraint("tier IN ('admin', 'officer')", name="ck_role_tiers_tier"),
    )


class GuildRow(Base):
    __tablename__ = "guilds"

    discord_server_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("clusters.discord_server_id", ondelete="CASCADE"),
        primary_key=True,
    )
    guild_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    api_key: Mapped[str] = mapped_column(Text, nullable=False, default="")
    api_key_hmac: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    role_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notification_channel_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class GuildMemberRoleRow(Base):
    __tablename__ = "guild_member_roles"

    discord_server_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    role_id: Mapped[int] = mapped_column(Integer, primary_key=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["discord_server_id", "guild_id"],
            ["guilds.discord_server_id", "guilds.guild_id"],
            ondelete="CASCADE",
        ),
    )


class PlayerRegistrationRow(Base):
    __tablename__ = "player_registrations"

    discord_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    discord_server_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("clusters.discord_server_id", ondelete="CASCADE"),
        nullable=False,
    )
    guild_id: Mapped[str] = mapped_column(String(64), nullable=False)
    api_key: Mapped[str] = mapped_column(Text, nullable=False)
    api_key_hmac: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    is_capped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["discord_server_id", "guild_id"],
            ["guilds.discord_server_id", "guilds.guild_id"],
            ondelete="CASCADE",
        ),
    )


class PlayerRow(Base):
    __tablename__ = "players"

    tacticus_user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    discord_server_id: Mapped[int] = mapped_column(Integer, nullable=False)
    guild_id: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    last_validated: Mapped[str] = mapped_column(String(32), nullable=False)
    is_former: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["discord_server_id", "guild_id"],
            ["guilds.discord_server_id", "guilds.guild_id"],
            ondelete="CASCADE",
        ),
    )


class BattleHitRow(Base):
    __tablename__ = "battle_hits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    discord_server_id: Mapped[int] = mapped_column(Integer, nullable=False)
    guild_id: Mapped[str] = mapped_column(String(64), nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    boss_id: Mapped[str] = mapped_column(String(64), nullable=False)
    encounter_index: Mapped[str] = mapped_column(String(8), nullable=False)
    tier_key: Mapped[str] = mapped_column(String(32), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    damage: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_on: Mapped[str] = mapped_column(String(32), nullable=False)
    hero_roster_key: Mapped[str] = mapped_column(String(255), nullable=False)
    mow_unit_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    encounter_type: Mapped[str | None] = mapped_column(String(32), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["discord_server_id", "guild_id"],
            ["guilds.discord_server_id", "guilds.guild_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "discord_server_id", "guild_id", "season",
            "boss_id", "encounter_index", "tier_key",
            "hero_roster_key", "user_id",
            name="uq_battle_hits_natural_key",
        ),
    )


class BombHitRow(Base):
    __tablename__ = "bomb_hits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    discord_server_id: Mapped[int] = mapped_column(Integer, nullable=False)
    guild_id: Mapped[str] = mapped_column(String(64), nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    boss_id: Mapped[str] = mapped_column(String(64), nullable=False)
    encounter_index: Mapped[str] = mapped_column(String(8), nullable=False)
    tier_key: Mapped[str] = mapped_column(String(32), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    damage: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_on: Mapped[str] = mapped_column(String(32), nullable=False)
    encounter_type: Mapped[str | None] = mapped_column(String(32), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["discord_server_id", "guild_id"],
            ["guilds.discord_server_id", "guilds.guild_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "discord_server_id", "guild_id", "season",
            "boss_id", "encounter_index", "tier_key",
            "user_id", "completed_on",
            name="uq_bomb_hits_natural_key",
        ),
    )


class ReplayThreadRow(Base):
    __tablename__ = "replay_threads"

    discord_server_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("clusters.discord_server_id", ondelete="CASCADE"),
        primary_key=True,
    )
    boss: Mapped[str] = mapped_column(String(64), primary_key=True)
    map_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    forum_channel_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    thread_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    index_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ReplayEntryRow(Base):
    __tablename__ = "replay_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    discord_server_id: Mapped[int] = mapped_column(Integer, nullable=False)
    boss: Mapped[str] = mapped_column(String(64), nullable=False)
    map_name: Mapped[str] = mapped_column(String(128), nullable=False)
    team: Mapped[str] = mapped_column(String(64), nullable=False)
    tier: Mapped[str] = mapped_column(String(64), nullable=False)
    position: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    damage_text: Mapped[str] = mapped_column(String(64), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    submitted_by: Mapped[str] = mapped_column(String(32), nullable=False)
    index_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["discord_server_id", "boss", "map_name"],
            ["replay_threads.discord_server_id", "replay_threads.boss", "replay_threads.map_name"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "discord_server_id", "boss", "map_name", "url",
            name="uq_replay_entries_url_per_thread",
        ),
    )


class LiveLeaderboardRow(Base):
    __tablename__ = "live_leaderboards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    discord_server_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("clusters.discord_server_id", ondelete="CASCADE"),
        nullable=False,
    )
    scope_key: Mapped[str] = mapped_column(String(64), nullable=False)
    guild_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    channel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    season: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "discord_server_id", "scope_key",
            name="uq_live_leaderboards_scope",
        ),
    )


class LiveLbMessageRow(Base):
    __tablename__ = "live_lb_messages"

    config_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("live_leaderboards.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tier_value: Mapped[str] = mapped_column(String(32), primary_key=True)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)