import logging
import os
from pathlib import Path

from bot.obs import emit_structured
from bot.repository import (
    ClusterRepository,
    GuildBinding,
    JsonClusterRepository,
    SupportsProbe,
)
from bot.migrations.player_list_migrations import PlayerListMigrator

logger = logging.getLogger(__name__)


class StartupRefused(Exception):
    """Raised when the composition root cannot honour the configured backend.

    A deploy that says `SCRAPCODE_REPO_BACKEND=sqlite` but cannot back it with
    a usable Fernet key or a present database file is broken, not degraded —
    the bot that comes up on a silent JSON fallback serves stale data with
    quarantine inert, which is every failure mode of this feature at once.
    Refusing to start converts a silent outage into a visible one; the
    obligation that creates is the message, which is why every refusal names
    the exact variable AND the exact fix.
    """


def _refuse_startup(step: str, reason: str, detail: str) -> None:
    """Emit a `health.startup.refused` record, then raise `StartupRefused`.

    The structured record is emitted through `bot/obs.py` so it has the same
    single-line JSON shape every KPI query assumes (DEVOPS U1). The raise
    carries the same text so `str(exc)` surfaces the variable name without
    the caller re-formatting — the composition root's caller (the import-
    time singleton, or `main.py`) lets the exception propagate and systemd
    marks the unit `failed`.
    """
    emit_structured(
        logger,
        logging.ERROR,
        "health.startup.refused",
        step=step,
        reason=reason,
        detail=detail,
    )
    raise StartupRefused(detail)


# A Fernet key is 32 url-safe base64-encoded bytes — exactly 44 chars from
# `[A-Za-z0-9_-]` plus a single trailing `=`. `Fernet.__init__` is LENIENT
# about surrounding whitespace (it delegates to `base64.urlsafe_b64decode`,
# which discards whitespace and accepts trailing garbage), so a CRLF-mangled
# `.env` value passes `Fernet(key)` and the failure surfaces hours later
# inside the hourly loop as a cryptography traceback. The shape is therefore
# validated explicitly: any whitespace, control char, wrong length, or
# non-alphabet byte is refused here, naming `SCRAPCODE_DB_KEY` before the
# probe runs (AC-010.2). The `Fernet` round-trip remains the final guard so a
# 44-char alphabet-only string that still is not a decodable key is refused.
_FERNET_KEY_ALPHABET = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_="
)


def _require_real_fernet_key(fernet_key: str) -> None:
    """Refuse startup unless `fernet_key` is byte-identical to a Fernet key.

    Raises `StartupRefused` (via `_refuse_startup`) whose message names
    `SCRAPCODE_DB_KEY`, so the composition root's caller surfaces the
    variable the operator has to fix. The trailing-carriage-return case is
    not hypothetical: a Windows-edited `.env` has already broken auth on
    this VM once (operator's notes), which is why the production-data
    criterion for this slice is to mangle the real file with `printf
    'KEY=abc\\r\\n'`.
    """
    if (not isinstance(fernet_key, str)
            or len(fernet_key) != 44
            or any(c not in _FERNET_KEY_ALPHABET for c in fernet_key)):
        _refuse_startup(
            step="db_key",
            reason="malformed_key",
            detail=(
                "SCRAPCODE_DB_KEY is not a valid Fernet key. A real key is "
                "exactly 44 url-safe base64 chars (`[A-Za-z0-9_-]` plus a "
                "trailing `=`) with no whitespace. A common cause is a "
                "Windows-edited `.env` that leaves a trailing carriage return "
                "on the value. Regenerate with `python -c \"from "
                "cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\"`, or roll back with "
                "SCRAPCODE_REPO_BACKEND=json. Refusing to start."
            ),
        )
    from cryptography.fernet import Fernet
    try:
        Fernet(fernet_key.encode())
    except (ValueError, TypeError) as exc:
        _refuse_startup(
            step="db_key",
            reason="malformed_key",
            detail=(
                f"SCRAPCODE_DB_KEY is not a valid Fernet key ({exc}). "
                "Regenerate with `python -c \"from cryptography.fernet "
                "import Fernet; print(Fernet.generate_key().decode())\"`, "
                "or roll back with SCRAPCODE_REPO_BACKEND=json. Refusing to "
                "start."
            ),
        )


def build_repo() -> ClusterRepository:
    """Construct the live ClusterRepository from SCRAPCODE_REPO_BACKEND
    (ADR-006 D9 — env-driven singleton; rollback = restart with =json).

    Selection order:
      1. `SCRAPCODE_REPO_BACKEND=json` → JsonClusterRepository (rollback
         path; the probe is skipped so a missing/invalid SCRAPCODE_DB_KEY
         does not block a JSON-backend rollback). This is the ONE fallback
         somebody chose; it is documented, reasoned, and correct under time
         pressure, so the bot STILL STARTS here.
      2. `SCRAPCODE_REPO_BACKEND=sqlite` (the post-cutover default) →
         SqlAlchemyClusterRepository, UNLESS the configuration cannot be
         honoured, in which case the bot REFUSES TO START:
           - SCRAPCODE_DB_KEY missing/empty → refuse, naming SCRAPCODE_DB_KEY.
             The SQLite impl cannot encrypt/decrypt guild keys without it,
             and a silent JSON fallback serves stale data with quarantine
             inert (slice 07 / AC-010.1).
           - SCRAPCODE_DB_PATH file missing AND its parent directory exists
             (i.e., the file was supposed to be there but is gone — deleted
             or corrupted) → refuse, naming SCRAPCODE_DB_PATH (AC-010.3).
           - A first-run path whose parent dir does NOT yet exist still
             CONSTRUCTS the SQLite impl, which creates both the dir and the
             file via create_all — refusing it would make a fresh install
             impossible.

    Refusing to start is an availability trade and is accepted (operator
    decision, 2026-08-02): a typo in `.env` becomes a visible outage instead
    of a silent one. The refusal message is the only thing between the
    operator and a bot that will not come up, so it names the exact variable
    AND the exact fix — write it for the person reading it at 2am.
    """
    backend = os.getenv("SCRAPCODE_REPO_BACKEND", "sqlite")
    if backend == "json":
        # ADR-006 D9: the deliberate rollback. DDD-4 gives the JSON adapter no
        # binding representation, so the guild-key quarantine guard is INERT
        # on this path — a drifted key is served, `quarantine()` writes
        # nowhere. An operator who rolls back at 2am to restore service must
        # be told that in the same breath, not in an ADR later (AC-010.4).
        emit_structured(
            logger,
            logging.WARNING,
            "health.startup.json_rollback",
            reason="guild_key_quarantine_inert",
            detail=(
                "SCRAPCODE_REPO_BACKEND=json: the guild-key quarantine guard "
                "is inert on this path (DDD-4 — the JSON adapter has no "
                "binding store). A drifted key is served and quarantine "
                "writes nowhere; the protection this feature adds is off "
                "until the sqlite backend is restored."
            ),
        )
        return JsonClusterRepository()
    db_path = os.getenv("SCRAPCODE_DB_PATH", "data/scrapcode.db")
    fernet_key = os.getenv("SCRAPCODE_DB_KEY", "")
    if not fernet_key:
        _refuse_startup(
            step="db_key",
            reason="missing_or_empty",
            detail=(
                "SCRAPCODE_DB_KEY is unset or empty. The sqlite backend "
                "(SCRAPCODE_REPO_BACKEND=sqlite) cannot encrypt or decrypt "
                "guild keys without it. Set SCRAPCODE_DB_KEY to a Fernet "
                "key (44 url-safe base64 chars, e.g. `python -c \"from "
                "cryptography.fernet import Fernet; print(Fernet.generate_key()."
                "decode())\"`), or roll back with SCRAPCODE_REPO_BACKEND=json. "
                "Refusing to start."
            ),
        )
    # AC-010.2: validate the key's SHAPE, not its truthiness. A CRLF-mangled
    # or truncated value (e.g. a Windows-edited `.env`) is truthy and the
    # right length to look plausible, but `Fernet` raises `ValueError` on it.
    # This gate runs BEFORE the probe so the refusal names SCRAPCODE_DB_KEY
    # rather than a WAL or alembic step that failed downstream — the operator
    # at 2am needs the variable, not a cryptography traceback from the hourly
    # loop hours later.
    _require_real_fernet_key(fernet_key)
    if Path(db_path).parent.exists() and not Path(db_path).exists():
        _refuse_startup(
            step="db_path",
            reason="missing_file",
            detail=(
                f"SCRAPCODE_DB_PATH={db_path} points at a missing file whose "
                "parent directory exists — the database was deleted or "
                "corrupted. Restore the file from backup, or point "
                "SCRAPCODE_DB_PATH at a fresh location to re-create it. "
                "Refusing to start."
            ),
        )
    from bot.repository_sqlalchemy import SqlAlchemyClusterRepository
    repo = SqlAlchemyClusterRepository()
    # ADR-006 D8: the startup probe "runs at composition time and MUST
    # succeed before the bot starts". The probe is invoked from the startup
    # entry point (`main.py`) rather than here so that the four health checks
    # (WAL mode, alembic revision, Fernet round-trip, write rollback) run
    # AFTER the repository is wired but BEFORE any cog consumes it — the
    # "wires, then probes, then hands out" order ADR-006 D8 requires. The
    # probe is deliberately NOT called inside `build_repo`: every test
    # fixture and every `importlib.reload(bot.guilds)` constructs through
    # this function against a `create_all`-only database that is not yet
    # alembic-stamped, and the probe's alembic step correctly refuses such a
    # database. Production always starts on an already-migrated database
    # (migrations are applied before the bot process starts), so the probe
    # passes there; the gate belongs at the process entry point, not in the
    # factory. See `main.py` for the single production caller (AC-010.5).
    return repo


repo: SupportsProbe = build_repo()

# `UUID_PATTERN` used to live here and had no caller left. It moved to
# `bot/services/tacticus/guild_client.py`, the guild-identity vocabulary's
# home, where `canonical_guild_id` is its one consumer. It could not be
# imported back from here: this module must not reach the policy/HTTP side
# (`test_the_guilds_wrapper_layer_stays_free_of_policy_and_http` /
# `test_archon_rules_hold`), and `guild_client` imports `httpx` inside
# `fetch_guild_snapshot`, which archon reads transitively.


# ==========================================
# GUILD REGISTRY
# ==========================================

def load_guilds(discord_server_id: int) -> dict:
    """Return {guild_id: {name, api_key, role_id, notification_channel_id, member_role_ids}} for a server.

    Pure delegation to the repository's ``load_guilds_dict`` — the
    Guild-to-dict projection lives in the sanctioned adapter (slice 07 /
    step 09-03), not here. This layer stays the cog-facing wrapper ADR-004
    rule 1 makes it; it delegates, it does not project.
    """
    return repo.load_guilds_dict(discord_server_id)


def save_guilds(discord_server_id: int, guilds: dict) -> None:
    """Persist the five-key guild dict for a server.

    Delegates to ``repo.save_guilds_dict`` — the dict-to-Guild projection
    lives in the sanctioned adapter. The adapter loads the existing cluster
    (preserving ``update_channel_id`` / ``role_tiers``), replaces guilds,
    and saves. A load-mutate-save cycle by an unrelated admin command must
    not blank a sibling's key — the round-trip invariant is load-bearing.
    """
    repo.save_guilds_dict(discord_server_id, guilds)


# ==========================================
# GUILD KEY BINDINGS  (feature: guild-key-integrity)
#
# These are thin wrappers over the ClusterRepository binding methods, in the
# same shape as every other wrapper in this module — brief §4.1 and ADR-004
# rule 1 make this the sanctioned cog-facing layer.
#
# Binding state lives in its own `guild_key_bindings` table, NOT on `Guild`
# (DDD-4). That is deliberate: `save_guilds` above rebuilds each `Guild` from
# a five-key dict, so any binding field threaded through the dataclass would
# be overwritten with a None default by the next unrelated admin command.
# Keeping it out of `Guild` makes that clobber structurally impossible rather
# than merely avoided.
#
# This module MUST NOT import `bot.guild_keys` or `httpx` — every cog imports
# this layer, and pulling policy or an HTTP client in here is the import cycle
# DDD-3 is shaped to avoid. Enforced by
# tests/acceptance/guild-key-integrity/test_architecture_chokepoint.py.
# ==========================================

def load_guild_binding(discord_server_id: int, guild_id: str) -> GuildBinding:
    """Return the guild's identity binding, or an unbound placeholder.

    Never returns None: an unbound guild is a real, expected state (every
    guild is unbound on the day Slice 01 deploys), and a None return would
    push that check onto seven call sites. On the JSON backend this always
    returns unbound — the feature degrades to inert there (ADR-006 D9).
    """
    return repo.load_guild_binding(discord_server_id, guild_id)


def save_guild_binding(discord_server_id: int, guild_id: str,
                       binding: GuildBinding) -> None:
    """Persist the guild's identity binding.

    No-ops on the JSON backend rather than raising, so a rollback under time
    pressure leaves a bot that runs.
    """
    repo.save_guild_binding(discord_server_id, guild_id, binding)


def replace_guild_key(discord_server_id: int, guild_id: str, api_key: str) -> None:
    """Swap a guild's `api_key` (and `api_key_hmac` where the backend has one)
    in one transaction, touching nothing else (ADR-006 D7 / AC-003.2).

    The targeted key-swap path — the ONLY sanctioned write for a key
    replacement. `save_guilds` rebuilds every guild row and CASCADE-deletes
    absent ones, which is the wrong tool for a key swap: this command's whole
    justification is that players and hit rows are byte-identical before and
    after, so this method touches only the two key columns and cannot reach a
    dependent row even by accident.
    """
    repo.replace_guild_key(discord_server_id, guild_id, api_key)


def list_guild_bindings(discord_server_id: int) -> dict[str, GuildBinding]:
    """Return {guild_id: binding} for every guild that has one."""
    return repo.list_guild_bindings(discord_server_id)


def add_cluster_role(discord_server_id: int, tier: str, role_id: int) -> None:
    cluster = repo.load(discord_server_id)
    existing = cluster.role_tiers.get(tier, [])
    if role_id not in existing:
        cluster.role_tiers[tier] = existing + [role_id]
    repo.save(cluster)


def add_guild_member_role(discord_server_id: int, guild_id: str, role_id: int) -> None:
    cluster = repo.load(discord_server_id)
    guild = cluster.guilds.get(guild_id)
    if guild and role_id not in guild.member_role_ids:
        guild.member_role_ids = guild.member_role_ids + [role_id]
    repo.save(cluster)


def get_guild_by_role(discord_server_id: int, role_id: int):
    for guild_id, guild_data in load_guilds(discord_server_id).items():
        if guild_data.get("role_id") == role_id:
            return guild_id, guild_data
    return None


def get_guild_data_path(discord_server_id: int, guild_id: str) -> Path:
    return repo.get_guild_data_path(discord_server_id, guild_id)


# ==========================================
# GUILD PLAYER LIST (v2 schema)
# ==========================================

def load_player_list(discord_server_id: int, guild_id: str) -> dict:
    return repo.load_player_list(discord_server_id, guild_id)


def get_player_list(discord_server_id: int, guild_id: str) -> dict:
    """Return {tacticus_id: display_name} for use in embeds/leaderboards."""
    players = load_player_list(discord_server_id, guild_id).get("players", {})
    result = {}
    for uid, entry in players.items():
        name = entry.get("display_name", uid[:8])
        if entry.get("is_former"):
            name += " (former)"
        result[uid] = name
    return result


def save_player_list(discord_server_id: int, guild_id: str, data: dict) -> None:
    repo.save_player_list(discord_server_id, guild_id, data)


# ==========================================
# PLAYER REGISTRATIONS  {discord_id: {api_key, guild_id}}
# ==========================================

def load_player_registrations(discord_server_id: int) -> dict:
    return repo.load_player_registrations(discord_server_id)


def save_player_registrations(discord_server_id: int, data: dict) -> None:
    repo.save_player_registrations(discord_server_id, data)


# ==========================================
# CAPPED STATE  {discord_id: bool}
# ==========================================

def load_capped_state(discord_server_id: int) -> dict:
    return repo.load_capped_state(discord_server_id)


def save_capped_state(discord_server_id: int, data: dict) -> None:
    repo.save_capped_state(discord_server_id, data)


# ==========================================
# LIVE LEADERBOARDS
# ==========================================

def load_live_leaderboards(discord_server_id: int) -> dict:
    return repo.load_live_leaderboards(discord_server_id)


def save_live_leaderboards(discord_server_id: int, data: dict) -> None:
    repo.save_live_leaderboards(discord_server_id, data)