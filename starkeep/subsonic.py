"""Minimal Subsonic API client (Navidrome-compatible; no heavy deps)."""

from __future__ import annotations

import hashlib
import secrets
import logging
import requests

log = logging.getLogger(__name__)


class SubsonicClient:
    def __init__(self, url: str, user: str, password: str) -> None:
        if not url or not user:
            raise ValueError("subsonic_url / subsonic_user are not set")
        self.url = url.rstrip("/")
        self.user = user
        self.password = password
        self._salt_seed = secrets.token_hex(8)

    def _params(self, **extra: str) -> dict:
        salt = secrets.token_hex(8)
        token = hashlib.md5((self.password + salt).encode()).hexdigest()
        p = {
            "u": self.user, "t": token, "s": salt,
            "v": "1.16.1", "c": "starkeep", "f": "json",
        }
        p.update(extra)
        return p

    def _call(self, endpoint: str, base: str = "rest/", **params: str) -> dict:
        r = requests.get(
            f"{self.url}/{base}{endpoint}", params=self._params(**params), timeout=30
        )
        r.raise_for_status()
        data = r.json().get("subsonic-response", {})
        if data.get("status") != "ok":
            raise RuntimeError(f"Subsonic {endpoint} error: {data.get('error')}")
        return data

    # ---- used by doctor ----------------------------------------------------

    def ping(self) -> None:
        self._call("ping")

    # ---- library checks ----------------------------------------------------

    def song_exists(self, artist: str, title: str) -> bool:
        """True if a matching song is already in the library (dedupe check)."""
        data = self._call("search3", artistQuery=artist, artistCount="5", songCount="0")
        for a in data.get("searchResult3", {}).get("artist", []):
            if a["name"].lower() == artist.strip().lower():
                songs = self._call(
                    "search3", artist=f"\"{artist}\"", songCount="500", songOffset="0",
                ).get("searchResult3", {}).get("song", [])
                want = title.strip().lower()
                return any(s.get("title", "").strip().lower() == want for s in songs)
        return False

    # ---- stars -------------------------------------------------------------

    def starred_songs(self) -> list[dict]:
        """All starred songs (id, title, artist, path, starred ISO timestamp)."""
        data = self._call("getStarred2")
        stars = data.get("starred2", {}).get("song", [])
        return [
            {
                "id": s["id"],
                "title": s.get("title"),
                "artist": s.get("artist"),
                "path": s.get("path"),
                "starred": s.get("starred"),  # ISO8601 — verify on your instance
            }
            for s in stars
        ]

    def unstar(self, song_id: str) -> None:
        self._call("unstar", id=song_id)
