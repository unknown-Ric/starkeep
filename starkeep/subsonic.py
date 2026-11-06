"""Minimal Subsonic API client (Navidrome-compatible; no heavy deps)."""

from __future__ import annotations

import hashlib
import logging
import secrets

import requests

log = logging.getLogger(__name__)


def _norm(text: str | None) -> str:
    return " ".join((text or "").casefold().split())


class SubsonicClient:
    def __init__(self, url: str, user: str, password: str) -> None:
        if not url or not user:
            raise ValueError("subsonic_url / subsonic_user are not set")
        self.url = url.rstrip("/")
        self.user = user
        self.password = password

    def _params(self, **extra: str) -> dict:
        salt = secrets.token_hex(8)
        token = hashlib.md5((self.password + salt).encode()).hexdigest()
        p = {
            "u": self.user, "t": token, "s": salt,
            "v": "1.16.1", "c": "starkeep", "f": "json",
        }
        p.update(extra)
        return p

    def _call(self, endpoint: str, **params: str) -> dict:
        try:
            r = requests.get(
                f"{self.url}/rest/{endpoint}", params=self._params(**params), timeout=30
            )
            r.raise_for_status()
        except requests.RequestException as e:
            raise RuntimeError(f"Subsonic {endpoint} unreachable at {self.url}: {e}") from e
        try:
            data = r.json().get("subsonic-response", {})
        except ValueError as e:
            raise RuntimeError(
                f"Subsonic {endpoint}: response was not JSON — check subsonic_url"
            ) from e
        if data.get("status") != "ok":
            raise RuntimeError(f"Subsonic {endpoint} error: {data.get('error')}")
        return data

    # ---- used by doctor ----------------------------------------------------

    def ping(self) -> None:
        self._call("ping")

    # ---- library checks ----------------------------------------------------

    def song_exists(self, artist: str, title: str) -> bool:
        """True if this artist+title is already in the library (dedupe check).

        One search3 call: Subsonic has no reliable exact-artist filter, so we
        search textually and compare normalized fields ourselves. The audition
        folder is normally inside the Navidrome library, so tracks still
        auditioning also match here — which is exactly what we want.
        """
        data = self._call(
            "search3",
            query=f"{artist} {title}",
            songCount="50", songOffset="0",
            artistCount="0", albumCount="0",
        )
        songs = data.get("searchResult3", {}).get("song", [])
        want_a, want_t = _norm(artist), _norm(title)
        return any(
            _norm(s.get("artist")) == want_a and _norm(s.get("title")) == want_t
            for s in songs
        )

    # ---- stars -------------------------------------------------------------

    def starred_songs(self) -> list[dict]:
        """All starred songs (id, title, artist, path, starred timestamp)."""
        data = self._call("getStarred2")
        stars = data.get("starred2", {}).get("song", [])
        return [
            {
                "id": s["id"],
                "title": s.get("title"),
                "artist": s.get("artist"),
                "path": s.get("path"),
                "starred": s.get("starred"),  # ISO8601 (not required by decide)
            }
            for s in stars
        ]

    def unstar(self, song_id: str) -> None:
        self._call("unstar", id=song_id)
