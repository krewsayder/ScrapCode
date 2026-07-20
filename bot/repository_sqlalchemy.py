"""SQLite-backed `ClusterRepository` (ADR-006 D2/D3/D7 / ADR-007 / US-004).

The second `ClusterRepository` impl behind the existing ABC. Implements the
11 easy-entity ABC methods against `bot.db.session.Database` +
`bot.db.models`. `api_key` is Fernet-encrypted at rest via `bot.db.secrets`;
decrypt-on-read keeps cogs unchanged (ADR-006 D7). `get_guild_data_path`
raises `NotImplementedError` (JSON-only; ADR-007 §2) and the 4 season-hit
methods (`upsert/load_battle_hits` / `upsert/load_bomb_hits`) land in 03-01
— this step ships only the empty-input no-op + empty-DB load shape so the
parametrized contract (RC1) round-trips. `update_channel_id` is not stored
(ADR-006 D12); `load_player_list` returns the `{"__meta__": {"version": 2},
"players": {...}}` shim dict cogs expect.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError

from bot.db import models
from bot.db.models import (
    BattleHitRow,
    BombHitRow,
    ClusterRow,
    GuildMemberRoleRow,
    GuildRow,
    LiveLeaderboardRow,
    LiveLbMessageRow,
    PlayerRegistrationRow,
    PlayerRow,
    ReplayEntryRow,
    ReplayThreadRow,
    RoleTierRow,
)
from bot.db.secrets import api_key_hmac, decrypt_api_key, encrypt_api_key
from bot.db.session import Database
from bot.models import Cluster, Guild
from bot.repository import (
    BattleHitEntry,
    BombHitEntry,
    ClusterRepository,
    DuplicateReplayUrlError,
    ReplayEntry,
    ReplayThreadInfo,
)
from bot.tracker import TOP_N


class SqlAlchemyClusterRepository(ClusterRepository):
    """Second `ClusterRepository` impl behind the existing ABC (ADR-006 D2)."""

    def __init__(self, db_path: str | None = None, fernet_key: str | None = None) -> None:
        self._fernet_key: str = fernet_key or os.getenv("SCRAPCODE_DB_KEY", "")
        self._db = Database(db_path=db_path, fernet_key=self._fernet_key)
        # SQLite won't create the parent directory; ensure it exists so the
        # engine can open (and create) the db file on first use.
        db_file = Path(self._db._db_path)  # noqa: SLF001 — Database owns the resolved path
        db_file.parent.mkdir(parents=True, exist_ok=True)
        # Ensure the schema exists (idempotent). Production manages migrations
        # via Alembic; for first-run / test fixtures this creates every table.
        models.Base.metadata.create_all(self._db.engine)
        # Release the connection `create_all` opened so the pool does not pin
        # the file handle — a later corruption of the db file (RC7) must be
        # observed by the next operation, not masked by a cached connection.
        self._db.engine.dispose()

    # ------------------------------------------------------------------
    # load / save (guilds + role_tiers + member_roles)
    # ------------------------------------------------------------------

    def load(self, discord_server_id: int) -> Cluster:
        with self._db.session_scope() as session:
            if session.get(ClusterRow, discord_server_id) is None:
                return Cluster(discord_server_id=discord_server_id)
            guilds = self._load_guilds(session, discord_server_id)
            role_tiers = self._load_role_tiers(session, discord_server_id)
            return Cluster(
                discord_server_id=discord_server_id,
                guilds=guilds,
                role_tiers=role_tiers,
            )

    def save(self, cluster: Cluster) -> None:
        with self._db.session_scope() as session:
            if session.get(ClusterRow, cluster.discord_server_id) is None:
                session.add(ClusterRow(discord_server_id=cluster.discord_server_id))
            self._replace_role_tiers(session, cluster.discord_server_id, cluster.role_tiers)
            self._upsert_guilds(session, cluster.discord_server_id, cluster.guilds)

    def _load_guilds(self, session, discord_server_id: int) -> dict[str, Guild]:
        rows = session.execute(
            select(GuildRow).where(GuildRow.discord_server_id == discord_server_id)
        ).scalars().all()
        guilds: dict[str, Guild] = {}
        for row in rows:
            member_role_ids = [
                r.role_id for r in session.execute(
                    select(GuildMemberRoleRow).where(
                        GuildMemberRoleRow.discord_server_id == discord_server_id,
                        GuildMemberRoleRow.guild_id == row.guild_id,
                    )
                ).scalars().all()
            ]
            guilds[row.guild_id] = Guild(
                id=row.guild_id,
                name=row.name,
                api_key=decrypt_api_key(row.api_key, self._fernet_key),
                role_id=row.role_id,
                notification_channel_id=row.notification_channel_id,
                member_role_ids=member_role_ids,
            )
        return guilds

    def _load_role_tiers(self, session, discord_server_id: int) -> dict[str, list[int]]:
        rows = session.execute(
            select(RoleTierRow).where(RoleTierRow.discord_server_id == discord_server_id)
        ).scalars().all()
        tiers: dict[str, list[int]] = {}
        for row in rows:
            tiers.setdefault(row.tier, []).append(row.role_id)
        return tiers

    def _replace_role_tiers(self, session, discord_server_id: int,
                            role_tiers: dict[str, list[int]]) -> None:
        session.execute(delete(RoleTierRow).where(
            RoleTierRow.discord_server_id == discord_server_id))
        for tier, role_ids in role_tiers.items():
            for role_id in role_ids:
                session.add(RoleTierRow(
                    discord_server_id=discord_server_id,
                    tier=tier,
                    role_id=role_id,
                ))

    def _upsert_guilds(self, session, discord_server_id: int,
                      guilds: dict[str, Guild]) -> None:
        existing_ids = {
            r.guild_id for r in session.execute(
                select(GuildRow).where(GuildRow.discord_server_id == discord_server_id)
            ).scalars().all()
        }
        new_ids = set(guilds.keys())
        # Delete removed guilds (FK ondelete=CASCADE drops their dependent rows).
        for gid in existing_ids - new_ids:
            session.execute(delete(GuildRow).where(
                GuildRow.discord_server_id == discord_server_id,
                GuildRow.guild_id == gid,
            ))
        for gid, g in guilds.items():
            self._upsert_one_guild(session, discord_server_id, g)

    def _upsert_one_guild(self, session, discord_server_id: int, g: Guild) -> None:
        row = session.get(GuildRow, (discord_server_id, g.id))
        cipher = encrypt_api_key(g.api_key, self._fernet_key)
        hmac_val = api_key_hmac(g.api_key, self._fernet_key)
        if row is None:
            session.add(GuildRow(
                discord_server_id=discord_server_id,
                guild_id=g.id,
                name=g.name,
                api_key=cipher,
                api_key_hmac=hmac_val,
                role_id=g.role_id,
                notification_channel_id=g.notification_channel_id,
            ))
        else:
            row.name = g.name
            row.api_key = cipher
            row.api_key_hmac = hmac_val
            row.role_id = g.role_id
            row.notification_channel_id = g.notification_channel_id
        # Member roles: full replace per guild (no cascade concerns — pure join-ish).
        session.execute(delete(GuildMemberRoleRow).where(
            GuildMemberRoleRow.discord_server_id == discord_server_id,
            GuildMemberRoleRow.guild_id == g.id,
        ))
        for role_id in g.member_role_ids:
            session.add(GuildMemberRoleRow(
                discord_server_id=discord_server_id,
                guild_id=g.id,
                role_id=role_id,
            ))

    # ------------------------------------------------------------------
    # player_registrations + capped_state (ADR-006 D5: is_capped column)
    # ------------------------------------------------------------------

    def load_player_registrations(self, discord_server_id: int) -> dict:
        with self._db.session_scope() as session:
            rows = session.execute(
                select(PlayerRegistrationRow).where(
                    PlayerRegistrationRow.discord_server_id == discord_server_id)
            ).scalars().all()
            return {
                row.discord_id: {
                    "api_key": decrypt_api_key(row.api_key, self._fernet_key),
                    "guild_id": row.guild_id,
                }
                for row in rows
            }

    def save_player_registrations(self, discord_server_id: int, data: dict) -> None:
        with self._db.session_scope() as session:
            session.execute(delete(PlayerRegistrationRow).where(
                PlayerRegistrationRow.discord_server_id == discord_server_id))
            for discord_id, info in data.items():
                plain = info["api_key"]
                session.add(PlayerRegistrationRow(
                    discord_id=discord_id,
                    discord_server_id=discord_server_id,
                    guild_id=info["guild_id"],
                    api_key=encrypt_api_key(plain, self._fernet_key),
                    api_key_hmac=api_key_hmac(plain, self._fernet_key),
                    is_capped=False,
                ))

    def load_capped_state(self, discord_server_id: int) -> dict:
        with self._db.session_scope() as session:
            rows = session.execute(
                select(PlayerRegistrationRow).where(
                    PlayerRegistrationRow.discord_server_id == discord_server_id)
            ).scalars().all()
            return {row.discord_id: row.is_capped for row in rows}

    def save_capped_state(self, discord_server_id: int, data: dict) -> None:
        with self._db.session_scope() as session:
            for discord_id, is_capped in data.items():
                row = session.get(PlayerRegistrationRow, str(discord_id))
                if row is not None:
                    row.is_capped = bool(is_capped)

    # ------------------------------------------------------------------
    # player_list (per guild) — returns the v2 shim dict (ADR-006 D12)
    # ------------------------------------------------------------------

    def load_player_list(self, discord_server_id: int, guild_id: str) -> dict:
        with self._db.session_scope() as session:
            rows = session.execute(
                select(PlayerRow).where(
                    PlayerRow.discord_server_id == discord_server_id,
                    PlayerRow.guild_id == guild_id,
                )
            ).scalars().all()
            players = {
                row.tacticus_user_id: {
                    "display_name": row.display_name,
                    "last_validated": row.last_validated,
                    "is_former": row.is_former,
                }
                for row in rows
            }
            # __meta__.version is a COMPATIBILITY SHIM kept for cog compatibility;
            # the SQL schema versions via Alembic instead (ADR-006 D12).
            return {"__meta__": {"version": 2}, "players": players}

    def save_player_list(self, discord_server_id: int, guild_id: str, data: dict) -> None:
        with self._db.session_scope() as session:
            session.execute(delete(PlayerRow).where(
                PlayerRow.discord_server_id == discord_server_id,
                PlayerRow.guild_id == guild_id,
            ))
            for uid, info in data.get("players", {}).items():
                session.add(PlayerRow(
                    tacticus_user_id=uid,
                    discord_server_id=discord_server_id,
                    guild_id=guild_id,
                    display_name=info["display_name"],
                    last_validated=info["last_validated"],
                    is_former=info.get("is_former", False),
                ))

    # ------------------------------------------------------------------
    # live_leaderboards (decomposed: LiveLeaderboardRow + LiveLbMessageRow)
    # ------------------------------------------------------------------

    def load_live_leaderboards(self, discord_server_id: int) -> dict:
        with self._db.session_scope() as session:
            rows = session.execute(
                select(LiveLeaderboardRow).where(
                    LiveLeaderboardRow.discord_server_id == discord_server_id)
            ).scalars().all()
            result: dict[str, dict] = {}
            for row in rows:
                messages = {
                    msg.tier_value: msg.message_id
                    for msg in session.execute(
                        select(LiveLbMessageRow).where(
                            LiveLbMessageRow.config_id == row.id)
                    ).scalars().all()
                }
                entry: dict[str, Any] = {"channel_id": row.channel_id, "messages": messages}
                if row.season is not None:
                    entry["season"] = row.season
                if row.guild_id is not None:
                    entry["guild_id"] = row.guild_id
                result[row.scope_key] = entry
            return result

    def save_live_leaderboards(self, discord_server_id: int, data: dict) -> None:
        with self._db.session_scope() as session:
            session.execute(delete(LiveLbMessageRow).where(
                LiveLbMessageRow.config_id.in_(
                    select(LiveLeaderboardRow.id).where(
                        LiveLeaderboardRow.discord_server_id == discord_server_id)
                )
            ))
            session.execute(delete(LiveLeaderboardRow).where(
                LiveLeaderboardRow.discord_server_id == discord_server_id))
            for scope_key, entry in data.items():
                row = LiveLeaderboardRow(
                    discord_server_id=discord_server_id,
                    scope_key=scope_key,
                    guild_id=entry.get("guild_id"),
                    channel_id=entry["channel_id"],
                    season=entry.get("season"),
                )
                session.add(row)
                session.flush()
                for tier_value, message_id in entry.get("messages", {}).items():
                    session.add(LiveLbMessageRow(
                        config_id=row.id,
                        tier_value=tier_value,
                        message_id=message_id,
                    ))

    # ------------------------------------------------------------------
    # get_guild_data_path + list_server_ids
    # ------------------------------------------------------------------

    def get_guild_data_path(self, discord_server_id: int, guild_id: str):
        # ADR-007 §2: JSON-specific; the SQLite impl raises (removed from the
        # ABC in Slice 04 once the 4 cog read sites + embeds.load_leaderboard_file
        # are rewired).
        raise NotImplementedError(
            "get_guild_data_path is JSON-only; use load_battle_hits / load_bomb_hits"
        )

    def list_server_ids(self) -> list[int]:
        with self._db.session_scope() as session:
            rows = session.execute(select(ClusterRow)).scalars().all()
            return [row.discord_server_id for row in rows]

    # ------------------------------------------------------------------
    # 4 new ADR-007 methods (Slice 03-01). The write path replaces
    # `tracker.try_insert`'s in-memory dedup with a SQL `ON CONFLICT ...
    # DO UPDATE SET damage = MAX(...)` upsert on the battle_hits natural key
    # (server, guild, season, boss, encounter, tier, roster_key, user_id) —
    # the keep-max(damage) rule. bomb_hits has no roster dedup (plain top-N
    # on read). The read path orders by `damage DESC, completed_on ASC` and
    # truncates to TOP_N=5 per (boss, encounter, tier) partition, preserving
    # the tiebreak pinned by bot/tests/test_tracker_tiebreak.py. The bare
    # `{"boss_hits": ...}` shape is what embeds.build_*_messages consume
    # (ADR-007 §1); the per-file `__meta__` version scheme is retired in SQL.
    # ------------------------------------------------------------------

    def load_battle_hits(self, discord_server_id: int, guild_id: str, season: int) -> dict:
        with self._db.session_scope() as session:
            rows = session.execute(
                select(BattleHitRow)
                .where(
                    BattleHitRow.discord_server_id == discord_server_id,
                    BattleHitRow.guild_id == guild_id,
                    BattleHitRow.season == season,
                )
                .order_by(BattleHitRow.damage.desc(), BattleHitRow.completed_on.asc())
            ).scalars().all()
            return self._rows_to_boss_hits(rows, self._battle_entry_from_row)

    def load_bomb_hits(self, discord_server_id: int, guild_id: str, season: int) -> dict:
        with self._db.session_scope() as session:
            rows = session.execute(
                select(BombHitRow)
                .where(
                    BombHitRow.discord_server_id == discord_server_id,
                    BombHitRow.guild_id == guild_id,
                    BombHitRow.season == season,
                )
                .order_by(BombHitRow.damage.desc(), BombHitRow.completed_on.asc())
            ).scalars().all()
            return self._rows_to_boss_hits(rows, self._bomb_entry_from_row)

    def upsert_battle_hits(self, discord_server_id: int, guild_id: str, season: int,
                           entries: list[dict]) -> None:
        if not entries:
            return
        with self._db.session_scope() as session:
            for entry in entries:
                session.execute(self._battle_upsert_stmt(), self._battle_params(
                    discord_server_id, guild_id, season, entry,
                ))

    def upsert_bomb_hits(self, discord_server_id: int, guild_id: str, season: int,
                         entries: list[dict]) -> None:
        if not entries:
            return
        with self._db.session_scope() as session:
            for entry in entries:
                session.execute(self._bomb_upsert_stmt(), self._bomb_params(
                    discord_server_id, guild_id, season, entry,
                ))

    def upsert_guild_hits(self, discord_server_id: int, guild_id: str, season: int,
                          battle_entries: list[dict], bomb_entries: list[dict]) -> None:
        """One transaction per guild (ADR-006 D6). Wraps the battle + bomb
        upserts in a single session_scope; if either raises, both roll back
        (within-guild atomicity). Cross-guild isolation comes from separate
        sessions per guild_id. Empty lists are skipped at the top so a
        no-op cycle does not open a session."""
        if not battle_entries and not bomb_entries:
            return
        with self._db.session_scope() as session:
            for entry in battle_entries:
                session.execute(self._battle_upsert_stmt(), self._battle_params(
                    discord_server_id, guild_id, season, entry,
                ))
            for entry in bomb_entries:
                session.execute(self._bomb_upsert_stmt(), self._bomb_params(
                    discord_server_id, guild_id, season, entry,
                ))

    # ------------------------------------------------------------------
    # Read-path shaping: order by damage DESC / completed_on ASC, truncate
    # to TOP_N per (boss, encounter, tier). Rows arrive globally sorted by
    # damage DESC, so the first TOP_N rows appended into each partition
    # ARE that partition's top-N.
    # ------------------------------------------------------------------

    def _rows_to_boss_hits(self, rows, entry_fn) -> dict:
        boss_hits: dict[str, dict] = {}
        for row in rows:
            partition = (boss_hits.setdefault(row.boss_id, {})
                         .setdefault(row.encounter_index, {})
                         .setdefault(row.tier_key, []))
            if len(partition) < TOP_N:
                partition.append(entry_fn(row))
        return {"boss_hits": boss_hits}

    def _battle_entry_from_row(self, row) -> BattleHitEntry:
        return {
            "encounterType": row.encounter_type,
            "damage": row.damage,
            "user_id": row.user_id,
            "completed_on": row.completed_on,
            "hero_details": row.hero_details or [],  # display-only JSON column (0002)
            "machine_of_war": {"unitId": row.mow_unit_id} if row.mow_unit_id else None,
        }

    def _bomb_entry_from_row(self, row) -> BombHitEntry:
        return {
            "encounterType": row.encounter_type,
            "damage": row.damage,
            "user_id": row.user_id,
            "completed_on": row.completed_on,
        }

    # ------------------------------------------------------------------
    # Write-path: raw INSERT ... ON CONFLICT DO UPDATE keep-max(damage).
    # battle_hits: completed_on follows the max-damage entry (preserves the
    # try_insert contract pinned by RC14: same-roster-higher replaces the
    # whole entry). bomb_hits: completed_on is part of the conflict key, so
    # the CASE is a no-op — set damage = MAX only.
    # ------------------------------------------------------------------

    def _battle_upsert_stmt(self):
        return text(
            """
            INSERT INTO battle_hits (
                discord_server_id, guild_id, season, boss_id, encounter_index,
                tier_key, user_id, damage, completed_on,
                hero_roster_key, mow_unit_id, encounter_type, hero_details
            ) VALUES (
                :server, :guild, :season, :boss, :eidx,
                :tier, :user, :dmg, :completed,
                :rkey, :mow, :etype, :heroes
            )
            ON CONFLICT (discord_server_id, guild_id, season, boss_id, encounter_index,
                         tier_key, hero_roster_key, user_id) DO UPDATE SET
                damage = MAX(excluded.damage, battle_hits.damage),
                completed_on = CASE WHEN excluded.damage > battle_hits.damage
                    THEN excluded.completed_on ELSE battle_hits.completed_on END,
                hero_details = CASE WHEN excluded.damage > battle_hits.damage
                    THEN excluded.hero_details ELSE battle_hits.hero_details END
            """
        )

    def _bomb_upsert_stmt(self):
        return text(
            """
            INSERT INTO bomb_hits (
                discord_server_id, guild_id, season, boss_id, encounter_index,
                tier_key, user_id, damage, completed_on, encounter_type
            ) VALUES (
                :server, :guild, :season, :boss, :eidx,
                :tier, :user, :dmg, :completed, :etype
            )
            ON CONFLICT (discord_server_id, guild_id, season, boss_id, encounter_index,
                         tier_key, user_id, completed_on) DO UPDATE SET
                damage = MAX(excluded.damage, bomb_hits.damage)
            """
        )

    def _battle_params(self, server, guild, season, entry) -> dict:
        hero_details = entry.get("heroDetails", [])
        return {
            "server": server, "guild": guild, "season": season,
            "boss": str(entry["unitId"]),
            "eidx": str(entry.get("encounterIndex", 0)),
            "tier": entry["tier_key"],
            "user": entry["userId"],
            "dmg": entry["damage"],
            "completed": entry["completedOn"],
            "rkey": self._roster_key(hero_details, entry.get("machineOfWarDetails")),
            "mow": self._mow_unit_id(entry.get("machineOfWarDetails")),
            "etype": entry.get("encounterType"),
            # `hero_details` is a JSON column; raw text() binding does not
            # engage the ORM's JSON type processor, so serialize manually.
            # The ORM read (`row.hero_details`) deserializes back to a list.
            "heroes": json.dumps(hero_details) if hero_details else None,
        }

    def _bomb_params(self, server, guild, season, entry) -> dict:
        return {
            "server": server, "guild": guild, "season": season,
            "boss": str(entry["unitId"]),
            "eidx": str(entry.get("encounterIndex", 0)),
            "tier": entry["tier_key"],
            "user": entry["userId"],
            "dmg": entry["damage"],
            "completed": entry["completedOn"],
            "etype": entry.get("encounterType"),
        }

    # ------------------------------------------------------------------
    # roster_key = deterministic serialization of (sorted hero unitIds,
    # mow_unit_id) — a TEXT column. Order-independent so the same hero set
    # dedups regardless of API ordering (data-dictionary §2.7). The dedup
    # uses roster_key only; hero_details are not stored as JSON.
    # ------------------------------------------------------------------

    def _roster_key(self, hero_details, mow) -> str:
        heroes = sorted(h.get("unitId", "") for h in (hero_details or []))
        return json.dumps([heroes, self._mow_unit_id(mow)], separators=(",", ":"))

    def _mow_unit_id(self, mow) -> str | None:
        if not mow:
            return None
        return mow.get("unitId") if isinstance(mow, dict) else None

    # ------------------------------------------------------------------
    # ADR-007-pattern replay impls (04-03). The per-tenant URL uniqueness
    # (discord_server_id, boss, map_name, url) is enforced by the
    # `uq_replay_entries_url_per_thread` constraint (models.py); the cog
    # translates `DuplicateReplayUrlError` into the byte-for-byte duplicate
    # reply (CS7). Thread IDs come from `replay_threads` (seeded in 03-03),
    # closing the hardcoded-thread-ID leak (ADR-006 D10).
    # ------------------------------------------------------------------

    def load_replay_entries(self, discord_server_id: int, boss: str, map_name: str) -> list[dict]:
        with self._db.session_scope() as session:
            rows = session.execute(
                select(ReplayEntryRow)
                .where(
                    ReplayEntryRow.discord_server_id == discord_server_id,
                    ReplayEntryRow.boss == boss,
                    ReplayEntryRow.map_name == map_name,
                )
                .order_by(ReplayEntryRow.id.asc())
            ).scalars().all()
            return [self._replay_entry_from_row(r) for r in rows]

    def upsert_replay_entry(self, discord_server_id: int, boss: str, map_name: str,
                            entry: dict) -> None:
        with self._db.session_scope() as session:
            existing = session.execute(
                select(ReplayEntryRow).where(
                    ReplayEntryRow.discord_server_id == discord_server_id,
                    ReplayEntryRow.boss == boss,
                    ReplayEntryRow.map_name == map_name,
                    ReplayEntryRow.url == entry["url"],
                )
            ).first()
            if existing is not None:
                raise DuplicateReplayUrlError(boss, map_name, entry["url"])
            # The SELECT-then-INSERT is a fast-path duplicate check for the
            # friendly reply; the `uq_replay_entries_url_per_thread` constraint
            # is the real guard. Flush inside a try so a TOCTOU duplicate (or
            # any IntegrityError on the URL constraint) is translated to the
            # typed exception the cog catches — never a raw IntegrityError.
            try:
                session.add(ReplayEntryRow(
                    discord_server_id=discord_server_id,
                    boss=boss,
                    map_name=map_name,
                    team=entry["team"],
                    tier=entry["tier"],
                    position=entry.get("position", ""),
                    damage_text=entry["damage"],
                    url=entry["url"],
                    comment=entry.get("comment", ""),
                    submitted_by=entry["submitted_by"],
                    index_message_id=entry.get("index_message_id"),
                ))
                session.flush()
            except IntegrityError:
                raise DuplicateReplayUrlError(boss, map_name, entry["url"])

    def delete_replay_entry(self, discord_server_id: int, boss: str, map_name: str,
                            url: str) -> bool:
        with self._db.session_scope() as session:
            row = session.execute(
                select(ReplayEntryRow).where(
                    ReplayEntryRow.discord_server_id == discord_server_id,
                    ReplayEntryRow.boss == boss,
                    ReplayEntryRow.map_name == map_name,
                    ReplayEntryRow.url == url,
                )
            ).scalars().first()
            if row is None:
                return False
            session.delete(row)
            return True

    def get_replay_thread(self, discord_server_id: int, boss: str, map_name: str) -> dict | None:
        with self._db.session_scope() as session:
            row = session.get(ReplayThreadRow,
                              (discord_server_id, boss, map_name))
            if row is None:
                return None
            return {
                "forum_channel_id": row.forum_channel_id,
                "thread_id": row.thread_id,
                "index_message_id": row.index_message_id,
            }

    def set_replay_thread_index_message(self, discord_server_id: int, boss: str,
                                         map_name: str, index_message_id: int) -> None:
        with self._db.session_scope() as session:
            row = session.get(ReplayThreadRow,
                              (discord_server_id, boss, map_name))
            if row is None:
                session.add(ReplayThreadRow(
                    discord_server_id=discord_server_id,
                    boss=boss,
                    map_name=map_name,
                    index_message_id=index_message_id,
                ))
            else:
                row.index_message_id = index_message_id

    def list_replay_threads(self, discord_server_id: int) -> dict:
        with self._db.session_scope() as session:
            rows = session.execute(
                select(ReplayThreadRow).where(
                    ReplayThreadRow.discord_server_id == discord_server_id
                )
            ).scalars().all()
            result: dict[str, dict] = {}
            for row in rows:
                result.setdefault(row.boss, {})[row.map_name] = {
                    "forum_channel_id": row.forum_channel_id,
                    "thread_id": row.thread_id,
                }
            return result

    def _replay_entry_from_row(self, row) -> ReplayEntry:
        return {
            "team": row.team,
            "tier": row.tier,
            "position": row.position or "",
            "damage": row.damage_text,
            "url": row.url,
            "comment": row.comment or "",
            "submitted_by": row.submitted_by,
        }

    # ------------------------------------------------------------------
    # ADR-006 D8: startup probe (Earned Trust). Delegates to Database.probe.
    # ------------------------------------------------------------------

    def probe(self) -> None:
        self._db.probe()