"""The chokepoint is only a chokepoint if bypassing it is caught (ADR-008 D3).

DEVOPS D10. ADR-008 D3 and DESIGN's Architecture Enforcement section both
state the project "already runs import-linter + AST pre-commit hooks". Verified
2026-07-31: there is no `.pre-commit-config.yaml`, no installed hook,
`pytest-archon` is imported by zero tests, and neither enforcement tool is in
`requirements.txt`. The four `lint-imports` contracts are real but run only
when someone types the command.

So the four DESIGN rules land here instead, on the gate that actually runs.

Note that `import-linter` alone would NOT have caught the leak these rules
exist to prevent: the cogs contract sets `allow_indirect_imports = true`, so a
cog reaching `bot.db` THROUGH `bot.guild_keys` satisfies the contract while
violating its intent. The AST scan is the part that closes that.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from domain_types import RECOVERY_ENTRY_POINTS, KeyConsumptionSite

RED = pytest.mark.skip(reason="RED scaffold — enable one at a time in DELIVER")

REPO_ROOT = Path(__file__).resolve().parents[3]

# The ONLY modules permitted to read a guild's `api_key`. Two adapters (they
# encrypt and decrypt it), the policy chokepoint (it decides whether the key
# may be used at all), and two one-shot migration modules (the read IS the
# migration — re-encrypting a guild key into the SQLite row during the JSON
# cutover, and relocating keys into the per-cluster layout). Everything else
# goes through the chokepoint.
SANCTIONED_KEY_READERS = {
    "bot/guild_keys.py",
    "bot/repository.py",
    "bot/repository_sqlalchemy.py",
    # The two migration modules read `guilds.api_key` to MIGRATE it — the
    # JSON→SQLite cutover re-encrypts each key into the new row, and the
    # cluster-layout migration relocates keys into their per-cluster home.
    # The read IS the migration; refusing it would make the cutover
    # impossible. Recorded in `distill/upstream-issues.md` UI-12.
    "bot/db/migrations_json_to_sqlite.py",
    "bot/migrations/to_cluster_layout.py",
}

GUARDED_TREES = ("bot/cogs", "bot/services")

# --------------------------------------------------------------------------
# Two exemptions, by two DIFFERENT mechanisms, because they are two different
# problems. Narrowed during DELIVER 2026-08-01 — see the feature-delta section
# `## Wave: DELIVER / [WHY] Upstream Issues`, UD-1.
#
# The scan matches the *identifier* `api_key`. This repository stores two
# unrelated secrets under that one name:
#
#   guilds.api_key                 one per guild — THIS feature's subject
#   player_registrations.api_key   one per registered person — explicitly
#                                  out of scope (DISCUSS `Out of Scope`;
#                                  liveness is covered by
#                                  `/registration validate_keys`)
#
# A module-level allowlist cannot separate them: `bot/cogs/tasks_cog.py`
# reads a PLAYER key in `cap_detect` and a GUILD key in `auto_update`. So the
# player-key sites are exempted by enclosing function, named individually.
# --------------------------------------------------------------------------

# Functions whose `api_key` reads are player-registration keys, not guild
# keys. Adding a name here is a deliberate act with a reason attached; a NEW
# player-key site in some other function still fails this test until someone
# consciously classifies it.
EXEMPT_PLAYER_KEY_FUNCTIONS = {
    "bot/cogs/tasks_cog.py": {"cap_detect"},
    "bot/cogs/bomb_cog.py": {"bomb_availability"},
    "bot/cogs/token_cog.py": {"token_availability"},
    "bot/cogs/registration_cog.py": {"register", "validate_keys"},
}


def _python_files(*relative_dirs: str) -> list[Path]:
    return [
        p
        for d in relative_dirs
        for p in (REPO_ROOT / d).rglob("*.py")
        if "__pycache__" not in p.parts
    ]


def _is_presence_test(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """True when this read only asks WHETHER a key exists, never uses it.

    The second exemption, and deliberately structural rather than a name on a
    list. `admin_cog._config_guilds` renders

        has_api_key = "OK" if guild_data.get("api_key") else "Missing"

    and AC-005.3 pins that "Missing" rendering as unchanged by this feature —
    so the read has to survive. But exempting the whole function by name would
    also stop the test noticing if someone later USED the key there, which is
    precisely a seven-becomes-eight regression.

    Testing truthiness cannot leak a key: the value is consumed by the branch
    and never reaches a request, a log or a variable. Using it always requires
    binding or passing it, which is not this shape.
    """
    parent = parents.get(node)
    if isinstance(parent, ast.UnaryOp) and isinstance(parent.op, ast.Not):
        return True
    return isinstance(parent, (ast.If, ast.IfExp)) and parent.test is node


def _reads_api_key(tree: ast.AST) -> list[int]:
    """Line numbers where `api_key` is read by subscript, .get() or attribute.

    Catches the three shapes the codebase actually uses today:
        guild_data["api_key"]        Subscript with a constant
        guild_data.get("api_key")    Call to .get with a constant
        guild.api_key                Attribute access
    """
    return [line for line, _, _ in _api_key_reads(tree)]


def _api_key_reads(tree: ast.AST) -> list[tuple[int, frozenset[str], bool]]:
    """Every `api_key` read as (line, enclosing function names, is_presence_test).

    The enclosing set carries EVERY function the read sits inside, not just
    the innermost, so a read buried in a closure — `bomb_availability` wraps
    its fetches in a local `_do_api_fetches` — is still attributable to the
    command a human would name.
    """
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    def enclosing(node: ast.AST) -> frozenset[str]:
        names, cur = [], parents.get(node)
        while cur is not None:
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.append(cur.name)
            cur = parents.get(cur)
        return frozenset(names)

    hits: dict[int, tuple[int, frozenset[str], bool]] = {}
    for node in ast.walk(tree):
        matched = (
            (isinstance(node, ast.Subscript)
             and isinstance(node.slice, ast.Constant)
             and node.slice.value == "api_key")
            or (isinstance(node, ast.Attribute) and node.attr == "api_key")
            or (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "api_key")
        )
        if matched:
            hits[node.lineno] = (
                node.lineno, enclosing(node), _is_presence_test(node, parents),
            )
    return [hits[line] for line in sorted(hits)]


# ===========================================================================

def test_no_cog_or_service_reads_a_guild_api_key_directly():
    """DESIGN rule 1 — the rule the incident's blast radius depended on.

    Seven call sites across three cogs plus a service read the key today. A
    guard on six of seven is a silent contamination path, and the only way
    to know it is seven and not eight is to make adding the eighth fail.

    Scoped to GUILD keys — see EXEMPT_PLAYER_KEY_FUNCTIONS for why, and for
    what is deliberately not covered here.
    """
    offenders: dict[str, list[int]] = {}
    for path in _python_files(*GUARDED_TREES):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in SANCTIONED_KEY_READERS:
            continue
        exempt_functions = EXEMPT_PLAYER_KEY_FUNCTIONS.get(rel, frozenset())
        lines = [
            line
            for line, enclosing, is_presence_test in _api_key_reads(
                ast.parse(path.read_text("utf-8"), filename=str(path))
            )
            if not (enclosing & exempt_functions) and not is_presence_test
        ]
        if lines:
            offenders[rel] = lines

    assert not offenders, (
        "these modules read a guild api_key directly instead of going through "
        f"bot/guild_keys.py: {offenders}"
    )


def test_the_key_consumption_inventory_matches_production():
    """`KeyConsumptionSite` IS the production site list, or this test fails.

    The enum's docstring has always claimed that "adding a site without
    adding it here is the mistake this type is shaped to make visible". Until
    2026-08-02 that was prose: nothing compared the enum against production,
    and the enum named three sites that do not exist while omitting three
    that do — including every site where a confirmed defect lives. AC-004.6,
    the one criterion that certifies "all sites are blocked", was
    parametrized over that set.

    This is the assertion that prose was standing in for. It scans `bot/` for
    every call to a `bot/guild_keys.py` entry point, attributes each to its
    enclosing function, and compares the resulting set against the `reader`
    coordinates the enum declares plus the explicitly-reasoned recovery
    entry point. Set equality in BOTH directions: a new site fails as
    unaccounted, and a deleted site fails as stale — the second half matters
    because a stale row is what lets a parametrization keep reporting eight
    green cases over seven real ones.

    Scanning `bot/` wholesale rather than `GUARDED_TREES` is deliberate: a
    new top-level module that reaches the chokepoint is exactly the eighth
    site nobody would think to add.
    """
    declared = {f"{site.module}::{site.reader}" for site in KeyConsumptionSite}
    accounted = declared | set(RECOVERY_ENTRY_POINTS)

    found = _chokepoint_call_sites()

    unaccounted = sorted(found - accounted)
    assert not unaccounted, (
        "these production functions reach a guild key through the chokepoint "
        "but are not declared in KeyConsumptionSite — AC-004.6 does not cover "
        f"them, so nothing proves they refuse a quarantined guild: {unaccounted}"
    )

    stale = sorted(declared - found)
    assert not stale, (
        "KeyConsumptionSite names these readers but no production code calls "
        "the chokepoint from them — a stale row makes AC-004.6 report a green "
        f"case for a site that does not exist: {stale}"
    )


def test_the_recovery_entry_point_exemption_still_describes_real_code():
    """The one site allowed NOT to refuse must still be the site we think.

    `RECOVERY_ENTRY_POINTS` is the single hole in the "every site refuses"
    rule, and it is only safe while the function named in it is still the
    `/update_guild_key` handler. A renamed or deleted handler would leave the
    exemption covering nothing — or, worse, covering some later function that
    happens to reuse the name.
    """
    found = _chokepoint_call_sites()
    stale = sorted(set(RECOVERY_ENTRY_POINTS) - found)
    assert not stale, (
        "these recovery entry points no longer call bot/guild_keys.py — "
        f"delete the exemption rather than leave a standing hole: {stale}"
    )


# The three `bot/guild_keys.py` entry points that hand a caller a usable key
# or a snapshot fetched with one. `key_ref`, `quarantine`, `release` and
# `re_report_persisting_drift` are deliberately absent: none of them returns
# key material or fetches with it, so calling them is not consumption.
CHOKEPOINT_ENTRY_POINTS = frozenset({
    "active_key", "verify_and_resolve", "install_guild_key",
})


def _chokepoint_call_sites() -> set[str]:
    """Every `module::function` in `bot/` that calls a chokepoint entry point.

    Matches both call shapes the codebase uses — `guild_keys.active_key(...)`
    after `import bot.guild_keys as guild_keys`, and a bare `active_key(...)`
    after `from bot.guild_keys import active_key`. `bot/guild_keys.py` itself
    is skipped: its internal calls are the chokepoint, not consumers of it.
    """
    sites: set[str] = set()
    for path in _python_files("bot"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel == "bot/guild_keys.py":
            continue
        tree = ast.parse(path.read_text("utf-8"), filename=str(path))
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.attr if isinstance(func, ast.Attribute)
                else func.id if isinstance(func, ast.Name)
                else None
            )
            if name not in CHOKEPOINT_ENTRY_POINTS:
                continue
            enclosing = _innermost_function(node, parents)
            if enclosing is not None:
                sites.add(f"{rel}::{enclosing}")
    return sites


def _innermost_function(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str | None:
    """The name of the nearest enclosing `def`, or None at module scope.

    Innermost rather than the full enclosing set (which
    `_api_key_reads` uses): a site is the function whose BODY holds the call,
    because that is the unit a reviewer moves, renames or deletes.
    """
    cur = parents.get(node)
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur.name
        cur = parents.get(cur)
    return None


def test_the_player_key_exemptions_still_describe_real_code():
    """The exemption list is only safe while every name on it still exists.

    A renamed or deleted function would silently widen the exemption to
    nothing — or, worse, leave a stale name covering a function someone later
    reintroduces for a different purpose. Assert the list is live, and that
    each exempt function genuinely still reads an `api_key`; an entry that no
    longer needs to be there should be deleted, not left as a standing hole.
    """
    stale: list[str] = []
    for rel, functions in EXEMPT_PLAYER_KEY_FUNCTIONS.items():
        tree = ast.parse((REPO_ROOT / rel).read_text("utf-8"), filename=rel)
        reading = {name for _, enclosing, _ in _api_key_reads(tree) for name in enclosing}
        stale += [f"{rel}::{fn}" for fn in sorted(functions - reading)]

    assert not stale, (
        "these functions are exempted from the guild-key rule but no longer "
        f"read an api_key — delete the exemption rather than leave it: {stale}"
    )


def test_the_chronicler_package_makes_no_http_calls():
    """DESIGN rule 2 / DDD-2. `_fetch_roster` is deleted by this feature and
    the Tacticus call moves out for good. An `httpx` import left behind is
    the signal that a second, unguarded call path survived the move."""
    offenders = [
        p.relative_to(REPO_ROOT).as_posix()
        for p in _python_files("bot/services/chronicl3r")
        if _imports(p, "httpx")
    ]
    assert not offenders, f"httpx still imported inside the Chronicler package: {offenders}"


def test_the_guilds_wrapper_layer_stays_free_of_policy_and_http():
    """DESIGN rule 3. `bot/guilds.py` is imported by every cog; if it
    imported `bot.guild_keys` the policy layer's HTTP client would become a
    hard dependency of everything, including on the JSON rollback path."""
    guilds = REPO_ROOT / "bot" / "guilds.py"
    for forbidden in ("bot.guild_keys", "httpx"):
        assert not _imports(guilds, forbidden), (
            f"bot/guilds.py imports {forbidden} — this is the import cycle "
            "DDD-3 is shaped to avoid"
        )


def test_storage_never_imports_policy():
    """DESIGN rule 4. Policy depends on storage; never the reverse. A
    repository that imported the chokepoint could quarantine during a read,
    which makes the quarantine state unreadable while quarantined."""
    for module in ("bot/repository.py", "bot/repository_sqlalchemy.py"):
        assert not _imports(REPO_ROOT / module, "bot.guild_keys"), (
            f"{module} imports bot.guild_keys — the dependency runs backwards"
        )


def test_import_linter_contracts_all_pass():
    """The four existing `lint-imports` contracts plus the fifth this feature
    adds, run as part of `pytest` rather than only when someone remembers.

    Pinning the tool into `requirements.txt` is part of DEVOPS D10 — a
    contract that only runs on machines where someone happened to
    `pip install` is not enforcement.
    """
    pytest.importorskip(
        "importlinter",
        reason="import-linter is not installed — DEVOPS D10 pins it into requirements.txt",
    )
    result = _run_lint_imports()
    assert result.returncode == 0, result.stdout + result.stderr


def test_archon_rules_hold():
    """`pytest-archon` rules for the two import boundaries best expressed as
    reachability rather than a direct-import check."""
    archon = pytest.importorskip(
        "pytest_archon",
        reason="pytest-archon is not installed — DEVOPS D10 pins it into requirements.txt",
    )
    from pytest_archon import archrule

    (
        archrule("chronicler makes no outbound calls")
        .match("bot.services.chronicl3r*")
        .should_not_import("httpx")
        .check("bot")
    )
    (
        archrule("the wrapper layer holds no policy")
        .match("bot.guilds")
        .should_not_import("bot.guild_keys")
        .should_not_import("httpx")
        .check("bot")
    )


def test_both_enforcement_tools_are_pinned():
    """DEVOPS D10's own precondition, asserted rather than assumed.

    This is the test that would have failed on 2026-07-31 and shown that
    ADR-008 D3's claim about existing enforcement was not true of the repo.
    """
    text = (REPO_ROOT / "requirements.txt").read_text("utf-8").lower()
    for tool in ("import-linter", "pytest-archon"):
        assert tool in text, (
            f"{tool} is not in requirements.txt — the enforcement it provides "
            "exists only on machines where someone installed it by hand"
        )


# ===========================================================================
# Helpers
# ===========================================================================

def _imports(path: Path, target: str) -> bool:
    """True when `path` imports `target` (or a submodule of it)."""
    tree = ast.parse(path.read_text("utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name == target or a.name.startswith(target + ".") for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module == target or node.module.startswith(target + "."):
                return True
    return False


def _run_lint_imports():
    """Invoke the real `lint-imports` console script as a subprocess.

    A subprocess and not the Python API on purpose: this asserts the command
    an operator would run, against the contracts as declared in
    `pyproject.toml`, with the same exit code the shell would see.
    """
    import subprocess
    import sys
    return subprocess.run(
        [sys.executable, "-c",
         "from importlinter.cli import lint_imports_command; lint_imports_command()"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
