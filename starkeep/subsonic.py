"""Minimal Subsonic API client (Navidrome-compatible; no heavy deps)."""

from __future__ import annotations

import hashlib
import io
import logging
import re
import secrets
import time
import unicodedata

import requests

log = logging.getLogger(__name__)


def _norm(text: str | None) -> str:
    # NFKC folds fullwidth/compatibility chars (Ａrtist == Artist); casefold+split
    # handles case and stray whitespace. Diacritics are deliberately NOT folded:
    # artists like "Beyoncé" vs "Beyonce" are usually different entries.
    return " ".join(unicodedata.normalize("NFKC", text or "").casefold().split())


_FEAT = re.compile(r"(?i)[(\[,]? *\b(?:featuring|feat|ft)\.?(?: |\b)")

def _strip_feat(text: str | None) -> str:
    """'Song (feat. B)' -> 'Song' — Last.fm and YouTube credit guests differently."""
    return _FEAT.split(_norm(text), maxsplit=1)[0].strip()


class SubsonicClient:
    def __init__(self, url: str, user: str, password: str) -> None:
        if not url or not user:
            raise ValueError("subsonic_url / subsonic_user are not set")
        self.url = url.rstrip("/")
        self.user = user
        self.password = password
        self._nd_token: str | None = None

    def _params(self, **extra) -> dict:
        salt = secrets.token_hex(8)
        token = hashlib.md5((self.password + salt).encode()).hexdigest()
        p = {
            "u": self.user, "t": token, "s": salt,
            "v": "1.16.1", "c": "starkeep", "f": "json",
        }
        p.update(extra)
        return p

    def _call(self, endpoint: str, **params) -> dict:
        # values may be lists (repeated API params, e.g. songId=... songId=...)
        flat = {k: (v if isinstance(v, list) else str(v)) for k, v in params.items()}
        try:
            r = requests.get(
                f"{self.url}/rest/{endpoint}", params=self._params(**flat), timeout=30
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
        fa, ft = _strip_feat(artist), _strip_feat(title)
        return any(
            (_norm(s.get("artist")) == want_a and _norm(s.get("title")) == want_t)
            or (_strip_feat(s.get("artist")) == fa and _strip_feat(s.get("title")) == ft)
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

    def star(self, song_id: str) -> None:
        self._call("star", id=song_id)

    # ---- library scan (Navidrome extensions; other servers: no-op) ----------

    def start_scan(self) -> bool:
        """Kick off a library scan so new files get song ids. False if unsupported."""
        try:
            self._call("startScan")
            return True
        except RuntimeError as e:
            log.info("server does not support startScan (Navidrome extension): %s", e)
            return False

    def wait_for_scan(self, timeout: float = 180.0) -> bool:
        """Trigger a scan and block until it finishes (needed before the
        audition playlist can reference freshly downloaded files)."""
        if not self.start_scan():
            return False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(2)
            try:
                if not self._call("getScanStatus").get("scanStatus", {}).get("scanning"):
                    return True
            except RuntimeError:
                return False
        log.warning("library scan still running after %.0fs; continuing", timeout)
        return False

    def get_top_songs(self, count: int = 50) -> list[dict]:
        """Top-played songs by the current user (Navidrome honours per-user
        play counts; other servers may return global counts or error).
        Returns [{id, artist, title}] ordered most-played first.
        Falls back to starred songs if getTopSongs is unavailable."""
        try:
            data = self._call("getTopSongs", count=str(count), username="me")
            songs = data.get("topSongs", {}).get("song", [])
            if songs:
                return [
                    {"id": s["id"], "artist": s.get("artist", ""), "title": s.get("title", "")}
                    for s in songs
                ]
        except RuntimeError as e:
            log.info("getTopSongs unavailable, falling back to starred: %s", e)
        # Fallback: starred songs as a proxy for favorites
        try:
            data = self._call("getStarred", count=str(count))
            songs = data.get("stars", {}).get("song", [])
            return [
                {"id": s["id"], "artist": s.get("artist", ""), "title": s.get("title", "")}
                for s in songs
            ]
        except RuntimeError:
            return []

    # ---- audition playlist -------------------------------------------------

    def search_songs(self, artist: str, title: str, limit: int = 10) -> list[dict]:
        """Raw search3 song hits: {id, title, artist, path}."""
        data = self._call(
            "search3",
            query=f"{artist} {title}",
            songCount=str(limit), songOffset="0",
            artistCount="0", albumCount="0",
        )
        return [
            {"id": s["id"], "title": s.get("title"), "artist": s.get("artist"),
             "path": s.get("path")}
            for s in data.get("searchResult3", {}).get("song", [])
        ]

    def playlists(self) -> list[dict]:
        data = self._call("getPlaylists")
        out = data.get("playlists", {}).get("playlist", [])
        # Navidrome returns "name"; OpenSubsonic spec says "playlistName".
        # Alias either to both so lookup-by-name works against any response shape.
        for pl in out:
            pl.setdefault("name", pl.get("playlistName"))
            pl.setdefault("playlistName", pl.get("name"))
        return out

    def playlist_playcounts(self, name: str) -> dict[str, int]:
        """Normalized (artist,title) -> playCount for songs in the named playlist.

        One getPlaylist call. Used by decide's run-based expiry: a candidate the
        user never played must not be expired as "heard and rejected".
        """
        pid = next((p["id"] for p in self.playlists()
                    if p.get("playlistName") == name), None)
        if not pid:
            return {}
        data = self._call("getPlaylist", id=pid)
        counts: dict[str, int] = {}
        for e in data.get("playlist", {}).get("entry", []):
            key = _norm(e.get("artist")) + "|" + _norm(e.get("title"))
            counts[key] = max(counts.get(key, 0), int(e.get("playCount") or 0))
        return counts

    def replace_playlist(self, name: str, song_ids: list[str]) -> str | None:
        """Full content replacement via createPlaylist (idempotent).

        An existing playlist is updated in place (`createPlaylist
        playlistId=...` replaces its songs per the Subsonic API), so the
        playlist id — and anything pinned to it — survives across runs.
        Only a missing playlist is created fresh (and there a create failure
        cannot lose an existing playlist). Returns the server id, or None if
        the server does not report it — callers that need the id handle None.
        """
        existing = next((p for p in self.playlists()
                         if p.get("playlistName") == name), None)
        if existing:
            self._call("createPlaylist", playlistId=existing["id"],
                       **{"songId": list(song_ids)})
            return existing["id"]
        data = self._call("createPlaylist", name=name,
                          **{"songId": list(song_ids)})
        # Navidrome nests the created playlist under "playlist" (not "createPlaylist").
        created = (data.get("createPlaylist", {}).get("playlist")
                   or data.get("playlist") or {})
        if created.get("id"):
            return str(created["id"])
        for pl in self.playlists():  # servers returning an empty body: look up by name
            if pl.get("playlistName") == name:
                return pl["id"]
        return None

    # ---- Navidrome-native playlist cover art --------------------------------
    # Subsonic API has no way to set playlist artwork; Navidrome added a native
    # endpoint in 2026-03 (#5110). Needs a JWT from /auth/login, not subsonic auth.

    def navidrome_token(self) -> str:
        if not getattr(self, "_nd_token", None):
            import requests as _rq  # already a hard dependency; import is free
            r = _rq.post(f"{self.url}/auth/login",
                         json={"username": self.user, "password": self.password},
                         timeout=30)
            r.raise_for_status()
            self._nd_token = r.json()["token"]
        return self._nd_token

    def set_playlist_image(self, playlist_id: str, data: bytes,
                           media_type: str = "image/png") -> None:
        import requests as _rq
        r = _rq.post(
            f"{self.url}/api/playlist/{playlist_id}/image",
            files={"image": (f"cover.{media_type.split('/')[1]}", io.BytesIO(data), media_type)},
            headers={"X-ND-Authorization": f"Bearer {self.navidrome_token()}"},  # Navidrome strips a Bearer prefix
            timeout=30,
        )
        r.raise_for_status()
