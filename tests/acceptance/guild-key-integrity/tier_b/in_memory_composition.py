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

__SCAFFOLD__ = True


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


class InMemoryComposition:
    """The Tier B composition root.

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

    # -- commands ---------------------------------------------------------

    def register_guild(self, guild_id: str, api_key: str) -> None:
        raise AssertionError("Not yet implemented — RED scaffold")

    def probe(self, guild_id: str, outcome: ProbeOutcome,
              identity: GuildIdentity | None = None) -> None:
        raise AssertionError("Not yet implemented — RED scaffold")

    def run_cycle(self) -> None:
        raise AssertionError("Not yet implemented — RED scaffold")

    def update_key(self, guild_id: str, api_key: str,
                   resolves_to: GuildIdentity, force: bool = False) -> bool:
        raise AssertionError("Not yet implemented — RED scaffold")

    # -- observables ------------------------------------------------------

    def capture_universe(self) -> dict:
        raise AssertionError("Not yet implemented — RED scaffold")

    def status_of(self, guild_id: str) -> str:
        raise AssertionError("Not yet implemented — RED scaffold")

    def total_rows(self) -> int:
        raise AssertionError("Not yet implemented — RED scaffold")
