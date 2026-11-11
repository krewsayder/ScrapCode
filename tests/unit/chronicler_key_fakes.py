"""Shared fakes for the Chronicl3r player-key tests (chronicler-key-link).

The pieces every one of those test modules built identically: a canned
`requests.Response`, the `requests` patch that routes Chronicl3r traffic to a
fake and refuses every other verb, and the `bot` log capture. Each module
still owns its own fake Chronicl3r (what it answers is that module's subject)
and its own state-delta universe.

Not a test module (no `test_` prefix), so pytest never collects it; imported
by basename, the way `tests/acceptance/*/domain_types.py` is.
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from unittest.mock import patch

import requests
from requests.structures import CaseInsensitiveDict

CLIENT_REQUESTS = "bot.services.chronicl3r.client.requests"


def response(status, *, json_body=None, text=None, content_type=None, retry_after=None):
    """A real `requests.Response` with this status, body and headers."""
    canned = requests.Response()
    canned.status_code = status
    headers = CaseInsensitiveDict()
    if json_body is not None:
        canned._content = json.dumps(json_body).encode()
        headers["Content-Type"] = content_type or "application/json"
    else:
        canned._content = (text or "").encode()
        if content_type is not None:
            headers["Content-Type"] = content_type
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    canned.headers = headers
    canned.encoding = "utf-8"
    return canned


def refuse(*args, **kwargs):
    raise AssertionError("a key call used a verb the contract does not name")


@contextmanager
def chronicler_transport(fake):
    """Route the client's POST and DELETE to `fake`; refuse every other verb."""
    with patch(f"{CLIENT_REQUESTS}.post", fake.post), \
            patch(f"{CLIENT_REQUESTS}.delete", fake.delete), \
            patch(f"{CLIENT_REQUESTS}.get", refuse), \
            patch(f"{CLIENT_REQUESTS}.patch", refuse), \
            patch(f"{CLIENT_REQUESTS}.put", refuse), \
            patch(f"{CLIENT_REQUESTS}.request", refuse):
        yield


@contextmanager
def recording_bot_logs(handler: logging.Handler):
    """Attach `handler` to the `bot` logger at DEBUG for the duration."""
    root = logging.getLogger("bot")
    previous_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)


class StructuredLogRecorder(logging.Handler):
    """Keeps every record raw, and each structured one as (event, chosen fields)."""

    FIELDS = ("server_id", "discord_id", "code")

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []
        self.raw = []

    def emit(self, record):
        self.raw.append(record.getMessage() + " " + repr(record.__dict__))
        if getattr(record, "structured", False):
            fields = {k: getattr(record, k) for k in self.FIELDS if hasattr(record, k)}
            self.records.append((record.event, fields))
        else:
            self.records.append(("message", record.getMessage()))
