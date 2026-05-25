import requests
from dotenv import load_dotenv
import os
load_dotenv()

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

    def get_player_profile(self, tacticus_user_id: str) -> dict:
        """
        Retrieve an existing player profile.
        Raises HTTPError on failure (404 if not found).
        """
        self._ensure_authenticated()

        r = requests.get(
            self.chronicl3r_root_url + self.player_base_url + f"player-profiles/{tacticus_user_id}/api-key/",
            headers=self._auth_headers(),
        )
        r.raise_for_status()
        return r.json()

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

    def set_player_api_key(self, tacticus_user_id: str, api_key: str) -> dict:
        """
        Register a Tacticus API key for a player profile.
        Raises HTTPError on failure (400 if key invalid/mismatched, 404 if profile not found).
        """
        self._ensure_authenticated()

        r = requests.patch(
            self.chronicl3r_root_url + self.player_base_url + f"player-profiles/{tacticus_user_id}/api-key/",
            json={"tacticus_player_api_key": api_key},
            headers=self._auth_headers(),
        )
        r.raise_for_status()
        return r.json()
