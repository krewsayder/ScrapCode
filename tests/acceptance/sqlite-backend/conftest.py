"""Acceptance test fixtures for the sqlite-backend feature.

Plain pytest + pytest-asyncio. No pytest-bdd. The `.feature` files in
`acceptance/` are the human-readable scenario SSOT; these modules are the
executable specs. Driven through the ClusterRepository ABC (the port),
embeds.build_*_messages (the read-side proxy for Discord commands), and
the JSON->SQLite migration CLI (a real subprocess adapter).

Fixtures:
  - tmp_clusters_tree: a synthetic JSON clusters/ tree in tmp_path covering
    two guilds, a v2 player_list, registrations, capped state, live
    leaderboard config, and a v1 player_list for one guild (for migrator
    coverage). The tree is the "with-existing-json-data" env's stand-in.
  - fernet_key / env_vars: real SCRAPCODE_DB_KEY + SCRAPCODE_DB_PATH +
    SCRAPCODE_REPO_BACKEND, monkeypatched.
  - json_repo / sqlite_repo: the two ClusterRepository impls. The JSON
    impl is real; the SQLite impl is a RED scaffold that raises
    AssertionError on construction (DELIVER replaces it).
  - impl_pair: parametrizes [json, sqlite] for the contract tests.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Constants / shared domain values
# ---------------------------------------------------------------------------

PROD_SERVER_ID = 1458181638453203099
OTHER_SERVER_ID = 9876543210
GUILD_NEURO = "neuro"
GUILD_MECH = "mech"
SEASON = 94

# A Fernet key the scaffold does not use yet; the real impl will. Generated
# deterministically so tests are hermetic. Fernet keys are 32 url-safe
# base64-encoded bytes (44 chars including padding). 02-02 fix: the prior
# value was 64 chars (48 bytes) — not a valid Fernet key — which made the
# probe's Fernet round-trip step untestable. Replaced with a deterministic
# 32-byte key (sha256("scrapcode-hermetic-fernet-key-v1")[:32]).
HERM_FERNET_KEY = "uvP1WBf4y1Ycqc1WZz-6baPp1uBwqaesNDmUL6fXfXU="


# ---------------------------------------------------------------------------
# JSON cluster tree fixture
# ---------------------------------------------------------------------------

def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


@pytest.fixture
def tmp_clusters_tree(tmp_path: Path) -> Path:
    """A synthetic clusters/ tree mimicking the production shape (with-existing-json-data)."""
    base = tmp_path / "clusters"
    server_dir = base / str(PROD_SERVER_ID)

    # guilds.json — cluster config + two guilds
    _write_json(server_dir / "guilds.json", {
        "update_channel_id": None,
        "role_tiers": {"admin": [111], "officer": [222]},
        "guilds": {
            GUILD_NEURO: {
                "name": "Neuro",
                "api_key": "tacticus-neuro-key",
                "role_id": 999,
                "notification_channel_id": 555,
                "member_role_ids": [555, 556],
            },
            GUILD_MECH: {
                "name": "Mech",
                "api_key": "",
                "role_id": 888,
                "notification_channel_id": None,
                "member_role_ids": [],
            },
        },
    })

    # player_registrations.json
    _write_json(server_dir / "player_registrations.json", {
        "123456789": {"api_key": "tacticus-abc", "guild_id": GUILD_NEURO},
        "234567890": {"api_key": "tacticus-def", "guild_id": GUILD_MECH},
    })

    # capped_state.json
    _write_json(server_dir / "capped_state.json", {"123456789": True, "234567890": False})

    # live_leaderboards.json
    _write_json(server_dir / "live_leaderboards.json", {
        f"guild:{GUILD_NEURO}": {
            "channel_id": 777,
            "guild_id": GUILD_NEURO,
            "messages": {"Legendary_0": 111111, "Mythic": 222222},
            "season": SEASON,
        },
        "cluster": {
            "channel_id": 888,
            "messages": {"Legendary_0": 333333},
            "season": SEASON,
        },
    })

    # neuro: v2 player list
    _write_json(server_dir / GUILD_NEURO / "player_list.json", {
        "__meta__": {"version": 2},
        "players": {
            "tacticus-uid-001": {
                "display_name": "Maria Santos",
                "last_validated": "2026-07-18T10:00:00Z",
                "is_former": False,
            },
            "tacticus-uid-002": {
                "display_name": "Jonas Klein",
                "last_validated": "2026-07-18T10:00:00Z",
                "is_former": True,
            },
        },
    })

    # mech: v1 player list (inverted shape) — exercises the migrator
    _write_json(server_dir / GUILD_MECH / "player_list.json", {
        "Aiko Tanaka": "tacticus-uid-003",
    })

    # neuro: season hit files — battle detailed + bomb
    _write_json(server_dir / GUILD_NEURO / "data" / f"highest_hits_season_{SEASON}.json", {
        "boss_hits": {
            "Avatar": {
                "0": {
                    "Legendary_0": [
                        {
                            "encounterType": "Battle",
                            "damage": 12000,
                            "user_id": "tacticus-uid-001",
                            "completed_on": "2026-07-18T10:00:00Z",
                            "hero_details": [{"unitId": "Aethana"}, {"unitId": "Eldryon"}],
                            "machine_of_war": {"unitId": "Khaine"},
                        }
                    ]
                }
            }
        }
    })
    _write_json(server_dir / GUILD_NEURO / "data" / f"highest_bombs_season_{SEASON}.json", {
        "boss_hits": {
            "Avatar": {
                "0": {
                    "Legendary_0": [
                        {
                            "encounterType": "Bomb",
                            "damage": 8000,
                            "user_id": "tacticus-uid-002",
                            "completed_on": "2026-07-18T11:00:00Z",
                        }
                    ]
                }
            }
        }
    })

    # replay_index.json at project root (global leak — single server assigned)
    _write_json(tmp_path / "replay_index.json", {
        "Avatar": {
            "GB_Khaine_01": {
                "index_message_id": 999999,
                "entries": [
                    {
                        "team": "Neuro",
                        "tier": "Legendary 1",
                        "position": "LHS",
                        "damage": "1.33M",
                        "url": "https://replay.example/abc",
                        "comment": "",
                        "submitted_by": "123456789",
                    }
                ],
            }
        },
    })

    return base


# ---------------------------------------------------------------------------
# Env / secrets fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fernet_key() -> str:
    return HERM_FERNET_KEY


@pytest.fixture
def sqlite_db_path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "scrapcode.db"


@pytest.fixture
def env_vars(monkeypatch, fernet_key: str, sqlite_db_path: Path):
    """Real SCRAPCODE_* env vars for the SQLite backend."""
    monkeypatch.setenv("SCRAPCODE_DB_KEY", fernet_key)
    monkeypatch.setenv("SCRAPCODE_DB_PATH", str(sqlite_db_path))
    monkeypatch.setenv("SCRAPCODE_REPO_BACKEND", "sqlite")
    return {
        "SCRAPCODE_DB_KEY": fernet_key,
        "SCRAPCODE_DB_PATH": str(sqlite_db_path),
        "SCRAPCODE_REPO_BACKEND": "sqlite",
    }


# ---------------------------------------------------------------------------
# Repository fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def json_repo(tmp_clusters_tree: Path):
    """Real JSON-backed repo against the synthetic tree."""
    from bot.repository import JsonClusterRepository
    return JsonClusterRepository(base_path=tmp_clusters_tree)


@pytest.fixture
def sqlite_repo(env_vars):
    """SQLite-backed repo. RED scaffold — construction raises AssertionError."""
    from bot.repository_sqlalchemy import SqlAlchemyClusterRepository
    return SqlAlchemyClusterRepository(
        db_path=env_vars["SCRAPCODE_DB_PATH"],
        fernet_key=env_vars["SCRAPCODE_DB_KEY"],
    )


@pytest.fixture(params=["json", "sqlite"], ids=["json", "sqlite"])
def impl_pair(request, json_repo, sqlite_repo):
    """Parametrize contract tests over BOTH ClusterRepository impls.

    The JSON parametrization is green (real impl + real JSON-backed impls of
    the 4 new ADR-007 methods). The SQLite parametrization is RED (scaffold
    raises AssertionError); it flips green once DELIVER lands the real impl.
    """
    return json_repo if request.param == "json" else sqlite_repo


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------

class _FakeChoice:
    """Stand-in for `discord.app_commands.Choice[str]` so we can drive
    `embeds.build_battle_messages` / `build_bomb_messages` without a Discord
    client. The builders only read `.value` and `.name`.
    """
    def __init__(self, value: str, name: str):
        self.value = value
        self.name = name


@pytest.fixture
def legendary_0_choice():
    return _FakeChoice("Legendary_0", "Legendary 0")


@pytest.fixture
def make_choice():
    return _FakeChoice


# ---------------------------------------------------------------------------
# Synthetic Tacticus API entry builder for tracker / upsert tests
# ---------------------------------------------------------------------------

@pytest.fixture
def make_tacticus_entry():
    def _make(*, unit_id="Avatar", encounter_index=0, rarity="Legendary", set_=0,
              damage_type="Battle", damage=12000, user_id="tacticus-uid-001",
              completed_on="2026-07-18T10:00:00Z", encounter_type="Battle",
              hero_details=None, machine_of_war=None):
        from bot.tracker import get_tier_key
        entry = {
            "unitId": unit_id,
            "encounterIndex": encounter_index,
            "rarity": rarity,
            "set": set_,
            "damageType": damage_type,
            "damageDealt": damage,
            # Normalized field the repo upsert reads (the entry contract both
            # ClusterRepository impls accept — see bot.repository.py upsert_*).
            "damage": damage,
            "userId": user_id,
            "completedOn": completed_on,
            "encounterType": encounter_type,
            "heroDetails": hero_details if hero_details is not None else [{"unitId": "Aethana"}],
            "machineOfWarDetails": machine_of_war,
        }
        # The repo upsert contract requires a pre-computed tier_key (the JSON
        # impl reads entry["tier_key"]; the SQLite impl matches). Derived from
        # rarity + set via the pure tracker.get_tier_key parser.
        entry["tier_key"] = get_tier_key(entry)
        return entry
    return _make