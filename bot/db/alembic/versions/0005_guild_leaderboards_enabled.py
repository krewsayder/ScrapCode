"""add guilds.leaderboards_enabled (per-guild leaderboard switch).

Adds ONE column to `guilds`. This is the first revision in this project to
alter `guilds` rather than add a table, so the reasoning is worth stating.

WHY A COLUMN ON `guilds` AND NOT A BOARD-SCOPED FLAG. The switch has to be
settable for a guild that has no live board yet, and it has to survive a board
being torn down and set up again. A flag on `live_leaderboards` dies with the
row it lives on, so neither property holds there.

WHY THIS IS NOT THE DDD-4 HAZARD. ADR-008 DDD-4 moved binding state OFF
`guilds` because `save_guilds` rebuilds every `Guild` from the five-key
cog-facing dict, so any field reachable from that dict is reset to its default
by the next unrelated admin command. This column is deliberately NOT in that
dict: both adapters' `save_guilds_dict` read the stored value and carry it
forward. That is what `test_save_guilds_dict_cycle_preserves_a_disabled_flag`
pins, on both adapters — the regression this design exists to prevent.

BACKFILL IS THE SERVER DEFAULT. `server_default=sa.true()` means SQLite's
`ALTER TABLE ... ADD COLUMN` populates every existing row with 1 in place, so
every currently-configured board keeps running across the upgrade and there is
nothing to backfill separately. The default is ON because the pre-upgrade
behaviour of every guild was "leaderboards run", and a migration that silently
turns a working board off is a worse outage than the feature is worth.

THE DOWNGRADE IS EXACT, AND THAT RULES OUT `batch_alter_table`.
`test_downgrade_restores_the_prior_shape_exactly` compares the raw
`sqlite_master.sql` of every object against the pre-feature baseline, byte for
byte. Alembic's batch mode removes a column by rebuilding the table, and the
rebuild re-emits the DDL from its own model — which renders the table name
QUOTED (`CREATE TABLE "guilds"`) where the baseline has it bare. Every column,
constraint and foreign key came back identical; the quoting alone failed the
comparison.

SQLite's native `ALTER TABLE ... DROP COLUMN` (3.35.0+, 2021) edits the STORED
schema text in place instead, so every surviving byte — table-name quoting,
column order, constraint spelling — is the baseline's own. That is a real
guarantee rather than a reconstruction that happens to match, so the plain
`op.drop_column` is the correct spelling here and the test is left strict.
Permitted because the column is not indexed, not part of the primary key and
not in a UNIQUE constraint, which are SQLite's three refusals for a native
drop. This VM runs 3.50.4.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-29T00:00:00Z
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "guilds",
        sa.Column(
            "leaderboards_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("guilds", "leaderboards_enabled")
