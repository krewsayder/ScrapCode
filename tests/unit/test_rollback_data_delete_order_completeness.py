"""The delete order tuple must name every data table — or name why not.

`migrations_json_to_sqlite._DATA_TABLES_DELETE_ORDER` is a tuple literal, and
a tuple literal is what let `guild_key_bindings` fall out of it: nothing
checks the set, the FK it relies on is switched off mid-rollback, and the
orphan survives silent. The class of defect is "a table was forgotten", and
the guard against it is set-equality over the schema, not another single-table
assertion. Hypothesis is unnecessary here — there is no input space to
quantify over, only one fixed set to compare — and the set-equality form is
the stronger one because it fails on the NEXT forgotten table, not just on
this one.

WHY-NEW-FILE: tests/unit/test_rollback_data_delete_order_completeness.py
  CLOSEST-EXISTING: tests/unit/test_quarantine_tombstone_history.py
  EXTENSION-COST: that module's universe is the row counts of six storage
    slots for ONE guild, entered through cog callbacks with a Discord
    interaction double. Extending it means dragging an interaction double and
    a `load_guilds` fixture into a test that compares two static sets and
    never starts the bot.
  PARALLEL-RATIONALE: different lifecycle. This test asserts a structural
    invariant of a tuple literal in the migration module — read straight off
    `Base.metadata` — and has no runtime state to observe. The tombstone
    module asserts a runtime delta through the composition root; the two
    cannot share a fixture or a universe.

SLICE-06 guard (AC-009.6). The acceptance scenario
`test_a_parity_rollback_leaves_no_orphaned_bindings` proves the row is gone
after one specific rollback; this test proves the delete order names every
table, so the next table added to the schema is forced into the order (or
into the exclusion set with a reason). The two together close the class.
"""
from __future__ import annotations

import pytest
from sqlalchemy import inspect

from bot.db.models import Base
from bot.db.migrations_json_to_sqlite import _DATA_TABLES_DELETE_ORDER


pytestmark = pytest.mark.slice_06


# Tables that are deliberately NOT cleared by a parity rollback.
#
# A parity rollback restores DATA parity against a JSON tree, and a table
# belongs here only if its rows are NOT data that the JSON tree owns. Each
# entry carries the reason it survives, in the module, so the next reader
# does not re-litigate the decision.
#
#   guild_key_quarantine_history — a quarantine that HAPPENED does not stop
#     having happened when the JSON tree is re-migrated. Losing it on
#     rollback is the same laundering step 08-03 exists to prevent; see
#     `GuildKeyQuarantineHistoryRow`'s docstring. Recorded as UI-11 / UI-14.
#
# `alembic_version` is metadata, not a data table, and is not declared on
# `Base` — it is excluded by the metadata-driven set-equality itself, not by
# this constant.
_ROLLBACK_EXCLUDED_TABLES: dict[str, str] = {
    "guild_key_quarantine_history": (
        "a quarantine record is a safety event, not migration data; "
        "losing it on rollback launders a quarantine the operator logged "
        "— see GuildKeyQuarantineHistoryRow (UI-11 / UI-14)"
    ),
}


def test_every_data_table_is_in_the_rollback_delete_order_or_explicitly_excluded():
    """Set-equality: every ORM data table is either deleted or reasoned-out.

    This is the structural guard. A NEW table added to the schema either
    lands in `_DATA_TABLES_DELETE_ORDER` in the position the FK order
    requires, or lands in `_ROLLBACK_EXCLUDED_TABLES` with a reason. A
    table in neither is the class of defect that left
    `guild_key_bindings` orphaned.
    """
    metadata_tables = {table.name for table in Base.metadata.sorted_tables}
    deleted = set(_DATA_TABLES_DELETE_ORDER)
    excluded = set(_ROLLBACK_EXCLUDED_TABLES)

    # Every table the metadata declares must be accounted for.
    unaccounted = metadata_tables - deleted - excluded
    assert not unaccounted, (
        "every table declared in the ORM metadata must appear in "
        "_DATA_TABLES_DELETE_ORDER or in _ROLLBACK_EXCLUDED_TABLES. "
        f"Unaccounted: {sorted(unaccounted)}"
    )

    # The two sets must not overlap — a table in both is a contradiction.
    overlap = deleted & excluded
    assert not overlap, (
        "a table is in both the delete order and the exclusion set — "
        f"pick one: {sorted(overlap)}"
    )

    # The exclusion set must carry a reason for every entry. An exclusion
    # without a reason is just a forgotten table with extra steps.
    reasonless = [
        name for name in excluded
        if not _ROLLBACK_EXCLUDED_TABLES.get(name)
    ]
    assert not reasonless, (
        "an excluded table has no recorded reason — a future reader cannot "
        f"tell intent from omission: {sorted(reasonless)}"
    )