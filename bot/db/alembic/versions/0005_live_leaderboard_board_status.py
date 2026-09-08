"""add live_leaderboards.board_status (the operator's off switch for a board).

Adds ONE column to an existing table. `live_lb_messages` is not touched: the
whole point of the switch is that a paused board's already-posted messages
stay exactly where they are, so the rows that address them must survive
untouched too.

A named status rather than a boolean (ADR-009 DDD-1), mirroring the
`key_status` column `0003` added: `'active'` / `'disabled'`, the string values
of the `BoardStatus` enum. The literals are duplicated here rather than
imported because policy depends on storage and never the reverse (ADR-008 D3).
A boolean says only which of two states a board is in; a status column names
the state it is in, and leaves room for a third without another migration.

`server_default="active"` and NOT NULL, so every board that exists at upgrade
time keeps updating. A nullable column, or one defaulting to disabled, would
pause every live board on the cluster the moment the migration ran — the
silent freeze this feature exists to make impossible (KPI-4).

The switch is scope-agnostic (it applies to `guild:{id}` rows as readily as to
`cluster`) even though only the cluster board has commands driving it today.
A column that already covers both scopes costs nothing here and spares a
second migration when the guild-scoped commands arrive.

Both directions use a NATIVE `ALTER TABLE`, and the downgrade must not use
`batch_alter_table`. Batch mode drops and recreates the table, and SQLAlchemy
re-emits the `CREATE TABLE` from its own model — which quotes the table name
and orders the constraints differently from the statement `0001` wrote, even
though the columns come out identical. `test_downgrade_restores_the_prior_
shape_exactly` (AC-006.2) compares the stored DDL as text and fails on exactly
that difference: a rebuild leaves a table that is equivalent but not the one
the baseline created. `op.drop_column` needs SQLite 3.35+ (2021) for the
native `DROP COLUMN`; that is a hard requirement of the downgrade path, and
an older runtime fails loudly at the ALTER rather than quietly reshaping the
schema.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-08T00:00:00Z
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
        "live_leaderboards",
        sa.Column(
            "board_status",
            sa.String(length=32),
            nullable=False,
            server_default="active",
        ),
    )


def downgrade() -> None:
    # Downgrading discards which boards were paused. That is acceptable and
    # deliberate: without the column there is no code left that honours the
    # pause, so a board that came back on is the accurate post-downgrade
    # state rather than a board frozen by a status nothing reads.
    #
    # Native DROP COLUMN, NOT batch mode — see the module docstring.
    op.drop_column("live_leaderboards", "board_status")
