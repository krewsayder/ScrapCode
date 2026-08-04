"""Property-based classification of the Fernet key SHAPE gate (slice 07, step 09-02).

WHY-NEW-FILE: tests/unit/test_slice_07_fernet_key_shape.py
  CLOSEST-EXISTING: tests/unit/test_slice_07_build_repo_classification.py
  EXTENSION-COST: that module classifies the composition root's
    `(backend, key-state, path-state)` configuration space and drives
    `build_repo()` end-to-end; re-using it for the key-shape validator would
    force every property to thread a throwaway filesystem + a patched
    environment through `build_repo` just to reach the one branch under test,
    which is exactly the state-coupling the validator's pure-function shape
    exists to avoid.
  PARALLEL-RATIONALE: different unit under test (the pure key-shape
    validator `_require_real_fernet_key`, not the composition root's
    configuration classifier) and an incompatible dependency surface — these
    properties need only `hypothesis` + the validator function, while the
    classifier module needs a per-example throwaway filesystem + env patch.
    Co-locating them would force the classifier's fixtures to be
    parameterised by a concern they do not share.

WHY PROPERTIES AND NOT EXAMPLES. The validator's contract is a total
predicate over the string space: exactly the byte-identical valid Fernet
key is accepted, and every perturbation — appended/inserted whitespace,
control characters, truncations, appended garbage — is refused with a
message naming `SCRAPCODE_DB_KEY`. `Fernet.__init__` is LENIENT about
whitespace (it delegates to `base64.urlsafe_b64decode`, which discards
whitespace and accepts trailing garbage), so a CRLF-mangled `.env` value
passes `Fernet(key)` and the failure surfaces hours later inside the
hourly loop. A property over generated valid keys + generated perturbations
exhausts the equivalence classes that matter (whitespace, control chars,
truncation, length drift) rather than asserting the one CRLF cell the
acceptance scenario inhabits — and Hypothesis shrinking will find the
shortest counter-example if any perturbation class slips through.

DECLARED UNIVERSE. Each property asserts over the full observable surface
a `_require_real_fernet_key(key)` call can produce through its driving
port:

    outcome  — "accepted" (returns None, no raise) or "refused" (raises
                StartupRefused whose message names SCRAPCODE_DB_KEY)
    message  — str(exc), present iff outcome == "refused"; MUST contain
                "SCRAPCODE_DB_KEY"

No hidden slots: the function returns None or raises, and the message is
the only side-channel. A valid key that raised or a perturbation that was
accepted is the regression this gate exists to prevent.
"""
from __future__ import annotations

import base64
import os

# `bot.guilds` evaluates `repo = build_repo()` at IMPORT time and reads the
# environment at that moment. Pin a harmless backend before any `bot.*`
# import so collection cannot construct a repository pointed at a live
# tree. Same precedent as `tests/unit/test_slice_07_build_repo_classification.py:66`.
os.environ.setdefault("SCRAPCODE_REPO_BACKEND", "json")

import pytest  # noqa: E402

hypothesis = pytest.importorskip(
    "hypothesis",
    reason="hypothesis is not installed — DISTILL pins it into requirements.txt",
)

import hypothesis.strategies as st  # noqa: E402
from hypothesis import given, settings  # noqa: E402

# Deselected from the 250-test baseline for the same reason the slice-07
# acceptance module is: this module belongs to the remediation slice, and
# the baseline command is the "nothing that shipped has regressed" gate.
pytestmark = [pytest.mark.property, pytest.mark.slice_07]


# ---------------------------------------------------------------------------
# Driving port
# ---------------------------------------------------------------------------

def _validate(key: str):
    """Call the production key-shape gate directly (the driving port).

    `_require_real_fernet_key` is the pure predicate the composition root
    calls before constructing + probing the repository. Driving it directly
    — rather than threading a throwaway filesystem through `build_repo` —
    isolates the key-shape contract from the configuration classifier that
    the sibling module already covers.
    """
    from bot.guilds import _require_real_fernet_key
    return _require_real_fernet_key(key)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# A valid Fernet key is 32 url-safe base64-encoded bytes (44 chars). Generate
# the 32 raw bytes and encode, so the property is over the real key space
# (`Fernet.generate_key`'s distribution), not a hand-picked fixture.
_valid_keys = st.binary(min_size=32, max_size=32).map(
    lambda b: base64.urlsafe_b64encode(b).decode()
)


def _insert_at(s: str, index: int, fragment: str) -> str:
    return s[:index] + fragment + s[index:]


# Whitespace that a Windows-edited `.env` or a copy-paste can introduce —
# the trailing carriage return is the recorded incident, not a thought
# experiment.
_whitespace = st.sampled_from(["\r", "\n", "\t", " ", "\f", "\v", "\r\n"])

# Control characters outside the base64 alphabet and outside whitespace —
# a binary grep that finds a NUL or an ESC in the value is the same class.
_control_chars = st.sampled_from(
    [chr(c) for c in range(0, 32) if chr(c) not in "\t\n\r\f\v"]
    + [chr(0x7F)]
)

# Perturbations that produce a DEFINITELY-invalid string: every one either
# adds an off-alphabet byte or changes the length away from 44. The property
# asserts each is refused naming SCRAPCODE_DB_KEY.
@st.composite
def _perturbed_keys(draw):
    valid = draw(_valid_keys)
    kind = draw(st.sampled_from(
        ["append_ws", "prepend_ws", "insert_ws", "truncate_head",
         "truncate_tail", "append_control", "append_garbage", "replace_with_ws"]
    ))
    if kind == "append_ws":
        return valid + draw(_whitespace)
    if kind == "prepend_ws":
        return draw(_whitespace) + valid
    if kind == "insert_ws":
        index = draw(st.integers(min_value=0, max_value=len(valid)))
        return _insert_at(valid, index, draw(_whitespace))
    if kind == "truncate_head":
        drop = draw(st.integers(min_value=1, max_value=4))
        return valid[drop:]
    if kind == "truncate_tail":
        drop = draw(st.integers(min_value=1, max_value=4))
        return valid[:-drop]
    if kind == "append_control":
        return valid + draw(_control_chars)
    if kind == "append_garbage":
        # Extra base64-ish chars after the 44: `urlsafe_b64decode` stops at the
        # padding `=`, so `Fernet` accepts this — but the length is no longer
        # 44, so the shape gate refuses it.
        extra = draw(st.text("abcABC012-_", min_size=1, max_size=4))
        return valid + extra
    # replace_with_ws: swap one alphabet char for a whitespace char — the
    # length stays 44 but an off-alphabet byte is now in the string.
    index = draw(st.integers(min_value=0, max_value=len(valid) - 1))
    return valid[:index] + draw(_whitespace) + valid[index + 1:]


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

@given(key=_valid_keys)
@settings(max_examples=80, deadline=None)
def test_a_byte_identical_valid_fernet_key_is_accepted(key):
    """The gate accepts exactly the valid key — no raise, returns None.

    A validator that refused a `Fernet.generate_key()` output would block
    every fresh install, so this property is the floor: the valid key space
    passes. Paired with the refusal property, the two together assert the
    gate accepts ONLY the valid space.
    """
    assert _validate(key) is None, (
        f"a byte-identical valid Fernet key {key!r} was refused — the gate "
        "would block every fresh install"
    )


@given(key=_perturbed_keys())
@settings(max_examples=120, deadline=None)
def test_every_perturbation_is_refused_naming_the_key(key):
    """Any whitespace, control char, truncation, or garbage appended to a
    valid key is refused, and the refusal message names `SCRAPCODE_DB_KEY`.

    `Fernet.__init__` is lenient about trailing whitespace (it discards it
    via `base64.urlsafe_b64decode`), so the SHAPE is validated explicitly
    here — a CRLF-mangled `.env` value is the recorded incident this gate
    exists to catch at startup instead of mid-cycle.
    """
    with pytest.raises(Exception) as refusal:
        _validate(key)
    message = str(refusal.value)
    assert "SCRAPCODE_DB_KEY" in message, (
        f"perturbed key {key!r} was refused but the message did not name "
        f"SCRAPCODE_DB_KEY — the operator at 2am gets a cryptography "
        f"traceback instead of the variable: {message!r}"
    )