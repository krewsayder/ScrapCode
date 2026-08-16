"""Slice 04 — the picker discovers tiers. Implements
`acceptance/slice-04-dynamic-tier-picker.feature`.

Covers US-006. This is the only slice carrying signature-level risk, and it is
last so that if its hypothesis fails, Slices 01-03 have already delivered the
whole Mythic 3 outcome and this can be abandoned at no loss.

Driving ports:
  * `/view_leaderboard`, `/view_bomb_leaderboard`, `/view_cluster_leaderboard`
  * `/upload_replay` — the one surface where the tier's LABEL is what gets stored
  * the autocomplete callback, invoked directly with an interaction double
"""
from __future__ import annotations

import os

import pytest

from tier_types import (
    MYTHIC_3_KEY,
    MYTHIC_3_LABEL,
    MYTHIC_4_KEY,
    MYTHIC_4_LABEL,
    TierReader,
)
from tier_types import GUILD_WB, PROD_SERVER_ID, SEASON  # noqa: F401

RED = pytest.mark.skipif(
    os.getenv("SCRAPCODE_RED_GATE") != "1",
    reason="RED scaffold — enable one at a time in DELIVER",
)

VIEW_COMMANDS = [
    "/view_leaderboard",
    "/view_bomb_leaderboard",
    "/view_cluster_leaderboard",
]


# ===========================================================================
# The union: what the picker knows
# ===========================================================================

@RED
@pytest.mark.real_io
@pytest.mark.driving_port
@pytest.mark.kpi
async def test_a_tier_present_only_in_the_stored_data_is_offered(
    sqlite_repo, registered_guilds, seed_hits
):
    """AC-006.1 — the whole point of the slice.

    Without this, Slice 04 has achieved nothing Slice 02 did not: a
    registry-derived list is still fixed at command-sync time, so the picker
    cannot show a tier the running process did not know at startup.

    The tier being offered on the strength of STORED ROWS ALONE is the proof.
    Staged by seeding rows at a key held out of the registry — a real row, a
    real query, a real gap.
    """
    seed_hits(MYTHIC_4_KEY)
    raise AssertionError("RED scaffold — autocomplete, assert Mythic 4 offered")


@RED
@pytest.mark.driving_port
async def test_a_registered_tier_with_no_hits_is_still_offered(
    sqlite_repo, registered_guilds
):
    """AC-006.6 — registry ∪ observed, never observed alone.

    Two consequences, both load-bearing. One malformed row cannot define the
    tier list. And a tier can be selected BEFORE its first hit lands, which is
    what makes the picker usable on the day a season opens rather than an hour
    later.
    """
    raise AssertionError("RED scaffold — empty tier from registry, assert offered")


@RED
@pytest.mark.driving_port
async def test_typing_narrows_the_offered_tiers(sqlite_repo, registered_guilds):
    """AC-006.5 — Discord's cap handled by FILTERING, not by truncation.

    A truncated unfiltered list silently omits exactly the tier being looked
    for, since the interesting tiers are the newest and sort last. The
    difference between filtering and truncating is invisible until the list
    grows, and then it is invisible in the worst possible way.
    """
    raise AssertionError("RED scaffold — >25 tiers, type a prefix, assert <=25 filtered")


# ===========================================================================
# The submitted value
# ===========================================================================

@RED
@pytest.mark.real_io
@pytest.mark.driving_port
@pytest.mark.parametrize("command", VIEW_COMMANDS)
async def test_a_chosen_tier_finds_its_stored_hits(
    sqlite_repo, registered_guilds, seed_hits, command
):
    """AC-006.2 — the label resolves to the stored key BEFORE any query runs.

    The failure mode this guards is a command that queries for the LABEL and
    returns nothing: an empty board, indistinguishable from a tier nobody
    cleared. Which is the ambiguity the feature exists to remove, reintroduced
    at the last step.
    """
    seed_hits(MYTHIC_3_KEY)
    raise AssertionError("RED scaffold — submit the label, assert the rows come back")


@RED
@pytest.mark.driving_port
@pytest.mark.error
async def test_text_matching_no_tier_is_refused_by_name(sqlite_repo, registered_guilds):
    """AC-006.4.

    Autocomplete does not CONSTRAIN input — a user can submit anything. Three
    assertions: it says the tier is unknown, it names the valid ones, and it
    shows no board. The third is the one that matters; the first two are how
    the operator recovers without asking anybody.
    """
    from bot import tiers

    assert tiers.resolve("Mythic 99") is None
    raise AssertionError("RED scaffold — drive the command, assert the named refusal")


@RED
@pytest.mark.real_io
@pytest.mark.driving_port
@pytest.mark.error
async def test_a_replay_submitted_through_the_picker_can_be_found_again(
    sqlite_repo, registered_guilds
):
    """DESIGN Open Question 4, closed here as a ROUND TRIP.

    `/upload_replay` is the one surface where the tier's LABEL is what gets
    stored (`replay_cog.py:208`) and rendering filters on it
    (`replay_cog.py:54`). A picker-only assertion would pass against an
    implementation that offers the right label and stores something else, and
    the replay would be unfindable from the moment it was uploaded.

    Submit, then retrieve. Anything less tests half of a two-sided contract.
    """
    raise AssertionError("RED scaffold — upload with a picked tier, then /get_replay")


# ===========================================================================
# AC-006.3 — the hypothesis, as an assertion
# ===========================================================================

@RED
@pytest.mark.architecture
@pytest.mark.kpi
@pytest.mark.parametrize("reader", list(TierReader), ids=lambda r: r.path)
def test_the_modules_that_read_a_tier_are_untouched(reader: TierReader):
    """AC-006.3 — FIVE MODULES, NOT ONE.

    DISCUSS framed the dependency as "the three `embeds.py` call sites" and
    recommended a one-hour SPIKE to confirm. The SPIKE, run during DESIGN,
    returned 26 raid-tier reads across five modules — wrong by an order of
    magnitude.

    The design is unaffected and arguably vindicated: `Tier` is structurally
    compatible with `app_commands.Choice[str]`, so all 26 sites keep working
    unmodified. With three sites a plain refactor would have been viable; at 26
    the structural compatibility is the only tractable option.

    What changed is the VERIFICATION SURFACE, which is why this is parametrized
    over the enum rather than asserted about `embeds.py`. If the Slice 04 diff
    touches any of these five, the hypothesis is disproved regardless of
    whether the tests pass — that is what makes it a hypothesis and not a
    preference.
    """
    raise AssertionError(
        f"RED scaffold — assert {reader.path} is unmodified by the slice-04 diff"
    )


@RED
@pytest.mark.architecture
@pytest.mark.error
def test_the_permission_tier_commands_are_untouched():
    """ADR-009 D10, as a behavioural assertion.

    A mechanical rename across everything called `tier` breaks
    `/scrapcode_help` and `/config_role_tier`, and BOTH WOULD STILL TYPE-CHECK.
    That is why this is a test rather than a comment: the language will not
    catch it, the reviewer probably will not either, and the symptom is a
    command that fails at invocation rather than at import.
    """
    raise AssertionError("RED scaffold — drive both permission-tier commands")


@RED
@pytest.mark.real_io
@pytest.mark.error
async def test_the_rollback_backend_can_still_answer_what_tiers_exist(json_repo):
    """The deployment hazard in this feature, as a test.

    `list_tier_keys` joins the shared repository interface in this slice. An
    abstract method added to the interface makes the file-based implementation
    UNINSTANTIABLE until it implements it — so shipping the interface change
    without the file-based implementation breaks the documented rollback path
    at process start, with a TypeError, precisely when somebody is reaching for
    it under pressure.

    Both implementations ship in the same commit or neither does. This test is
    what makes that non-negotiable rather than remembered.
    """
    assert json_repo is not None, "the file-based repository could not be constructed"
    raise AssertionError("RED scaffold — call list_tier_keys on the JSON adapter")
