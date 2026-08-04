"""Properties of the quarantine tombstone that outlives `/deregister_guild` (step 08-03).

WHY-NEW-FILE: tests/unit/test_quarantine_tombstone_history.py
  CLOSEST-EXISTING: tests/unit/test_replace_guild_key_refusals.py
  EXTENSION-COST: that module's universe is six storage slots read for ONE
    guild's key columns, and every property there enters through the driven
    port `ClusterRepository.replace_guild_key` directly. The claims below
    enter through the `/deregister_guild` and `/register_guild` cog callbacks
    — which need a Discord interaction double, a patched
    `fetch_guild_snapshot`, and a universe partitioned target/sibling because
    the whole point is that the CASCADE empties one side and must not touch
    the other. Extending means every existing property re-declares eight new
    slots it does not care about and drags an interaction double into a pure
    repository test.
  PARALLEL-RATIONALE: different lifecycle. Those properties observe a write
    that is FORBIDDEN to cascade; these observe the one command whose cascade
    is intended, and assert that exactly one row escapes it. A strict delta
    cannot be shared between a surface that forbids deletion and one that
    requires it.

WHAT IS BEING QUANTIFIED. `/deregister_guild` deletes the `guilds` row;
`PRAGMA foreign_keys=ON` plus `ondelete="CASCADE"` then destroys the players,
the hits AND the binding. Destroying them is intended (operator, 2026-08-02).
Losing the QUARANTINE with them is not: an admin who quarantined a guild on
Monday, deregistered it on Tuesday and re-registered it on Wednesday has
undone the quarantine without ever being told one existed, and the drifted key
— the incident itself — is now the bound identity, adopted on purpose.

The retention mechanism is a TOMBSTONE ROW in `guild_key_quarantine_history`
with NO foreign key to `guilds` (DELIVER's answer to UI-11, recorded in
`docs/feature/guild-key-integrity/feature-delta.md`). No FK is the entire
design: anything with one dies in the same CASCADE as the binding it is meant
to outlive.

DECLARED UNIVERSE, strict, partitioned by guild:

    history.target      — tombstone rows for the guild being deregistered
    history.sibling     — tombstone rows for the guild that is not
    guilds.target       — the registry row the command deletes
    guilds.sibling
    bindings.target     — the binding the CASCADE destroys
    bindings.sibling
    players.target      — the roster the CASCADE destroys
    players.sibling

The sibling half is not decoration. "A tombstone is written exactly when the
binding was quarantined" is a claim about ONE guild; an implementation that
tombstoned every quarantined binding in the cluster, or that widened the
delete past the named guild, satisfies the target half and shows up here as a
sibling slot that moved. `_assert_state_delta` is strict — a slot with no
declared predicate must be byte-identical — so a slot moving that no property
declared is a failure rather than an omission.

PARADIGM. The two storage properties are property-based over binding states,
per the step's test paradigm. The reply-text assertion is a GOLDEN example and
is exempt: the deliverable there is the literal words an operator reads at
re-registration, and a property over rendered strings would assert less than
the string itself does. The JSON degradation check is a single example because
ADR-006 D9 is a claim about one call shape — "returns empty rather than
raising" — with no input space to quantify over.
"""
from __future__ import annotations

import os

# `bot.guilds` evaluates `repo = build_repo()` at import time and reads the
# environment at that moment. Pin a harmless backend before any `bot.*` import
# so collection cannot construct a repository pointed at a live tree. Same
# precedent as `tests/unit/test_replace_guild_key_refusals.py`.
os.environ.setdefault("SCRAPCODE_REPO_BACKEND", "json")

# `bot/cogs/admin_cog.py` imports `config`, which reads these two at import
# time and `int()`s them unconditionally. Neutral values, so a cog imported
# here cannot inherit a channel id from a developer's `.env`. Same defaults
# the acceptance conftest sets.
os.environ.setdefault("UPDATE_CHANNEL_ID", "0")
os.environ.setdefault("REPLAY_INDEX_CHANNEL_ID", "0")

import base64  # noqa: E402
import sqlite3  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

hypothesis = pytest.importorskip(
    "hypothesis",
    reason="hypothesis is not installed — DISTILL pins it into requirements.txt",
)

import hypothesis.strategies as st  # noqa: E402
from hypothesis import given, settings  # noqa: E402

# Deselected from the 250-test baseline for the same reason the slice-06
# acceptance module is: these belong to the remediation slice, and the baseline
# command is the "nothing that shipped has regressed" gate.
pytestmark = [pytest.mark.property, pytest.mark.slice_06]

SERVER_ID = 1458181638453203099
GUILD_TARGET = "word_bearers"
GUILD_SIBLING = "dark_mechanicum"
TARGET_KEY = "wb-key-not-in-any-reply"
SIBLING_KEY = "dm-key-not-in-any-reply"

FERNET_KEY = base64.urlsafe_b64encode(b"guild-key-integrity-unit-tests!!"[:32]).decode()

UNIVERSE = (
    "history.target",
    "history.sibling",
    "guilds.target",
    "guilds.sibling",
    "bindings.target",
    "bindings.sibling",
    "players.target",
    "players.sibling",
)

# What the CASCADE destroys on the named guild, every time, quarantined or
# not. Declared once because all three properties assert the same deletion —
# the axis under test is what SURVIVES it.
_THE_CASCADE = {
    "guilds.target": 0,
    "bindings.target": 0,
    "players.target": 0,
}

_TAGS = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ", min_size=3, max_size=5)
_NAMES = st.text(min_size=1, max_size=24).filter(lambda candidate: candidate.strip())
_UUIDS = st.uuids().map(str)


def _key_statuses():
    """Every stored `key_status`, including values no migration ever wrote.

    The third axis is what makes the claim an "if and only if". Without it the
    property would only say "a tombstone is written when the status is not
    active", which an implementation that tombstoned every UNBOUND guild also
    satisfies — and that implementation writes a quarantine warning for guilds
    that were never quarantined, which is worse than none.
    """
    from bot.services.tacticus.guild_client import KeyStatus

    return st.one_of(
        st.just(KeyStatus.ACTIVE.value),
        st.just(KeyStatus.QUARANTINED.value),
        st.text(max_size=12),
    )


@st.composite
def _bindings(draw):
    """A binding in an arbitrary state, plus the observed uuid its reason embeds."""
    from bot.repository import GuildBinding

    observed_uuid = draw(_UUIDS)
    bound_tag = draw(_TAGS)
    return (
        GuildBinding(
            tacticus_guild_id=draw(_UUIDS),
            tacticus_guild_tag=bound_tag,
            tacticus_guild_name=draw(_NAMES),
            identity_bound_at="2026-07-30T04:00:00.000Z",
            key_status=draw(_key_statuses()),
            quarantine_reason=(
                f"key drift: bound 【{bound_tag}】 but resolves to 【DRIFT】 "
                f"— observed={observed_uuid}"
            ),
            quarantined_at="2026-07-31T04:00:00.000Z",
        ),
        observed_uuid,
    )


# ===========================================================================
# Storage — one migrated database, reset to a known baseline per example
# ===========================================================================

@pytest.fixture(scope="module")
def storage(tmp_path_factory):
    """One migrated database for the whole module.

    Module-scoped deliberately: Hypothesis rejects function-scoped fixtures
    under `@given`, and running alembic per generated example would put a
    schema migration inside the inner loop of a property test. Every example
    calls `_reset` before it acts, so the state it enters on is fully
    determined by that example rather than inherited from the previous one.
    """
    from alembic import command
    from alembic.config import Config

    import bot.db
    from bot.repository_sqlalchemy import SqlAlchemyClusterRepository

    db_path = tmp_path_factory.mktemp("quarantine-history") / "data" / "scrapcode.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    db_pkg = Path(bot.db.__file__).parent
    cfg = Config(str(db_pkg / "alembic.ini"))
    cfg.set_main_option("script_location", str(db_pkg / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "head")

    return _Storage(
        repo=SqlAlchemyClusterRepository(db_path=str(db_path), fernet_key=FERNET_KEY),
        db_path=db_path,
    )


class _Storage:
    """The repository under test and the file its rows live in.

    The file is carried alongside because the universe is read with plain
    `sqlite3`, outside the ORM and outside the repository: the question is
    whether the ROWS survived a CASCADE, and asking through a repository that
    filters by server id could not distinguish "the row is gone" from "the row
    is invisible to this reader".
    """

    def __init__(self, *, repo, db_path: Path) -> None:
        self.repo = repo
        self.db_path = db_path


@pytest.fixture(scope="module")
def live_repo(storage):
    """Point `bot.guilds.repo` at the migrated database for this module.

    `bot/guilds.py` binds `repo` at import time; every wrapper resolves it as a
    module global at CALL time, so patching the attribute is what a cog driven
    in-process actually reads.

    Module-scoped with its own `MonkeyPatch` context rather than the
    function-scoped `monkeypatch` fixture: Hypothesis rejects function-scoped
    fixtures under `@given`, and the alternative — suppressing that health
    check — would hide the real hazard it exists to report, which is state
    leaking between generated examples. `_reset` handles that explicitly.
    """
    import bot.guilds as guilds_mod

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(guilds_mod, "repo", storage.repo)
        yield storage


def _reset(storage: _Storage, *, binding=None) -> None:
    """Put both guilds back to a declared state before an example.

    Raw SQL for the deletes — the tables are being emptied, not exercised —
    and the repository for the writes, so the rows an example acts on are the
    shape production writes rather than a hand-assembled approximation.
    """
    from bot.models import Cluster, Guild
    from bot.repository import GuildBinding

    conn = sqlite3.connect(str(storage.db_path))
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        for table in ("guild_key_quarantine_history", "guild_key_bindings",
                      "players", "guilds"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()

    storage.repo.save(Cluster(
        discord_server_id=SERVER_ID,
        guilds={
            GUILD_TARGET: Guild(
                id=GUILD_TARGET, name="Word Bearers", api_key=TARGET_KEY, role_id=1,
            ),
            GUILD_SIBLING: Guild(
                id=GUILD_SIBLING, name="Dark Mechanicum", api_key=SIBLING_KEY, role_id=2,
            ),
        },
    ))
    if binding is not None:
        storage.repo.save_guild_binding(SERVER_ID, GUILD_TARGET, binding)
    # The sibling is bound and healthy: a tombstone written for it would be a
    # tombstone written for a guild nobody deregistered.
    storage.repo.save_guild_binding(SERVER_ID, GUILD_SIBLING, GuildBinding(
        tacticus_guild_id="1f2e3d4c-5b6a-7089-9a8b-7c6d5e4f3a2b",
        tacticus_guild_tag="DMEC",
        tacticus_guild_name="Dark Mechanicum",
        identity_bound_at="2026-07-30T04:00:00.000Z",
    ))
    _seed_players(storage.db_path)


def _seed_players(db_path: Path) -> None:
    """One roster row per guild — what the CASCADE is expected to destroy on
    the named guild and forbidden to touch on the other."""
    conn = sqlite3.connect(str(db_path))
    try:
        for guild_id in (GUILD_TARGET, GUILD_SIBLING):
            conn.execute(
                "INSERT INTO players (discord_server_id, guild_id, tacticus_user_id, "
                "display_name, last_validated, is_former) VALUES (?, ?, ?, ?, ?, 0)",
                (SERVER_ID, guild_id, f"uid-{guild_id}", "Player One",
                 "2026-07-31T04:00:00Z"),
            )
        conn.commit()
    finally:
        conn.close()


def _capture(db_path: Path) -> dict:
    """Snapshot every declared universe slot."""
    conn = sqlite3.connect(str(db_path))
    try:
        def _count(table: str, guild_id: str) -> int:
            return conn.execute(
                f"SELECT COUNT(*) FROM {table} "
                "WHERE discord_server_id = ? AND guild_id = ?",
                (SERVER_ID, guild_id),
            ).fetchone()[0]

        return {
            f"{slot}.{side}": _count(table, guild_id)
            for slot, table in (
                ("history", "guild_key_quarantine_history"),
                ("guilds", "guilds"),
                ("bindings", "guild_key_bindings"),
                ("players", "players"),
            )
            for side, guild_id in (("target", GUILD_TARGET), ("sibling", GUILD_SIBLING))
        }
    finally:
        conn.close()


def _assert_state_delta(before: dict, after: dict, expected: dict) -> None:
    """Strict: every universe slot without a declared predicate is unchanged.

    Strict is the whole point. The bug class this guards is "the code did the
    right thing to the slot the test looked at, and something else to the one
    it did not" — an implicit-unchanged assertion over the declared universe
    turns that from invisible into a failure.
    """
    assert set(before) == set(UNIVERSE) == set(after), (
        "the capture drifted from the declared universe — a slot was added or "
        "removed without being declared, so nothing asserts on it"
    )
    for slot in UNIVERSE:
        if slot not in expected:
            assert after[slot] == before[slot], (
                f"{slot} moved during an operation that declared it unchanged: "
                f"{before[slot]!r} -> {after[slot]!r}"
            )
            continue
        assert after[slot] == expected[slot], (
            f"{slot} is {after[slot]!r}, expected {expected[slot]!r}"
        )


def _tombstones(db_path: Path, guild_id: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT * FROM guild_key_quarantine_history "
            "WHERE discord_server_id = ? AND guild_id = ?",
            (SERVER_ID, guild_id),
        ).fetchall()
    finally:
        conn.close()


# ===========================================================================
# Driving the real cog callbacks — no seam that bypasses them
# ===========================================================================

class _Response:
    def __init__(self, interaction) -> None:
        self._interaction = interaction

    async def send_message(self, content="", *, embed=None, ephemeral=False, **kwargs):
        self._interaction.replies.append(content)

    async def defer(self, *, ephemeral=False, **kwargs):
        return None

    def is_done(self) -> bool:
        return bool(self._interaction.replies)


class _Followup:
    def __init__(self, interaction) -> None:
        self._interaction = interaction

    async def send(self, content="", *, embed=None, ephemeral=False, **kwargs):
        self._interaction.replies.append(content)


class _Interaction:
    def __init__(self) -> None:
        self.guild_id = SERVER_ID
        self.replies: list[str] = []
        self.extras: dict = {}
        self.response = _Response(self)
        self.followup = _Followup(self)

    @property
    def all_replies(self) -> str:
        return "\n".join(self.replies)


class _Role:
    def __init__(self, role_id: int) -> None:
        self.id = role_id
        self.mention = f"<@&{role_id}>"


def _command(name: str):
    from bot.cogs.admin_cog import AdminCog

    for cmd in AdminCog.__cog_app_commands__:
        if cmd.name == name:
            return cmd
    raise AssertionError(f"no `{name}` command is registered on AdminCog")


async def _invoke(command_name: str, interaction, /, **kwargs) -> None:
    """Call the real callback. The permission checks are decorators on the
    command, not behaviour under test here, so they are not re-run — the
    acceptance suite drives them.

    Positional-only: `/register_guild` takes a `name` parameter, and a keyword
    of that name would otherwise collide with this helper's own."""
    from bot.cogs.admin_cog import AdminCog
    from bot.services.chronicl3r.player_service import PlayerService

    cog = AdminCog.__new__(AdminCog)
    cog.player_service = PlayerService(_ChroniclerClient())
    await _command(command_name).callback(cog, interaction, **kwargs)


class _ChroniclerClient:
    def authenticate(self) -> None:
        return None

    def register_user(self, tacticus_user_id: str) -> dict:
        return self.get_profile(tacticus_user_id)

    def get_profile(self, tacticus_user_id: str) -> dict:
        return {
            "tacticus_user_id": tacticus_user_id,
            "tacticus_display_nm": f"player-{tacticus_user_id}",
        }


# ===========================================================================
# Property 1 — a tombstone is written exactly when the binding was quarantined
# ===========================================================================

@settings(max_examples=40, deadline=None)
@given(binding_and_observed=_bindings())
async def test_a_tombstone_is_written_exactly_when_the_binding_was_quarantined(
    live_repo, binding_and_observed,
):
    """The retention rule, quantified over every state a binding can be in.

    "Exactly when" is the claim, and both halves of it are defects. Writing no
    tombstone for a quarantined binding launders the incident — that is
    AC-009.5. Writing one for a healthy binding puts the word "quarantined" in
    front of an admin re-registering a guild that never was, which trains them
    to ignore the warning that matters. The generated `key_status` axis
    includes values no migration ever wrote so the branch cannot be satisfied
    by "not active".

    The CASCADE itself is asserted alongside, not because this step changes it
    — it is intended — but because a tombstone written on a path that no
    longer deletes anything would satisfy the history claim while silently
    reverting the operator's 2026-08-02 decision.
    """
    from bot.services.tacticus.guild_client import KeyStatus

    binding, _observed_uuid = binding_and_observed
    _reset(live_repo, binding=binding)
    before = _capture(live_repo.db_path)

    await _invoke("deregister_guild", _Interaction(), guild_id=GUILD_TARGET)

    expected = dict(_THE_CASCADE)
    if binding.key_status == KeyStatus.QUARANTINED.value:
        expected["history.target"] = before["history.target"] + 1
    _assert_state_delta(before, _capture(live_repo.db_path), expected)


# ===========================================================================
# Property 2 — what the surviving row carries, and what it must not
# ===========================================================================

@settings(max_examples=40, deadline=None)
@given(binding_and_observed=_bindings())
async def test_the_tombstone_carries_both_identities_and_no_key_material(
    live_repo, binding_and_observed,
):
    """A tombstone that cannot say WHICH identities drifted is a warning with
    nothing in it.

    Three fields make the warning actionable and are quantified rather than
    pinned: the bound identity (what the guild was), the observed identity
    (what its key had drifted to — recovered from `quarantine_reason`, the
    codebase's single carrier for it), and `quarantined_at` (when). KPI-6 is
    the fourth claim and is asserted over the WHOLE row rendered as text: the
    tombstone outlives every other trace of the guild, so a key value written
    into it is a leak with no expiry.
    """
    from bot.services.tacticus.guild_client import KeyStatus

    binding, observed_uuid = binding_and_observed
    if binding.key_status != KeyStatus.QUARANTINED.value:
        return
    _reset(live_repo, binding=binding)

    await _invoke("deregister_guild", _Interaction(), guild_id=GUILD_TARGET)

    rows = _tombstones(live_repo.db_path, GUILD_TARGET)
    assert len(rows) == 1, "the quarantine history did not survive the CASCADE"
    row = dict(rows[0])
    assert row["tacticus_guild_id"] == binding.tacticus_guild_id, (
        "the tombstone does not name the identity the guild was bound to"
    )
    assert row["observed_tacticus_guild_id"] == observed_uuid, (
        "the tombstone does not name the identity the key had drifted to"
    )
    assert row["quarantined_at"] == binding.quarantined_at, (
        "the tombstone does not say when the quarantine happened"
    )
    rendered = " ".join(str(value) for value in row.values())
    for label, material in _forbidden_disclosures(TARGET_KEY).items():
        assert material and material not in rendered, (
            f"the {label} was written into the tombstone: {rendered!r}"
        )


def _forbidden_disclosures(api_key: str) -> dict[str, str]:
    """Everything derived from the guild's key that KPI-6 says appears in zero
    records. The hmac and the ciphertext are the ones a plaintext-only
    assertion misses: they are the STORED artefacts, and the tombstone is
    written from a row that holds both."""
    from bot.db.secrets import api_key_hmac, encrypt_api_key

    return {
        "plaintext key": api_key,
        "api_key_hmac": api_key_hmac(api_key, FERNET_KEY) or "",
        "Fernet ciphertext": encrypt_api_key(api_key, FERNET_KEY),
    }


# ===========================================================================
# Golden — the words an admin reads when they re-register the slug
# ===========================================================================

async def test_re_registering_a_slug_with_quarantine_history_says_so_and_is_allowed(
    live_repo, monkeypatch,
):
    """PARADIGM EXEMPTION — golden assertion on an operator-facing string.

    The deliverable is the literal words: "quarantined" has to appear, because
    that is the one term the admin can search for and act on, and the
    registration has to SUCCEED, because refusing it would break legitimate
    re-registration and the operator's decision of 2026-08-02 is that
    deregistering destroys data by design. A property over rendered text would
    assert strictly less than the two claims below.
    """
    from bot.repository import GuildBinding, QuarantineTombstone
    from bot.services.tacticus.guild_client import (
        GuildIdentity, GuildSnapshot, KeyStatus, ProbeOutcome,
    )
    import bot.services.tacticus.guild_client as guild_client

    _reset(live_repo, binding=GuildBinding(
        tacticus_guild_id="0a1b2c3d-4e5f-6071-8293-a4b5c6d7e8f9",
        tacticus_guild_tag="WBRS",
        tacticus_guild_name="Word Bearers",
        identity_bound_at="2026-07-30T04:00:00.000Z",
        key_status=KeyStatus.QUARANTINED.value,
        quarantine_reason=(
            "key drift: bound 【WBRS】 but resolves to 【DMEC】 "
            "— observed=1f2e3d4c-5b6a-7089-9a8b-7c6d5e4f3a2b"
        ),
        quarantined_at="2026-07-31T04:00:00.000Z",
    ))

    async def _drifted(api_key: str) -> GuildSnapshot:
        return GuildSnapshot(
            outcome=ProbeOutcome.MATCH,
            identity=GuildIdentity(
                uuid="1f2e3d4c-5b6a-7089-9a8b-7c6d5e4f3a2b",
                tag="DMEC", name="Dark Mechanicum",
            ),
            members=["uid-1"],
        )

    monkeypatch.setattr(guild_client, "fetch_guild_snapshot", _drifted)

    await _invoke("deregister_guild", _Interaction(), guild_id=GUILD_TARGET)
    reregistration = _Interaction()
    await _invoke(
        "register_guild", reregistration,
        name="Word Bearers", guild_id=GUILD_TARGET,
        api_key="the-drifted-key", role=_Role(role_id=1),
    )

    reply = reregistration.all_replies
    assert "quarantin" in reply.lower(), (
        f"the re-registration never mentioned the quarantine history: {reply!r}"
    )
    assert GUILD_TARGET in live_repo.repo.load(SERVER_ID).guilds, (
        "the history was used to REFUSE the re-registration — it may only be "
        "surfaced (AC-009.5)"
    )
    assert isinstance(
        live_repo.repo.list_quarantine_tombstones(SERVER_ID, GUILD_TARGET)[0],
        QuarantineTombstone,
    ), "the port returns rows, not the port-level value the cogs read"


# ===========================================================================
# ADR-006 D9 — the rollback adapter degrades, it does not raise
# ===========================================================================

def test_the_json_rollback_adapter_degrades_to_no_history(tmp_path):
    """`SCRAPCODE_REPO_BACKEND=json` is the rollback an operator reaches for
    under time pressure. On that path the history must read back EMPTY and the
    write must be dropped — never raise. A half-working warning would take out
    `/deregister_guild` for the operator who rolled back to restore service,
    which is the failure ADR-006 D9 exists to rule out. Same degradation as
    the binding methods on this adapter.

    Single example rather than a property: the contract has no input space —
    it is "this call shape returns empty and that one is a no-op".
    """
    from bot.repository import JsonClusterRepository, QuarantineTombstone

    repo = JsonClusterRepository(base_path=tmp_path / "clusters")
    repo.record_quarantine_tombstone(SERVER_ID, QuarantineTombstone(
        guild_id=GUILD_TARGET,
        tacticus_guild_id="0a1b2c3d-4e5f-6071-8293-a4b5c6d7e8f9",
        quarantined_at="2026-07-31T04:00:00.000Z",
    ))
    assert repo.list_quarantine_tombstones(SERVER_ID, GUILD_TARGET) == []
