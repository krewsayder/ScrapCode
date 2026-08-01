"""add guild_key_bindings (guild API key -> Tacticus guild identity).

Creates the thirteenth table declared in `bot/db/models.py`
(`GuildKeyBindingRow`) per ADR-008 DDD-4: 1:1 with `guilds`, composite FK
`ondelete="CASCADE"`.

Strictly additive. `guilds` is NOT touched — binding columns on `guilds`
were rejected because `bot/guilds.py:save_guilds` reconstructs each `Guild`
from a five-key dict, so any binding field reachable from the `Guild`
dataclass would be written back as `None` by an unrelated admin command.

No backfill: trust-on-first-use (DDD-8) populates the table on the first
successful probe, so an empty table is the correct post-upgrade state and
`downgrade()` is a plain `drop_table` with no production data at risk in
either direction.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-01T17:10:00Z
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guild_key_bindings",
        sa.Column("discord_server_id", sa.Integer(), nullable=False),
        sa.Column("guild_id", sa.String(length=64), nullable=False),
        # The binding itself (DDD-1). Tag and name are display-only and may
        # be absent — a retag or rename must never trip the lock.
        sa.Column("tacticus_guild_id", sa.String(length=64), nullable=True),
        sa.Column("tacticus_guild_tag", sa.String(length=32), nullable=True),
        sa.Column("tacticus_guild_name", sa.String(length=128), nullable=True),
        # ISO-8601 UTC strings in the same String(32) shape as
        # `battle_hits.completed_on` (e.g. 2026-07-25T14:45:19Z). KPI-2
        # compares `quarantined_at` against `completed_on` AS STRINGS.
        sa.Column("identity_bound_at", sa.String(length=32), nullable=True),
        # 'active' / 'quarantined' — the string values of
        # bot.services.tacticus.guild_client.KeyStatus, duplicated here so
        # storage does not depend on the service layer. No server_default:
        # the Python-side default lives on the ORM column, matching how
        # `is_capped` and `api_key` are declared in 0001.
        sa.Column("key_status", sa.String(length=32), nullable=False),
        sa.Column("quarantine_reason", sa.Text(), nullable=True),
        sa.Column("quarantined_at", sa.String(length=32), nullable=True),
        sa.Column("last_alerted_at", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(
            ["discord_server_id", "guild_id"],
            ["guilds.discord_server_id", "guilds.guild_id"],
            ondelete="CASCADE", name="fk_guild_key_bindings_guild",
        ),
        sa.PrimaryKeyConstraint(
            "discord_server_id", "guild_id", name="pk_guild_key_bindings",
        ),
    )


def downgrade() -> None:
    op.drop_table("guild_key_bindings")
