"""The tier-literal chokepoint — AC-004.4, and TK-4's instrument.

ADR-008 D3 states the rule the three single-source modules in this codebase
share: *"a wrapper is only a chokepoint if bypassing it is caught."* This module
is the catching, for `bot/tiers.py`.

TK-4 IS MEASURED HERE, NOT REVIEWED LATER. DISCUSS specified TK-4's
measurement as "diff review at the next real tier addition". The next real tier
addition may be a year away — Mythic 2 stood a long time before Mythic 3
shipped — so that metric could not fail during this feature's life, and a
metric that cannot fail cannot inform. If tier literals live only in
`bot/tiers.py`, then adding a tier requires editing exactly that file: TK-4 = 1
by construction and by assertion, on every test run. DEVOPS D11; see
devops/upstream-changes.md item 1.

THE EXEMPTION IS LOAD-BEARING. `tier` names two unrelated concepts in this
codebase: a raid tier and a PERMISSION tier (`member`/`officer`/`admin`). A
rule written without exempting the permission paths by name either fires on
correct code — after which the operator learns to ignore it, which is worse
than having no rule at all — or gets loosened until it stops catching what it
was written for. ADR-009 D10.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from tier_types import PERMISSION_TIER_PATHS

RED = pytest.mark.skipif(
    os.getenv("SCRAPCODE_RED_GATE") != "1",
    reason="RED scaffold — enable one at a time in DELIVER",
)

REPO_ROOT = Path(__file__).resolve().parents[3]
REGISTRY = REPO_ROOT / "bot" / "tiers.py"

# The literals that name a raid tier. Deliberately the PREFIXES rather than the
# full keys: `"Mythic_2"` is the one that broke, and a rule matching only the
# keys that exist today would not notice `"Mythic_4"` being hand-written into a
# cog the day after the game ships it.
TIER_LITERAL_PREFIXES = ("Mythic_", "Legendary_")

# Files allowed to contain them. Exactly one production module plus this
# suite's own vocabulary, which has to name the frozen key set to pin it.
SANCTIONED = {
    "bot/tiers.py",
    "tests/acceptance/dynamic-tier-registry/tier_types.py",
    "tests/acceptance/dynamic-tier-registry/test_architecture_tier_literals.py",
}


def _python_sources() -> list[Path]:
    """Every production Python file the rule governs.

    `bot/` plus the top-level `config.py`, which is where half the original
    duplication lived. Test trees are excluded: a test naming a tier key is
    asserting about one, which is the point.
    """
    sources = sorted(p for p in (REPO_ROOT / "bot").rglob("*.py")
                     if "__pycache__" not in p.parts)
    sources.append(REPO_ROOT / "config.py")
    return sources


def _string_constants(path: Path) -> list[str]:
    """Every string literal in a module, via AST rather than grep.

    AST and not a regex because a grep matches the word inside a comment or a
    docstring, and a rule that fires on prose gets disabled. Docstrings are
    `ast.Constant` too, so they are filtered by position: a bare string
    expression at the head of a module, class or function is documentation.
    """
    tree = ast.parse(path.read_text("utf-8"), filename=str(path))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant) and \
                    isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    return [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and id(n) not in docstrings
    ]


@RED
@pytest.mark.architecture
@pytest.mark.kpi
def test_tier_literals_appear_only_in_the_registry():
    """AC-004.4. THE TK-4 INSTRUMENT.

    Fails today by design: `config.py:22-30` holds seven of them and
    `bot/tracker.py` holds four more, which is the two-files-that-must-agree
    problem the feature was opened to fix. It goes green when Slice 02's
    precursor commit lands.
    """
    offenders: dict[str, list[str]] = {}
    for path in _python_sources():
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in SANCTIONED:
            continue
        found = [
            s for s in _string_constants(path)
            if any(s.startswith(p) for p in TIER_LITERAL_PREFIXES)
        ]
        if found:
            offenders[rel] = found

    assert not offenders, (
        "tier names appear outside the registry:\n"
        + "\n".join(f"  {k}: {sorted(set(v))}" for k, v in sorted(offenders.items()))
        + "\n\nTK-4 counts the files a new tier forces someone to edit. Every "
        "module listed here is one of them."
    )


@pytest.mark.architecture
def test_the_registry_imports_nothing_but_the_standard_library():
    """DDD-2, as an assertion rather than as a docstring promise.

    NOT a RED scaffold. It is green against the scaffold and must STAY green
    through DELIVER — it is a constraint on what gets written, not a scenario
    waiting to be implemented, so it runs in the ordinary suite from today
    rather than waiting to be unskipped.

    The same claim an `import-linter` contract makes (DEVOPS D10), asserted
    here too so it holds on the gate that actually runs — `lint-imports` is a
    separate command somebody has to remember to type, and this project has no
    CI to remember for them.

    Purity is what keeps a rule table testable without an event loop, and what
    keeps `config.py` — imported by every cog — from depending on storage.
    """
    tree = ast.parse(REGISTRY.read_text("utf-8"), filename=str(REGISTRY))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    forbidden = {"discord", "config", "bot", "sqlalchemy", "aiosqlite", "alembic",
                 "httpx", "cryptography"}
    assert not (imported & forbidden), (
        f"bot/tiers.py imports {sorted(imported & forbidden)} — the direction "
        "runs config -> tiers, never the reverse"
    )


@pytest.mark.architecture
@pytest.mark.traceability
def test_the_permission_tier_paths_named_in_the_exemption_still_exist():
    """The exemption cannot be allowed to rot into a lie.

    NOT a RED scaffold — this asserts about the codebase as it is today, and it
    is green now. It is here because an exemption list that names a file which
    has since been renamed silently stops exempting anything, and the next
    person to run a mechanical rename across "tier" breaks `/scrapcode_help`
    and `/config_role_tier` with a green build behind them.

    Both would still type-check. That is the whole reason this is a test and
    not a comment.
    """
    for entry in PERMISSION_TIER_PATHS:
        module = entry.split("::", 1)[0]
        assert (REPO_ROOT / module).exists(), (
            f"{module} is named in the permission-tier exemption and no longer "
            "exists — the exemption is now silently empty"
        )


@pytest.mark.architecture
@pytest.mark.traceability
def test_the_exemption_actually_covers_permission_tier_readers():
    """The other half: the named files must really read a permission tier.

    An exemption for a file that does not need one is an exemption that will be
    copied to a file that does not need one either. Asserting the reason is
    what keeps the list honest.
    """
    haystacks = {
        entry.split("::", 1)[0]: (REPO_ROOT / entry.split("::", 1)[0]).read_text("utf-8")
        for entry in PERMISSION_TIER_PATHS
    }
    for module, text in haystacks.items():
        assert any(t in text for t in ("officer", "admin", "member")), (
            f"{module} is exempted as a permission-tier reader but names no "
            "permission tier"
        )
