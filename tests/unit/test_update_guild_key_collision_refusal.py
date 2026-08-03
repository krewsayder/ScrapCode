"""Properties of `/update_guild_key`'s key-collision refusal (step 08-02).

WHY-NEW-FILE: tests/unit/test_update_guild_key_collision_refusal.py
  CLOSEST-EXISTING: tests/unit/test_replace_guild_key_refusals.py
  EXTENSION-COST: every property there drives a REAL SQLite repository through
    an alembic migration and asserts on stored columns — its whole subject is
    what the storage boundary does with a colliding write. These properties
    never reach storage: they drive the `/update_guild_key` COMMAND CALLBACK
    and assert on the string an admin reads, with the repository replaced at
    the driven port so the collision can be summoned on demand.
  PARALLEL-RATIONALE: incompatible dependency set and a different observable.
    That module's fixture builds a database precisely so the UNIQUE constraint
    is real; the claim here has to hold for holder ids that database can never
    produce — the `_HOLDER_VANISHED` empty string the race path raises, and
    slugs absent from the registry — so it must construct the refusal from the
    exception, not from a row.

WHY A PROPERTY HERE AT ALL. The acceptance scenario that owns this behaviour
(`test_a_key_held_by_a_sibling_is_refused_without_disclosing_it`) is
parametrized over `force` and asserts one generated key's material is absent
from one reply. A single example proves ONE message is clean. KPI-6 is
recorded as "0 by construction", and "by construction" is a universally
quantified claim: for EVERY key an admin can paste, on EVERY install path, the
rendered refusal and every log record it emits contain none of
{plaintext, Fernet ciphertext, api_key_hmac}. That is what Property 1 states.

The second claim is structural and is the reason this step exists at all. Step
08-01 made the acceptance scenarios pass by accident: the typed exception
escaped the cog, reached `main.py`'s generic handler, and that handler happened
to render a message clean enough to satisfy the assertions. One `str()` change
away from the disclosure returning. So both properties assert that the generic
handler is NEVER entered — the refusal must be produced by the cog.

DELTA-FIRST BYPASS: interaction test. The driven ports (`replace_guild_key`,
`save_guild_binding`) are recording doubles and their call surface IS the
universe these properties declare; there is no stored state to snapshot,
because the point of a refusal is that storage was never reached.
"""
from __future__ import annotations

import os

# `bot.guilds` evaluates `repo = build_repo()` at import time and reads the
# environment at that moment. Pin a harmless backend and the two channel ids
# `config.py` demands BEFORE any `bot.*` import, so collection cannot construct
# a repository pointed at a live tree. Precedent:
# `tests/unit/test_admin_cog_quarantine_refusal.py`.
os.environ.setdefault("SCRAPCODE_REPO_BACKEND", "json")
os.environ.setdefault("UPDATE_CHANNEL_ID", "0")
os.environ.setdefault("REPLAY_INDEX_CHANNEL_ID", "0")

import asyncio  # noqa: E402
import logging  # noqa: E402
import string  # noqa: E402
from contextlib import contextmanager  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402

import pytest  # noqa: E402

hypothesis = pytest.importorskip(
    "hypothesis",
    reason="hypothesis is not installed — DISTILL pins it into requirements.txt",
)

import hypothesis.strategies as st  # noqa: E402
from hypothesis import given, settings  # noqa: E402

from bot.repository import GuildBinding, GuildKeyAlreadyRegisteredError  # noqa: E402
from bot.services.tacticus.guild_client import (  # noqa: E402
    GuildIdentity,
    GuildSnapshot,
    ProbeOutcome,
)

# Deselected from the 250-test baseline for the same reason the slice-06
# acceptance module is: these belong to the remediation slice, and the baseline
# command is the "nothing that shipped has regressed" gate.
pytestmark = [pytest.mark.property, pytest.mark.slice_06]


SERVER_ID = 1458181638453203099
GUILD_TARGET = "word_bearers"
GUILD_HOLDER = "dark_mechanicum"
HOLDER_DISPLAY_NAME = "Dark Mechanicum"

# A real Fernet key, so `api_key_hmac` and `encrypt_api_key` produce the
# artefacts SQLAlchemy would have inlined. Deriving them for real is the whole
# point: the plaintext is NOT what an `IntegrityError` discloses, so a property
# that only searched for the plaintext would hold while the leak was total.
FERNET_KEY = "kA1Aap2fW0Q7iLQMrp3sQ3aXqBQ0cQ0kIqcOCG1a-Xg="

WORD_BEARERS = GuildIdentity(
    uuid="11111111-1111-1111-1111-111111111111", tag="WB", name="Word Bearers"
)
DARK_MECHANICUM = GuildIdentity(
    uuid="22222222-2222-2222-2222-222222222222", tag="DM", name=HOLDER_DISPLAY_NAME,
)

# What `main.py:91-101` interpolates an escaped exception into, and what
# `repository_sqlalchemy` would have handed it. Any of these in a reply means
# the exception reached the generic handler after all.
SQL_MARKERS = ("INSERT INTO", "UPDATE ", "[parameters:", "sqlite3.IntegrityError")

# 16 characters minimum, and the reason is a trap step 08-01 walked into: a
# 1-3 character generated key is a coincidental substring of almost any English
# sentence, so `assert key not in reply` fails for the STRATEGY's reason rather
# than production's and the property stops saying anything. Nothing the cog
# renders contains a 16-character run of this alphabet, so a hit is a real leak.
_API_KEYS = st.text(
    alphabet=string.ascii_letters + string.digits + "-_", min_size=16, max_size=48
)

# The three install paths `replace_guild_key` is reached from. Generated rather
# than fixed because the criterion is that ONE guard covers all three: a fix
# that only guarded the path the acceptance fixture happens to take would leave
# the disclosure fully present behind a different binding state.
_INSTALL_PATHS = st.sampled_from(("unbound", "matching", "mismatching"))

# Who the repository says already holds the key. `""` is not a filler value —
# it is `repository_sqlalchemy._HOLDER_VANISHED`, raised when the holder row
# disappears between the SELECT and the flush, and it is the one holder id a
# real database can hand the cog that names no guild at all.
_HOLDERS = st.sampled_from((GUILD_HOLDER, "a_guild_this_server_never_registered", ""))

_SETTINGS = settings(max_examples=100, deadline=None)


# ===========================================================================
# Property 1 — KPI-6, stated as the universal claim the contract records
# ===========================================================================

@given(
    api_key=_API_KEYS,
    force=st.booleans(),
    collision=st.booleans(),
    path=_INSTALL_PATHS,
    holder=_HOLDERS,
)
@_SETTINGS
def test_no_reply_or_record_on_the_install_path_ever_carries_key_material(
    api_key: str, force: bool, collision: bool, path: str, holder: str
):
    """For every key, every path and both values of `force`: nothing leaks.

    THE THREE ARTEFACTS ARE DERIVED, NOT ASSUMED. `api_key_hmac` is what the
    violated constraint is on, so it is what SQLAlchemy inlines into
    `[parameters: ...]`, and the Fernet ciphertext rides along in the same
    tail. The hmac is not reversible, but it is a stable fingerprint: anyone
    holding it can confirm whether a key they possess is the one installed.

    The log side is asserted against the record's FULL attribute dictionary,
    not just its message, because `emit_structured` puts the interesting
    values in `extra` — a message-only check would miss a field named
    `api_key`. `key_ref` is expected to be there and is expected to be a
    PREFIX of the hmac; the assertion is that the whole hmac is not, which is
    exactly the line `docs/product/kpi-contracts.yaml` draws.

    AND THE GENERIC HANDLER IS NEVER ENTERED. That is the structural half:
    `main.py`'s handler is what disclosed the parameters, and a refusal that
    depends on it rendering cleanly is one `str()` change from regressing.
    """
    secrets = _key_material_for(api_key)

    outcome = _drive_update_guild_key(
        api_key=api_key, force=force, path=path,
        holder=holder if collision else None,
    )

    assert not outcome.generic_handler_entered, (
        "the typed refusal escaped the command callback and was rendered by "
        f"main.py's generic handler: {outcome.escaped!r}"
    )
    for label, material in secrets.items():
        assert material not in outcome.reply, (
            f"the {label} reached a Discord message: {outcome.reply!r}"
        )
        assert material not in outcome.log_text, (
            f"the {label} reached the log"
        )
    for marker in SQL_MARKERS:
        assert marker not in outcome.reply, (
            f"raw SQL ({marker!r}) reached a Discord message: {outcome.reply!r}"
        )


# ===========================================================================
# Property 2 — one guard, all three paths, and AC-003.4 still reachable
# ===========================================================================

@given(
    api_key=_API_KEYS,
    force=st.booleans(),
    collision=st.booleans(),
    path=_INSTALL_PATHS,
    holder=_HOLDERS,
)
@_SETTINGS
def test_a_collision_is_refused_by_the_cog_on_every_path_that_would_write(
    api_key: str, force: bool, collision: bool, path: str, holder: str
):
    """The iff: refused exactly when a write was attempted onto a held key.

    Read in one direction it is AC-009.1/AC-009.2 — every path that reaches
    the write refuses, `force` included, so the disclosure cannot be reached
    by adding one argument. Read in the other it is AC-003.4, the regression
    guard: where NO collision exists the install still happens, so a slice
    that closed the disclosure by refusing every force would fail here rather
    than ship the only sanctioned way to re-point a binding in a broken state.

    `mismatching` without `force` is the third outcome and it is neither: the
    key is never written, so there is nothing to collide with, and the reply
    is the existing mismatch refusal. Including it in the quantification is
    what stops the property being satisfied by a cog that refuses everything.
    """
    write_authorised = path in ("unbound", "matching") or force

    outcome = _drive_update_guild_key(
        api_key=api_key, force=force, path=path,
        holder=holder if collision else None,
    )

    refused_for_collision = collision and write_authorised
    assert outcome.keys_written == ([] if refused_for_collision or not write_authorised
                                    else [api_key]), (
        "a colliding key was stored, or a legitimate install was refused: "
        f"path={path} force={force} collision={collision} "
        f"wrote={outcome.keys_written!r}"
    )
    if not refused_for_collision:
        return

    assert not outcome.generic_handler_entered, (
        "the refusal was rendered by main.py's generic handler, so this "
        "property would be satisfied by the very error path the step closes: "
        f"{outcome.escaped!r}"
    )
    assert not outcome.bindings_written, (
        "the binding was updated on a refused install — a refusal must leave "
        f"every column byte-identical: {outcome.bindings_written!r}"
    )
    assert _names_the_holder(outcome.reply, holder), (
        "the refusal did not name the guild that already holds the key, so "
        f"the admin cannot act on it: {outcome.reply!r}"
    )


def _names_the_holder(reply: str, holder: str) -> bool:
    """Slug or display name — AC-009.1 pins the information, not the wording.

    The empty holder is `_HOLDER_VANISHED`: the row disappeared mid-flush and
    there is genuinely no guild to name. An admin told "that key is taken"
    without a name can still retry; an admin sent the ciphertext cannot
    un-disclose it. So the requirement there is that the refusal still READS
    as a refusal about a key that is already registered.
    """
    if not holder:
        return "already registered" in reply.lower()
    return holder in reply or HOLDER_DISPLAY_NAME in reply


# ===========================================================================
# The driving port — the real `/update_guild_key` callback, main.py behind it
# ===========================================================================

@dataclass
class _Outcome:
    """Everything the command was observed to do, from outside the hexagon."""

    reply: str = ""
    log_text: str = ""
    keys_written: list = field(default_factory=list)
    bindings_written: list = field(default_factory=list)
    generic_handler_entered: bool = False
    escaped: BaseException | None = None


def _drive_update_guild_key(*, api_key: str, force: bool, path: str,
                            holder: str | None) -> _Outcome:
    """Invoke the real command callback, then handle escapes as `main.py` does.

    Hypothesis drives this, so the doubles are installed by a context manager
    rather than by `monkeypatch`: a function-scoped fixture under `@given` is
    the health-check failure that gets silenced with `suppress_health_check`,
    and silencing it is a forbidden bypass.

    The permission checks are deliberately NOT run. `@require_tier("admin")` is
    an `app_commands` check, so it gates dispatch and not the callback, and
    ADR-001 puts permission in exactly one place with its own tests. What is
    under test here is what the callback renders once it has been let through.
    """
    outcome = _Outcome()
    with _a_cluster_where(api_key=api_key, path=path, holder=holder, outcome=outcome):
        from bot.cogs.admin_cog import AdminCog

        interaction = _FakeInteraction()
        cog = AdminCog.__new__(AdminCog)
        command = _find_admin_command("update_guild_key")
        try:
            asyncio.run(
                command.callback(
                    cog, interaction, guild_id=GUILD_TARGET,
                    api_key=api_key, force=force,
                )
            )
        except Exception as error:  # noqa: BLE001 — mirrors main.py's bare handler
            # `main.py:91-101` verbatim. Slice 06 is scoped so the fix does NOT
            # depend on redacting here: reaching this block at all is the
            # failure, whatever it goes on to render.
            outcome.generic_handler_entered = True
            outcome.escaped = error
            interaction.replies.append(f"❌ An error occurred: {error}")

    outcome.reply = "\n".join(interaction.replies)
    return outcome


@contextmanager
def _a_cluster_where(*, api_key: str, path: str, holder: str | None,
                     outcome: _Outcome):
    """Replace the driven ports for one command invocation, then put them back.

    Everything swapped here is a port boundary — cluster storage and the
    Tacticus guild service. Nothing inside the hexagon is doubled: the real
    `install_guild_key` policy and the real renderer run.
    """
    import bot.cogs.admin_cog as admin_cog
    import bot.guild_keys as guild_keys
    from bot.services.tacticus import guild_client

    binding = _BINDINGS[path]

    def _load_guilds(server_id):
        return {
            GUILD_TARGET: {"name": "Word Bearers"},
            GUILD_HOLDER: {"name": HOLDER_DISPLAY_NAME},
        }

    def _replace_guild_key(server_id, guild_id, key):
        if holder is not None:
            raise GuildKeyAlreadyRegisteredError(holder)
        outcome.keys_written.append(key)

    def _save_guild_binding(server_id, guild_id, new_binding):
        outcome.bindings_written.append(new_binding)

    async def _fetch_guild_snapshot(key):
        return GuildSnapshot(outcome=ProbeOutcome.MATCH, identity=WORD_BEARERS)

    patches = (
        (admin_cog, "load_guilds", _load_guilds),
        (guild_keys, "load_guilds", _load_guilds),
        (guild_keys, "load_guild_binding", lambda *a: binding),
        (guild_keys, "replace_guild_key", _replace_guild_key),
        (guild_keys, "save_guild_binding", _save_guild_binding),
        (guild_client, "fetch_guild_snapshot", _fetch_guild_snapshot),
    )
    originals = [(module, name, getattr(module, name)) for module, name, _ in patches]
    for module, name, double in patches:
        setattr(module, name, double)

    previous_db_key = os.environ.get("SCRAPCODE_DB_KEY")
    os.environ["SCRAPCODE_DB_KEY"] = FERNET_KEY
    recorder = _RecordingHandler(outcome)
    bot_logger = logging.getLogger("bot")
    previous_level = bot_logger.level
    bot_logger.setLevel(logging.DEBUG)
    bot_logger.addHandler(recorder)
    try:
        yield
    finally:
        bot_logger.removeHandler(recorder)
        bot_logger.setLevel(previous_level)
        for module, name, original in originals:
            setattr(module, name, original)
        if previous_db_key is None:
            os.environ.pop("SCRAPCODE_DB_KEY", None)
        else:
            os.environ["SCRAPCODE_DB_KEY"] = previous_db_key


_BINDINGS = {
    # Trust-on-first-use: no stored identity, so the probe's answer is adopted.
    "unbound": GuildBinding(),
    # The ordinary key rotation: the new key resolves to the bound guild.
    "matching": GuildBinding(
        tacticus_guild_id=WORD_BEARERS.uuid,
        tacticus_guild_tag=WORD_BEARERS.tag,
        tacticus_guild_name=WORD_BEARERS.name,
        identity_bound_at="2026-07-31T04:00:00.000Z",
    ),
    # The drift: only `force=True` may re-point this one (AC-003.4).
    "mismatching": GuildBinding(
        tacticus_guild_id=DARK_MECHANICUM.uuid,
        tacticus_guild_tag=DARK_MECHANICUM.tag,
        tacticus_guild_name=DARK_MECHANICUM.name,
        identity_bound_at="2026-07-31T04:00:00.000Z",
    ),
}


class _RecordingHandler(logging.Handler):
    """Append every `bot.*` record — message AND `extra` fields — to the trace.

    `emit_structured` puts the interesting values in `extra`, which never
    reaches `record.getMessage()`. A property that only read the message would
    hold while a field named `api_key` sat on the record.
    """

    def __init__(self, outcome: _Outcome) -> None:
        super().__init__(logging.DEBUG)
        self._outcome = outcome

    def emit(self, record: logging.LogRecord) -> None:
        self._outcome.log_text += f"{record.getMessage()} {record.__dict__!r}\n"


def _key_material_for(api_key: str) -> dict[str, str]:
    """Everything derived from `api_key` that must never be disclosed."""
    from bot.db.secrets import api_key_hmac, encrypt_api_key

    return {
        "plaintext key": api_key,
        "api_key_hmac": api_key_hmac(api_key, FERNET_KEY) or "",
        "Fernet ciphertext": encrypt_api_key(api_key, FERNET_KEY),
    }


def _find_admin_command(name: str):
    from bot.cogs.admin_cog import AdminCog

    for command in AdminCog.__cog_app_commands__:
        if command.name == name:
            return command
    raise AssertionError(f"no `{name}` command is registered on AdminCog")


class _FakeResponse:
    def __init__(self, interaction) -> None:
        self._interaction = interaction

    async def send_message(self, content="", **kwargs):
        self._interaction.replies.append(content)

    async def defer(self, **kwargs):
        return None

    def is_done(self):
        return bool(self._interaction.replies)


class _FakeFollowup:
    def __init__(self, interaction) -> None:
        self._interaction = interaction

    async def send(self, content="", **kwargs):
        self._interaction.replies.append(content)


class _FakeInteraction:
    def __init__(self) -> None:
        self.guild_id = SERVER_ID
        self.replies: list[str] = []
        self.extras: dict = {}
        self.response = _FakeResponse(self)
        self.followup = _FakeFollowup(self)
