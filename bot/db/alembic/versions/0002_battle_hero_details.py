"""add battle_hits.hero_details JSON column (display-only).

Adds a nullable `hero_details` JSON column to `battle_hits` storing the
list of `{"unitId": "..."}` dicts from the API entry's `heroDetails`.
Display-only: NOT part of the dedup unique constraint (dedup uses
`hero_roster_key`); stored so `load_battle_hits` can return the
data-dictionary §2.7 shape `embeds.build_battle_messages` renders
(`_build_hero_display`) — closes the KPI-4 / CS1 byte-identity gap left
by 03-01's drop of `hero_details` (user-stories.md line 734 permits the
JSON column for display).

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-19T21:00:00Z
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "battle_hits",
        sa.Column("hero_details", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("battle_hits", "hero_details")