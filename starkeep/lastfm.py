"""Last.fm API wrapper (only what the pipeline needs)."""

from __future__ import annotations

import datetime as dt
import logging
import requests

log = logging.getLogger(__name__)
API = "https://ws.audioscrobbler.com/2.0/"


class LastfmClient:
    def __init__(self, api_key: str, user: str) -> None:
        if not api_key:
            raise ValueError("Last.fm API key is not set")
        self.key = api_key
        self.user = user

    def _call(self, method: str, **params: str) -> dict:
        params.update({"method": method, "api_key": self.key, "format": "json"})
        r = requests.get(API, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise RuntimeError(f"Last.fm {method} error {data['error']}: {data.get('message')}")
        return data

    # ---- scrobble history --------------------------------------------------

    def recent_track_seeds(self, days: int = 7, limit: int = 100) -> list[tuple[str, str]]:
        """Most-heard *distinct* (artist, title) pairs over the last N days.

        Paginates the whole window so `harvest_days` really reaches back N days
        (Last.fm names the parameter `from`; page size maxes out at 200).
        """
        from_ts = int((dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).timestamp())
        counts: dict[tuple[str, str], int] = {}
        page = 1
        while True:
            data = self._call(
                "user.getRecentTracks", user=self.user, limit="200",
                page=str(page), extended="0", **{"from": str(from_ts)},
            )
            recent = data.get("recenttracks", {})
            for t in recent.get("track", []):
                if t.get("@attr", {}).get("nowplaying") == "true":
                    continue
                artist = (t.get("artist") or {}).get("#text") if isinstance(t.get("artist"), dict) else t.get("artist")
                title = t.get("name")
                if not artist or not title:
                    continue
                counts[(artist, title)] = counts.get((artist, title), 0) + 1
            total_pages = int(recent.get("@attr", {}).get("totalPages") or 1)
            if page >= total_pages or len(counts) >= limit:
                break
            page += 1
        ranked = [k for k, _ in sorted(counts.items(), key=lambda kv: -kv[1])]
        return ranked[:limit]

    def similar_tracks(self, artist: str, title: str, limit: int = 3) -> list[tuple[str, str]]:
        try:
            data = self._call("track.getSimilar", artist=artist, track=title, limit=str(limit))
            ts = data.get("similartracks", {}).get("track", [])
            return [(_fmt(t["artist"]), t["name"]) for t in ts]
        except Exception as e:  # recommendations must not be fatal
            log.warning("track.getSimilar failed for %s - %s: %s", artist, title, e)
            return []

    def similar_artists(self, artist: str, limit: int = 2) -> list[str]:
        try:
            data = self._call("artist.getSimilar", artist=artist, limit=str(limit))
            return [_fmt(a["name"]) for a in data.get("similarartists", {}).get("artist", [])]
        except Exception as e:
            log.warning("artist.getSimilar failed for %s: %s", artist, e)
            return []

    def artist_top_tracks(self, artist: str, limit: int = 3) -> list[tuple[str, str]]:
        try:
            data = self._call("artist.getTopTracks", artist=artist, limit=str(limit))
            ts = data.get("toptracks", {}).get("track", [])
            return [(_fmt(t.get("artist")), t["name"]) for t in ts if t.get("name")]
        except Exception as e:
            log.warning("artist.getTopTracks failed for %s: %s", artist, e)
            return []

    def track_album_info(self, artist: str, title: str) -> dict | None:
        """Album enrichment at promotion time: {'album': ..., 'track_number': ...}."""
        try:
            data = self._call("track.getInfo", artist=artist, track=title, username=self.user)
            info = data.get("track") or {}
            album = info.get("album")
            out: dict = {}
            if album and album.get("title"):
                out["album"] = album["title"]
            if album and album.get("@attr", {}).get("position"):
                out["track_number"] = int(album["@attr"]["position"])
            if info.get("duration"):  # milliseconds -> seconds
                out["duration"] = int(info["duration"]) // 1000
            for size in ("mega", "extralarge"):
                img = next((i.get("#text") for i in album.get("image") or []
                            if isinstance(i, dict) and i.get("size") == size and i.get("#text")), None)
                if img:
                    out["image"] = img
                    break
            return out or None
        except Exception as e:
            log.warning("track.getInfo failed for %s - %s: %s", artist, title, e)
            return None


def _fmt(o: object) -> str:
    """Last.fm returns artist as {#text: ...} in scrobbles but {name: ...} elsewhere."""
    if isinstance(o, dict):
        return o.get("#text") or o.get("name") or ""
    return str(o)
