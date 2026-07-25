"""Unit tests for /registration validate_keys (registration_cog).

The command's testable logic is split into two module-level helpers so it can
be exercised without a Discord interaction or a live Tacticus endpoint:

  * ``_probe_api_keys``  — bounded-concurrency HTTP probe; mocked httpx.
  * ``_format_key_validation`` — pure rendering of probe results.

The wiring (defer / load / resolve_members / followup) is thin glue and is
covered by running the command live on the VM.
"""
import os

# The cog import chain (registration_cog -> bot.embeds -> config) reads env at
# module load: config.py does int(os.getenv("UPDATE_CHANNEL_ID")) and the same
# for REPLAY_INDEX_CHANNEL_ID, and bot.guilds builds the repo singleton. None of
# these values are exercised by these tests (httpx is mocked, helpers are pure),
# so placeholders suffice — set them before the import.
os.environ.setdefault("SCRAPCODE_REPO_BACKEND", "json")
os.environ.setdefault("UPDATE_CHANNEL_ID", "0")
os.environ.setdefault("REPLAY_INDEX_CHANNEL_ID", "0")

from unittest.mock import AsyncMock, patch

import httpx

from bot.cogs.registration_cog import (
    _format_key_validation,
    _probe_api_keys,
)


# ------------------------------------
# _format_key_validation (pure)
# ------------------------------------

def test_format_all_valid():
    results = {"1": (200, None), "2": (200, None)}
    out = _format_key_validation(results, {"1": "Alice", "2": "Bob"}, "Word Bearers")
    assert "Word Bearers" in out
    assert "Valid: 2" in out
    assert "All keys valid" in out
    assert "Dead" not in out
    assert "Could not check" not in out
    assert "API error" not in out


def test_format_classifies_dead_unreachable_and_api_error():
    results = {
        "1": (200, None),
        "2": (403, None),
        "3": (401, None),
        "4": (None, "ReadTimeout"),
        "5": (500, None),
    }
    names = {"1": "A", "2": "B", "3": "C", "4": "D", "5": "E"}
    out = _format_key_validation(results, names, "WB")
    assert "Valid: 1" in out
    # 403 AND 401 are both "dead key" (the live register command only checks 401;
    # revoked keys have been observed returning 403).
    assert "Dead key" in out
    assert "B" in out and "C" in out
    assert "Could not check" in out
    assert "D" in out and "ReadTimeout" in out
    assert "API error" in out and "E" in out and "HTTP 500" in out
    assert "All keys valid" not in out


def test_format_falls_back_to_raw_id_when_name_missing():
    out = _format_key_validation({"999": (403, None)}, {}, "WB")
    assert "`999`" in out


def test_format_dead_count_and_names_match():
    results = {d: (403, None) for d in ("1", "2", "3")}
    out = _format_key_validation(results, {"1": "A", "2": "B", "3": "C"}, "WB")
    assert "Dead key — ask to re-register: 3 — A, B, C" in out


# ------------------------------------
# _probe_api_keys (httpx mocked)
# ------------------------------------

def _mock_client(status_by_key: dict[str, int], exc_by_key: dict[str, Exception] | None = None):
    """Build a mock `httpx.AsyncClient` context manager.

    `async with httpx.AsyncClient() as client` yields a client whose `.get`
    inspects the X-API-KEY header and returns a response with the configured
    status, or raises the configured exception. Returns the context-manager
    mock to use as `httpx.AsyncClient`'s return_value."""
    exc_by_key = exc_by_key or {}

    async def _get(url, headers=None, **kwargs):
        key = headers["X-API-KEY"]
        if key in exc_by_key:
            raise exc_by_key[key]
        resp = AsyncMock()
        resp.status_code = status_by_key[key]
        return resp

    mock_client = AsyncMock()
    mock_client.get = _get
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_client
    mock_ctx.__aexit__.return_value = None
    return mock_ctx


async def test_probe_classifies_status_and_exceptions():
    mock_ctx = _mock_client(
        status_by_key={"good": 200, "forbidden": 403, "unauth": 401},
        exc_by_key={"slow": httpx.ReadTimeout("timed out")},
    )
    with patch("bot.cogs.registration_cog.httpx.AsyncClient", return_value=mock_ctx):
        results = await _probe_api_keys(
            {"1": "good", "2": "forbidden", "3": "unauth", "4": "slow"}
        )
    assert results["1"] == (200, None)
    assert results["2"] == (403, None)
    assert results["3"] == (401, None)
    assert results["4"][0] is None
    assert results["4"][1] == "ReadTimeout"


async def test_probe_skips_falsy_keys():
    mock_ctx = _mock_client(status_by_key={"good": 200})
    with patch("bot.cogs.registration_cog.httpx.AsyncClient", return_value=mock_ctx):
        results = await _probe_api_keys({"1": "good", "2": "", "3": None})
    assert results == {"1": (200, None)}


async def test_probe_empty_input_returns_empty_without_calling_httpx():
    # No AsyncClient should be constructed for empty input.
    with patch("bot.cogs.registration_cog.httpx.AsyncClient") as mock_ctor:
        results = await _probe_api_keys({})
    assert results == {}
    mock_ctor.assert_not_called()