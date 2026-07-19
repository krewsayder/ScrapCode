"""Fernet + HKDF-derived HMAC helpers for `api_key` columns (ADR-006 D7).

Two responsibilities, both pure functions (this module is its own driving
port — port-to-port at the domain-function scope):

  - `encrypt_api_key` / `decrypt_api_key` — Fernet symmetric encryption of
    `guilds.api_key` and `player_registrations.api_key`. The ciphertext is
    what is stored at rest; decrypt-on-read returns the plaintext cogs see.
  - `api_key_hmac` — a deterministic HMAC-SHA256 hexdigest of the plaintext,
    keyed by an HKDF-derived subkey of `SCRAPCODE_DB_KEY`. This is the column
    that enforces the 1:1 api_key binding the non-deterministic Fernet
    ciphertext cannot (ADR-006 D7 rationale).

Empty-string handling (RC12): an empty `api_key` is NOT encrypted (Fernet
cannot meaningfully encrypt a zero-length secret, and encrypting it would
store a non-empty ciphertext indistinguishable from a real one). Instead
`encrypt_api_key("")` returns `""` (stored as the empty string / NULL by
the ORM), `decrypt_api_key("")` returns `""`, and `api_key_hmac("")` returns
`None`. The 02-01 schema made `guilds.api_key_hmac` NULLABLE UNIQUE so
multiple empty-key guilds coexist; `player_registrations.api_key_hmac` is
NOT NULL UNIQUE (a registration always carries a real api_key).
"""

from __future__ import annotations

import hashlib
import hmac

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_HKDF_INFO = b"scrapcode-api-key-hmac-v1"


def _fernet(fernet_key: str) -> Fernet:
    return Fernet(fernet_key.encode())


def _derive_hmac_key(fernet_key: str) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=_HKDF_INFO,
    ).derive(fernet_key.encode())


def encrypt_api_key(plaintext: str, fernet_key: str) -> str:
    """Fernet-encrypt `plaintext`; return `""` for empty plaintext (NULL-safe)."""
    if not plaintext:
        return ""
    return _fernet(fernet_key).encrypt(plaintext.encode()).decode()


def decrypt_api_key(ciphertext: str, fernet_key: str) -> str:
    """Fernet-decrypt `ciphertext`; return `""` for empty ciphertext."""
    if not ciphertext:
        return ""
    return _fernet(fernet_key).decrypt(ciphertext.encode()).decode()


def api_key_hmac(plaintext: str, fernet_key: str) -> str | None:
    """Deterministic HMAC-SHA256 hexdigest of `plaintext`; `None` for empty."""
    if not plaintext:
        return None
    return hmac.new(_derive_hmac_key(fernet_key), plaintext.encode(), hashlib.sha256).hexdigest()