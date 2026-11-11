from dataclasses import dataclass, field

import requests
from dotenv import load_dotenv
import os
load_dotenv()

LINK_TIMEOUT_SECONDS = 60
KEY_CALL_TIMEOUT_SECONDS = 10

_NOT_JSON = object()


@dataclass(frozen=True)
class KeyCallResult:
    """Outcome of one key call. Holds no key; the profile is kept out of repr."""

    status: int | None
    code: str | None
    retry_after: int | None
    profile: dict | None = field(repr=False)


TRANSPORT_FAILURE = KeyCallResult(status=None, code="transport", retry_after=None, profile=None)


def _json_body(response: requests.Response) -> object:
    if "json" not in response.headers.get("Content-Type", "").lower():
        return _NOT_JSON
    try:
        return response.json()
    except ValueError:
        return _NOT_JSON


def _contract_code(status: int, body: object) -> str | None:
    if body is _NOT_JSON and status == 404:
        return "not_deployed"
    code = body.get("code") if isinstance(body, dict) else None
    return code if isinstance(code, str) else None


def _retry_after_seconds(response: requests.Response) -> int | None:
    value = response.headers.get("Retry-After", "").strip()
    if value.isascii() and value.isdigit():
        return int(value)
    return None


def _key_call_result(response: requests.Response) -> KeyCallResult:
    body = _json_body(response)
    status = response.status_code
    return KeyCallResult(
        status=status,
        code=_contract_code(status, body),
        retry_after=_retry_after_seconds(response),
        profile=body if status == 200 and isinstance(body, dict) else None,
    )

class chronicl3rClient():

    auth_url           = "api/auth/token/"
    player_base_url    = "tacticus-guild-raid/api/v1/"
    token              = None

    def __init__(self):
        self.chronicl3r_username = os.getenv('CHRONICL3R_APP_USERNAME')
        self.chronicl3r_password = os.getenv('CHRONICL3R_APP_PASSWORD')
        self.chronicl3r_root_url = "https://www.chronicl3r.com/"

    def authenticate(self) -> None:
        """Generates token from Chronicl3r"""
        r = requests.post(
            self.chronicl3r_root_url + self.auth_url,
            json={
                "username": self.chronicl3r_username,
                "password": self.chronicl3r_password
            }
        )
        r.raise_for_status()
        self.token = r.json()['token']

    def is_authenticated(self):
        """
        Test if token exists.
        Tokens don't expire, so return true if token exists.
        """
        return bool(self.token)

    def _auth_headers(self):
        return {"Authorization": f"Token {self.token}"}

    def _ensure_authenticated(self):
        if not self.is_authenticated():
            self.authenticate()

    def register_user(self, tacticus_user_id: str) -> dict:
        """
        Register a new Tacticus player profile.
        Returns:
        {
            "id": <Chronicl3r unique id>,
            "tacticus_user_id": <SnowPrint User ID>,
            "tacticus_display_nm": <SnowPrint User Name>,
            "has_valid_api_key": <bool>,
            "last_updated": <datetime>
        }
        Raises HTTPError on failure (409 if already registered).
        """
        self._ensure_authenticated()

        r = requests.post(
            self.chronicl3r_root_url + self.player_base_url + "player-profile/register/",
            json={"tacticus_user_id": tacticus_user_id},
            headers=self._auth_headers(),
        )
        r.raise_for_status()
        return r.json()["data"]

    def get_profile(self, tacticus_user_id: str) -> dict:
        """
        Retrieve a player profile by Tacticus user ID.
        Raises HTTPError on failure (404 if not registered).
        """
        self._ensure_authenticated()

        r = requests.get(
            self.chronicl3r_root_url + self.player_base_url + f"player-profiles/{tacticus_user_id}/api-key/",
            headers=self._auth_headers(),
        )
        r.raise_for_status()
        return r.json()

    def link_api_key(self, key: str) -> KeyCallResult:
        """Link a Tacticus player API key to its profile. Never raises."""
        return self._key_call(
            requests.post, "player-profiles/api-keys/", LINK_TIMEOUT_SECONDS,
            body={"tacticus_player_api_key": key},
        )

    def lookup_api_key(self, key: str) -> KeyCallResult:
        """Look up the profile a Tacticus player API key belongs to. Never raises."""
        return self._key_call(
            requests.post, "player-profiles/lookup/", KEY_CALL_TIMEOUT_SECONDS,
            body={"tacticus_player_api_key": key},
        )

    def delete_api_key(self, tacticus_user_id: str) -> KeyCallResult:
        """Remove the API key linked to a profile. Never raises."""
        return self._key_call(
            requests.delete, f"player-profiles/{tacticus_user_id}/api-key/",
            KEY_CALL_TIMEOUT_SECONDS,
        )

    def _key_call(self, send, route: str, timeout: int, body: dict | None = None) -> KeyCallResult:
        """One key call: no raise_for_status, and no request body in any message."""
        payload = {} if body is None else {"json": body}
        try:
            self._ensure_authenticated()
            response = send(
                self.chronicl3r_root_url + self.player_base_url + route,
                headers=self._auth_headers(),
                timeout=timeout,
                **payload,
            )
        except requests.RequestException:
            return TRANSPORT_FAILURE
        return _key_call_result(response)
