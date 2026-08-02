"""In-memory composition root for Tier B (Mandate 10).

Same interfaces as production, in-memory doubles behind them. Tier A wires
the shared step vocabulary through the real cogs and real SQLite; Tier B
wires the SAME vocabulary through this, so the two tiers agree on what the
observable names mean.

What this CANNOT model, and therefore what Tier A still has to prove:
  * transaction boundaries — writes here are dict mutations, so a partial
    write is not reachable
  * alembic revision state and the startup probe
  * Fernet encryption and the api_key_hmac derivation
  * anything about Discord: ephemerality, permissions, embed limits
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from domain_types import GuildIdentity, KeyStatus, ProbeOutcome  # noqa: E402


@dataclass
class Binding:
    tacticus_guild_id: str | None = None
    tacticus_guild_tag: str | None = None
    tacticus_guild_name: str | None = None
    identity_bound_at: str | None = None
    key_status: str = KeyStatus.ACTIVE.value
    quarantine_reason: str | None = None
    quarantined_at: str | None = None

    @property
    def is_unbound(self) -> bool:
        return self.tacticus_guild_id is None


@dataclass
class GuildState:
    guild_id: str
    api_key: str
    binding: Binding = field(default_factory=Binding)
    battle_hits: int = 0
    bomb_hits: int = 0
    players: int = 0


# A monotonic clock for `identity_bound_at` / `quarantined_at`. The in-memory
# model does not assert timestamp shape (that is Tier A's concern against
# `battle_hits.completed_on`); it only needs the fields populated so a
# release that clears `quarantined_at` is observable as a state change.
_tick = 0


def _now_iso() -> str:
    global _tick
    _tick += 1
    return f"2026-08-01T00:00:{_tick:02d}.000Z"


class InMemoryComposition:
    """The Tier B composition root.

    Mirrors production semantics behind in-memory doubles:
      * `active_key` returns None for a quarantined guild, so `run_cycle`
        skips it (no probe, no write) — the same fall-through DDD-7 relies on.
      * `verify_and_resolve` with `enforce=True` quarantines on MISMATCH
        BEFORE any write (DDD-2/5) and leaves UNVERIFIABLE / UNREACHABLE /
        DEAD untouched (DDD-6); UNVERIFIABLE and UNREACHABLE still ingest
        (an outage must not block), DEAD does not (no data to write).
      * `install_guild_key` adopts on unbound, releases on a matching key,
        rebinds on `force=True`, and refuses a mismatch without force.

    `capture_universe()` returns ONLY port-exposed observable names. Nothing
    here exposes a private field: a Universe keyed on internals reds the test
    for a rename, which is a refactoring-hostile signal rather than a defect
    signal.
    """

    def __init__(self, enforcement: bool = True) -> None:
        self.enforcement = enforcement
        self.guilds: dict[str, GuildState] = {}
        self.alerts: list[str] = []
        self.adoptions: list[str] = []
        self.events: list[str] = []
        self._programmed: dict[str, tuple[ProbeOutcome, GuildIdentity | None]] = {}

    # -- commands ---------------------------------------------------------

    def register_guild(self, guild_id: str, api_key: str) -> None:
        self.guilds[guild_id] = GuildState(guild_id=guild_id, api_key=api_key)

    def probe(self, guild_id: str, outcome: ProbeOutcome,
              identity: GuildIdentity | None = None) -> None:
        """Program the NEXT cycle's probe for `guild_id`.

        Mirrors `fake_guild_service.program`: stores the classification the
        next `run_cycle` will read. Does not run the cycle.
        """
        self._programmed[guild_id] = (outcome, identity)

    def run_cycle(self) -> None:
        for guild_id, state in list(self.guilds.items()):
            self._run_one(guild_id, state)

    def update_key(self, guild_id: str, api_key: str,
                   resolves_to: GuildIdentity, force: bool = False) -> bool:
        """Install a submitted key, mirroring `guild_keys.install_guild_key`.

        The submitted key is implicitly a MATCH for `resolves_to` (the admin
        submits a key that resolves to a known identity). Returns True when
        the key was installed, False when a mismatch was refused without
        force — the gate the negative test asserts is real.
        """
        state = self.guilds[guild_id]
        if state.binding.is_unbound:
            state.api_key = api_key
            state.binding = Binding(
                tacticus_guild_id=resolves_to.uuid,
                tacticus_guild_tag=resolves_to.tag,
                tacticus_guild_name=resolves_to.name,
                identity_bound_at=_now_iso(),
                key_status=KeyStatus.ACTIVE.value,
            )
            self.events.append("guild.key.probe.ok")
            return True

        bound = GuildIdentity(uuid=state.binding.tacticus_guild_id)
        if bound.matches(resolves_to):
            state.api_key = api_key
            self._release(state)
            self.events.append("guild.key.probe.ok")
            return True

        if force:
            state.api_key = api_key
            state.binding = Binding(
                tacticus_guild_id=resolves_to.uuid,
                tacticus_guild_tag=resolves_to.tag,
                tacticus_guild_name=resolves_to.name,
                identity_bound_at=_now_iso(),
                key_status=KeyStatus.ACTIVE.value,
            )
            return True

        self.events.append("guild.key.mismatch")
        return False

    # -- observables ------------------------------------------------------

    def capture_universe(self) -> dict:
        """Port-exposed observables, keyed on names a rename must survive.

        Per-guild status and total rows plus the event trace — the surface
        the state-delta universe would declare, kept here so a future
        migration to `assert_state_delta` has its vocabulary ready.
        """
        return {
            "status": {
                gid: state.binding.key_status
                for gid, state in self.guilds.items()
            },
            "total_rows": self.total_rows(),
            "events": list(self.events),
        }

    def status_of(self, guild_id: str) -> str:
        return self.guilds[guild_id].binding.key_status

    def total_rows(self) -> int:
        return sum(
            s.battle_hits + s.bomb_hits + s.players for s in self.guilds.values()
        )

    # -- internals --------------------------------------------------------

    def _run_one(self, guild_id: str, state: GuildState) -> None:
        if state.binding.key_status == KeyStatus.QUARANTINED.value:
            return  # `active_key` returns None — the cycle skips (DDD-7)
        if guild_id not in self._programmed:
            return
        outcome, identity = self._programmed.pop(guild_id)

        if outcome is ProbeOutcome.DEAD:
            self.events.append("guild.key.dead")
            return  # no data, no status change (DDD-6)

        if identity is None:
            # UNVERIFIABLE or UNREACHABLE: never quarantine (DDD-6), still ingest.
            if outcome is ProbeOutcome.UNVERIFIABLE:
                self.events.append("guild.key.unverifiable")
            else:
                self.events.append("guild.key.unreachable")
            self._ingest(state)
            return

        if state.binding.is_unbound:
            self._adopt(state, identity)
            self._ingest(state)
            return

        bound = GuildIdentity(uuid=state.binding.tacticus_guild_id)
        if bound.matches(identity):
            self._refresh(state, identity)
            self.events.append("guild.key.probe.ok")
            self._ingest(state)
            return

        self.events.append("guild.key.mismatch")
        if self.enforcement:
            self._quarantine(state, identity)
            self.events.append("guild.key.quarantined")
            return  # no ingest — DDD-2/5
        self._ingest(state)  # slice-01 non-enforcement: still ingest

    def _adopt(self, state: GuildState, identity: GuildIdentity) -> None:
        state.binding = Binding(
            tacticus_guild_id=identity.uuid,
            tacticus_guild_tag=identity.tag,
            tacticus_guild_name=identity.name,
            identity_bound_at=_now_iso(),
            key_status=KeyStatus.ACTIVE.value,
        )
        self.events.append("guild.key.bound")
        self.events.append("guild.key.probe.ok")
        self.adoptions.append(identity.uuid)

    def _refresh(self, state: GuildState, identity: GuildIdentity) -> None:
        state.binding = replace(
            state.binding,
            tacticus_guild_tag=identity.tag,
            tacticus_guild_name=identity.name,
            identity_bound_at=_now_iso(),
        )

    def _quarantine(self, state: GuildState, observed: GuildIdentity) -> None:
        already = state.binding.key_status == KeyStatus.QUARANTINED.value
        state.binding = replace(
            state.binding,
            key_status=KeyStatus.QUARANTINED.value,
            quarantine_reason=(
                f"key drift: bound {state.binding.tacticus_guild_tag} "
                f"but resolves to {observed.tag}"
            ),
        )
        if not already:
            state.binding = replace(state.binding, quarantined_at=_now_iso())

    def _release(self, state: GuildState) -> None:
        state.binding = replace(
            state.binding,
            key_status=KeyStatus.ACTIVE.value,
            quarantine_reason=None,
            quarantined_at=None,
        )

    def _ingest(self, state: GuildState) -> None:
        state.battle_hits += 1
        state.bomb_hits += 1
        state.players += 1