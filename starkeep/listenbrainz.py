"""ListenBrainz client — weekly algorithm playlists (Exploration / Jams).

Used as a second discovery signal alongside Last.fm. Endpoints (no API token
needed for these public playlist reads; User-Agent with contact info is
required by the LB API):

  GET /1/user/{username}/playlists/createdfor  -> playlists LB generated for the user
  GET /1/playlist/{playlist_id}                -> tracks of one playlist

Track credit resolution follows the JSPF extension
(``additional_metadata.artists[].artist_credit_name``) so multi-artist
tracks keep the full credit instead of just the first artist.
"""

from __future__ import annotations

import datetime as dt
import logging
import time

import requests

log = logging.getLogger(__name__)

API_BASE = "https://api.listenbrainz.org"
USER_AGENT = "starkeep/1.0 (music discovery bot)"

# source_patch values for the algorithm playlists we want
WEEKLY_SOURCES = ("weekly-exploration", "weekly-jams")

_MAX_RETRIES = 5


class ListenbrainzClient:
    def __init__(self, username: str) -> None:
        if not username:
            raise ValueError("listenbrainz username is not set")
        self.username = username
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT})

    def _get(self, path: str, **params) -> dict:
        for attempt in range(1, _MAX_RETRIES + 1):
            r = self._session.get(API_BASE + path, params=params, timeout=30)
            if r.status_code == 429:
                wait = 2 * attempt
                log.warning("ListenBrainz 429, backing off %ss (attempt %d)", wait, attempt)
                time.sleep(wait)
                continue
            if r.status_code == 404 and "Cannot find user" in r.text:
                raise RuntimeError(f"ListenBrainz user not found: {self.username}")
            r.raise_for_status()
            return r.json()
        raise RuntimeError("ListenBrainz still rate-limited after retries")

    def weekly_playlists(self) -> list[dict]:
        """Playlists LB has generated for this user, with id/name/date/type."""
        data = self._get(f"/1/user/{self.username}/playlists/createdfor", count=20)
        out = []
        for item in data.get("playlists", []):
            p = item.get("playlist") or {}
            ext = (p.get("extension") or {}).get("https://musicbrainz.org/doc/jspf#playlist") or {}
            algo = (ext.get("additional_metadata") or {}).get("algorithm_metadata", {}).get("source_patch", "")
            if algo not in WEEKLY_SOURCES:
                continue
            identifier = p.get("identifier") or ""
            out.append({
                "id": identifier.rstrip("/").split("/")[-1],
                "name": p.get("name", ""),
                "date": p.get("date", ""),
                "type": algo,
            })
        return out

    @staticmethod
    def _playlist_week(playlist_date: str) -> int | None:
        try:
            d = dt.datetime.fromisoformat(playlist_date.replace("Z", "+00:00"))
            return d.isocalendar()[1]
        except ValueError:
            return None

    def weekly_candidates(self, tracks_per_playlist: int = 10) -> list[dict]:
        """(artist, title) candidates from the *current* weekly playlists.

        Returns a list of {artist, title, source} dicts, exploration first.
        """
        now_week = dt.datetime.now(dt.timezone.utc).isocalendar()[1]
        pls = self.weekly_playlists()
        candidates: list[dict] = []
        for pl in sorted(pls, key=lambda p: 0 if p["type"] == "weekly-exploration" else 1):
            week = self._playlist_week(pl["date"])
            if week is not None and week != now_week:
                log.debug("skipping stale %s playlist (week %d, now %d)", pl["type"], week, now_week)
                continue
            for t in self.playlist_tracks(pl["id"], limit=tracks_per_playlist):
                candidates.append({**t, "source": pl["type"]})
        return candidates

    def playlist_tracks(self, playlist_id: str, limit: int = 50) -> list[dict]:
        """Track list of one LB playlist: {artist, title} with full credits."""
        data = self._get(f"/1/playlist/{playlist_id}", count=limit)
        tracks = []
        for t in data.get("playlist", {}).get("track", []):
            title = t.get("title") or ""
            artist = t.get("creator") or ""
            ext = (t.get("extension") or {}).get("https://musicbrainz.org/doc/jspf#track") or {}
            artists = (ext.get("additional_metadata") or {}).get("artists") or []
            if len(artists) > 1:
                names = [a.get("artist_credit_name", "") for a in artists if a.get("artist_credit_name")]
                if names:
                    artist = ", ".join(names)
            if title and artist:
                tracks.append({"artist": artist, "title": title})
        return tracks
