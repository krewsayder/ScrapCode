"""Slice 06 — admin command safety. Implements
`acceptance/slice-06-admin-command-safety.feature`.

AUTHORED IN DISTILL, 2026-08-02. Expected RED; the failures are the
deliverable. See `docs/feature/guild-key-integrity/distill/red-classification.md`.

Every scenario in this module is on an error path, deliberately. KPI-6 is
recorded as holding "by construction", and for the SUCCESS paths it does:
`_KeyContext` cannot carry a plaintext key, so no record it emits can leak
one however it is later serialised. The claim was never tested against the
paths where nobody built a context at all — where a raw `IntegrityError`
from SQLAlchemy carries the Fernet ciphertext and the full 64-hex
`api_key_hmac` inside its `[parameters: ...]` tail, straight into
`main.py`'s generic handler, which prints it and sends it to Discord.

`_invoke_admin_command_with_main_py_error_handler` reproduces that handler
rather than letting the exception surface in the test, because the leak
happens IN the handler and a scenario that stopped at the raise would prove
the exception exists while missing what it discloses.
"""
from __future__ import annotations

import pytest

from domain_types import DARK_MECHANICUM, WORD_BEARERS, KeyStatus, ProbeOutcome
from conftest import (
    GUILD_DM,
    GUILD_WB,
    PROD_SERVER_ID,
    SEASON,
    GuildServiceResponse,
)

pytestmark = pytest.mark.slice_06

SHARED_KEY = "one-key-two-guilds"

# What a confirmation button says, in the words a Discord admin would read.
# Matched against `label` and `custom_id` so a view built either way is found.
# See `_confirm_if_awaiting` — AC-009.4 pins the guarantee, not the widget, so
# this list is deliberately generous rather than an agreed constant production
# has to import.
_CONFIRMATION_WORDS = (
    "confirm", "yes", "proceed", "delete", "destroy", "deregister",
)


# ===========================================================================
# AC-009.1 / AC-009.2 — the KPI-6 disclosure
# ===========================================================================

@pytest.mark.kpi
@pytest.mark.error
@pytest.mark.driving_port
@pytest.mark.parametrize("force", [False, True], ids=["no-force", "force"])
async def test_a_key_held_by_a_sibling_is_refused_without_disclosing_it(
    sqlite_repo, registered_guilds, fake_guild_service, caplog, force: bool,
):
    """AC-009.1 / AC-009.2 / KPI-6 — the confirmed disclosure.

    `guilds.api_key_hmac` is UNIQUE table-global
    (`models.py:87`, `0001_baseline_schema.py:60`). Nothing on the key-write
    path catches `IntegrityError`: the only catch, at
    `repository_sqlalchemy.py:620`, is scoped to the replay-URL constraint.
    So installing on guild B a key that guild A already holds raises out of
    `replace_guild_key`, through `install_guild_key`, out of the cog callback,
    and into `main.py:91-97`, which does BOTH
    `print(f"Command error: {error}")` and
    `followup.send(f"❌ An error occurred: {error}")`.

    SQLAlchemy renders `IntegrityError` with the bound parameters inlined, so
    the string carries the Fernet ciphertext of the key AND the full 64-hex
    hmac. That reaches `discord.log`, the systemd journal, and a Discord
    message — three copies of material KPI-6 says appears in zero records.
    The hmac is not reversible, but it is a stable fingerprint: anyone
    holding it can confirm whether a key they possess is the one installed.

    Parametrized over `force` because the collision fires before the force
    branch is ever consulted — `replace_guild_key` is called on all three
    install paths — so a fix that only guards the non-forced path would leave
    the disclosure fully present behind one extra argument.

    The assertions look for the STORED artefacts (ciphertext, hmac) and for
    SQL, not for the plaintext: the plaintext is not what SQLAlchemy inlines,
    so a test that only looked for it would pass while the leak was total.
    """
    _install_key_directly(GUILD_DM, SHARED_KEY)
    fake_guild_service.program(
        SHARED_KEY, GuildServiceResponse(identity=WORD_BEARERS, members=["u1"])
    )
    secrets = _key_material_for(SHARED_KEY)

    interaction = _admin_interaction()
    with _tacticus_answered_by(fake_guild_service):
        await _invoke_admin_command_with_main_py_error_handler(
            "update_guild_key", interaction,
            guild_id=GUILD_WB, api_key=SHARED_KEY, force=force,
        )

    reply = interaction.all_replies
    assert GUILD_DM in reply or "Dark Mechanicum" in reply, (
        "the refusal did not name the guild that already holds the key, so "
        f"the admin cannot act on it: {reply!r}"
    )
    for label, material in secrets.items():
        assert material not in reply, (
            f"the {label} reached a Discord message: {reply!r}"
        )
        assert material not in caplog.text, (
            f"the {label} reached the log"
        )
    for sql_marker in ("INSERT INTO", "UPDATE ", "[parameters:", "sqlite3.IntegrityError"):
        assert sql_marker not in reply, (
            f"raw SQL ({sql_marker!r}) reached a Discord message: {reply!r}"
        )


@pytest.mark.driving_port
async def test_a_legitimate_forced_rebind_still_succeeds(
    sqlite_repo, registered_guilds, bound_guild, fake_guild_service
):
    """AC-009.3 / AC-003.4 — the regression guard the typed refusal must not eat.

    AC-003.4 is force-rebind: an admin deliberately re-points a guild at the
    identity its new key resolves to. Where no collision exists that path
    must keep working exactly as it does — a slice that closed the
    disclosure by refusing every force would satisfy AC-009.1 and remove the
    only sanctioned way to re-point a binding.
    """
    from bot.guilds import load_guild_binding
    import bot.guild_keys as guild_keys

    fake_guild_service.program(
        "a-key-nobody-else-holds",
        GuildServiceResponse(identity=DARK_MECHANICUM, members=["dm1"]),
    )

    with _tacticus_answered_by(fake_guild_service):
        result = await guild_keys.install_guild_key(
            PROD_SERVER_ID, GUILD_WB, "a-key-nobody-else-holds", force=True
        )

    assert result.forced is True
    binding = load_guild_binding(PROD_SERVER_ID, GUILD_WB)
    assert binding.tacticus_guild_id == DARK_MECHANICUM.uuid, (
        "a legitimate force-rebind stopped re-pointing the binding"
    )


# ===========================================================================
# AC-009.4 — /deregister_guild tells the truth
# ===========================================================================

@pytest.mark.error
@pytest.mark.driving_port
async def test_deregistering_states_what_it_destroys_and_waits(
    sqlite_repo, guild_with_recorded_rows, fake_guild_service
):
    """AC-009.4 — the reply is false, and it is false in the reassuring
    direction.

    `admin_cog.py:223-225` replies "⚠️ Their data folder has been left intact
    in case you need it." That was true on JSON, where deregistering edited
    one dict and left the directory alone. Post-cutover `save_guilds` deletes
    the `GuildRow`, `PRAGMA foreign_keys=ON` is live, and every child FK is
    `ondelete="CASCADE"` — players, battle_hits, bomb_hits and the binding
    are all destroyed. Measured: `{players:1, battle_hits:1, bomb_hits:1,
    bindings:1}` → all `0`.

    The operator decision of 2026-08-02 is that destroying the data is
    INTENDED. So this scenario does not ask for a soft delete; it asks for
    the reply to be true and for the command to pause. A command that
    irreversibly deletes a guild's entire raid history with no undo and no
    backup, fired by one keystroke, on the strength of a message saying the
    opposite, is the combination worth breaking.

    "Nothing has been deleted yet" is asserted BEFORE any confirmation, so
    it is satisfied by any confirmation mechanism DELIVER chooses — a button,
    a follow-up, a typed guild id. The scenario pins the guarantee, not the
    widget.
    """
    before = _row_counts(GUILD_WB)
    assert before["players"] > 0 and before["battle_hits"] > 0, (
        "fixture precondition lost — there is nothing for the command to destroy"
    )

    interaction = _admin_interaction()
    with _tacticus_answered_by(fake_guild_service):
        await _invoke_admin_command_with_main_py_error_handler(
            "deregister_guild", interaction, guild_id=GUILD_WB,
        )

    reply = interaction.all_replies
    assert "left intact" not in reply, (
        f"the reply still claims the data survives, which it does not: {reply!r}"
    )
    for label, count in before.items():
        assert str(count) in reply, (
            f"the reply does not state how many {label} rows will be "
            f"destroyed (expected {count}): {reply!r}"
        )
    assert _row_counts(GUILD_WB) == before, (
        "the guild's history was destroyed before the admin confirmed"
    )


@pytest.mark.error
@pytest.mark.driving_port
async def test_re_registering_a_quarantined_slug_does_not_adopt_silently(
    sqlite_repo, registered_guilds, fake_guild_service
):
    """AC-009.5 — two commands, no warning, quarantine laundered.

    The CASCADE that makes `/deregister_guild` destructive also drops the
    binding, so re-registering the same slug returns `is_unbound=True` and
    trust-on-first-use (DDD-8) silently adopts whatever the key now resolves
    to. An admin who quarantined a guild on Monday, deregistered it on
    Tuesday to "start clean", and re-registered it on Wednesday has undone
    the quarantine without ever being told a quarantine existed — and the
    drifted key is now the bound identity, which is the incident, adopted on
    purpose.

    The assertion is on the REPLY rather than on a refusal: refusing outright
    would break legitimate re-registration, and the operator decision on
    deregistration was explicitly that it destroys data by design. Surfacing
    the history is the minimum the slice asks for.

    WHERE the history is retained is DELIVER's call and is deliberately not
    pinned here. The `guild.key.mismatch` records survive the CASCADE and
    carry `observed_id`, which is one candidate; a tombstone row is another.
    Raised in `distill/upstream-issues.md`.

    THE DEREGISTRATION IS ASSERTED, not assumed. AC-009.4 adds a
    confirmation pause to the command this scenario drives on its way to the
    state it is about. If the pause lands and the confirmation is never
    taken, the guild is still registered, `/register_guild` refuses it as
    already-registered, and — once AC-008.1 lands — that refusal names the
    quarantine, so the assertion below would pass while testing a completely
    different scenario. The guard turns that into a loud failure naming the
    seams `_confirm_if_awaiting` knows how to press.
    """
    from bot.guilds import load_guilds

    _quarantine(GUILD_WB, bound=WORD_BEARERS, observed=DARK_MECHANICUM)
    fake_guild_service.program(
        "the-drifted-key",
        GuildServiceResponse(identity=DARK_MECHANICUM, members=["dm1"]),
    )

    interaction = _admin_interaction()
    with _tacticus_answered_by(fake_guild_service):
        await _invoke_admin_command_with_main_py_error_handler(
            "deregister_guild", interaction, guild_id=GUILD_WB,
        )
        await _confirm_if_awaiting(interaction)
        assert GUILD_WB not in load_guilds(PROD_SERVER_ID), (
            "the guild is still registered, so what follows is not a "
            "re-registration and this scenario would assert AC-008.1's "
            "refusal instead of its own. If /deregister_guild now pauses "
            "for confirmation, expose it as a `view=` on the reply or as "
            "`interaction.extras['pending_confirmation']` — see "
            "`_confirm_if_awaiting`"
        )
        reregistration = _admin_interaction()
        await _invoke_admin_command_with_main_py_error_handler(
            "register_guild", reregistration,
            name="Word Bearers", guild_id=GUILD_WB,
            api_key="the-drifted-key", role=_FakeRole(role_id=1),
        )

    reply = reregistration.all_replies
    assert "quarantin" in reply.lower(), (
        "a guild id whose previous binding was quarantined was re-adopted "
        "with no mention of that history — the quarantine was laundered by "
        f"two commands: {reply!r}"
    )


# ===========================================================================
# AC-009.6 / AC-009.7 — the storage-layer holes
# ===========================================================================

@pytest.mark.error
@pytest.mark.real_io
def test_a_parity_rollback_leaves_no_orphaned_bindings(
    sqlite_repo, sqlite_db_path, registered_guilds
):
    """AC-009.6 / DDD-4 — the orphan that re-adopts a drifted key.

    `_DATA_TABLES_DELETE_ORDER` (`migrations_json_to_sqlite.py:65-77`) does
    not list `guild_key_bindings`, and `_rollback_data` deletes with
    `PRAGMA foreign_keys=OFF`, so the CASCADE that would otherwise remove
    them is switched off precisely when it is needed. A parity rollback
    therefore leaves every binding — quarantines included — with no parent
    row, waiting for the next registration of the same slug to silently
    re-adopt them.

    `GuildKeyBindingRow`'s own docstring calls that CASCADE "load-bearing"
    against exactly this scenario. It is load-bearing and it is switched off.

    THE TWO SLICES DESCRIBE ONE STATE FROM TWO ENDS, and until 2026-08-03
    they described it through the same function — AC-008.1 built its `Given`
    by calling `_rollback_data`, which is what this scenario changes. That
    could not survive: satisfying this AC turns that `Given` into an UNBOUND
    guild, which registration correctly adopts. AC-008.1 was split and its
    orphan half (AC-008.1c) now reproduces the residue directly instead of
    calling this function. Nothing here changed; the coupling did. See
    upstream-issues UI-13.

    THIS FIX IS FORWARD-ONLY. It stops NEW orphans; it does not delete the
    rows an earlier rollback already left, and any database that went through
    a parity rollback during the cutover is carrying them. That is why
    AC-008.1c is kept rather than withdrawn — the two guard the same hazard
    independently, and one of them is a tuple literal that was already wrong
    once. Recorded as UI-14.
    """
    from bot.db.migrations_json_to_sqlite import _rollback_data

    _quarantine(GUILD_WB, bound=WORD_BEARERS, observed=DARK_MECHANICUM)
    assert _binding_row_count(sqlite_db_path) > 0, "fixture precondition lost"

    _rollback_data(str(sqlite_db_path))

    assert _binding_row_count(sqlite_db_path) == 0, (
        "a parity rollback left orphaned guild key bindings behind. A later "
        "re-registration of the same slug adopts them without a word"
    )


@pytest.mark.error
def test_blanking_a_guild_key_is_refused(sqlite_repo, registered_guilds):
    """AC-009.7 — the sanctioned write path should not depend on luck.

    `replace_guild_key(server, guild, "")` blanks `api_key` and NULLs
    `api_key_hmac` with no error: `encrypt_api_key("")` returns `""` and
    `api_key_hmac("")` returns `None` (both documented in
    `bot/db/secrets.py`, and both correct in isolation — that is how the
    schema lets several keyless guilds coexist under a UNIQUE constraint).
    Composed, they turn the one method that is allowed to write a key into a
    method that can silently erase one.

    Unreachable through the cog today, because `/update_guild_key` probes
    first and an empty key never returns an identity. That is the argument
    FOR the guard, not against it: the repository method is the sanctioned
    write path (DDD-3), and "it is safe because today's only caller happens
    to validate first" is the same reasoning that left `verify_and_resolve`
    ungated in slice 05.
    """
    before = _stored_key(GUILD_WB)
    assert before, "fixture precondition lost — the guild has no key to blank"

    with pytest.raises(ValueError):
        sqlite_repo.replace_guild_key(PROD_SERVER_ID, GUILD_WB, "")

    assert _stored_key(GUILD_WB) == before, (
        "the guild's key was blanked by a call that should have been refused"
    )


# ===========================================================================
# Helpers — wiring only
# ===========================================================================
from contextlib import contextmanager  # noqa: E402 — helpers-only dependency


def _install_key_directly(guild_id: str, api_key: str) -> None:
    """`Given a guild whose key is X` — through the sanctioned write path.

    `replace_guild_key` and not raw SQL: ADR-006 D7 makes `api_key` +
    `api_key_hmac` one transaction, and a hand-written UPDATE that set only
    one of them would build the collision this scenario is about out of a
    desync the production code cannot produce.
    """
    from bot.guilds import replace_guild_key
    replace_guild_key(PROD_SERVER_ID, guild_id, api_key)


def _key_material_for(api_key: str) -> dict[str, str]:
    """Everything derived from `api_key` that must never be disclosed.

    The hmac is the important one and the one a plaintext-only assertion
    misses: it is what SQLAlchemy inlines into the `IntegrityError` message,
    because it is the column the violated constraint is on.
    """
    import bot.guilds as guilds_mod
    from bot.db.secrets import api_key_hmac, encrypt_api_key

    fernet_key = guilds_mod.repo._fernet_key
    return {
        "plaintext key": api_key,
        "api_key_hmac": api_key_hmac(api_key, fernet_key) or "",
        "Fernet ciphertext": encrypt_api_key(api_key, fernet_key),
    }


def _stored_key(guild_id: str) -> str:
    from bot.guilds import load_guilds
    return load_guilds(PROD_SERVER_ID).get(guild_id, {}).get("api_key", "")


def _row_counts(guild_id: str) -> dict[str, int]:
    """What `/deregister_guild` is about to destroy, in the operator's terms."""
    from bot.guilds import load_player_list
    import bot.guilds as guilds_mod

    def _entries(hits: dict) -> int:
        return sum(
            len(entries)
            for encounters in hits.get("boss_hits", {}).values()
            for tiers in encounters.values()
            for entries in tiers.values()
        )

    return {
        "players": len(load_player_list(PROD_SERVER_ID, guild_id).get("players", {})),
        "battle_hits": _entries(
            guilds_mod.repo.load_battle_hits(PROD_SERVER_ID, guild_id, SEASON)
        ),
        "bomb_hits": _entries(
            guilds_mod.repo.load_bomb_hits(PROD_SERVER_ID, guild_id, SEASON)
        ),
    }


def _binding_row_count(db_path) -> int:
    """Count `guild_key_bindings` rows straight from SQLite.

    Read-only and outside the ORM on purpose: the question is whether the
    ROWS survived a rollback that ran with `PRAGMA foreign_keys=OFF`, and
    asking through a repository that filters by server id would not
    distinguish "no rows" from "no rows this repository can see".
    """
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute("SELECT COUNT(*) FROM guild_key_bindings").fetchone()[0]
    finally:
        conn.close()


def _quarantine(guild_id: str, *, bound, observed) -> None:
    from bot.guilds import save_guild_binding
    from bot.repository import GuildBinding

    save_guild_binding(PROD_SERVER_ID, guild_id, GuildBinding(
        tacticus_guild_id=bound.uuid,
        tacticus_guild_tag=bound.tag,
        tacticus_guild_name=bound.name,
        identity_bound_at="2026-07-31T04:00:00Z",
        key_status=KeyStatus.QUARANTINED.value,
        quarantine_reason=(
            f"key drift: bound 【{bound.tag}】 but resolves to 【{observed.tag}】 "
            f"— observed={observed.uuid}"
        ),
        quarantined_at="2026-07-31T04:00:00.000Z",
    ))


async def _confirm_if_awaiting(interaction) -> None:
    """Take the confirmation step AC-009.4 asks for, if one is offered.

    REWRITTEN 2026-08-03, and the reason is worth stating because the first
    version named a seam production CANNOT implement. It read
    `interaction.pending_confirmation`. `discord.Interaction` declares
    `__slots__` and no `__dict__` (verified against discord.py 2.7.1), so
    `interaction.pending_confirmation = callback` raises `AttributeError`
    against a real interaction. A crafter building to that seam would have
    produced code that satisfied this double and crashed on the first real
    click — the exact class of defect this feature exists to remove, planted
    by its own test helper. The old docstring's "DELIVER wires it here" also
    asked DELIVER to edit an acceptance asset, which it may not do.

    TWO SEAMS ARE SUPPORTED, and both are things production can really do:

      1. A `discord.ui.View` passed as `view=` to `response.send_message` or
         `followup.send`, carrying a button whose label or `custom_id`
         contains one of `_CONFIRMATION_WORDS`. This is the real widget an
         admin clicks and is the expected choice. The double records every
         view; this helper invokes the button's `callback`.
      2. A zero- or one-argument callable stashed in `interaction.extras`
         under `"pending_confirmation"`. `extras` is a real `Interaction`
         slot that discord.py provides for exactly this kind of hand-off, so
         unlike the old attribute it works in production. Offered for a
         confirmation that is not a button (a typed guild id, a modal).

    AC-009.4 still does not pin WHICH — it asserts the guarantee, not the
    widget — and this scenario is about the LATER re-registration, so it must
    keep working before and after that change lands. Today no confirmation is
    offered and this is a no-op.

    IF NEITHER SEAM MATCHES, this stays a no-op and the caller's
    `assert GUILD_WB not in load_guilds(...)` guard fires with instructions.
    That guard is not optional: with a confirmation in place and no way to
    take it, `/deregister_guild` leaves the guild registered, the following
    `/register_guild` is refused as already-registered, and — once AC-008.1
    lands — that refusal names the quarantine, so this scenario would go
    GREEN while asserting AC-008.1's behaviour instead of its own.

    The original interaction is passed to the button rather than a fresh
    click interaction. That is an approximation: a real click arrives on its
    own `Interaction`. It is the right one here because the assertions read
    the reply text, and a double that split the replies across two objects
    would hide a refusal posted on either.
    """
    import inspect

    for view in getattr(interaction, "views", ()):
        for child in getattr(view, "children", ()):
            label = " ".join(
                str(getattr(child, attr, "") or "")
                for attr in ("label", "custom_id")
            ).lower()
            if any(word in label for word in _CONFIRMATION_WORDS):
                await child.callback(interaction)
                return

    stashed = getattr(interaction, "extras", {}).get("pending_confirmation")
    if stashed is None:
        return
    takes_interaction = bool(inspect.signature(stashed).parameters)
    result = stashed(interaction) if takes_interaction else stashed()
    if inspect.isawaitable(result):
        await result


@contextmanager
def _tacticus_answered_by(guild_service):
    import httpx

    real_client = httpx.AsyncClient
    httpx.AsyncClient = lambda *args, **kwargs: _RecordedTacticus(guild_service)
    try:
        yield
    finally:
        httpx.AsyncClient = real_client


class _RecordedTacticus:
    def __init__(self, guild_service) -> None:
        self._guild_service = guild_service

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url: str, headers: dict | None = None, **kwargs):
        from bot.services.tacticus.guild_client import TACTICUS_GUILD_URL

        if url != TACTICUS_GUILD_URL:
            raise AssertionError(f"a command called an endpoint no scenario declared: {url}")
        answer = self._guild_service.answer_for((headers or {}).get("X-API-KEY", ""))
        return answer.as_httpx_response(url)


# ---------------------------------------------------------------------------
# Driving the real AdminCog commands THROUGH main.py's error handler.
# ---------------------------------------------------------------------------

async def _invoke_admin_command_with_main_py_error_handler(
    command_name: str, interaction, **kwargs
) -> None:
    """Drive a real command, then handle any escape exactly as production does.

    `main.py:91-101` is where the disclosure happens, so a scenario that let
    the exception surface into pytest would prove the exception exists and
    say nothing about what it discloses. This reproduces that handler
    verbatim — `print` and the `f"❌ An error occurred: {error}"` reply — so
    the assertions read the string an admin would actually see in Discord.

    It is a faithful copy, not an approximation: if `main.py`'s handler is
    later changed to redact, this helper must be updated with it, and the
    scenarios here would then be testing a handler that no longer exists.
    Slice 06 is explicitly scoped so the fix does NOT depend on redacting
    here — the exception must never reach this handler at all — so these
    scenarios go green by the exception disappearing, not by the handler
    changing.
    """
    from bot.cogs.admin_cog import AdminCog
    from bot.services.chronicl3r.player_service import PlayerService

    cmd = _find_admin_command(command_name)
    for chk in cmd.checks:
        predicate = chk.predicate if hasattr(chk, "predicate") else chk
        if not await predicate(interaction):
            await interaction.response.send_message(
                "❌ You don't have permission to use this command.", ephemeral=True
            )
            return
    cog = AdminCog.__new__(AdminCog)
    cog.player_service = PlayerService(_FakeChroniclerClient())
    try:
        await cmd.callback(cog, interaction, **kwargs)
    except Exception as error:  # noqa: BLE001 — mirrors main.py's bare handler
        print(f"Command error: {error}")
        msg = f"❌ An error occurred: {error}"
        await interaction.followup.send(msg, ephemeral=True)


def _find_admin_command(name: str):
    from bot.cogs.admin_cog import AdminCog

    for cmd in AdminCog.__cog_app_commands__:
        if cmd.name == name:
            return cmd
    raise AssertionError(
        f"no `{name}` command is registered on AdminCog — delete the command "
        "method and this helper errors, which is the port-to-port litmus test"
    )


class _FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id
        self.mention = f"<@&{role_id}>"


class _FakeResponse:
    def __init__(self, interaction) -> None:
        self._interaction = interaction

    async def send_message(self, content="", *, embed=None, ephemeral=False, **kwargs):
        self._interaction._record(content or (getattr(embed, "description", "") or ""))
        self._interaction._offer(kwargs.get("view"))
        self._interaction._replied = True

    async def defer(self, *, ephemeral=False, **kwargs):
        return None

    def is_done(self):
        return self._interaction._replied


class _FakeFollowup:
    def __init__(self, interaction) -> None:
        self._interaction = interaction

    async def send(self, content="", *, embed=None, ephemeral=False, **kwargs):
        self._interaction._record(content or (getattr(embed, "description", "") or ""))
        self._interaction._offer(kwargs.get("view"))


class _FakeInteraction:
    """Captures every reply AND every view offered alongside one.

    `view` used to be swallowed with the rest of `**kwargs`. A confirmation
    an admin is meant to click IS a view, so a double that drops it cannot
    tell "the command paused for confirmation" from "the command did not
    pause" — and `_confirm_if_awaiting` would have nothing to press. See
    that helper for the two seams AC-009.4 may be implemented through.

    `extras` mirrors the real `Interaction.extras` slot, which is the only
    place production can legally stash a per-interaction callback: the real
    class declares `__slots__` and no `__dict__`.
    """

    def __init__(self, *, administrator: bool = True) -> None:
        self.guild_id = PROD_SERVER_ID
        self.replies: list[str] = []
        self.views: list = []
        self.extras: dict = {}
        self._replied = False
        self.user = _FakeUser(administrator=administrator)
        self.response = _FakeResponse(self)
        self.followup = _FakeFollowup(self)

    def _record(self, content: str) -> None:
        self.replies.append(content)

    def _offer(self, view) -> None:
        if view is not None:
            self.views.append(view)

    @property
    def reply_text(self) -> str:
        return self.replies[0] if self.replies else ""

    @property
    def all_replies(self) -> str:
        return "\n".join(self.replies)


class _FakePermissions:
    def __init__(self, *, administrator: bool) -> None:
        self.administrator = administrator


class _FakeUser:
    def __init__(self, *, administrator: bool) -> None:
        self.guild_permissions = _FakePermissions(administrator=administrator)
        self.roles = []


def _admin_interaction():
    return _FakeInteraction(administrator=True)


class _FakeChroniclerClient:
    def authenticate(self) -> None:
        return None

    def register_user(self, tacticus_user_id: str) -> dict:
        return self.get_profile(tacticus_user_id)

    def get_profile(self, tacticus_user_id: str) -> dict:
        assert tacticus_user_id, "chronicl3r rejects an empty tacticus_user_id"
        return {
            "tacticus_user_id": tacticus_user_id,
            "tacticus_display_nm": f"player-{tacticus_user_id}",
        }
