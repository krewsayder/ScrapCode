"""Property-based round-trip of the Guild-to-dict projection (slice 07, step 09-03).

WHY-NEW-FILE: tests/unit/test_slice_07_guild_projection_round_trip.py
  CLOSEST-EXISTING: tests/unit/test_slice_07_build_repo_classification.py
  EXTENSION-COST: that module classifies the composition root's
    `(backend, key-state, path-state)` configuration space and drives
    `build_repo()` end-to-end; re-using it for the projection round-trip would
    force every property to thread a full `build_repo` env patch + throwaway
    filesystem through the composition root just to reach `load_guilds` /
    `save_guilds`, coupling a projection invariant to the backend selection
    logic it has nothing to do with.
  PARALLEL-RATIONALE: different unit under test (the Guild-to-dict projection
    that moves into the sanctioned adapters, not the composition root's
    configuration classifier) and a different driving port
    (`load_guilds`/`save_guilds`, not `build_repo`). The projection's
    invariant is backend-agnostic — it must hold on BOTH adapters — so the
    property drives through the wrapper layer with a JSON repo (no Fernet key
    needed) and asserts the round-trip, while the composition-root module
    drives `build_repo` itself.

WHY PROPERTIES AND NOT EXAMPLES. The step's contract is a round-trip
invariant: `save_guilds(load_guilds(x))` leaves every field of every guild,
`api_key` included, byte-identical. A single-example test pins one dict
shape; a property over generated guild dicts exhausts the equivalence
classes that matter (empty api_key, None notification_channel_id, empty
member_role_ids, multiple guilds) and Hypothesis shrinking will find the
shortest counter-example if any field is silently blanked.

The isolation property (a mutation to one guild's non-key field changes no
other guild's key) is the load-mutate-save contract: an unrelated admin
command that calls `save_guilds(load_guilds(x))` after touching one guild's
name must not blank a sibling's key. That is the regression this step's
projection move could introduce if the two adapter sides drift apart.

DECLARED UNIVERSE. Each property captures the full observable surface of a
guilds dict — every `{guild_id}.{field}` slot for the five fields
(`name`, `api_key`, `role_id`, `notification_channel_id`,
`member_role_ids`) — and asserts with `strict=True` that every slot in the
universe is either in the expected delta or unchanged. This catches a
hidden mutation on an adjacent slot (e.g. a save that blanks a sibling's
`api_key` when it should not) that a single-slot assertion would miss.

`nwave_ai.state_delta` is not in this project's stack (this is a Discord
bot, not an nWave project), so a minimal `_assert_state_delta` helper is
implemented inline with the same `strict=True` semantics: every universe
slot not in `expected` must be unchanged, and every slot in `expected` must
satisfy its predicate.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from pathlib import Path

# `bot.guilds` evaluates `repo = build_repo()` at IMPORT time. Pin a harmless
# backend before any `bot.*` import so collection cannot construct a
# repository pointed at a live tree. Same precedent as
# `tests/unit/test_guild_keys_policy.py`.
os.environ.setdefault("SCRAPCODE_REPO_BACKEND", "json")

import pytest  # noqa: E402

hypothesis = pytest.importorskip(
    "hypothesis",
    reason="hypothesis is not installed — DISTILL pins it into requirements.txt",
)

import hypothesis.strategies as st  # noqa: E402
from hypothesis import given, settings  # noqa: E402

# Deselected from the 250-test baseline: this module belongs to the
# remediation slice, same as the other slice_07 unit modules.
pytestmark = [pytest.mark.property, pytest.mark.slice_07]

_SERVER_ID = 999_999
_FIELDS = (
    "name",
    "api_key",
    "role_id",
    "notification_channel_id",
    "member_role_ids",
)


# ---------------------------------------------------------------------------
# State-delta helper (minimal inline version — nwave_ai not in this project)
# ---------------------------------------------------------------------------

def _capture_state(guilds: dict) -> dict[str, object]:
    """Flatten a guilds dict into ``{gid.field: value}`` slots.

    The universe of observable state for the projection round-trip is every
    ``{guild_id}.{field}`` pair across the five dict keys. Capturing it flat
    lets the delta assertion name the exact slot that drifted rather than
    printing two nested dicts and leaving the operator to diff them.
    """
    state: dict[str, object] = {}
    for gid, data in guilds.items():
        for field in _FIELDS:
            state[f"{gid}.{field}"] = data.get(field)
    return state


def _assert_state_delta(
    before: dict[str, object],
    after: dict[str, object],
    universe: set[str],
    expected: dict[str, object],
    *,
    strict: bool = True,
) -> None:
    """Assert every universe slot is either in ``expected`` or unchanged.

    Mirrors ``nwave_ai.state_delta.assert_state_delta`` with ``strict=True``:
    a slot in ``expected`` must satisfy its predicate (a callable
    ``(before, after) -> bool``); a slot NOT in ``expected`` must be
    byte-identical. This is what catches a save that silently blanks a
    sibling's ``api_key`` — that slot is not in the expected delta, so
    ``strict`` requires it to be unchanged, and it is not.
    """
    for slot in sorted(universe):
        b = before.get(slot)
        a = after.get(slot)
        if slot in expected:
            predicate = expected[slot]
            assert predicate(b, a), (
                f"{slot}: expected delta not satisfied "
                f"(before={b!r}, after={a!r})"
            )
        elif strict:
            assert a == b, f"{slot} changed unexpectedly: {b!r} -> {a!r}"


# ---------------------------------------------------------------------------
# Guild-dict strategy
# ---------------------------------------------------------------------------

@st.composite
def guild_dicts(draw, min_guilds: int = 1, max_guilds: int = 5) -> dict:
    """Generate a ``{guild_id: {name, api_key, role_id, ...}}`` dict.

    The universe of fields mirrors ``bot.guilds.load_guilds``'s five-key
    return shape. ``api_key`` may be empty (the codebase uses
    ``data.get("api_key", "")``); ``notification_channel_id`` may be None;
    ``member_role_ids`` may be empty. Guild ids are short ASCII slugs to
    match the real key space without generating unprintable characters that
    would make a shrunk counter-example unreadable.
    """
    guild_ids = draw(
        st.lists(
            st.text(
                min_size=1,
                max_size=20,
                alphabet=st.characters(
                    min_codepoint=97, max_codepoint=122,
                    include_characters="_-0123456789",
                ),
            ),
            min_size=min_guilds,
            max_size=max_guilds,
            unique=True,
        )
    )
    guilds: dict[str, dict] = {}
    for gid in guild_ids:
        guilds[gid] = {
            "name": draw(st.text(min_size=1, max_size=50)),
            "api_key": draw(st.text(min_size=0, max_size=100)),
            "role_id": draw(st.integers(min_value=0, max_value=99999)),
            "notification_channel_id": draw(
                st.one_of(st.none(), st.integers(min_value=1, max_value=999999999))
            ),
            "member_role_ids": draw(
                st.lists(
                    st.integers(min_value=1, max_value=99999),
                    max_size=5,
                )
            ),
        }
    return guilds


@st.composite
def guild_dicts_with_two_or_more(draw) -> dict:
    """Guild dicts with at least 2 guilds — for the isolation property."""
    return draw(guild_dicts(min_guilds=2, max_guilds=5))


# ---------------------------------------------------------------------------
# Per-example repo patching (no fixtures inside @given — Hypothesis health)
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _patched_repo(tmpdir: str):
    """Point ``bot.guilds.repo`` at a fresh ``JsonClusterRepository``.

    ``bot/guilds.py`` resolves through a module-level ``repo`` singleton.
    ``@given`` examples cannot use ``monkeypatch`` (function-scoped fixture,
    not reset between examples), so each example manages its own throwaway
    directory and patches the singleton directly, restoring in ``finally``.
    """
    import bot.guilds as guilds_mod
    from bot.repository import JsonClusterRepository

    repo = JsonClusterRepository(base_path=Path(tmpdir) / "clusters")
    old = guilds_mod.repo
    guilds_mod.repo = repo
    try:
        yield
    finally:
        guilds_mod.repo = old


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

@given(guilds=guild_dicts())
@settings(max_examples=100, deadline=None)
def test_save_load_round_trip_preserves_every_field(guilds: dict) -> None:
    """``save_guilds(load_guilds(x))`` leaves every field byte-identical.

    The projection that moves from ``bot/guilds.py`` into the sanctioned
    adapters must be a stable round-trip: save a guilds dict, load it back,
    save the loaded dict, load again, and the second load equals the first
    on every field — ``api_key`` included. A drift between the dict-to-Guild
    and Guild-to-dict sides of the projection would silently blank a key
    here, which is the regression this step's move could introduce.
    """
    tmpdir = tempfile.mkdtemp()
    try:
        with _patched_repo(tmpdir):
            from bot.guilds import load_guilds, save_guilds

            save_guilds(_SERVER_ID, guilds)
            loaded = load_guilds(_SERVER_ID)
            save_guilds(_SERVER_ID, loaded)
            reloaded = load_guilds(_SERVER_ID)

            universe = {f"{gid}.{f}" for gid in guilds for f in _FIELDS}
            before = _capture_state(loaded)
            after = _capture_state(reloaded)
            _assert_state_delta(
                before, after, universe, expected={}, strict=True,
            )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@given(guilds=guild_dicts_with_two_or_more())
@settings(max_examples=100, deadline=None)
def test_mutating_one_guilds_non_key_field_changes_no_other_guilds_key(
    guilds: dict,
) -> None:
    """A load-mutate-save cycle on one guild's ``name`` blanks no sibling's key.

    The isolation invariant: an unrelated admin command that loads the
    guilds dict, mutates one guild's non-key field (e.g. ``name``), and
    saves must not change any other guild's ``api_key``. This is the
    load-mutate-save contract every cog relies on — ``save_guilds`` rebuilds
    every guild from the dict, so a projection that drops a key on the
    rebuild side would blank every sibling on the next save.
    """
    tmpdir = tempfile.mkdtemp()
    try:
        with _patched_repo(tmpdir):
            from bot.guilds import load_guilds, save_guilds

            save_guilds(_SERVER_ID, guilds)
            loaded = load_guilds(_SERVER_ID)

            # Mutate the first guild's name (a non-key field).
            first_gid = next(iter(loaded))
            mutated = dict(loaded)
            mutated[first_gid] = dict(loaded[first_gid])
            mutated[first_gid]["name"] = "MUTATED-NAME-" + str(len(loaded[first_gid]["name"]))

            save_guilds(_SERVER_ID, mutated)
            reloaded = load_guilds(_SERVER_ID)

            universe = {f"{gid}.{f}" for gid in guilds for f in _FIELDS}
            before = _capture_state(loaded)
            after = _capture_state(reloaded)

            expected = {
                f"{first_gid}.name": lambda b, a: a == "MUTATED-NAME-" + str(len(b)),
            }
            _assert_state_delta(
                before, after, universe, expected=expected, strict=True,
            )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)