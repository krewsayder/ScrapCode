"""Slice 07 — composition-root integrity. Implements
`acceptance/slice-07-composition-root-integrity.feature`.

AUTHORED IN DISTILL, 2026-08-02. Expected RED; the failures are the
deliverable. See `docs/feature/guild-key-integrity/distill/red-classification.md`.

Every other slice is conditional on this one. A perfect chokepoint that the
composition root routes around is not a chokepoint, and today three
reachable startup configurations route around it: `build_repo` falls back to
`JsonClusterRepository`, whose binding store does not exist (DDD-4, by
design), so `active_key` returns the drifted key and `quarantine()` becomes a
no-op. Reproduced end-to-end by two independent reviewers.

The driving port here is `bot.guilds.build_repo` — the composition root
itself. It is called at IMPORT time (`bot/guilds.py:61`), which is why these
scenarios call it directly with a patched environment rather than importing
the module and hoping the singleton was built under the right one.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.slice_07


# ===========================================================================
# AC-010.1 / AC-010.2 / AC-010.3 — refuse to start
# ===========================================================================

@pytest.mark.error
@pytest.mark.driving_port
def test_a_missing_encryption_key_stops_the_bot_and_names_itself(
    monkeypatch, tmp_path
):
    """AC-010.1 / ADR-006 D9 — the fallback nobody chose.

    `build_repo` treats a missing `SCRAPCODE_DB_KEY` as a reason to fall back
    to JSON "for one cycle", logs one WARNING, and returns a working-looking
    repository. Nothing chose that: `SCRAPCODE_REPO_BACKEND=sqlite` is an
    explicit instruction, and a deploy that cannot honour it is broken, not
    degraded. The bot then runs for an hour with quarantine fully inert —
    alerts firing while contaminated data is written, which is every failure
    mode of this feature at once.

    The message is asserted, not just the refusal. Once the bot stops
    starting, that message is the only thing between the operator and an
    outage; a generic "probe failed" turns a five-second fix into an
    incident (slice 07's stated obligation).
    """
    monkeypatch.setenv("SCRAPCODE_REPO_BACKEND", "sqlite")
    monkeypatch.setenv("SCRAPCODE_DB_PATH", str(tmp_path / "data" / "scrapcode.db"))
    monkeypatch.delenv("SCRAPCODE_DB_KEY", raising=False)

    with pytest.raises(Exception) as refusal:
        _build_repo_fresh()

    assert "SCRAPCODE_DB_KEY" in str(refusal.value), (
        "the startup failure did not name the variable the operator has to "
        f"set: {refusal.value!r}"
    )


@pytest.mark.error
@pytest.mark.driving_port
def test_a_malformed_encryption_key_stops_the_bot_at_startup(monkeypatch, tmp_path):
    """AC-010.2 — `if not fernet_key` validates truthiness, not the key.

    Any non-empty string passes, so a CRLF-mangled or truncated
    `SCRAPCODE_DB_KEY` builds the SQLite repository successfully and the
    first `decrypt_api_key` fails MID-CYCLE, hours later, inside the hourly
    loop, as a traceback about cryptography rather than about a deploy.

    The trailing `\\r` is not hypothetical: a Windows-edited `.env` has
    already broken auth on this VM once (recorded in the operator's notes,
    and the reason slice 07's production-data criterion is to mangle the real
    file). The startup probe is the gate that catches it, which is what
    AC-010.5 wires up.
    """
    db_path = tmp_path / "data" / "scrapcode.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.touch()
    monkeypatch.setenv("SCRAPCODE_REPO_BACKEND", "sqlite")
    monkeypatch.setenv("SCRAPCODE_DB_PATH", str(db_path))
    # Exactly what `source .env` yields from a CRLF file: the value keeps the
    # carriage return, so it is truthy, the right length to look plausible,
    # and not a valid Fernet key.
    monkeypatch.setenv("SCRAPCODE_DB_KEY", _a_real_fernet_key() + "\r")

    with pytest.raises(Exception) as refusal:
        _build_repo_fresh()

    assert "SCRAPCODE_DB_KEY" in str(refusal.value), (
        "the bot came up with an unusable encryption key, or refused without "
        f"naming which setting is wrong: {refusal.value!r}"
    )


@pytest.mark.error
@pytest.mark.driving_port
def test_a_missing_database_file_stops_the_bot(monkeypatch, tmp_path):
    """AC-010.3 — the third silent fallback.

    A `SCRAPCODE_DB_PATH` whose parent directory exists but whose file is
    gone means the database was deleted or corrupted. `build_repo` reads that
    as a cue to serve the JSON tree instead. The JSON tree is months stale
    and has no binding store, so the bot comes up serving old data with
    enforcement off — while `/view_config` reports guilds as healthy because
    the quarantine rows it would have read are in the database that is not
    there.

    A first-run path whose parent does NOT yet exist is a different case and
    is deliberately not covered here: that one legitimately creates the
    database, and refusing it would make a fresh install impossible.
    """
    db_path = tmp_path / "data" / "scrapcode.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SCRAPCODE_REPO_BACKEND", "sqlite")
    monkeypatch.setenv("SCRAPCODE_DB_PATH", str(db_path))
    monkeypatch.setenv("SCRAPCODE_DB_KEY", _a_real_fernet_key())

    with pytest.raises(Exception) as refusal:
        _build_repo_fresh()

    assert str(db_path) in str(refusal.value) or "SCRAPCODE_DB_PATH" in str(refusal.value), (
        "the startup failure did not name the database the operator has to "
        f"restore: {refusal.value!r}"
    )


# ===========================================================================
# AC-010.4 — the one fallback that IS a decision
# ===========================================================================

@pytest.mark.driving_port
def test_a_deliberate_rollback_starts_and_says_what_it_gives_up(
    monkeypatch, tmp_path, caplog
):
    """AC-010.4 / ADR-006 D9 — keep the rollback, make it loud.

    `SCRAPCODE_REPO_BACKEND=json` is the only one of the three JSON paths
    somebody chose. It is documented, reasoned, and correct for a rollback
    under time pressure, so this scenario asserts the bot STILL STARTS — a
    slice that hardened the composition root by refusing every JSON
    configuration would pass AC-010.1-3 and remove the rollback ADR-006 D9
    exists to preserve.

    What is missing is the announcement. DDD-4 gives the JSON adapter no
    binding representation, so on this path quarantine is inert BY DESIGN:
    a drifted key is served, `quarantine()` writes nowhere, and the
    protection this whole feature adds is switched off. An operator who rolls
    back at 2am to restore service needs to be told that in the same breath,
    not to find it in an ADR later.

    Asserted on the word "quarantine" rather than on an event name: the name
    is DELIVER's to choose, but an announcement that does not mention
    quarantine has not announced this.
    """
    import logging

    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("SCRAPCODE_REPO_BACKEND", "json")
    monkeypatch.delenv("SCRAPCODE_DB_KEY", raising=False)
    monkeypatch.chdir(tmp_path)

    repo = _build_repo_fresh()

    assert repo is not None, "the deliberate rollback path stopped starting"
    announcements = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING and "quarantin" in r.getMessage().lower()
    ]
    assert announcements, (
        "the bot came up on the JSON backend without announcing that the "
        "guild-key guard is inert. Quarantine writes nowhere on this path "
        "(DDD-4), so the operator is running unprotected and has not been "
        f"told. Records seen: {[r.getMessage() for r in caplog.records]}"
    )


# ===========================================================================
# AC-010.5 — the probe that nothing calls
# ===========================================================================

@pytest.mark.error
@pytest.mark.driving_port
def test_the_startup_health_check_has_a_production_caller():
    """AC-010.5 / ADR-006 D8 — the gate that was built and never wired.

    D8 states the probe "runs at composition time and MUST succeed before the
    bot starts". `SqlAlchemyClusterRepository.probe()` exists
    (`repository_sqlalchemy.py:781`), delegates to `Database.probe`'s four
    health checks (WAL mode, alembic revision, Fernet round-trip, write
    rollback), and every one of those checks is specified and tested — by the
    `sqlite-backend` feature, which built them.

    Nothing calls it. `grep '\\.probe()'` across the repository finds tests
    only. The Fernet round-trip check in particular is exactly what would
    catch AC-010.2's CRLF-mangled key at startup instead of mid-cycle, so
    this scenario and that one are the same defect from two ends: the gate
    exists, and the door it guards has no hinge.

    An AST scan rather than a `grep`, so a call in a comment or a docstring
    cannot satisfy it.
    """
    callers = _production_probe_callers()

    assert callers, (
        "no production module calls `probe()`. ADR-006 D8 says the startup "
        "probe MUST succeed before the bot starts; today it is dead code "
        "that only the test suite exercises"
    )


# ===========================================================================
# AC-010.6 — the scan that cannot see the module holding the keys
# ===========================================================================

@pytest.mark.error
def test_the_direct_key_read_rule_covers_every_module(monkeypatch):
    """AC-010.6 — the chokepoint scan's blind spot is the composition root.

    `test_architecture_chokepoint.py:37` sets
    `GUARDED_TREES = ("bot/cogs", "bot/services")`. Unscanned: `bot/guilds.py`
    — which reads `g.api_key` at line 79 and puts the PLAINTEXT into the dict
    handed to every cog, and is not in `SANCTIONED_KEY_READERS` — plus
    `bot/tracker.py`, `bot/embeds.py`, `bot/models.py`, `bot/db/`, `main.py`,
    and any new top-level module. The rule that exists to make an eighth
    reader impossible cannot see the module that hands the key to everybody.

    This scenario runs the EXISTING scan logic over `bot/` wholesale, which
    is what slice 07 changes `GUARDED_TREES` to. It reds today on
    `bot/guilds.py`, and slice 07 closes it one of the two ways its brief
    allows — sanction the module with a stated reason, or refactor the read
    out of the scan's way (hand out a snapshot/handle rather than the key).
    Either satisfies this; nothing else does.

    Importing the helpers from `test_architecture_chokepoint` is safe here
    and is the exception to UD-10: that module is pure AST, imports no
    fixtures, and defines no `conftest` constant that could collide. Copying
    the matcher instead would create exactly the drift where the scan and its
    coverage test disagree about what a read looks like.
    """
    import ast

    from test_architecture_chokepoint import (
        EXEMPT_PLAYER_KEY_FUNCTIONS,
        REPO_ROOT,
        SANCTIONED_KEY_READERS,
        _api_key_reads,
        _python_files,
    )

    offenders: dict[str, list[int]] = {}
    for path in _python_files("bot"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in SANCTIONED_KEY_READERS:
            continue
        exempt = EXEMPT_PLAYER_KEY_FUNCTIONS.get(rel, frozenset())
        lines = [
            line
            for line, enclosing, is_presence_test in _api_key_reads(
                ast.parse(path.read_text("utf-8"), filename=str(path))
            )
            if not (enclosing & exempt) and not is_presence_test
        ]
        if lines:
            offenders[rel] = lines

    assert not offenders, (
        "these modules read a guild api_key directly and sit outside the "
        "two directories the chokepoint scan looks at, so nothing stops the "
        f"eighth reader from being added next to them: {offenders}"
    )


# ===========================================================================
# Helpers — wiring only
# ===========================================================================

def _build_repo_fresh():
    """Call the composition root with the environment the scenario just set.

    `bot/guilds.py:61` evaluates `repo = build_repo()` in its module body, so
    the singleton every other test sees was built under whatever environment
    existed at first import. These scenarios are ABOUT that construction, so
    they call the function again rather than reading the singleton — reading
    it would assert something about import order, not about the composition
    root's rules.
    """
    from bot.guilds import build_repo
    return build_repo()


def _a_real_fernet_key() -> str:
    """A valid Fernet key, so a scenario about a MALFORMED key is varying one
    thing. Same derivation as the `fernet_key` fixture."""
    import base64
    return base64.urlsafe_b64encode(b"guild-key-integrity-distill-32b!"[:32]).decode()


def _production_probe_callers() -> list[str]:
    """Every `module::function` in production that calls `.probe()`.

    Scans `bot/` plus `main.py`, because the composition root's caller could
    legitimately live in either — D8 says "at composition time", and
    `main.py` is where composition is triggered. The repository
    IMPLEMENTATIONS of `probe` are excluded: a method calling itself, or the
    ABC declaring it, is not a caller.
    """
    import ast
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    implementations = {
        "bot/repository.py", "bot/repository_sqlalchemy.py", "bot/db/session.py",
    }

    paths = [
        p for p in (repo_root / "bot").rglob("*.py")
        if "__pycache__" not in p.parts
    ] + [repo_root / "main.py"]

    callers: list[str] = []
    for path in paths:
        rel = path.relative_to(repo_root).as_posix()
        if rel in implementations:
            continue
        tree = ast.parse(path.read_text("utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "probe"):
                callers.append(f"{rel}:{node.lineno}")
    return callers
