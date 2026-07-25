"""baseline schema.

Creates the 12 tables declared in `bot/db/models.py` per ADR-006 D3 +
data-dictionary §4. This is the Alembic baseline revision; the JSON ->
SQLite data migration lands in a subsequent revision (Slice 03).

Revision ID: 0001
Revises:
Create Date: 2026-07-19T13:10:00Z
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # clusters -------------------------------------------------------------
    op.create_table(
        "clusters",
        sa.Column("discord_server_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("discord_server_id", name="pk_clusters"),
    )

    # role_tiers -----------------------------------------------------------
    op.create_table(
        "role_tiers",
        sa.Column("discord_server_id", sa.Integer(), nullable=False),
        sa.Column("tier", sa.String(length=32), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["discord_server_id"], ["clusters.discord_server_id"],
            ondelete="CASCADE", name="fk_role_tiers_cluster",
        ),
        sa.PrimaryKeyConstraint("discord_server_id", "tier", "role_id", name="pk_role_tiers"),
        sa.CheckConstraint("tier IN ('admin', 'officer')", name="ck_role_tiers_tier"),
    )

    # guilds ---------------------------------------------------------------
    op.create_table(
        "guilds",
        sa.Column("discord_server_id", sa.Integer(), nullable=False),
        sa.Column("guild_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("api_key", sa.Text(), nullable=False),
        sa.Column("api_key_hmac", sa.String(length=64), nullable=True),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("notification_channel_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["discord_server_id"], ["clusters.discord_server_id"],
            ondelete="CASCADE", name="fk_guilds_cluster",
        ),
        sa.PrimaryKeyConstraint("discord_server_id", "guild_id", name="pk_guilds"),
        sa.UniqueConstraint("api_key_hmac", name="uq_guilds_api_key_hmac"),
    )

    # guild_member_roles ---------------------------------------------------
    op.create_table(
        "guild_member_roles",
        sa.Column("discord_server_id", sa.Integer(), nullable=False),
        sa.Column("guild_id", sa.String(length=64), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["discord_server_id", "guild_id"],
            ["guilds.discord_server_id", "guilds.guild_id"],
            ondelete="CASCADE", name="fk_guild_member_roles_guild",
        ),
        sa.PrimaryKeyConstraint(
            "discord_server_id", "guild_id", "role_id", name="pk_guild_member_roles",
        ),
    )

    # player_registrations -------------------------------------------------
    op.create_table(
        "player_registrations",
        sa.Column("discord_id", sa.String(length=32), nullable=False),
        sa.Column("discord_server_id", sa.Integer(), nullable=False),
        sa.Column("guild_id", sa.String(length=64), nullable=False),
        sa.Column("api_key", sa.Text(), nullable=False),
        sa.Column("api_key_hmac", sa.String(length=64), nullable=False),
        sa.Column("is_capped", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["discord_server_id"], ["clusters.discord_server_id"],
            ondelete="CASCADE", name="fk_player_registrations_cluster",
        ),
        sa.ForeignKeyConstraint(
            ["discord_server_id", "guild_id"],
            ["guilds.discord_server_id", "guilds.guild_id"],
            ondelete="CASCADE", name="fk_player_registrations_guild",
        ),
        sa.PrimaryKeyConstraint("discord_id", name="pk_player_registrations"),
        sa.UniqueConstraint("api_key_hmac", name="uq_player_registrations_api_key_hmac"),
    )

    # players --------------------------------------------------------------
    op.create_table(
        "players",
        sa.Column("tacticus_user_id", sa.String(length=64), nullable=False),
        sa.Column("discord_server_id", sa.Integer(), nullable=False),
        sa.Column("guild_id", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("last_validated", sa.String(length=32), nullable=False),
        sa.Column("is_former", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["discord_server_id", "guild_id"],
            ["guilds.discord_server_id", "guilds.guild_id"],
            ondelete="CASCADE", name="fk_players_guild",
        ),
        sa.PrimaryKeyConstraint("tacticus_user_id", name="pk_players"),
    )

    # battle_hits ----------------------------------------------------------
    op.create_table(
        "battle_hits",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("discord_server_id", sa.Integer(), nullable=False),
        sa.Column("guild_id", sa.String(length=64), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("boss_id", sa.String(length=64), nullable=False),
        sa.Column("encounter_index", sa.String(length=8), nullable=False),
        sa.Column("tier_key", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("damage", sa.Integer(), nullable=False),
        sa.Column("completed_on", sa.String(length=32), nullable=False),
        sa.Column("hero_roster_key", sa.String(length=255), nullable=False),
        sa.Column("mow_unit_id", sa.String(length=64), nullable=True),
        sa.Column("encounter_type", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(
            ["discord_server_id", "guild_id"],
            ["guilds.discord_server_id", "guilds.guild_id"],
            ondelete="CASCADE", name="fk_battle_hits_guild",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_battle_hits"),
        sa.UniqueConstraint(
            "discord_server_id", "guild_id", "season",
            "boss_id", "encounter_index", "tier_key",
            "hero_roster_key", "user_id",
            name="uq_battle_hits_natural_key",
        ),
    )

    # bomb_hits ------------------------------------------------------------
    op.create_table(
        "bomb_hits",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("discord_server_id", sa.Integer(), nullable=False),
        sa.Column("guild_id", sa.String(length=64), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("boss_id", sa.String(length=64), nullable=False),
        sa.Column("encounter_index", sa.String(length=8), nullable=False),
        sa.Column("tier_key", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("damage", sa.Integer(), nullable=False),
        sa.Column("completed_on", sa.String(length=32), nullable=False),
        sa.Column("encounter_type", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(
            ["discord_server_id", "guild_id"],
            ["guilds.discord_server_id", "guilds.guild_id"],
            ondelete="CASCADE", name="fk_bomb_hits_guild",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bomb_hits"),
        sa.UniqueConstraint(
            "discord_server_id", "guild_id", "season",
            "boss_id", "encounter_index", "tier_key",
            "user_id", "completed_on",
            name="uq_bomb_hits_natural_key",
        ),
    )

    # replay_threads -------------------------------------------------------
    op.create_table(
        "replay_threads",
        sa.Column("discord_server_id", sa.Integer(), nullable=False),
        sa.Column("boss", sa.String(length=64), nullable=False),
        sa.Column("map_name", sa.String(length=128), nullable=False),
        sa.Column("forum_channel_id", sa.Integer(), nullable=True),
        sa.Column("thread_id", sa.Integer(), nullable=True),
        sa.Column("index_message_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["discord_server_id"], ["clusters.discord_server_id"],
            ondelete="CASCADE", name="fk_replay_threads_cluster",
        ),
        sa.PrimaryKeyConstraint(
            "discord_server_id", "boss", "map_name", name="pk_replay_threads",
        ),
    )

    # replay_entries -------------------------------------------------------
    op.create_table(
        "replay_entries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("discord_server_id", sa.Integer(), nullable=False),
        sa.Column("boss", sa.String(length=64), nullable=False),
        sa.Column("map_name", sa.String(length=128), nullable=False),
        sa.Column("team", sa.String(length=64), nullable=False),
        sa.Column("tier", sa.String(length=64), nullable=False),
        sa.Column("position", sa.String(length=32), nullable=False),
        sa.Column("damage_text", sa.String(length=64), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("submitted_by", sa.String(length=32), nullable=False),
        sa.Column("index_message_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["discord_server_id", "boss", "map_name"],
            ["replay_threads.discord_server_id", "replay_threads.boss", "replay_threads.map_name"],
            ondelete="CASCADE", name="fk_replay_entries_thread",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_replay_entries"),
        sa.UniqueConstraint(
            "discord_server_id", "boss", "map_name", "url",
            name="uq_replay_entries_url_per_thread",
        ),
    )

    # live_leaderboards ----------------------------------------------------
    op.create_table(
        "live_leaderboards",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("discord_server_id", sa.Integer(), nullable=False),
        sa.Column("scope_key", sa.String(length=64), nullable=False),
        sa.Column("guild_id", sa.String(length=64), nullable=True),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["discord_server_id"], ["clusters.discord_server_id"],
            ondelete="CASCADE", name="fk_live_leaderboards_cluster",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_live_leaderboards"),
        sa.UniqueConstraint(
            "discord_server_id", "scope_key", name="uq_live_leaderboards_scope",
        ),
    )

    # live_lb_messages -----------------------------------------------------
    op.create_table(
        "live_lb_messages",
        sa.Column("config_id", sa.Integer(), nullable=False),
        sa.Column("tier_value", sa.String(length=32), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["config_id"], ["live_leaderboards.id"],
            ondelete="CASCADE", name="fk_live_lb_messages_config",
        ),
        sa.PrimaryKeyConstraint("config_id", "tier_value", name="pk_live_lb_messages"),
    )


def downgrade() -> None:
    for table in (
        "live_lb_messages",
        "live_leaderboards",
        "replay_entries",
        "replay_threads",
        "bomb_hits",
        "battle_hits",
        "players",
        "player_registrations",
        "guild_member_roles",
        "guilds",
        "role_tiers",
        "clusters",
    ):
        op.drop_table(table)