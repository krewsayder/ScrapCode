"""Guild key policy — the single sanctioned reader of a guild's `api_key`.

ADR-008 D3 / DDD-3. Seven call sites across three cogs plus a service read a
guild key today. Six-of-seven is not "mostly fixed" — it is a silent
contamination path that looks fixed. Every one of those sites routes through
this module, and
`tests/acceptance/guild-key-integrity/test_architecture_chokepoint.py` fails
the build when a new one does not.

Two entry points, deliberately different in kind:

  `verify_and_resolve` — async. Refuses an already-quarantined guild before
      it asks anything, then probes Tacticus, compares the resolved identity
      against the stored binding, reports the result, and returns a snapshot
      the caller can ingest from. This is what an ingestion path calls, so the
      refusal is INSIDE it: a gate the caller has to remember to ask for is
      the defect this module exists to close, not a fix for it.

  `active_key` — sync, storage-only, no network. Returns the key ONLY if the
      guild is not quarantined. This is what season discovery calls, and the
      reason it exists separately is DDD-7: season discovery must be able to
      fall through to the next guild cheaply, without a probe per candidate.

This module MUST NOT be imported by `bot/repository*.py`: policy depends on
storage, never the reverse.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from bot.guilds import (
    load_guild_binding,
    load_guilds,
    replace_guild_key,
    save_guild_binding,
)
from bot.obs import emit_structured
from bot.services.tacticus import guild_client
from bot.services.tacticus.guild_client import (
    GuildIdentity,
    GuildSnapshot,
    KeyStatus,
    ProbeOutcome,
)

logger = logging.getLogger(__name__)

# One alert per guild per 24 hours while a quarantine persists (ADR-008 D5).
# An hourly loop that alerts hourly gets its channel muted, and a muted
# channel defeats KPI-1 entirely.
ALERT_SUPPRESSION_HOURS = 24

# Shown in place of a `key_ref` that cannot be derived — an unregistered
# guild, an empty key, or the JSON rollback path where SCRAPCODE_DB_KEY does
# not exist. A placeholder rather than an omitted field: every KPI query in
# `docs/product/kpi-contracts.yaml` selects on `key_ref`, and a record missing
# the column silently drops out of the result set instead of showing up as the
# uncorrelatable record it is.
UNKNOWN_KEY_REF = "--------"


class GuildQuarantined(Exception):
    """Raised when a caller asks for the key of a quarantined guild.

    Carries both identities so the caller can render an actionable message
    without re-reading the binding.
    """

    def __init__(self, guild_id: str, bound: GuildIdentity | None,
                 observed: GuildIdentity | None) -> None:
        super().__init__(f"{guild_id} is quarantined")
        self.guild_id = guild_id
        self.bound = bound
        self.observed = observed


async def verify_and_resolve(
    discord_server_id: int,
    guild_id: str,
    *,
    enforce: bool = False,
) -> GuildSnapshot:
    """Probe the guild's key, compare identity, and report the result.

    `enforce=False` is the Slice 01 behaviour and the shipped default:
    classify and report, block nothing. Slice 03 flips it, after Slice 02 has
    shipped the recovery path (`/update_guild_key`). Shipping the block first
    would make the first quarantine unrecoverable without an SSH session
    (ADR-008 D3), so `enforce=True` refuses loudly here rather than quietly
    returning an unenforced result to a caller who asked to be protected.

    Only `uuid` is ever compared (DDD-1). The tag and the name are
    display-only and are refreshed from every successful probe, so a retag or
    a rename updates what the operator sees without touching the lock.

    A probe that did not succeed — UNVERIFIABLE, UNREACHABLE or DEAD — leaves
    the stored binding byte-identical. Refreshing `identity_bound_at` on a
    check that never happened would report a verification date for a
    verification nobody performed.

    Enforcement (Slice 03, `enforce=True`) blocks ONLY a mismatch: the guild
    is quarantined and `GuildQuarantined` is raised BEFORE any further request
    is made (DDD-2/5). UNREACHABLE, UNVERIFIABLE and DEAD each leave
    `key_status` untouched (DDD-6) — an outage must not quarantine a trusted
    key, and an unverifiable one is still trusted (only the check is offline),
    so those paths still return a snapshot and the caller still ingests.

    An ALREADY quarantined guild is refused on the first line, whatever
    `enforce` says (AC-008.3). `enforce` governs whether a NEWLY OBSERVED
    mismatch quarantines; it has never governed whether a guild the cluster
    already stopped trusting may be probed again, and reading a flag the
    caller passed to decide that is the shape of the original defect. Every
    caller was safe only because it happened to call `active_key` first and
    bail — `admin_cog.register_guild` is the one that did not, and it is the
    one that wrote another guild's roster over 60 `players` rows. The gate
    lives here so the NEXT caller is safe without knowing any of that.
    """
    _refuse_a_quarantined_guild(discord_server_id, guild_id)
    api_key = _registered_key(discord_server_id, guild_id)
    context = _KeyContext(discord_server_id, guild_id, _key_ref_for(api_key))
    if not api_key:
        return _unreachable_without_a_key(context)

    snapshot = await guild_client.fetch_guild_snapshot(api_key)
    if snapshot.identity is None:
        return _report_failed_probe(context, snapshot)
    return _resolve_identity(context, snapshot, enforce=enforce)


def active_key(discord_server_id: int, guild_id: str) -> str | None:
    """Return the guild's key, or None when it is quarantined or absent.

    Sync and storage-only by design (DDD-7): season discovery iterates
    candidate guilds and must be able to skip a quarantined one without
    paying for a probe.
    """
    if _is_quarantined(discord_server_id, guild_id):
        return None
    return _registered_key(discord_server_id, guild_id) or None


def quarantine(
    discord_server_id: int,
    guild_id: str,
    *,
    bound: GuildIdentity,
    observed: GuildIdentity,
) -> None:
    """Mark a guild quarantined, recording both identities and the moment.

    `quarantined_at` MUST be written ISO-8601 UTC in the SAME shape as
    `battle_hits.completed_on` (String(32), stored verbatim from the Tacticus
    payload). KPI-2 compares them as strings; a shape mismatch returns a
    wrong result set silently rather than erroring.

    NOT a leftover scaffold — a deliberate slice boundary. Slice 01 reports
    and blocks nothing (ADR-008 D3): enforcement cannot ship before
    `/update_guild_key` (Slice 02) provides the only exit, or the first
    quarantine strands the operator in an SSH session with updates also
    stopped. Nothing in Slice 01 calls this, and `verify_and_resolve` refuses
    `enforce=True` for the same reason.
    """
    binding = load_guild_binding(discord_server_id, guild_id)
    already_quarantined = _quarantined(binding)
    reason = _quarantine_reason(bound, observed)
    updates: dict = {
        "key_status": KeyStatus.QUARANTINED.value,
        "quarantine_reason": reason,
    }
    if not already_quarantined:
        updates["quarantined_at"] = _utc_now()
    save_guild_binding(discord_server_id, guild_id, replace(binding, **updates))
    if not already_quarantined:
        context = _KeyContext(discord_server_id, guild_id, _key_ref_for(_registered_key(discord_server_id, guild_id)))
        context.emit(
            logging.ERROR, "guild.key.quarantined",
            reason=reason, quarantined_at=updates["quarantined_at"],
        )


def release(discord_server_id: int, guild_id: str) -> None:
    """Clear quarantine after a matching key is installed.

    Quarantine must never be a trap (DISCUSS D3): this ships in Slice 02, one
    slice before anything can enter quarantine, so the exit provably exists
    before the entrance opens.

    Clears `key_status` → active, `quarantine_reason`, `quarantined_at` and
    `last_alerted_at` (a fresh quarantine's first alert must fire, so the
    rate-limit clock resets too), KEEPING the identity fields
    (`tacticus_guild_id`, tag, name, `identity_bound_at`). The identity is the
    lock; quarantine is a state of the key, not a rebinding.
    """
    binding = load_guild_binding(discord_server_id, guild_id)
    save_guild_binding(discord_server_id, guild_id, replace(
        binding,
        key_status=KeyStatus.ACTIVE.value,
        quarantine_reason=None,
        quarantined_at=None,
        last_alerted_at=None,
    ))


async def install_guild_key(
    discord_server_id: int,
    guild_id: str,
    api_key: str,
    *,
    force: bool = False,
) -> "InstallResult":
    """Probe a submitted key, install it when it verifies, release quarantine.

    The policy half of `/update_guild_key` (Slice 02). Probes the SUBMITTED
    key via `guild_client.fetch_guild_snapshot` BEFORE storing it, so an
    unverified key is never written (AC-003.3 / criterion 3). Only a probe that
    returns an identity (a well-formed 200) authorises a write:

      * identity matches the bound identity (or the guild is unbound →
        trust-on-first-use adopt, or `force=True` → rebind) → write the key +
        hmac in one transaction via `replace_guild_key`, release quarantine if
        the guild was quarantined, refresh the binding display fields, emit
        `guild.key.updated`, return the result.
      * failed probe (UNREACHABLE / DEAD / UNVERIFIABLE) → no write, return the
        outcome. An untrusted key must not enter on an outage (AC-003.6).
      * mismatch without `force` → no write, return MISMATCH. Slice 03's
        quarantine + 04-02's refusal reply layer on top; the no-store guard is
        correct by construction now.

    The 04-02 cog wraps this in the real `/update_guild_key` command
    (permission tier, force, refusal replies, unknown-guild listing); the
    signature here is shaped so that wrap is an extension, not a rewrite.

    `key_ref` is derived from the SUBMITTED key at call time (the plaintext is
    in scope here and nowhere else); no plaintext reaches the record.
    """
    context = _KeyContext(discord_server_id, guild_id, _key_ref_for(api_key))
    started = time.perf_counter()
    snapshot = await guild_client.fetch_guild_snapshot(api_key)
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    if snapshot.identity is None:
        return InstallResult(
            outcome=snapshot.outcome,
            bound_name=load_guild_binding(discord_server_id, guild_id).tacticus_guild_name,
        )

    observed: GuildIdentity = snapshot.identity
    binding = load_guild_binding(discord_server_id, guild_id)

    if binding.is_unbound:
        replace_guild_key(discord_server_id, guild_id, api_key)
        _commit_install(
            context, binding, observed, elapsed_ms,
            set_identity=True, clear_quarantine=False,
            forced=False, rebound_from=None,
        )
        context.emit_probe_ok(observed)
        return InstallResult(outcome=ProbeOutcome.MATCH, identity=observed)

    bound = GuildIdentity(uuid=binding.tacticus_guild_id)
    if bound.matches(observed):
        replace_guild_key(discord_server_id, guild_id, api_key)
        _rebind(context, binding, observed)
        context.emit_probe_ok(observed)
        if _quarantined(binding):
            release(discord_server_id, guild_id)
        _emit_updated(context, observed, elapsed_ms, forced=False, rebound_from=None)
        return InstallResult(outcome=ProbeOutcome.MATCH, identity=observed)

    if force:
        rebound_from = binding.tacticus_guild_id
        replace_guild_key(discord_server_id, guild_id, api_key)
        _commit_install(
            context, binding, observed, elapsed_ms,
            set_identity=True, clear_quarantine=True,
            forced=True, rebound_from=rebound_from,
        )
        return InstallResult(
            outcome=ProbeOutcome.MISMATCH, identity=observed,
            forced=True, rebound_from=rebound_from,
        )

    context.emit(
        logging.ERROR, "guild.key.mismatch",
        bound_id=binding.tacticus_guild_id, observed_id=observed.uuid,
        observed_tag=observed.tag, observed_name=observed.name,
    )
    return InstallResult(
        outcome=ProbeOutcome.MISMATCH, identity=observed,
        bound_name=binding.tacticus_guild_name,
    )


@dataclass(frozen=True)
class InstallResult:
    """What `install_guild_key` did — rendered to the Discord reply by the cog.

    `outcome` is the probe classification; the cog renders a refusal for every
    non-MATCH (and for MISMATCH-with-force it renders the rebind). `identity`
    is the resolved guild (None on a failed probe). `bound_name` is the
    previously-bound guild's display name, carried so the mismatch refusal
    can name both guilds without re-reading the binding. Carries no key
    material — `key_ref` is on the record, not the plaintext.
    """

    outcome: ProbeOutcome
    identity: GuildIdentity | None = None
    forced: bool = False
    rebound_from: str | None = None
    bound_name: str | None = None


def _commit_install(
    context: _KeyContext, binding, observed: GuildIdentity, elapsed_ms: int, *,
    set_identity: bool, clear_quarantine: bool,
    forced: bool, rebound_from: str | None,
) -> None:
    """Write the refreshed binding + `guild.key.updated` after a key install.

    The one helper for the three install-commit paths (adopt, force-rebind,
    and the unbound adoption): build the column delta explicitly so a column
    added to `GuildBinding` cannot be silently dropped here. `set_identity`
    re-points the lock (adoption / force); `clear_quarantine` exits it.
    """
    updates: dict = {
        "tacticus_guild_tag": observed.tag,
        "tacticus_guild_name": observed.name,
        "identity_bound_at": _utc_now(),
    }
    if set_identity:
        updates["tacticus_guild_id"] = observed.uuid
    if clear_quarantine:
        updates["key_status"] = KeyStatus.ACTIVE.value
        updates["quarantine_reason"] = None
        updates["quarantined_at"] = None
        updates["last_alerted_at"] = None
    save_guild_binding(context.server_id, context.guild_id, replace(binding, **updates))
    _emit_updated(context, observed, elapsed_ms, forced=forced, rebound_from=rebound_from)


def _emit_updated(
    context: _KeyContext, observed: GuildIdentity, elapsed_ms: int, *,
    forced: bool, rebound_from: str | None,
) -> None:
    """Emit the `guild.key.updated` KPI record (key_ref + outcome correlation).

    `key_ref` is on the context (derived from the SUBMITTED key); no plaintext
    key value reaches this record (KPI-6 by construction).
    """
    context.emit(
        logging.INFO, "guild.key.updated",
        tacticus_guild_id=observed.uuid, elapsed_ms=elapsed_ms,
        forced=forced, rebound_from=rebound_from,
    )


def key_ref(api_key_hmac: str | None) -> str:
    """Correlation ID for log records: first 8 hex of `api_key_hmac`.

    Lets an operator follow one key across bind → mismatch → quarantine →
    update. Not key material: `api_key_hmac` is an HKDF-SHA256 derivation
    keyed by SCRAPCODE_DB_KEY (ADR-006 D7) and is not reversible without that
    key. KPI-6 stays at zero by construction.
    """
    if not api_key_hmac:
        return UNKNOWN_KEY_REF
    return api_key_hmac[:8]


# ===========================================================================
# Internals — the classification, one shape per outcome (DDD-6).
#
# Collapsing any two of these is a documented failure mode, so they are kept
# as separate, individually readable branches rather than one table lookup:
#   MISMATCH vs UNREACHABLE  → a Tacticus outage quarantines the cluster
#   MISMATCH vs UNVERIFIABLE → a vendor change quarantines the cluster
#   MISMATCH vs DEAD         → a revoked key needs recovery it does not need
# ===========================================================================

@dataclass(frozen=True)
class _KeyContext:
    """Who a record is about: which guild, and which key was used on it.

    Every `guild.key.*` record carries `ts`, `server_id`, `guild_id` and
    `key_ref` (`docs/product/kpi-contracts.yaml` `required_fields`); a record
    missing one of them drops silently out of a KPI result set instead of
    showing up as the uncorrelatable record it is. Adding them here rather
    than at nine call sites is what makes "no record ships without them"
    structural.

    Three fields rather than Object Calisthenics' two, deliberately: all
    three are the correlation key, and dropping any one of them is what the
    rule would cost. NONE of them is key material — the plaintext `api_key`
    is not held here, so no record this object can emit is able to leak one,
    however it is later serialised. KPI-6 ("0 key values in logs or Discord")
    holds by construction rather than by filtering.
    """

    server_id: int
    guild_id: str
    key_ref: str

    def emit(self, level: int, event: str, **fields) -> None:
        emit_structured(
            logger, level, event,
            ts=_utc_now(),
            server_id=self.server_id,
            guild_id=self.guild_id,
            key_ref=self.key_ref,
            **fields,
        )

    def emit_probe_ok(self, observed: GuildIdentity) -> None:
        """The `last_probe_ok_at` half of KPI-1's `alerted_at − last_probe_ok_at`.

        Emitted only when the probe AGREED — on a first-use adoption or on a
        matching identity — because the metric is the width of the window in
        which drift could have gone unnoticed.
        """
        self.emit(
            logging.INFO, "guild.key.probe.ok",
            tacticus_guild_id=observed.uuid,
        )


def _resolve_identity(
    context: _KeyContext, snapshot: GuildSnapshot, *, enforce: bool = False,
) -> GuildSnapshot:
    """200 with an identity: adopt it, refresh it, or report the drift.

    Under `enforce=True` a mismatch quarantines the guild and raises
    `GuildQuarantined` BEFORE returning — the caller never receives the
    snapshot, so no further request is made with the drifted key (DDD-2/5).
    The mismatch RECORD is still emitted first (`_report_mismatch`), because it
    is the KPI-1 operand and the alert's justification; only the ALERT is
    rate-limited, never the record.
    """
    observed: GuildIdentity = snapshot.identity
    binding = load_guild_binding(context.server_id, context.guild_id)

    if binding.is_unbound:
        return _adopt(context, snapshot, binding, observed)

    bound = GuildIdentity(uuid=binding.tacticus_guild_id)
    if not bound.matches(observed):
        snapshot = _report_mismatch(context, snapshot, binding, observed)
        if enforce:
            _enforce_mismatch(context, binding, observed)
        return snapshot

    _rebind(context, binding, observed)
    context.emit_probe_ok(observed)
    return snapshot


def _enforce_mismatch(
    context: _KeyContext, binding, observed: GuildIdentity,
) -> None:
    """Quarantine the guild, then raise — never returns.

    The bound identity is reconstructed from the binding (uuid + display
    fields) so `GuildQuarantined` can carry both identities to the caller's
    catch handler without re-reading the binding after the write. Same
    reconstruction the already-quarantined gate uses, through the same
    helper: a display field added to `GuildBinding` and carried in only one
    of the two makes a first refusal and a repeat refusal say different
    things about the same guild.

    `_bound_identity` cannot return None here — `_resolve_identity` has
    already taken the `binding.is_unbound` branch, so this path is reached
    only on a bound binding.
    """
    bound = _bound_identity(binding)
    quarantine(context.server_id, context.guild_id, bound=bound, observed=observed)
    raise GuildQuarantined(context.guild_id, bound=bound, observed=observed)


def _adopt(
    context: _KeyContext,
    snapshot: GuildSnapshot,
    binding,
    observed: GuildIdentity,
) -> GuildSnapshot:
    """Trust-on-first-use (DDD-8), announced exactly once.

    There is no historical record to reconstruct a binding from, so the
    announcement IS the verification step: an operator reading it is the only
    thing standing between "we adopted the right guild" and "we adopted
    whatever the key happened to resolve to on deploy day".
    """
    _rebind(context, replace(binding, tacticus_guild_id=observed.uuid), observed)
    context.emit(
        logging.INFO, "guild.key.bound",
        tacticus_guild_id=observed.uuid,
        tacticus_guild_tag=observed.tag,
        # Deliberately NOT `name`: `logging` refuses an `extra` key that
        # collides with a LogRecord attribute, and `record.name` is the
        # logger's own name. A `name` field here raises at emit time, which
        # would turn the one announcement that matters into an exception
        # inside the hourly loop.
        tacticus_guild_name=observed.name,
    )
    context.emit_probe_ok(observed)
    return snapshot


def _rebind(context: _KeyContext, binding, observed: GuildIdentity) -> None:
    """Write the display fields and the verification date from a probe that
    SUCCEEDED — never from one that did not.

    `tacticus_guild_id` is not touched here: adoption sets it once (DDD-8) and
    nothing else may, which is what keeps a drifted key from quietly
    re-pointing the binding at the guild it drifted to. The tag and the name
    ARE refreshed on every agreeing probe, so a retag or a rename updates what
    the operator sees without going anywhere near the lock (DDD-1).
    """
    save_guild_binding(
        context.server_id,
        context.guild_id,
        replace(
            binding,
            tacticus_guild_tag=observed.tag,
            tacticus_guild_name=observed.name,
            identity_bound_at=_utc_now(),
        ),
    )


def _report_mismatch(
    context: _KeyContext,
    snapshot: GuildSnapshot,
    binding,
    observed: GuildIdentity,
) -> GuildSnapshot:
    """The incident. Slice 01 reports it and still hands the data back.

    No `guild.key.probe.ok`: KPI-1's detection latency is the gap back to the
    last probe that AGREED, and a probe that disagreed is the event being
    measured, not the baseline it is measured from.
    """
    context.emit(
        logging.ERROR, "guild.key.mismatch",
        bound_id=binding.tacticus_guild_id,
        observed_id=observed.uuid,
        observed_tag=observed.tag,
        observed_name=observed.name,
    )
    return replace(snapshot, outcome=ProbeOutcome.MISMATCH)


def _report_failed_probe(
    context: _KeyContext, snapshot: GuildSnapshot
) -> GuildSnapshot:
    """No identity came back. Report it; change nothing.

    UNVERIFIABLE is an ERROR and not a downgrade to a weaker check: there is
    NO fallback to comparing `guildTag`. Both guilds in the 2026-07-28
    incident carried the 【UNDV】 alliance prefix, so a tag comparison is
    exactly the check that would have looked reassuring and proved nothing
    (DDD-10).
    """
    if snapshot.outcome is ProbeOutcome.UNVERIFIABLE:
        context.emit(
            logging.ERROR, "guild.key.unverifiable", reason="guildId_absent"
        )
        return snapshot

    if snapshot.outcome is ProbeOutcome.DEAD:
        # Never quarantined: a refused key returns no data, so there is
        # nothing to contaminate and a recovery step would buy zero safety.
        context.emit(logging.ERROR, "guild.key.dead", status=snapshot.status)
        return snapshot

    context.emit(
        logging.WARNING, "guild.key.unreachable",
        reason=snapshot.error, status=snapshot.status,
    )
    return snapshot


def _unreachable_without_a_key(context: _KeyContext) -> GuildSnapshot:
    """A guild with no registered key is UNREACHABLE, never probed.

    Not DEAD — nothing was refused, there is simply nothing to ask with, and
    the correct response is to retry next cycle. Sending an empty credential
    to Tacticus to find that out would be a request whose only possible
    outcome is a 401 the operator then has to interpret.
    """
    snapshot = GuildSnapshot(
        outcome=ProbeOutcome.UNREACHABLE,
        error="no api_key is registered for this guild",
    )
    context.emit(
        logging.WARNING, "guild.key.unreachable",
        reason=snapshot.error, status=None,
    )
    return snapshot


# ===========================================================================
# Storage + observability helpers
# ===========================================================================

def _registered_key(discord_server_id: int, guild_id: str) -> str:
    """The guild's `api_key`, or `""` when the guild or the key is absent.

    This is THE sanctioned read (DDD-3). Every other module asks this one.
    """
    guild = load_guilds(discord_server_id).get(guild_id) or {}
    return guild.get("api_key") or ""


def _is_quarantined(discord_server_id: int, guild_id: str) -> bool:
    return _quarantined(load_guild_binding(discord_server_id, guild_id))


def _quarantined(binding) -> bool:
    """The one comparison that decides whether a stored key may still be used.

    One predicate rather than the four hand-written `== QUARANTINED.value`
    comparisons this module used to carry: the whole point of DDD-3 is that
    there is a single place to change, and a status the storage layer starts
    spelling differently must not leave three of four sites still trusting a
    quarantined key.
    """
    return binding.key_status == KeyStatus.QUARANTINED.value


def _refuse_a_quarantined_guild(discord_server_id: int, guild_id: str) -> None:
    """Raise `GuildQuarantined` for an already-quarantined guild. No request.

    Reading the quarantine is a storage read, so refusing costs nothing and
    happens BEFORE the probe: fetching the drifted guild's data and then
    discarding it still pulls that roster into memory and possibly into a
    traceback, which is the damage the refusal exists to prevent.

    QUARANTINED, not "unverified" (DDD-8). An UNBOUND guild has no stored
    identity to be wrong about, and trust-on-first-use is the entire reason
    the probe sits inside `/register_guild` — a gate that refused every guild
    without a binding would close the write hole by making the command
    useless. `key_status` is the whole discrimination.

    Both identities come from the stored binding and its `quarantine_reason`,
    never from a second probe: `GuildQuarantined` is what the caller renders
    its refusal from, and re-asking the drifted key who it belongs to in order
    to say "we refuse to ask the drifted key" is the request being refused.
    """
    binding = load_guild_binding(discord_server_id, guild_id)
    if not _quarantined(binding):
        return
    raise GuildQuarantined(
        guild_id,
        bound=_bound_identity(binding),
        observed=_observed_identity(binding),
    )


def _bound_identity(binding) -> GuildIdentity | None:
    """The identity the binding locks onto, or None when it locks onto none."""
    if not binding.tacticus_guild_id:
        return None
    return GuildIdentity(
        uuid=binding.tacticus_guild_id,
        tag=binding.tacticus_guild_tag,
        name=binding.tacticus_guild_name,
    )


def _observed_identity(binding) -> GuildIdentity | None:
    """The drifted identity, recovered from `quarantine_reason`.

    Only the uuid survives the round trip — `_quarantine_reason` embeds it
    after a parseable marker precisely so a persisting quarantine can name the
    drift without a second probe. The tag and the name are display-only and
    are deliberately not reconstructed from the prose around them.
    """
    observed_uuid = _observed_uuid_from_reason(binding.quarantine_reason or "")
    if not observed_uuid:
        return None
    return GuildIdentity(uuid=observed_uuid)


def _key_ref_for(api_key: str) -> str:
    """Derive the log correlation ID for `api_key`.

    `bot.db.secrets` is imported inside the function, not at module scope, and
    only when the Fernet key is actually present. `bot/guild_keys.py` is
    imported by three cogs and a service, so a module-scope import here would
    put `cryptography` on the JSON rollback path where ADR-006 D9 says the
    SQLite stack stays untouched.
    """
    fernet_key = os.getenv("SCRAPCODE_DB_KEY", "")
    if not api_key or not fernet_key:
        return UNKNOWN_KEY_REF
    from bot.db.secrets import api_key_hmac
    return key_ref(api_key_hmac(api_key, fernet_key))


def _quarantine_reason(bound: GuildIdentity, observed: GuildIdentity) -> str:
    """Both identities, in a form an operator can act on a week later.

    Carries both tags (the operator-facing discriminator) and embeds the
    observed uuid after a parseable marker so a persisting quarantine can
    re-report the drift from the stored binding alone, without a second
    probe (the skip path must not request the other guild's data again).
    """
    return (
        f"key drift: bound 【{bound.tag or '—'}】 {bound.name or '—'} "
        f"but resolves to 【{observed.tag or '—'}】 {observed.name or '—'} "
        f"— observed={observed.uuid}"
    )


def re_report_persisting_drift(discord_server_id: int, guild_id: str) -> None:
    """Re-emit `guild.key.mismatch` for a quarantine that persists.

    Called from the cog's skip path (a quarantined guild whose
    `active_key` returned None). The mismatch RECORD is never suppressed —
    only the ALERT is rate-limited (AC-002.6 / the persistent-mismatch
    scenario). The drift is reconstructed from the stored binding +
    `quarantine_reason`; no second request is made.
    """
    binding = load_guild_binding(discord_server_id, guild_id)
    observed_uuid = _observed_uuid_from_reason(binding.quarantine_reason or "")
    context = _KeyContext(
        discord_server_id, guild_id,
        _key_ref_for(_registered_key(discord_server_id, guild_id)),
    )
    context.emit(
        logging.ERROR, "guild.key.mismatch",
        bound_id=binding.tacticus_guild_id, observed_id=observed_uuid,
        observed_tag=None, observed_name=None,
    )


def _observed_uuid_from_reason(reason: str) -> str | None:
    """Extract the observed uuid embedded by `_quarantine_reason`."""
    marker = "— observed="
    if marker not in reason:
        return None
    return reason.split(marker, 1)[1].strip() or None


def record_quarantine_alert(
    discord_server_id: int, guild_id: str, channel,
) -> str | None:
    """One alert per guild per `ALERT_SUPPRESSION_HOURS` (24h) while a
    quarantine persists.

    The single decision point for BOTH the first quarantine (called from the
    cog's catch handler) and a persisting quarantine (called from the cog's
    skip path). Returns the line to post when the alert fires, or None when
    it is suppressed (the suppressed alert is RECORDED, not dropped, so the
    log can distinguish "we suppressed 23" from "the loop stopped").

    The FIRST alert of a quarantine always fires: `release()` resets
    `last_alerted_at` to None, so a fresh quarantine starts the clock clean.
    """
    binding = load_guild_binding(discord_server_id, guild_id)
    key_ref = _key_ref_for(_registered_key(discord_server_id, guild_id))
    now = _utc_now()
    now_dt = _parse_utc(now)
    last = binding.last_alerted_at
    channel_id = getattr(channel, "id", None)

    if last is not None:
        last_dt = _parse_utc(last)
        if (now_dt - last_dt) < timedelta(hours=ALERT_SUPPRESSION_HOURS):
            emit_structured(
                logger, logging.DEBUG, "guild.key.alert.suppressed",
                ts=now, server_id=discord_server_id, guild_id=guild_id,
                key_ref=key_ref, last_alerted_at=last, suppressed=True,
                channel_id=channel_id,
            )
            return None

    emit_structured(
        logger, logging.INFO, "guild.key.alert.sent",
        ts=now, server_id=discord_server_id, guild_id=guild_id,
        key_ref=key_ref, channel_id=channel_id, suppressed_until=None,
    )
    save_guild_binding(discord_server_id, guild_id, replace(binding, last_alerted_at=now))
    return None


def _parse_utc(iso: str) -> datetime:
    """Parse an `_utc_now`-shaped ISO-8601 UTC string back to a datetime.

    The 24h suppression boundary needs a real delta, not a string compare, so
    `record_quarantine_alert` parses `last_alerted_at` with this. Accepts the
    millisecond-precision shape `_utc_now` produces (`...%Y-%m-%dT%H:%M:%S.%fZ`);
    `%f` in `strptime` takes 1-6 digits, so the 3-digit millisecond form parses.
    """
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def _utc_now() -> str:
    """ISO-8601 UTC, millisecond precision, `String(32)`-shaped.

    The same shape as `battle_hits.completed_on`, which KPI-2 compares AS
    STRINGS — a different shape returns a wrong result set silently instead
    of erroring. Milliseconds rather than whole seconds because KPI-1 asserts
    `alerted_at − last_probe_ok_at > 0`: at second resolution two records
    emitted inside the same second are indistinguishable, and the metric
    reads zero for a real, non-zero latency.
    """
    now = datetime.now(timezone.utc)
    return f"{now.strftime('%Y-%m-%dT%H:%M:%S')}.{now.microsecond // 1000:03d}Z"
