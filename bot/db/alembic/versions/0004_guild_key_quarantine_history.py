"""add guild_key_quarantine_history (a quarantine that outlives its guild).

Creates the fourteenth table declared in `bot/db/models.py`
(`GuildKeyQuarantineHistoryRow`) — DELIVER's answer to UI-11, recorded in
`docs/feature/guild-key-integrity/feature-delta.md`.

NO FOREIGN KEY TO `guilds`, and that is the whole point. `/deregister_guild`
deletes the `guilds` row with `PRAGMA foreign_keys=ON`, so the CASCADE that
destroys the players, the hits and the binding destroys anything attached to
them. This table exists to survive exactly that deletion (AC-009.5).

Strictly additive. `guilds` is NOT touched — no column is added to it and no
row is rewritten, which
`test_upgrade_creates_the_binding_store_and_touches_no_guild_record` asserts
byte-for-byte including the column list.

No backfill: the history begins at the first deregistration of a quarantined
guild after this ships. There is nothing to backfill FROM — a quarantine that
was already laundered by a deregistration left no record anywhere, which is
the defect. An empty table is therefore the correct post-upgrade state, and
`downgrade()` is a plain `drop_table` with no production data at risk in
either direction beyond the history itself.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-03T00:00:00Z
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guild_key_quarantine_history",
        # Surrogate key: a second quarantine of the same slug is a second row,
        # not an overwrite. The composite (server, guild) key the rest of the
        # schema uses would collapse a history into its most recent entry.
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("discord_server_id", sa.Integer(), nullable=False),
        sa.Column("guild_id", sa.String(length=64), nullable=False),
        # The identity the guild WAS bound to when the quarantine was raised.
        sa.Column("tacticus_guild_id", sa.String(length=64), nullable=True),
        sa.Column("tacticus_guild_tag", sa.String(length=32), nullable=True),
        sa.Column("tacticus_guild_name", sa.String(length=128), nullable=True),
        # The identity its key had drifted TO — recovered from the binding's
        # `quarantine_reason`, the only carrier the codebase has for it.
        sa.Column("observed_tacticus_guild_id", sa.String(length=64), nullable=True),
        sa.Column("quarantine_reason", sa.Text(), nullable=True),
        # ISO-8601 UTC strings in the same String(32) shape as
        # `battle_hits.completed_on` and `guild_key_bindings.quarantined_at`.
        # KPI-2 compares them AS STRINGS.
        sa.Column("quarantined_at", sa.String(length=32), nullable=True),
        sa.Column("recorded_at", sa.String(length=32), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_guild_key_quarantine_history"),
        # No ForeignKeyConstraint. See the module docstring.
    )
    op.create_index(
        "ix_guild_key_quarantine_history_guild",
        "guild_key_quarantine_history",
        ["discord_server_id", "guild_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_guild_key_quarantine_history_guild",
        table_name="guild_key_quarantine_history",
    )
    op.drop_table("guild_key_quarantine_history")
