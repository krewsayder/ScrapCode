import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import NotRequired, Optional, Protocol, TypedDict, runtime_checkable

from bot.models import Cluster, Guild
from bot.migrations.player_list_migrations import PlayerListMigrator


# ---------------------------------------------------------------------------
# TypedDict contracts for the dict shapes the ABC returns / accepts.
#
# These are L4 documentation-as-type contracts for the cog layer. The ABC
# method signatures stay `dict` (runtime callers pass plain dicts); the
# TypedDicts give cogs and adapters a named field set the data-dictionary
# §2.7/§2.9/§2.10 describes prose-wise today. Internal adapter helpers and
# cog-local variables annotate against these so mypy can flag field-name
# drift the moment a column rename or schema change lands.
# ---------------------------------------------------------------------------


class BattleHitEntry(TypedDict):
    """One Battle hit row in the data-dictionary §2.7 load shape.

    `machine_of_war` is `{"unitId": ...}` or None (matches the API's
    `machineOfWarDetails` shape that `process_api_response` forwards).
    """

    encounterType: str | None
    damage: int
    user_id: str
    completed_on: str
    hero_details: list
    machine_of_war: dict | None


class BombHitEntry(TypedDict):
    """One Bomb hit row in the data-dictionary §2.9 load shape."""

    encounterType: str | None
    damage: int
    user_id: str
    completed_on: str


class ReplayEntry(TypedDict):
    """One replay entry in the data-dictionary §2.10 shape.

    `index_message_id` is optional: the cog does not set it on upload
    (the repo records it on the thread row via a separate call); the
    JSON rollback impl preserves it when present in `replay_index.json`.
    """

    team: str
    tier: str
    position: str
    damage: str
    url: str
    comment: str
    submitted_by: str
    index_message_id: NotRequired[int | None]


class ReplayThreadInfo(TypedDict):
    """The thread-info dict returned by `get_replay_thread` / listed by
    `list_replay_threads` (data-dictionary §2.10). `forum_channel_id` and
    `thread_id` are None on the JSON rollback path (ADR-006 D9 acceptable
    degradation)."""

    forum_channel_id: int | None
    thread_id: int | None
    index_message_id: NotRequired[int | None]


@dataclass(frozen=True)
class GuildBinding:
    """Which Tacticus guild a guild's API key is bound to (ADR-008 DDD-4).

    The port-level shape of one `guild_key_bindings` row, so that cogs and the
    key-policy chokepoint never import the ORM row. Frozen and value-compared
    on purpose: AC-001.9 / D6 assert a binding is BYTE-IDENTICAL after a failed
    probe, and `==` on a frozen dataclass says exactly that — an implementation
    that refreshed `identity_bound_at` on a probe that never succeeded would
    report a verification date for a check that did not happen.

    The default instance IS the unbound state. No row means UNBOUND, and that
    is a NORMAL state, never an error: trust-on-first-use (DDD-8) writes the
    row on the first successful probe, so every guild is unbound on the day
    Slice 01 deploys. `load_guild_binding` therefore returns this rather than
    None, keeping the None-check off seven call sites.

    Carries no key material. Log correlation uses `key_ref` — the first 8 hex
    of `api_key_hmac` — which the policy layer derives, not this row.

    Timestamps are ISO-8601 UTC strings in the SAME `String(32)` shape as
    `battle_hits.completed_on`; KPI-2 compares them AS STRINGS, so a different
    shape returns a wrong result set silently instead of erroring.

    `key_status` holds the string values of
    `bot.services.tacticus.guild_client.KeyStatus` (`active` / `quarantined`).
    The literal is duplicated rather than imported: policy depends on storage
    and never the reverse (ADR-008 D3), and a repository that imported the
    chokepoint could quarantine during a read — which would make the
    quarantine state unreadable exactly while quarantined.
    """

    tacticus_guild_id: str | None = None
    tacticus_guild_tag: str | None = None
    tacticus_guild_name: str | None = None
    identity_bound_at: str | None = None
    key_status: str = "active"
    quarantine_reason: str | None = None
    quarantined_at: str | None = None
    last_alerted_at: str | None = None

    @property
    def is_unbound(self) -> bool:
        """True while no identity has ever been adopted for this guild.

        Keyed on `tacticus_guild_id` alone (DDD-1): the tag and the name are
        display-only and may legitimately be absent, so treating either as
        evidence of a binding would call an unverifiable guild bound.
        """
        return self.tacticus_guild_id is None


@dataclass(frozen=True)
class QuarantineTombstone:
    """A quarantine that happened, kept after the guild it happened to is gone.

    The port-level shape of one `guild_key_quarantine_history` row. Written by
    `/deregister_guild` from the binding it is about to destroy, and read by
    `/register_guild` when the same slug comes back.

    IT HAS NO FOREIGN KEY AND THAT IS THE DESIGN (DELIVER's answer to UI-11,
    recorded in the feature delta). The CASCADE that makes `/deregister_guild`
    destructive is what drops the binding, so anything attached to `guilds`
    dies with it — including the only record that the guild was ever
    quarantined. Without this row, deregistering and re-registering a slug
    launders the quarantine in two commands and trust-on-first-use (DDD-8)
    adopts the drifted key as the new truth, with no warning (AC-009.5).

    It is SURFACED, never enforced. Re-registration stays allowed: refusing it
    would break a legitimate re-registration, and the operator's decision of
    2026-08-02 is that deregistering destroys data by design.

    Frozen and value-compared for the same reason as `GuildBinding` — history
    that can be edited in place is not history.

    Carries no key material (KPI-6). This row outlives every other trace of
    the guild, so a key value written into it is a leak with no expiry.
    `observed_tacticus_guild_id` is the drifted uuid recovered from the
    binding's `quarantine_reason`, which is the only carrier the codebase has
    for it.
    """

    guild_id: str
    tacticus_guild_id: str | None = None
    tacticus_guild_tag: str | None = None
    tacticus_guild_name: str | None = None
    observed_tacticus_guild_id: str | None = None
    quarantine_reason: str | None = None
    quarantined_at: str | None = None
    recorded_at: str | None = None


# ADR-006 D8 / §Architecture enforcement: every ClusterRepository adapter
# wired into the composition root (`bot.guilds.repo`) MUST expose a `probe()`
# method. The probe is the Earned-Trust startup gate; the composition root
# refuses to start if the probe raises `ProbeRefusedError`. The JSON impl's
# probe is a no-op (the probe is skipped on the JSON rollback path). This
# Protocol is the mypy + runtime-checkable contract asserted at the
# composition-root boundary.
@runtime_checkable
class SupportsProbe(Protocol):
    def probe(self) -> None: ...

# ADR-007: the ABC carries 4 storage-medium-agnostic season-hit read/write
# methods. `get_guild_data_path` (JSON-specific — returned a filesystem dir)
# was removed from the ABC in Slice 04 once the 4 cog read sites +
# `embeds.load_leaderboard_file` were rewired to `load_battle_hits` /
# `load_bomb_hits` (US-008 / 04-02). The dict shape returned by the load_*
# methods is the existing `{"boss_hits": ...}` shape that
# `bot/embeds.build_battle_messages` / `build_bomb_messages` and
# `bot/tracker.process_api_response` consume today (data-dictionary §2.7/§2.9).


class ClusterRepository(ABC):
    @abstractmethod
    def load(self, discord_server_id: int) -> Cluster: ...

    @abstractmethod
    def save(self, cluster: Cluster) -> None: ...

    # --- Guild-dict projection (slice 07 / step 09-03) ---
    # `bot.guilds.load_guilds` / `save_guilds` previously projected between
    # `Guild` objects and the five-key cog-facing dict INSIDE the wrapper
    # layer. That put a plaintext `api_key` read in `bot/guilds.py`, which the
    # widened chokepoint scan (AC-010.6) catches. The projection moves HERE
    # because the adapters are the `SANCTIONED_KEY_READERS` — they already
    # encrypt and decrypt `api_key`, so the Guild-to-dict and dict-to-Guild
    # mapping belongs beside the column access it is a projection of.
    #
    # The dict shape is `{guild_id: {name, api_key, role_id,
    # notification_channel_id, member_role_ids}}` — byte-identical to what
    # `load_guilds` returned before the move, so `admin_cog._config_guilds`'s
    # presence test (AC-005.3) and every load-mutate-save cog are unchanged.

    @abstractmethod
    def load_guilds_dict(self, discord_server_id: int) -> dict:
        """Return ``{guild_id: {name, api_key, role_id,
        notification_channel_id, member_role_ids}}`` for a server.

        The five-key shape is the cog-facing contract; ``api_key`` is
        included because cogs read it as a presence test (AC-005.3) and
        load-mutate-save commands round-trip it. Removing it is a wider
        refactor (slice 07 OUT-of-scope) and is NOT attempted here.
        """

    @abstractmethod
    def save_guilds_dict(self, discord_server_id: int, guilds: dict) -> None:
        """Persist the five-key guild dict, preserving non-guild cluster state.

        Loads the existing cluster (to keep ``update_channel_id`` /
        ``role_tiers``), replaces ``guilds`` from the dict, and saves. A
        load-mutate-save cycle by an unrelated admin command must not blank a
        sibling guild's key — the round-trip invariant is load-bearing.
        """

    @abstractmethod
    def load_player_registrations(self, discord_server_id: int) -> dict: ...

    @abstractmethod
    def save_player_registrations(self, discord_server_id: int, data: dict) -> None: ...

    @abstractmethod
    def load_capped_state(self, discord_server_id: int) -> dict: ...

    @abstractmethod
    def save_capped_state(self, discord_server_id: int, data: dict) -> None: ...

    @abstractmethod
    def load_player_list(self, discord_server_id: int, guild_id: str) -> dict: ...

    @abstractmethod
    def save_player_list(self, discord_server_id: int, guild_id: str, data: dict) -> None: ...

    @abstractmethod
    def load_live_leaderboards(self, discord_server_id: int) -> dict: ...

    @abstractmethod
    def save_live_leaderboards(self, discord_server_id: int, data: dict) -> None: ...

    @abstractmethod
    def list_server_ids(self) -> list[int]: ...

    # --- ADR-007: storage-medium-agnostic season-hit read/write methods ---

    @abstractmethod
    def load_battle_hits(self, discord_server_id: int, guild_id: str, season: int) -> dict:
        """Return `{"boss_hits": {boss_id: {encounter_index: {tier_key: [entries]}}}}`
        — the exact shape `bot/embeds.build_battle_messages` and
        `bot/tracker.process_api_response` consume today (data-dictionary §2.7).
        """

    @abstractmethod
    def load_bomb_hits(self, discord_server_id: int, guild_id: str, season: int) -> dict:
        """Return `{"boss_hits": ...}` with the bomb entry shape (data-dictionary §2.9)."""

    @abstractmethod
    def upsert_battle_hits(self, discord_server_id: int, guild_id: str, season: int,
                           entries: list[dict]) -> None:
        """Upsert Battle hit entries with per-player-per-roster dedup
        (keep-max(damage)). Replaces `bot/tracker.try_insert(check_roster=True)`
        + `save_json` (ADR-006 D4 / ADR-007 / US-006)."""

    @abstractmethod
    def upsert_bomb_hits(self, discord_server_id: int, guild_id: str, season: int,
                         entries: list[dict]) -> None:
        """Upsert Bomb hit entries with plain top-N (no roster dedup)
        (data-dictionary §2.9 / US-006)."""

    @abstractmethod
    def upsert_guild_hits(self, discord_server_id: int, guild_id: str, season: int,
                          battle_entries: list[dict], bomb_entries: list[dict]) -> None:
        """Upsert one guild's battle + bomb hits in a SINGLE transaction
        (ADR-006 D6 — one transaction per guild). A failure in either upsert
        rolls back the whole guild's writes for this cycle, so a mid-cycle
        crash leaves that guild's pre-cycle state intact. Cross-guild
        isolation is provided by separate transactions per guild_id."""

    @abstractmethod
    def count_guild_destruction_rows(self, discord_server_id: int, guild_id: str) -> dict:
        """Count the rows a deregistration will CASCADE-delete for this guild.

        Returns `{"players": N, "battle_hits": N, "bomb_hits": N}` — every row
        that will be destroyed when the `guilds` row is dropped, across ALL
        seasons (`ondelete="CASCADE"` is not season-scoped). `/deregister_guild`
        states these counts to the operator BEFORE the deletion so an admin
        who has learned the (false) "left intact" message can see what is
        actually about to happen (AC-009.4). The CASCADE itself is unchanged;
        this is a read, not a change to the destructive semantics.
        """

    # --- ADR-007-pattern replay methods (added in 04-03; ADR-006 D10/D11) ---
    # The replay cog routes through these instead of replay_index.json +
    # hardcoded FORUM_CHANNELS/MAP_THREADS. Per-tenant URL uniqueness is
    # enforced on (discord_server_id, boss, map_name, url) — the global
    # uniqueness leak (ADR-004 §3) is closed. Thread IDs come from
    # replay_threads (seeded in 03-03), closing the hardcoded-thread-ID leak.

    @abstractmethod
    def load_replay_entries(self, discord_server_id: int, boss: str, map_name: str) -> list[dict]:
        """Return the replay entries for (server, boss, map_name) in insertion
        order — the shape `build_index_message` consumes (data-dictionary §2.10)."""

    @abstractmethod
    def upsert_replay_entry(self, discord_server_id: int, boss: str, map_name: str,
                            entry: dict) -> None:
        """Insert one replay entry. Raises `DuplicateReplayUrlError` when the
        URL already exists for (server, boss, map_name) (ADR-006 D11)."""

    @abstractmethod
    def delete_replay_entry(self, discord_server_id: int, boss: str, map_name: str,
                            url: str) -> bool:
        """Delete the entry with matching URL. Return True if a row was removed,
        False if no matching entry existed."""

    @abstractmethod
    def get_replay_thread(self, discord_server_id: int, boss: str, map_name: str) -> dict | None:
        """Return `{"forum_channel_id", "thread_id", "index_message_id"}` for
        the (server, boss, map_name) thread, or None if no such thread is
        registered (ADR-006 D10)."""

    @abstractmethod
    def set_replay_thread_index_message(self, discord_server_id: int, boss: str,
                                         map_name: str, index_message_id: int) -> None:
        """Record the Discord message id of the forum-thread index message for
        (server, boss, map_name) so subsequent renders edit it in place."""

    @abstractmethod
    def list_replay_threads(self, discord_server_id: int) -> dict:
        """Return `{boss: {map_name: {"forum_channel_id", "thread_id"}}}` for
        every registered (boss, map_name) — drives boss/map autocomplete."""

    # --- ADR-007-pattern guild-key-binding methods (02-02; ADR-008 DDD-4) ---
    # Binding state is reached ONLY through these three. It deliberately does
    # not travel on `Cluster`/`Guild`: `bot.guilds.save_guilds` rebuilds each
    # `Guild` from a five-key dict, so a binding field reachable from the
    # dataclass would be written back as `None` by the next unrelated admin
    # command — `/set_ping_channel` alone would wipe it. A separate table
    # reached by separate methods makes that clobber structurally impossible
    # rather than merely avoided.

    @abstractmethod
    def load_guild_binding(self, discord_server_id: int, guild_id: str) -> GuildBinding:
        """Return the guild's identity binding, or an unbound `GuildBinding`.

        Never returns None — see `GuildBinding` for why absence is modelled as
        a value rather than a missing one."""

    @abstractmethod
    def save_guild_binding(self, discord_server_id: int, guild_id: str,
                           binding: GuildBinding) -> None:
        """Persist the guild's identity binding, inserting the row on first
        adoption (DDD-8 trust-on-first-use) and replacing it thereafter."""

    @abstractmethod
    def list_guild_bindings(self, discord_server_id: int) -> dict[str, GuildBinding]:
        """Return `{guild_id: binding}` for every guild that HAS a binding.

        Guilds with no row are absent from the mapping rather than present as
        unbound: the caller pairs this with the guild registry, and inventing
        entries for guilds that may not be registered would make the two
        disagree."""

    @abstractmethod
    def replace_guild_key(self, discord_server_id: int, guild_id: str,
                          api_key: str) -> None:
        """UPDATE only `api_key` (and `api_key_hmac` where the backend has one)
        on the single named guild row, in one transaction.

        This is the targeted key-swap path (ADR-006 D7 / AC-003.2). Unlike
        `save` — which rewrites every guild row and CASCADE-deletes absent ones
        — this touches ONLY the two key columns, so dependent player/hit rows
        cannot be affected and CASCADE is impossible by construction, not by
        avoidance. Both `api_key` and `api_key_hmac` are written in the SAME
        transaction: a write that updates one without the other leaves a row
        whose uniqueness check no longer matches its key.

        THREE REFUSALS, and every adapter owes all three (08-01):

        - `KeyError` when the guild row is absent: installing a key for an
          unregistered guild is a caller-side refusal (04-02), never a silent
          no-op here.
        - `ValueError` when `api_key` is empty or whitespace-only. Blanking is
          not a key write. `encrypt_api_key("")` returns `""` and
          `api_key_hmac("")` returns `None` — both correct in isolation, and
          both the reason several keyless guilds coexist under a NULLABLE
          UNIQUE constraint — but composed they turn the one method allowed to
          write a key into one that silently erases it. A whitespace-only key
          is the same erasure wearing a disguise: nothing can authenticate
          with it, so the guild's real key is gone either way. Use `save` to
          register a guild with no key; this method only ever replaces one.
          Refused BEFORE anything is written, so a refusal leaves every column
          byte-identical (`AC-009.7`).
        - `GuildKeyAlreadyRegisteredError` when another guild already holds
          this key, on backends that enforce key uniqueness. `guilds.api_key_
          hmac` is UNIQUE, and an untranslated violation surfaces as a raw
          `IntegrityError` carrying the Fernet ciphertext and the full 64-hex
          hmac in its inlined bound parameters — which `main.py`'s generic
          handler prints and sends to Discord (KPI-6). The typed refusal names
          the holding guild and carries no key material. Same pattern, same
          reason, as `DuplicateReplayUrlError` on the replay-URL constraint
          (ADR-007). The JSON rollback backend has no `api_key_hmac` column
          and therefore no uniqueness to violate; per ADR-006 D9 it degrades —
          it honours the blank refusal identically and cannot raise this one.

        Raw-SQL key edits are forbidden — this is the only sanctioned write
        path for a key replacement.
        """

    # --- Quarantine history (08-03; ADR-008 DDD-4, UI-11) ---
    # Separate from the binding methods above because the two have opposite
    # lifetimes: a binding is 1:1 with a guild and CASCADEs away with it, a
    # tombstone is append-only and exists to OUTLIVE that deletion.

    @abstractmethod
    def record_quarantine_tombstone(self, discord_server_id: int,
                                    tombstone: QuarantineTombstone) -> None:
        """Append one quarantine to the guild's history.

        Append-only: a second quarantine of the same slug is a second entry,
        never an overwrite. Nothing removes one — a quarantine that HAPPENED
        does not stop having happened when the guild is re-registered, and
        `guild_keys.release` clears the live binding rather than this.

        Called on the path that PERFORMS the deregistration, from the binding
        it is about to destroy, so that a later confirmation gate on the
        command cannot leave a tombstone for a deletion that never happened.
        """

    @abstractmethod
    def list_quarantine_tombstones(self, discord_server_id: int,
                                   guild_id: str) -> list[QuarantineTombstone]:
        """Every quarantine recorded against this guild id, oldest first.

        Empty is the normal answer and never an error: most slugs have no
        history, and a re-registration of one of them proceeds silently.
        """

    @staticmethod
    def _refuse_blank_guild_key(api_key: str) -> None:
        """Guard shared by every adapter, so the refusal cannot drift apart.

        Declared on the ABC rather than duplicated per adapter: this is the
        `ValueError` clause of `replace_guild_key`'s contract, and a second
        copy of it is exactly the divergence between the live and the rollback
        path that ADR-006 D9 exists to keep narrow.
        """
        if api_key.strip():
            return
        raise ValueError(
            "replace_guild_key refuses a blank api_key: it would erase the "
            "guild's key rather than replace it"
        )


class DuplicateReplayUrlError(Exception):
    """Raised by `upsert_replay_entry` when (server, boss, map_name, url) is
    already present (ADR-006 D11 per-tenant URL uniqueness). Carries the
    (boss, map_name) so the cog can render the byte-for-byte duplicate reply."""
    def __init__(self, boss: str, map_name: str, url: str):
        self.boss = boss
        self.map_name = map_name
        self.url = url
        super().__init__(f"Duplicate replay URL for {boss!r}/{map_name!r}: {url!r}")


class GuildKeyAlreadyRegisteredError(Exception):
    """Raised by `replace_guild_key` when another guild already holds the key.

    The `guilds.api_key_hmac` UNIQUE constraint is what refuses the write; this
    is that refusal translated at the repository boundary, so a raw
    `IntegrityError` never leaves the adapter. Same move, same reason, as
    `DuplicateReplayUrlError`.

    CARRIES `guild_id` — the guild that ALREADY holds the key — and nothing
    else. That one field is the difference between a refusal an admin can act
    on and a dead end. What it deliberately does NOT carry is any key
    material: no plaintext, no Fernet ciphertext, no hmac, no SQL fragment and
    no bound parameters. `main.py`'s handler interpolates `{error}` into both
    a log line and a Discord message, so whatever this renders IS what is
    disclosed (KPI-6) — which is why the message is built from the guild id
    alone and the originating `IntegrityError` is suppressed at the raise
    (`from None`) rather than chained into a traceback that carries it.
    """
    def __init__(self, guild_id: str):
        self.guild_id = guild_id
        super().__init__(f"That API key is already registered to guild {guild_id!r}")


class JsonClusterRepository(ClusterRepository):
    def __init__(self, base_path: Path = Path("clusters")):
        self._base = base_path

    def _server_path(self, discord_server_id: int) -> Path:
        path = self._base / str(discord_server_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _guild_path(self, discord_server_id: int, guild_id: str) -> Path:
        path = self._server_path(discord_server_id) / guild_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _read_json(self, path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_json(self, path: Path, data: dict) -> None:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def load(self, discord_server_id: int) -> Cluster:
        guilds_file = self._server_path(discord_server_id) / "guilds.json"
        raw = self._read_json(guilds_file)
        if not raw:
            return Cluster(discord_server_id=discord_server_id)

        guilds = {
            guild_id: Guild(
                id=guild_id,
                name=data["name"],
                api_key=data.get("api_key", ""),
                role_id=data.get("role_id", 0),
                notification_channel_id=data.get("notification_channel_id"),
                member_role_ids=data.get("member_role_ids", []),
            )
            for guild_id, data in raw.get("guilds", {}).items()
        }

        return Cluster(
            discord_server_id=discord_server_id,
            guilds=guilds,
            update_channel_id=raw.get("update_channel_id"),
            role_tiers=raw.get("role_tiers", {}),
        )

    def save(self, cluster: Cluster) -> None:
        guilds_file = self._server_path(cluster.discord_server_id) / "guilds.json"
        self._write_json(guilds_file, {
            "update_channel_id": cluster.update_channel_id,
            "role_tiers":        cluster.role_tiers,
            "guilds": {
                guild_id: {
                    "name":                    g.name,
                    "api_key":                 g.api_key,
                    "role_id":                 g.role_id,
                    "notification_channel_id": g.notification_channel_id,
                    "member_role_ids":         g.member_role_ids,
                }
                for guild_id, g in cluster.guilds.items()
            },
        })

    # --- Guild-dict projection (slice 07 / step 09-03) ---
    # The projection that lived in `bot/guilds.load_guilds` / `save_guilds`
    # moves here so the `api_key` read stays inside the sanctioned adapter.
    # `load` already projects dict-from-file → Guild (with defaults); this
    # method projects Guild → dict, producing the five-key shape cogs expect.
    # `save_guilds_dict` projects dict → Guild and delegates to `save`,
    # which projects Guild → dict-on-disk — a round trip that is
    # byte-identical by construction (the same projection runs both ways).

    def load_guilds_dict(self, discord_server_id: int) -> dict:
        cluster = self.load(discord_server_id)
        return {
            gid: {
                "name":                    g.name,
                "api_key":                 g.api_key,
                "role_id":                 g.role_id,
                "notification_channel_id": g.notification_channel_id,
                "member_role_ids":         g.member_role_ids,
            }
            for gid, g in cluster.guilds.items()
        }

    def save_guilds_dict(self, discord_server_id: int, guilds: dict) -> None:
        cluster = self.load(discord_server_id)
        cluster.guilds = {
            gid: Guild(
                id=gid,
                name=data["name"],
                api_key=data.get("api_key", ""),
                role_id=data.get("role_id", 0),
                notification_channel_id=data.get("notification_channel_id"),
                member_role_ids=data.get("member_role_ids", []),
            )
            for gid, data in guilds.items()
        }
        self.save(cluster)

    def load_player_registrations(self, discord_server_id: int) -> dict:
        path = self._server_path(discord_server_id) / "player_registrations.json"
        return self._read_json(path)

    def save_player_registrations(self, discord_server_id: int, data: dict) -> None:
        path = self._server_path(discord_server_id) / "player_registrations.json"
        self._write_json(path, data)

    def load_capped_state(self, discord_server_id: int) -> dict:
        path = self._server_path(discord_server_id) / "capped_state.json"
        return self._read_json(path)

    def save_capped_state(self, discord_server_id: int, data: dict) -> None:
        path = self._server_path(discord_server_id) / "capped_state.json"
        self._write_json(path, data)

    def load_player_list(self, discord_server_id: int, guild_id: str) -> dict:
        path = self._guild_path(discord_server_id, guild_id) / "player_list.json"
        if not path.exists():
            return {"__meta__": {"version": PlayerListMigrator.CURRENT_VERSION}, "players": {}}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            data, was_migrated = PlayerListMigrator.migrate(raw)
            if was_migrated:
                path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            return data
        except Exception:
            return {"__meta__": {"version": PlayerListMigrator.CURRENT_VERSION}, "players": {}}

    def save_player_list(self, discord_server_id: int, guild_id: str, data: dict) -> None:
        path = self._guild_path(discord_server_id, guild_id) / "player_list.json"
        self._write_json(path, data)

    def load_live_leaderboards(self, discord_server_id: int) -> dict:
        path = self._server_path(discord_server_id) / "live_leaderboards.json"
        return self._read_json(path)

    def save_live_leaderboards(self, discord_server_id: int, data: dict) -> None:
        path = self._server_path(discord_server_id) / "live_leaderboards.json"
        self._write_json(path, data)

    def list_server_ids(self) -> list[int]:
        if not self._base.exists():
            return []
        return [
            int(d.name)
            for d in self._base.iterdir()
            if d.is_dir() and d.name.isdigit()
        ]

    # --- ADR-007: JSON-backed impls of the 4 new ABC methods ---
    # These keep the parametrized contract tests green against the JSON impl
    # (rollback path / `SCRAPCODE_REPO_BACKEND=json`) and preserve the
    # existing on-disk shape (data-dictionary §2.7 / §2.9).
    # `get_guild_data_path` was removed from the ABC in Slice 04 (ADR-007 §2);
    # the JSON impl inlines the data-dir path here instead.

    def _season_file(self, discord_server_id: int, guild_id: str, season: int,
                     kind: str) -> Path:
        data_dir = self._guild_path(discord_server_id, guild_id) / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        if kind == "battle":
            return data_dir / f"highest_hits_season_{season}.json"
        if kind == "bomb":
            return data_dir / f"highest_bombs_season_{season}.json"
        raise ValueError(f"unknown season-file kind: {kind}")

    def load_battle_hits(self, discord_server_id: int, guild_id: str, season: int) -> dict:
        return self._read_json(self._season_file(discord_server_id, guild_id, season, "battle")) \
            or {"boss_hits": {}}

    def load_bomb_hits(self, discord_server_id: int, guild_id: str, season: int) -> dict:
        return self._read_json(self._season_file(discord_server_id, guild_id, season, "bomb")) \
            or {"boss_hits": {}}

    def upsert_battle_hits(self, discord_server_id: int, guild_id: str, season: int,
                           entries: list[dict]) -> None:
        # Lazy import to avoid a circular dependency at module import time
        # (tracker imports repository; repository does not import tracker).
        from bot.tracker import try_insert
        path = self._season_file(discord_server_id, guild_id, season, "battle")
        data = self._read_json(path) or {"boss_hits": {}}
        boss_hits = data.setdefault("boss_hits", {})
        for entry in entries:
            boss_id = str(entry["unitId"])
            e_index = str(entry.get("encounterIndex", 0))
            tier_key = entry["tier_key"]
            detailed = {
                "encounterType":   entry.get("encounterType"),
                "damage":           entry["damage"],
                "user_id":          entry["userId"],
                "completed_on":      entry["completedOn"],
                "hero_details":      entry.get("heroDetails", []),
                "machine_of_war":   entry.get("machineOfWarDetails"),
            }
            tier_list = (boss_hits.setdefault(boss_id, {})
                         .setdefault(e_index, {})
                         .setdefault(tier_key, []))
            try_insert(tier_list, detailed, check_roster=True)
        self._write_json(path, {"boss_hits": boss_hits})

    def upsert_bomb_hits(self, discord_server_id: int, guild_id: str, season: int,
                         entries: list[dict]) -> None:
        from bot.tracker import try_insert
        path = self._season_file(discord_server_id, guild_id, season, "bomb")
        data = self._read_json(path) or {"boss_hits": {}}
        boss_hits = data.setdefault("boss_hits", {})
        for entry in entries:
            boss_id = str(entry["unitId"])
            e_index = str(entry.get("encounterIndex", 0))
            tier_key = entry["tier_key"]
            bomb_entry = {
                "encounterType": entry.get("encounterType"),
                "damage":         entry["damage"],
                "user_id":         entry["userId"],
                "completed_on":    entry["completedOn"],
            }
            tier_list = (boss_hits.setdefault(boss_id, {})
                         .setdefault(e_index, {})
                         .setdefault(tier_key, []))
            try_insert(tier_list, bomb_entry, check_roster=False)
        self._write_json(path, {"boss_hits": boss_hits})

    def upsert_guild_hits(self, discord_server_id: int, guild_id: str, season: int,
                          battle_entries: list[dict], bomb_entries: list[dict]) -> None:
        """JSON rollback impl: write battle then bomb. JSON has no
        transactions, so within-guild atomicity is best-effort (the JSON
        path is the one-cycle rollback, not the live write path). The
        SQLite impl wraps both in one session (ADR-006 D6)."""
        self.upsert_battle_hits(discord_server_id, guild_id, season, battle_entries)
        self.upsert_bomb_hits(discord_server_id, guild_id, season, bomb_entries)

    def count_guild_destruction_rows(self, discord_server_id: int, guild_id: str) -> dict:
        """JSON rollback impl: count the rows a deregistration would drop.

        The JSON path has no CASCADE; deregistration deletes the guild's
        directory tree, which holds `player_list.json` and every
        `highest_(hits|bombs)_season_*.json`. The count mirrors that surface:
        every player in `player_list.json` and every entry in every season
        file. Counted (not deleted) here so the operator sees the truth
        BEFORE the deletion (AC-009.4). Degradation matches ADR-006 D9 — a
        missing guild dir reads zero rather than raising.
        """
        guild_dir = self._base / str(discord_server_id) / guild_id
        if not guild_dir.exists():
            return {"players": 0, "battle_hits": 0, "bomb_hits": 0}
        players = len(self.load_player_list(discord_server_id, guild_id).get("players", {}))
        data_dir = guild_dir / "data"
        battle_hits = self._count_season_entries(data_dir, "highest_hits_season_")
        bomb_hits = self._count_season_entries(data_dir, "highest_bombs_season_")
        return {"players": players, "battle_hits": battle_hits, "bomb_hits": bomb_hits}

    def _count_season_entries(self, data_dir: Path, prefix: str) -> int:
        """Count hit entries across every season file of one kind.

        Each season file holds `{"boss_hits": {boss: {enc: {tier: [entries]}}}}`;
        an entry in the nested list is one row. Globbed rather than scoped to
        a season because deregistration is not season-scoped either.
        """
        if not data_dir.exists():
            return 0
        total = 0
        for path in data_dir.glob(f"{prefix}*.json"):
            data = self._read_json(path)
            for encounters in data.get("boss_hits", {}).values():
                for tiers in encounters.values():
                    for entries in tiers.values():
                        total += len(entries)
        return total

    def probe(self) -> None:
        """No-op probe (ADR-006 D8). The probe is the SQLite Earned-Trust
        gate; the JSON rollback path skips it. Exposed so the composition
        root's `SupportsProbe` Protocol check passes for both adapters
        (ADR-006 §Architecture enforcement — every wired adapter exposes
        probe())."""
        return None

    # --- ADR-007-pattern replay impls (04-03). The JSON impl reads/writes
    # the existing `replay_index.json` at the project root (`self._base.parent`)
    # so the rollback path stays real. Thread IDs are NOT in the JSON shape
    # (they were hardcoded constants pre-cutover); the JSON impl returns None
    # for forum_channel_id/thread_id — the SQLite impl returns replay_threads
    # rows. This is an acceptable rollback degradation (ADR-006 D9).
    def _replay_index_file(self) -> Path:
        return self._base.parent / "replay_index.json"

    def load_replay_entries(self, discord_server_id: int, boss: str, map_name: str) -> list[dict]:
        data = self._read_json(self._replay_index_file())
        return list(data.get(boss, {}).get(map_name, {}).get("entries", []))

    def upsert_replay_entry(self, discord_server_id: int, boss: str, map_name: str,
                            entry: dict) -> None:
        path = self._replay_index_file()
        data = self._read_json(path)
        # Global duplicate-URL scan (pre-cutover semantics for rollback fidelity).
        for b, maps in data.items():
            for m, mdata in maps.items():
                for existing in mdata.get("entries", []):
                    if existing.get("url") == entry.get("url"):
                        raise DuplicateReplayUrlError(b, m, entry["url"])
        map_data = (data.setdefault(boss, {})
                    .setdefault(map_name, {"index_message_id": None, "entries": []}))
        map_data["entries"].append(entry)
        self._write_json(path, data)

    def delete_replay_entry(self, discord_server_id: int, boss: str, map_name: str,
                            url: str) -> bool:
        path = self._replay_index_file()
        data = self._read_json(path)
        map_data = data.get(boss, {}).get(map_name)
        if not map_data:
            return False
        entries = map_data.get("entries", [])
        for i, e in enumerate(entries):
            if e.get("url") == url:
                del entries[i]
                self._write_json(path, data)
                return True
        return False

    def get_replay_thread(self, discord_server_id: int, boss: str, map_name: str) -> dict | None:
        data = self._read_json(self._replay_index_file())
        map_data = data.get(boss, {}).get(map_name)
        if map_data is None:
            return None
        return {
            "forum_channel_id": None,
            "thread_id": None,
            "index_message_id": map_data.get("index_message_id"),
        }

    def set_replay_thread_index_message(self, discord_server_id: int, boss: str,
                                         map_name: str, index_message_id: int) -> None:
        path = self._replay_index_file()
        data = self._read_json(path)
        map_data = (data.setdefault(boss, {})
                    .setdefault(map_name, {"index_message_id": None, "entries": []}))
        map_data["index_message_id"] = index_message_id
        self._write_json(path, data)

    def list_replay_threads(self, discord_server_id: int) -> dict:
        data = self._read_json(self._replay_index_file())
        return {
            boss: {
                map_name: {"forum_channel_id": None, "thread_id": None}
                for map_name in maps
            }
            for boss, maps in data.items()
        }

    # --- Guild-key bindings: deliberately inert (ADR-006 D9 / ADR-008) ---
    # `SCRAPCODE_REPO_BACKEND=json` is the rollback an operator reaches for
    # under time pressure. On that path the provenance guard must go INERT —
    # every guild reads back unbound, every write is dropped — rather than
    # raise. A half-working guard is worse than an absent one: it would
    # quarantine on state it cannot persist, and the operator who rolled back
    # to restore service would get an outage instead. Same shape as
    # `get_replay_thread` returning None for thread ids here. NOT a stub; the
    # binding store has no JSON representation by design (DDD-4), because a
    # file the pre-cutover code also rewrites is exactly the clobber the
    # separate table exists to rule out.

    def load_guild_binding(self, discord_server_id: int, guild_id: str) -> GuildBinding:
        return GuildBinding()

    def save_guild_binding(self, discord_server_id: int, guild_id: str,
                           binding: GuildBinding) -> None:
        return None

    def list_guild_bindings(self, discord_server_id: int) -> dict[str, GuildBinding]:
        return {}

    def record_quarantine_tombstone(self, discord_server_id: int,
                                    tombstone: QuarantineTombstone) -> None:
        """Dropped, like every other binding write on this path (ADR-006 D9).

        A tombstone records that a QUARANTINE happened, and this adapter
        cannot quarantine anything: `load_guild_binding` returns the unbound
        value, so no guild here is ever quarantined and there is no history to
        keep. Degrading to a no-op rather than raising is what keeps
        `/deregister_guild` working for the operator who rolled back to
        restore service.
        """
        return None

    def list_quarantine_tombstones(self, discord_server_id: int,
                                   guild_id: str) -> list[QuarantineTombstone]:
        """Always empty — nothing on this path can have written one.

        "No history" is a truthful answer here rather than a degraded one, and
        it is the fail-safe direction: a re-registration proceeds exactly as
        it did before the SQLite cutover instead of reporting a quarantine
        this adapter cannot substantiate.
        """
        return []

    def replace_guild_key(self, discord_server_id: int, guild_id: str,
                          api_key: str) -> None:
        """JSON rollback impl (ADR-006 D9). No `api_key_hmac` concept on the
        JSON path, so only `api_key` is updated. Edits the one guild's entry
        in `guilds.json` directly — no deletion, no rewrite of siblings — so
        the rollback path mirrors the SQLite impl's "touch only the key
        column" guarantee as closely as a flat file allows.

        The blank refusal is honoured identically and BEFORE the file is read,
        so a refused call leaves `guilds.json` untouched on disk. The
        collision refusal is not raisable here: there is no `api_key_hmac`
        column and so no uniqueness for a write to violate — the documented
        ADR-006 D9 degradation, same class as the binding methods above."""
        self._refuse_blank_guild_key(api_key)
        guilds_file = self._server_path(discord_server_id) / "guilds.json"
        data = self._read_json(guilds_file)
        guilds = data.get("guilds", {})
        if guild_id not in guilds:
            raise KeyError(guild_id)
        guilds[guild_id]["api_key"] = api_key
        self._write_json(guilds_file, data)