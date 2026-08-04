"""Fixtures for the `guild-key-integrity` acceptance suite.

Follows the precedent set by `tests/acceptance/sqlite-backend/conftest.py`:
plain pytest + pytest-asyncio, real SQLite in `tmp_path`, real alembic, and a
programmable double at the httpx boundary. The `.feature` files under
`acceptance/` are the scenario SSOT; these modules are the executable specs.

What the doubles CANNOT model (Mandate 5 disclosure, self-review item 4):

  * `fake_guild_service` returns whatever it is told to. It cannot discover
    that Tacticus renamed or dropped `guildId` — only the `@requires_external`
    scenarios in `tacticus-guild-contract.feature` can, and only when they are
    actually run. This is the residual risk ADR-008 D1 accepts.
  * `fake_discord_channel` records message text. It cannot catch a Discord
    rate-limit, a permissions failure on the ping channel, or an embed that
    exceeds the field limit.
  * The suite runs one process. It cannot model two hourly cycles overlapping,
    which is why the alert-suppression scenarios advance a clock rather than
    racing.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from domain_types import (
    DARK_MECHANICUM,
    WORD_BEARERS,
    Environment,
    GuildIdentity,
    GuildIdVariant,
    KeyStatus,
    ProbeOutcome,
    VendorBody,
)

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[3]
ENVIRONMENTS_YAML = (
    REPO_ROOT / "docs" / "feature" / "guild-key-integrity" / "environments.yaml"
)

PROD_SERVER_ID = 1458181638453203099
GUILD_WB = "word_bearers"
GUILD_DM = "dark_mechanicum"
SEASON = 106


# ---------------------------------------------------------------------------
# config.py env-var precondition.
#
# `config.py` reads UPDATE_CHANNEL_ID / REPLAY_INDEX_CHANNEL_ID at import time
# via `int(os.getenv(...))`, raising TypeError when either is unset. Any test
# that imports a cog imports config transitively. Setting harmless values
# before collection means the scenarios fail RED for the real reason
# (behaviour missing) rather than for an env-var TypeError — the wrong-reason
# RED the pre-DELIVER gate exists to catch.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True, scope="session")
def _config_env_precondition():
    os.environ.setdefault("UPDATE_CHANNEL_ID", "0")
    os.environ.setdefault("REPLAY_INDEX_CHANNEL_ID", "0")
    yield


# ---------------------------------------------------------------------------
# Storage — real SQLite, real alembic, real JSON tree
# ---------------------------------------------------------------------------

@pytest.fixture
def fernet_key() -> str:
    """A real Fernet key, generated deterministically so tests are hermetic."""
    import base64
    return base64.urlsafe_b64encode(b"guild-key-integrity-distill-32b!"[:32]).decode()


@pytest.fixture
def sqlite_db_path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "scrapcode.db"


@pytest.fixture
def env_vars(monkeypatch, sqlite_db_path: Path, fernet_key: str):
    monkeypatch.setenv("SCRAPCODE_REPO_BACKEND", "sqlite")
    monkeypatch.setenv("SCRAPCODE_DB_PATH", str(sqlite_db_path))
    monkeypatch.setenv("SCRAPCODE_DB_KEY", fernet_key)
    yield


@pytest.fixture
def json_env_vars(monkeypatch, tmp_path: Path):
    """The ADR-006 D9 rollback path: file-based storage, no Fernet key."""
    monkeypatch.setenv("SCRAPCODE_REPO_BACKEND", "json")
    monkeypatch.delenv("SCRAPCODE_DB_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "clusters").mkdir(exist_ok=True)
    yield tmp_path


def alembic_config(db_path: Path):
    """Alembic Config rooted at bot/db/alembic for the given DB path."""
    from alembic.config import Config
    import bot.db
    db_pkg = Path(bot.db.__file__).parent
    cfg = Config(str(db_pkg / "alembic.ini"))
    cfg.set_main_option("script_location", str(db_pkg / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


# The last alembic revision that predates `guild-key-integrity`. Every
# migration this feature adds sits on top of it, so it is the shape a rollback
# has to restore and the shape an upgrade has to leave untouched.
#
# NAMED ONCE, AND ABSOLUTE. Two rules follow from this constant existing:
#
#   * migration scenarios state the revision they mean, never a DISTANCE from
#     head. `downgrade(cfg, "-1")` is only "back to the pre-feature shape"
#     while this feature owns exactly one revision — it silently stopped being
#     that the moment 0004 landed, and it would break again for every future
#     migration in the project, feature-related or not.
#   * the fixture and the scenarios read the SAME name, so the baseline cannot
#     be changed in one place and left stale in the other.
#
# Bump this only when a revision predating the feature is squashed away.
PRE_FEATURE_HEAD = "0002"


@pytest.fixture
def db_at_previous_head(sqlite_db_path: Path, env_vars) -> Path:
    """A database at `PRE_FEATURE_HEAD` — the shape before this feature existed.

    The `Given a copy of a cluster whose guilds were registered before this
    feature existed` precondition. Migration scenarios upgrade from here, so
    they test the real transition rather than a fresh create_all.

    The name says "previous head" because 0002 WAS head when this fixture was
    written. It is now two revisions back (0003 bindings, 0004 quarantine
    history) and will keep receding. What it pins is the pre-feature baseline,
    which is the stable idea; read `PRE_FEATURE_HEAD` for the authoritative
    value rather than inferring one from the fixture's name.
    """
    from alembic import command
    sqlite_db_path.parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(alembic_config(sqlite_db_path), PRE_FEATURE_HEAD)
    return sqlite_db_path


@pytest.fixture
def migrated_db(sqlite_db_path: Path, env_vars) -> Path:
    """A database at the compiled head, including this feature's revision."""
    from alembic import command
    sqlite_db_path.parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(alembic_config(sqlite_db_path), "head")
    return sqlite_db_path


@pytest.fixture(autouse=True)
def _repo_singleton_never_escapes_tmp_path(monkeypatch, tmp_path: Path):
    """Point `bot.guilds.repo` at tmp_path for EVERY test in this suite.

    `bot/guilds.py:61` evaluates `repo = build_repo()` at IMPORT time, reading
    SCRAPCODE_REPO_BACKEND / SCRAPCODE_DB_PATH / SCRAPCODE_DB_KEY at that
    moment. The `env_vars` fixture sets those with `monkeypatch.setenv` during
    the test — far too late to affect a singleton that already exists. So every
    call through a `bot/guilds.py` wrapper reached whichever repository was
    built at first import, NOT the tmp_path one the test set up.

    Two consequences, both bad. The tests exercised the wrong object, so they
    could pass or fail for reasons unrelated to their subject. And with no
    Fernet key present the composition root's safety net falls back to
    `JsonClusterRepository()`, whose base_path is the real `clusters/` tree —
    a full-suite run created `clusters/1458181638453203099/guilds.json` at the
    repository root. On a machine holding a live JSON tree that is a write to
    production data, and `save_guilds` would overwrite rather than append.

    Autouse and unconditional, so a test that forgets to request a repository
    fixture still cannot reach the real tree. `sqlite_repo` and `json_repo`
    override this with their own instance; the default here is deliberately a
    JSON repo under tmp_path so the failure mode of forgetting is an empty
    cluster, not a production write.

    Found during DELIVER step 02-02; see the feature-delta
    `## Wave: DELIVER / [WHY] Upstream Issues`, UD-3.
    """
    import bot.guilds as guilds_mod
    import bot.permissions as permissions_mod
    from bot.repository import JsonClusterRepository
    tmp_repo = JsonClusterRepository(base_path=tmp_path / "clusters")
    monkeypatch.setattr(guilds_mod, "repo", tmp_repo)
    # `bot/permissions.py:4` does `from bot.guilds import repo` (by value), so
    # `check_tier` reads the IMPORT-TIME singleton, NOT the per-test monkey-
    # patched `bot.guilds.repo`. Without this, the officer scenario in slice 02
    # would have `check_tier("admin")` read the real `clusters/` tree (UD-3
    # shape). Defensive wiring consistent with the fixture's stated purpose;
    # only applies to this acceptance suite (monkeypatch auto-reverts).
    monkeypatch.setattr(permissions_mod, "repo", tmp_repo)
    yield


@pytest.fixture
def sqlite_repo(migrated_db: Path, fernet_key: str, monkeypatch):
    from bot.repository_sqlalchemy import SqlAlchemyClusterRepository
    repo = SqlAlchemyClusterRepository(db_path=str(migrated_db), fernet_key=fernet_key)
    # The wrappers in bot/guilds.py resolve through the module singleton, so a
    # test that drives a cog or a wrapper must see THIS repository, not the
    # import-time one. See _repo_singleton_never_escapes_tmp_path.
    import bot.guilds as guilds_mod
    monkeypatch.setattr(guilds_mod, "repo", repo)
    return repo


@pytest.fixture
def json_repo(json_env_vars, monkeypatch):
    from bot.repository import JsonClusterRepository
    repo = JsonClusterRepository()
    import bot.guilds as guilds_mod
    monkeypatch.setattr(guilds_mod, "repo", repo)
    return repo


@pytest.fixture
def registered_guilds(sqlite_repo):
    """`Given a registered guild` — the cluster as it existed before this feature.

    Two guilds so that scenarios about one guild not affecting another have
    something to say. Word Bearers is FIRST in insertion order because
    `auto_update` derives the season from `next(iter(guilds.values()))`; the
    season SPOF only misbehaves in that ordering, so a fixture that happened to
    put it second would pass while the bug was fully present.
    """
    from bot.guilds import save_guilds
    save_guilds(PROD_SERVER_ID, {
        GUILD_WB: {
            "name": "Word Bearers",
            "api_key": "wb-key",
            "role_id": 1,
            "notification_channel_id": 4242,
            "member_role_ids": [],
        },
        GUILD_DM: {
            "name": "Dark Mechanicum",
            "api_key": "dm-key",
            "role_id": 2,
            "notification_channel_id": None,
            "member_role_ids": [],
        },
    })
    return PROD_SERVER_ID


@pytest.fixture
def bound_guild(registered_guilds):
    """`Given a guild with a stored binding` — the precondition several
    scenarios declare in Gherkin and no fixture supplied.

    Without it `load_guilds` returned `{}` on a freshly migrated database, so
    `test_changing_the_ping_channel_leaves_the_binding_untouched` raised
    KeyError before reaching its assertion, and
    `test_load_and_save_unchanged_preserves_every_field` compared an unbound
    placeholder against an unbound placeholder — passing while asserting
    nothing. A round-trip test whose Given is missing cannot catch a
    round-trip bug.

    Binds Word Bearers to the identity from the real incident, so the values
    a failure prints are the ones in the postmortem.

    Found during DELIVER step 02-02; see the feature-delta
    `## Wave: DELIVER / [WHY] Upstream Issues`, UD-4.
    """
    from bot.guilds import save_guild_binding
    from bot.repository import GuildBinding
    binding = GuildBinding(
        tacticus_guild_id=WORD_BEARERS.uuid,
        tacticus_guild_tag=WORD_BEARERS.tag,
        tacticus_guild_name=WORD_BEARERS.name,
        identity_bound_at="2026-07-31T04:00:00Z",
    )
    save_guild_binding(PROD_SERVER_ID, GUILD_WB, binding)
    return binding


@pytest.fixture
def bound_cluster(registered_guilds):
    """`Given a cluster where EVERY guild is bound to the identity its key
    resolves to` — the cluster-wide equivalent of `bound_guild`.

    `bound_guild` binds one guild, which is all a single-guild scenario needs.
    The `bound-matching` ENVIRONMENT is a claim about a whole cluster in its
    steady state, and a cluster with one bound guild and one unbound one is not
    that state: the unbound guild takes the trust-on-first-use path (DDD-8),
    which is silent for a different reason than the one the environment is
    about. A scenario that accepted it would report "no alert was raised" while
    never having compared an identity at all — the same vacuity that made
    `drifted_guild` need `bound_guild` (UD-7).

    Each guild is bound to ITS OWN canonical identity, so the arrangement is
    stated here rather than inferred from whatever the guild-service double was
    later programmed to return.

    Added during DELIVER step 03-05 for `test_environment_matrix.py`.
    """
    from bot.guilds import save_guild_binding
    from bot.repository import GuildBinding

    bindings: dict[str, GuildBinding] = {}
    for guild_id, identity in ((GUILD_WB, WORD_BEARERS), (GUILD_DM, DARK_MECHANICUM)):
        binding = GuildBinding(
            tacticus_guild_id=identity.uuid,
            tacticus_guild_tag=identity.tag,
            tacticus_guild_name=identity.name,
            identity_bound_at="2026-07-31T04:00:00Z",
        )
        save_guild_binding(PROD_SERVER_ID, guild_id, binding)
        bindings[guild_id] = binding
    return bindings


@pytest.fixture
def guild_with_recorded_rows(registered_guilds):
    """`Given a registered guild with recorded players, battle hits and bomb
    hits` — the precondition AC-003.2 asserts over (Slice 02 scenario
    `test_replacing_a_key_destroys_nothing`).

    The scenario's signature supplied only `(sqlite_repo, fake_guild_service)`,
    so `_row_counts` read `(0, 0, 0)` and `assert all(n > 0)` failed for a
    fixture gap rather than missing behaviour. This fixture seeds GUILD_WB with
    a couple of players and one battle hit + one bomb hit for SEASON through
    the real repo, the same path production writes take. The counts are
    asserted non-zero by the scenario itself, so the row-preservation claim has
    something to preserve.

    Added during DELIVER step 04-01; wiring for a declared `Given`, not test
    authoring — see the step's CRITICAL BOUNDARY note.
    """
    from bot.guilds import save_player_list
    from bot.tracker import get_tier_key

    save_player_list(PROD_SERVER_ID, GUILD_WB, {
        "__meta__": {"version": 2},
        "players": {
            "tacticus-uid-001": {
                "display_name": "BearOne",
                "last_validated": "2026-07-31T04:00:00.000Z",
                "is_former": False,
            },
            "tacticus-uid-002": {
                "display_name": "BearTwo",
                "last_validated": "2026-07-31T04:00:00.00.000Z",
                "is_former": False,
            },
        },
    })

    def _entry(*, damage_type="Battle", encounter_type="Battle"):
        entry = {
            "unitId": "Avatar",
            "encounterIndex": 0,
            "rarity": "Legendary",
            "set": 0,
            "damageType": damage_type,
            "damage": 12000,
            "userId": "tacticus-uid-001",
            "completedOn": "2026-07-18T10:00:00Z",
            "encounterType": encounter_type,
            "heroDetails": [{"unitId": "Aethana"}],
            "machineOfWarDetails": None,
        }
        entry["tier_key"] = get_tier_key(entry)
        return entry

    import bot.guilds as guilds_mod
    guilds_mod.repo.upsert_battle_hits(
        PROD_SERVER_ID, GUILD_WB, SEASON, [_entry(damage_type="Battle")]
    )
    guilds_mod.repo.upsert_bomb_hits(
        PROD_SERVER_ID, GUILD_WB, SEASON,
        [_entry(damage_type="Bomb", encounter_type="Bomb")],
    )
    return PROD_SERVER_ID


# ---------------------------------------------------------------------------
# The guild-service double
# ---------------------------------------------------------------------------

@dataclass
class GuildServiceResponse:
    """One programmed answer from the guild service.

    EXTENDED 2026-08-02 to the full vendor input domain. Before that, this
    class could render exactly two things: a well-formed `{"guild": {...}}`
    whose `guildId` was one of two hand-picked canonical constants, or the
    same with a field dropped. It could not express a non-JSON body, a
    non-dict payload, a re-cased or whitespace-padded `guildId`, a non-string
    `guildId`, or a malformed roster entry.

    That was not a coverage gap, it was an EXPRESSIVENESS gap, and it is the
    root cause behind the whole slice-04 defect class: every one of those
    defects was unreachable from this suite by construction, so no amount of
    diligence writing scenarios against this double could have found them.
    A double that can only emit well-formed input certifies a parser that
    only handles well-formed input.

    `body` and `guild_id` are orthogonal knobs (see `VendorBody` /
    `GuildIdVariant`): the body chooses the SHAPE, the variant chooses the
    `guildId` VALUE within a dict-shaped body. The defaults reproduce the old
    behaviour byte-for-byte, so every existing scenario is untouched.
    """

    identity: GuildIdentity | None = None
    members: list[str] = field(default_factory=list)
    status: int = 200
    raises: BaseException | None = None
    drop_fields: tuple[str, ...] = ()
    body: VendorBody = VendorBody.WELL_FORMED
    guild_id: GuildIdVariant = GuildIdVariant.CANONICAL

    # -- the JSON-shaped half --------------------------------------------

    def payload(self):
        """Render the `/api/v1/guild` body as a decoded JSON value.

        Returns a dict for every dict-shaped body — which is every case the
        pre-2026-08-02 suite could produce, so existing call sites see no
        change. Returns a list / str / bool / None for the bodies that are
        valid JSON but not an object, because `response.json()` hands
        `parse_guild_snapshot` exactly those values and the classifier has to
        survive them.

        Raises for the bodies that are not JSON at all: those cannot be
        rendered through a `json=` kwarg and must go through `raw_body()`.
        Raising rather than silently substituting `{}` is deliberate — a
        transport double that quietly downgrades a hostile body to a benign
        one is how the suite got here.
        """
        if not self.is_json:
            raise AssertionError(
                f"{self.body.value} is not a JSON body — render it through "
                "`raw_body()` / `render_into(httpx)`, not `payload()`. A "
                "double that substitutes a benign body for a hostile one is "
                "the defect this class was extended to remove."
            )
        if self.body is VendorBody.JSON_NULL:
            return None
        if self.body is VendorBody.JSON_LIST:
            return [{"guildId": self._guild_id_value()}]
        if self.body is VendorBody.JSON_STRING:
            return "guild service temporarily unavailable"
        if self.body is VendorBody.JSON_BOOL:
            return True
        if self.body is VendorBody.GUILD_NOT_A_DICT:
            # Truthy, so `payload.get("guild") or {}` keeps it and the very
            # next `.get("guildId")` raises AttributeError on a str.
            return {"guild": "unavailable"}
        if self.body is VendorBody.GUILD_NULL:
            return {"guild": None}
        return {"guild": self._guild_object()}

    def _guild_object(self) -> dict:
        guild: dict = {
            "guildId": self._guild_id_value(),
            "guildTag": self.identity.tag if self.identity else None,
            "name": self.identity.name if self.identity else None,
            "members": self._member_entries(),
        }
        if self.guild_id is GuildIdVariant.ABSENT:
            guild.pop("guildId")
        # `drop_fields` predates `GuildIdVariant.ABSENT` and remains the way
        # the `unverifiable` environment is built: a real recorded response
        # with a key removed, never a hand-written stub. A stub would let the
        # test pass against an implementation reading a field name Tacticus
        # does not use.
        for f in self.drop_fields:
            guild.pop(f, None)
        return guild

    def _member_entries(self) -> list[dict]:
        entries = [{"userId": m} for m in self.members]
        if self.body is VendorBody.MEMBER_WITHOUT_USER_ID and entries:
            # One entry loses `userId`. The eager
            # `frozenset(m["userId"] for m in ...)` in `parse_guild_snapshot`
            # raises KeyError on it, which kills the cycle for a roster the
            # rest of which is perfectly usable.
            entries[-1] = {"displayName": "a member the vendor sent partially"}
        return entries

    def _guild_id_value(self):
        """The `guildId` VALUE for this variant.

        The six same-guild variants are built FROM `self.identity.uuid`
        rather than from a literal, so a scenario cannot accidentally assert
        that a hard-coded string matches a different hard-coded string.
        """
        uuid = self.identity.uuid if self.identity else None
        v = GuildIdVariant
        return {
            v.CANONICAL: uuid,
            v.UPPERCASE: uuid.upper() if uuid else None,
            v.MIXED_CASE: _alternating_case(uuid) if uuid else None,
            v.SURROUNDING_WHITESPACE: f"  {uuid}  " if uuid else None,
            v.BOM_PREFIXED: f"﻿{uuid}" if uuid else None,
            v.TRAILING_NEWLINE: f"{uuid}\n" if uuid else None,
            v.WHITESPACE_ONLY: "   ",
            v.EMPTY_STRING: "",
            v.JSON_NUMBER: 12345,
            v.JSON_BOOL: True,
            v.JSON_NULL: None,
            v.NOT_A_UUID: "not-a-uuid-at-all",
            v.ABSENT: None,          # popped by `_guild_object`
            v.MISMATCHED_UUID: DARK_MECHANICUM.uuid,
        }[self.guild_id]

    # -- the raw half -----------------------------------------------------

    @property
    def is_json(self) -> bool:
        return self.body not in {
            VendorBody.NOT_JSON_HTML, VendorBody.EMPTY, VendorBody.TRUNCATED_JSON,
        }

    @property
    def content_type(self) -> str:
        return "text/html" if self.body is VendorBody.NOT_JSON_HTML else "application/json"

    def raw_body(self) -> bytes:
        """The literal bytes on the wire, for bodies that are not JSON.

        These are the ones that make `response.json()` raise. Today that
        exception escapes `fetch_guild_snapshot` — which documents itself as
        "Never raises for an expected failure" — travels up through
        `verify_and_resolve` and `_update_one_guild` (whose only `except` is
        `GuildQuarantined`), and ends the hourly loop for EVERY server until
        the process is restarted.
        """
        if self.body is VendorBody.NOT_JSON_HTML:
            return (
                b"<html>\r\n<head><title>502 Bad Gateway</title></head>\r\n"
                b"<body>\r\n<center><h1>502 Bad Gateway</h1></center>\r\n"
                b"<hr><center>nginx</center>\r\n</body>\r\n</html>\r\n"
            )
        if self.body is VendorBody.EMPTY:
            return b""
        if self.body is VendorBody.TRUNCATED_JSON:
            return b'{"guild": {"guildId": "b64bdba4-36ac-4229-bd29-'
        raise AssertionError(
            f"{self.body.value} IS a JSON body — render it through `payload()`"
        )

    def as_httpx_response(self, url: str):
        """A REAL `httpx.Response` for this answer, hostile bodies included.

        One renderer, so a transport double cannot accidentally serve a
        different body than the scenario programmed. Real `httpx.Response`
        rather than a stub so `raise_for_status()`, `.json()` and
        `.status_code` behave exactly as production will see them — including
        `.json()` RAISING, which is the whole point of the non-JSON members.
        """
        import httpx

        request = httpx.Request("GET", url)
        if self.is_json:
            return httpx.Response(self.status, json=self.payload(), request=request)
        return httpx.Response(
            self.status, content=self.raw_body(),
            headers={"content-type": self.content_type}, request=request,
        )


def _alternating_case(value: str) -> str:
    """`b64bdba4-…` → `B64BdBa4-…`. Not `.upper()`, so a scenario that passes
    for `UPPERCASE` and fails here has found a comparison that normalises one
    direction only."""
    return "".join(
        c.upper() if i % 2 == 0 else c.lower() for i, c in enumerate(value)
    )


class FakeGuildService:
    """Programmable stand-in for `GET /api/v1/guild`, keyed by api_key.

    Records every call so a `Then` can assert that a request was NOT made —
    which is the actual observable for "the quarantined guild is refused".
    Asserting only that no row was written would pass against an
    implementation that fetches the data and then discards it, leaking the
    other guild's roster into memory and the logs.
    """

    def __init__(self) -> None:
        self._by_key: dict[str, GuildServiceResponse] = {}
        self._default: GuildServiceResponse | None = None
        self.calls: list[str] = []

    def program(self, api_key: str, response: GuildServiceResponse) -> None:
        self._by_key[api_key] = response

    def program_default(self, response: GuildServiceResponse) -> None:
        self._default = response

    def answer_for(self, api_key: str) -> GuildServiceResponse:
        self.calls.append(api_key)
        resp = self._by_key.get(api_key, self._default)
        if resp is None:
            raise AssertionError(
                f"FakeGuildService got an unprogrammed key {api_key[:8]}… — "
                "the scenario is exercising a path it did not declare"
            )
        if resp.raises is not None:
            raise resp.raises
        return resp

    def was_called_with(self, api_key: str) -> bool:
        return api_key in self.calls

    @property
    def call_count(self) -> int:
        return len(self.calls)


@pytest.fixture
def fake_guild_service() -> FakeGuildService:
    return FakeGuildService()


@pytest.fixture
def matching_guild(fake_guild_service: FakeGuildService):
    """`bound-matching`: the key resolves to the guild it is bound to."""
    fake_guild_service.program(
        "wb-key",
        GuildServiceResponse(identity=WORD_BEARERS, members=["u1", "u2", "u3"]),
    )
    return fake_guild_service


@pytest.fixture
def drifted_guild(bound_guild, fake_guild_service: FakeGuildService):
    """`bound-drifted`: THE INCIDENT. Bound to Word Bearers, resolves to
    Dark Mechanicum. Same key string, different answer — exactly what
    happened when the guild master changed guilds on 2026-07-28.

    Depends on `bound_guild` because `environments.yaml` defines this
    environment as TWO facts, and this fixture used to program only one. The
    guild is BOUND to Word Bearers — history, from the weeks it was verified
    before the key-holder moved — AND its key now resolves to Dark Mechanicum.

    Without the stored binding the guild is unbound on a clean database, so
    the production trust-on-first-use path (DDD-8) adopts Dark Mechanicum and
    reports no mismatch at all. Every drift scenario would then pass against
    an implementation that never compares anything — which is precisely the
    failure this feature exists to prevent, reproduced inside its own test
    suite. Found during DELIVER step 03-03; see the feature-delta
    `## Wave: DELIVER / [WHY] Upstream Issues`, UD-7.
    """
    fake_guild_service.program(
        "wb-key",
        GuildServiceResponse(identity=DARK_MECHANICUM, members=["x1", "x2"]),
    )
    return fake_guild_service


@pytest.fixture
def unverifiable_guild(fake_guild_service: FakeGuildService):
    """`unverifiable`: 200 OK, well-formed guild, no `guildId`."""
    fake_guild_service.program(
        "wb-key",
        GuildServiceResponse(
            identity=WORD_BEARERS,
            members=["u1", "u2"],
            drop_fields=("guildId",),
        ),
    )
    return fake_guild_service


# ---------------------------------------------------------------------------
# Recorded vendor responses
# ---------------------------------------------------------------------------

@pytest.fixture
def recorded_guild_response() -> dict:
    """A real `/api/v1/guild` body, captured and scrubbed of member names.

    Used by `tacticus-guild-contract.feature`. Keeping this on disk rather
    than in a fixture function is deliberate: re-recording it after a vendor
    change is a file diff a reviewer can read.
    """
    return json.loads((FIXTURES / "guild_response_recorded.json").read_text("utf-8"))


# ---------------------------------------------------------------------------
# Discord double
# ---------------------------------------------------------------------------

class FakeChannel:
    """Captures posted message text so a `Then` can assert on it — including
    asserting that NOTHING was posted, which is the `bound-matching`
    environment's whole point."""

    def __init__(self, channel_id: int) -> None:
        self.id = channel_id
        self.messages: list[str] = []

    async def send(self, content: str = "", **kwargs) -> None:
        embed = kwargs.get("embed")
        self.messages.append(content or (getattr(embed, "description", "") or ""))

    @property
    def text(self) -> str:
        return "\n".join(self.messages)


@pytest.fixture
def update_channel() -> FakeChannel:
    return FakeChannel(channel_id=1)


@pytest.fixture
def ping_channel() -> FakeChannel:
    return FakeChannel(channel_id=2)


# ---------------------------------------------------------------------------
# Structured-log capture (the KPI instrument)
# ---------------------------------------------------------------------------

@pytest.fixture
def key_events(caplog):
    """Return a reader over `guild.key.*` / `auto_update.cycle` records.

    The KPI queries in `docs/product/kpi-contracts.yaml` run against these
    exact event names. Asserting on them here is what keeps the documented
    dashboard and the implementation in step — a renamed event breaks the
    test before it breaks the operator's grep.
    """
    import logging
    caplog.set_level(logging.DEBUG)

    class Reader:
        @staticmethod
        def named(event: str) -> list:
            return [r for r in caplog.records if getattr(r, "event", None) == event]

        @staticmethod
        def clear() -> None:
            """Forget everything captured so far.

            For multi-cycle scenarios that assert about the SECOND cycle
            only. Without it, a scenario that clears `update_channel.messages`
            between cycles still sees cycle one's records and asserts against
            both — which made `test_second_verification_refreshes_the_date_
            without_announcing` unsatisfiable, since the first cycle must emit
            exactly one `guild.key.bound` and the second must emit none.
            Found during DELIVER step 03-03; see the feature-delta
            `## Wave: DELIVER / [WHY] Upstream Issues`, UD-8.
            """
            caplog.clear()

        @staticmethod
        def all_events() -> list[str]:
            return [
                e for e in (getattr(r, "event", None) for r in caplog.records)
                if e
            ]

        @staticmethod
        def any_named(*events: str) -> bool:
            present = set(Reader.all_events())
            return bool(present & set(events))

    return Reader


# ---------------------------------------------------------------------------
# Environment parametrization (Mandate 4)
# ---------------------------------------------------------------------------

def environment_names_from_devops_artifact() -> list[str]:
    """Parse `target_environments[].name` out of environments.yaml.

    Deliberately a regex and not PyYAML: this suite must not add a runtime
    dependency to read one list of eight strings, and `requirements.txt` has
    no yaml parser. If the file's shape changes enough to break this regex,
    the traceability test fails loudly, which is the correct outcome.
    """
    text = ENVIRONMENTS_YAML.read_text("utf-8")
    body = text.split("target_environments:", 1)[1].split("\ncoexistence_matrix:", 1)[0]
    return re.findall(r"^\s*-\s*name:\s*(\S+)\s*$", body, flags=re.MULTILINE)


@pytest.fixture(params=list(Environment), ids=lambda e: e.value)
def environment(request) -> Environment:
    return request.param
