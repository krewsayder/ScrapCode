"""Property-based classification of the composition root (slice 07, step 09-01).

WHY-NEW-FILE: tests/unit/test_slice_07_build_repo_classification.py
  CLOSEST-EXISTING: tests/unit/test_guild_keys_policy.py
  EXTENSION-COST: that module drives `bot.guild_keys.py` policy branches with a
    stubbed `guild_client` and a real repository rebound onto the singleton;
    these properties drive `bot.guilds.build_repo` itself — the composition
    root — and would have to bypass every fixture in the policy module to
    re-point the singleton at a per-example filesystem, which is exactly the
    behaviour under test.
  PARALLEL-RATIONALE: different unit under test (the composition root's
    configuration classifier, not the policy layer) and an incompatible
    dependency set — these tests need `hypothesis` + a per-example throwaway
    filesystem, while every test in the policy module needs a stable
    rebound singleton. Co-locating them would force the policy module's
    fixtures to be parameterised by a concern they do not share.

WHY PROPERTIES AND NOT EXAMPLES. The step's contract is a TOTAL function over
the `(backend, key-present, path-state)` configuration space: every
configuration either constructs the REQUESTED repository or raises naming the
offending variable, and NO configuration does neither (the silent JSON
fallback `build_repo` performs today is the "does neither" the step removes).
A total-function claim over an 18-cell configuration space is established by
exhausting the space, not by picking the three cells the acceptance scenarios
inhabit — and the dangerous cell is the one the scenario author did not
think to write, which a property covers and an example does not.

WHY NO FIXTURES INSIDE `@given`. `@given` runs many examples inside one test
call, and pytest function-scoped fixtures (`monkeypatch`, `tmp_path`) are NOT
reset between examples — Hypothesis flags that as a health check. Rather than
suppress the check (a forbidden bypass), each example manages its own env
via `_env_patch` (a snapshot/restore context manager over `os.environ`) and
its own throwaway directory via `tempfile.mkdtemp`. This is the
Hypothesis-idiomatic shape for stateful properties.

DECLARED UNIVERSE. Each property asserts over the full observable surface a
`build_repo()` call can return through, not a single slot:

    outcome    — "constructs" (a repository whose type matches the requested
                  backend) or "raises" (an exception whose message names the
                  offending SCRAPCODE_* variable)
    repo_type  — the concrete class name, present iff outcome == "constructs"
    named_var  — the SCRAPCODE_* variable named in the refusal message,
                  present iff outcome == "raises"

`_classify()` is the model: given a configuration, it returns the outcome the
contract requires. The property asserts the observed outcome equals the
modelled outcome for every generated configuration. A configuration that
silently falls back to `JsonClusterRepository` on a `sqlite` instruction is
the regression this step exists to remove, and the model marks it as
"raises", so the property fails on it.
"""
from __future__ import annotations

import contextlib
import itertools
import os
import shutil
import tempfile
from pathlib import Path

# `bot.guilds` evaluates `repo = build_repo()` at IMPORT time and reads the
# environment at that moment. Pin a harmless backend before any `bot.*` import
# so collection cannot construct a repository pointed at a live tree. Same
# precedent as `tests/unit/test_guild_keys_policy.py`.
os.environ.setdefault("SCRAPCODE_REPO_BACKEND", "json")

import base64  # noqa: E402

import pytest  # noqa: E402

hypothesis = pytest.importorskip(
    "hypothesis",
    reason="hypothesis is not installed — DISTILL pins it into requirements.txt",
)

import hypothesis.strategies as st  # noqa: E402
from hypothesis import given, settings  # noqa: E402

# Deselected from the 250-test baseline for the same reason the slice-07
# acceptance module is: this module belongs to the remediation slice, and the
# baseline command is the "nothing that shipped has regressed" gate.
pytestmark = [pytest.mark.property, pytest.mark.slice_07]


# A valid Fernet key, so a configuration about a MALFORMED key is a separate
# concern (AC-010.2, a later step). This step owns the unset/empty + missing-
# file gates only. Same derivation as the acceptance suite's
# `_a_real_fernet_key` helper.
_REAL_FERNET_KEY = base64.urlsafe_b64encode(
    b"guild-key-integrity-distill-32b!"[:32]
).decode()


@contextlib.contextmanager
def _env_patch(**overrides):
    """Snapshot `os.environ`, apply `overrides` (None = delete), restore.

    Replaces `monkeypatch.setenv`/`delenv` so `@given` examples do not depend
    on a function-scoped fixture that Hypothesis will not reset between
    examples (which would fire a `function_scoped_fixture` health check).
    """
    snapshot = dict(os.environ)
    try:
        for key, value in overrides.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        os.environ.clear()
        os.environ.update(snapshot)


def _build_repo_fresh():
    """Call the composition root with the environment the property just set.

    Identical to the acceptance suite's helper: `bot/guilds.py:61` evaluates
    `repo = build_repo()` at import time, so reading the singleton would
    assert something about import order. The property is about the
    composition root's RULES, so it calls the function directly.
    """
    from bot.guilds import build_repo

    return build_repo()


def _expected_outcome(backend: str, key_state: str, path_state: str) -> tuple[str, str]:
    """Model the contract: return (outcome, expected_value).

    outcome == "constructs"  -> expected_value is the repo class name
    outcome == "raises"      -> expected_value is the SCRAPCODE_* var the
                                refusal message must name
    """
    if backend == "json":
        # ADR-006 D9: the deliberate rollback is a decision somebody made; the
        # bot STILL STARTS on this path regardless of key/path state.
        return ("constructs", "JsonClusterRepository")
    # backend == "sqlite"
    if key_state in ("missing", "empty"):
        # AC-010.1: a deploy that cannot honour `=sqlite` without a Fernet
        # key is broken, not degraded. Refuses, naming the variable.
        return ("raises", "SCRAPCODE_DB_KEY")
    # key_state == "present"
    if path_state == "parent_exists_file_missing":
        # AC-010.3: parent dir present, file gone = deleted/corrupted.
        # Refuses, naming the path variable.
        return ("raises", "SCRAPCODE_DB_PATH")
    # path_state in (parent_missing, parent_exists_file_present)
    # First-run (parent absent) constructs and creates both dir + file;
    # existing file constructs in place. Both honour the sqlite instruction.
    return ("constructs", "SqlAlchemyClusterRepository")


def _setup_path(base: Path, path_state: str) -> Path:
    """Materialise `path_state` under `base`; return the db path."""
    db_path = base / "data" / "scrapcode.db"
    if path_state == "parent_exists_file_present":
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_path.touch()
    elif path_state == "parent_exists_file_missing":
        db_path.parent.mkdir(parents=True, exist_ok=True)
    # parent_missing: leave the tree absent (first-run path).
    return db_path


@given(
    backend=st.sampled_from(["sqlite", "json"]),
    key_state=st.sampled_from(["missing", "empty", "present"]),
    path_state=st.sampled_from(
        ["parent_missing", "parent_exists_file_present", "parent_exists_file_missing"]
    ),
)
@settings(max_examples=60, deadline=None)
def test_build_repo_classifies_every_configuration(backend, key_state, path_state):
    """Every (backend, key, path) configuration either constructs the
    requested repository or raises naming the offending SCRAPCODE_* variable.

    NO configuration silently falls back to a different backend, returns
    None, or raises without naming the variable to fix. This is the
    total-function claim the step makes; the property exhausts the
    configuration space rather than asserting the three cells the
    acceptance scenarios inhabit.
    """
    base = Path(tempfile.mkdtemp(prefix="slice07_class_"))
    try:
        db_path = _setup_path(base, path_state)
        key_env = (
            None if key_state == "missing"
            else "" if key_state == "empty"
            else _REAL_FERNET_KEY
        )
        with _env_patch(
            SCRAPCODE_REPO_BACKEND=backend,
            SCRAPCODE_DB_PATH=str(db_path),
            SCRAPCODE_DB_KEY=key_env,
        ), _chdir(base):
            expected_outcome, expected_value = _expected_outcome(
                backend, key_state, path_state
            )

            if expected_outcome == "constructs":
                repo = _build_repo_fresh()
                assert repo is not None, (
                    f"configuration {backend}/{key_state}/{path_state} returned "
                    "None instead of constructing a repository — the composition "
                    "root did neither thing the contract allows"
                )
                assert type(repo).__name__ == expected_value, (
                    f"configuration {backend}/{key_state}/{path_state} "
                    f"constructed {type(repo).__name__}, not the "
                    f"{expected_value} the SCRAPCODE_REPO_BACKEND={backend} "
                    "instruction requested — a silent fallback to a different "
                    "backend is the regression this step exists to remove"
                )
            else:  # raises
                with pytest.raises(Exception) as refusal:
                    _build_repo_fresh()
                message = str(refusal.value)
                assert expected_value in message, (
                    f"configuration {backend}/{key_state}/{path_state} refused "
                    f"to start but the message {message!r} did not name the "
                    f"variable the operator has to fix ({expected_value}). A "
                    "refusal that does not name the setting turns a "
                    "five-second fix into an incident (slice 07 obligation)."
                )
    finally:
        shutil.rmtree(base, ignore_errors=True)


@given(
    key_state=st.sampled_from(["missing", "empty"]),
    path_state=st.sampled_from(
        ["parent_missing", "parent_exists_file_present", "parent_exists_file_missing"]
    ),
)
@settings(max_examples=30, deadline=None)
def test_a_missing_or_empty_key_refuses_before_any_file_state_is_inspected(
    key_state, path_state
):
    """AC-010.1 ordering: the key gate fires BEFORE the file gate.

    A missing/empty `SCRAPCODE_DB_KEY` must refuse naming `SCRAPCODE_DB_KEY`
    regardless of path state — including the `parent_exists_file_missing`
    cell where the file gate would otherwise fire first. An operator who
    sees "fix the file" when the actual fault is the missing key is sent on
    a wrong errand, which is the message-quality obligation the step makes.
    """
    base = Path(tempfile.mkdtemp(prefix="slice07_key_"))
    try:
        db_path = _setup_path(base, path_state)
        key_env = None if key_state == "missing" else ""
        with _env_patch(
            SCRAPCODE_REPO_BACKEND="sqlite",
            SCRAPCODE_DB_PATH=str(db_path),
            SCRAPCODE_DB_KEY=key_env,
        ), _chdir(base):
            with pytest.raises(Exception) as refusal:
                _build_repo_fresh()
            assert "SCRAPCODE_DB_KEY" in str(refusal.value), (
                f"key_state={key_state} path_state={path_state} refused without "
                f"naming SCRAPCODE_DB_KEY first: {refusal.value!r}"
            )
    finally:
        shutil.rmtree(base, ignore_errors=True)


@given(
    path_state=st.sampled_from(
        ["parent_missing", "parent_exists_file_present", "parent_exists_file_missing"]
    ),
)
@settings(max_examples=15, deadline=None)
def test_a_present_key_and_missing_file_refuses_naming_the_path(path_state):
    """AC-010.3: parent exists, file gone = the database was deleted/corrupted.

    The refusal must name `SCRAPCODE_DB_PATH` (or the path itself) so the
    operator knows WHICH file to restore. The first-run path (parent
    absent) and the healthy path (file present) both construct — this
    property asserts the missing-file cell is the one that refuses, and
    that the message names the path variable.
    """
    base = Path(tempfile.mkdtemp(prefix="slice07_path_"))
    try:
        db_path = _setup_path(base, path_state)
        with _env_patch(
            SCRAPCODE_REPO_BACKEND="sqlite",
            SCRAPCODE_DB_PATH=str(db_path),
            SCRAPCODE_DB_KEY=_REAL_FERNET_KEY,
        ), _chdir(base):
            if path_state == "parent_exists_file_missing":
                with pytest.raises(Exception) as refusal:
                    _build_repo_fresh()
                message = str(refusal.value)
                assert "SCRAPCODE_DB_PATH" in message or str(db_path) in message, (
                    f"missing-file cell refused without naming the path: "
                    f"{message!r}"
                )
            else:
                repo = _build_repo_fresh()
                assert type(repo).__name__ == "SqlAlchemyClusterRepository", (
                    f"{path_state} should construct SqlAlchemy, got "
                    f"{type(repo).__name__}"
                )
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_the_first_run_path_creates_both_directory_and_file():
    """A first-run path whose parent does NOT yet exist still constructs the
    SQLite repository and creates both the directory and the file.

    Refusing the first-run path would make a fresh install impossible, so
    this test guards the one cell that must NOT refuse even though it
    looks superficially like the missing-file case. Not `@given`-decorated:
    there is exactly one first-run cell, so generation would add no
    coverage — a single example is the correct granularity for this
    invariant (skill's "Pure-function / single-cell" bypass).
    """
    base = Path(tempfile.mkdtemp(prefix="slice07_first_"))
    try:
        db_path = base / "data" / "scrapcode.db"
        with _env_patch(
            SCRAPCODE_REPO_BACKEND="sqlite",
            SCRAPCODE_DB_PATH=str(db_path),
            SCRAPCODE_DB_KEY=_REAL_FERNET_KEY,
        ), _chdir(base):
            # Leave the tree absent — first-run.
            repo = _build_repo_fresh()
            assert type(repo).__name__ == "SqlAlchemyClusterRepository"
            assert db_path.parent.exists(), (
                "the first-run path did not create the parent directory"
            )
            assert db_path.exists(), (
                "the first-run path did not create the database file"
            )
    finally:
        shutil.rmtree(base, ignore_errors=True)


@contextlib.contextmanager
def _chdir(target: Path):
    """`JsonClusterRepository()` reads `clusters/` relative to cwd; chdir to
    the per-example base so the JSON backend never touches the real tree."""
    cwd = Path.cwd()
    os.chdir(target)
    try:
        yield
    finally:
        os.chdir(cwd)