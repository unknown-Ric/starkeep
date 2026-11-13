"""Qobuz download backend for starkeep.

Replaces yt-dlp with Qobuz FLAC downloads.
Auth: scrapes app_id + secrets from the Qobuz web bundle, uses the
X-User-Auth-Token from the environment (same token OctoFiesta uses).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import time
import unicodedata
from pathlib import Path

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://www.qobuz.com/api.json/0.2/"
LOGIN_PAGE = "https://play.qobuz.com/login"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:83.0) Gecko/20100101 Firefox/83.0"

# Format IDs (from OctoFiesta)
FORMAT_FLAC_24_HIGH = 27  # 24-bit >= 96 kHz
FORMAT_FLAC_24_LOW = 7    # 24-bit < 96 kHz
FORMAT_FLAC_16 = 6        # 16-bit 44.1 kHz
FORMAT_MP3_320 = 5        # 320 kbps MP3 (last resort)

# Preferred priority: best quality first
FORMAT_PRIORITY = [FORMAT_FLAC_24_HIGH, FORMAT_FLAC_24_LOW, FORMAT_FLAC_16, FORMAT_MP3_320]


_ENV_FILES = (
    Path.home() / ".starkeep" / ".env",
    Path.home() / "octofiesta" / ".env",
)


def _env_file_value(canonical: str) -> str | None:
    """Look up a key in the environment, then in the .env files.

    Key names match case-insensitively; both `KEY=value` and `key: value`
    forms are accepted (OctoFiesta's .env uses lowercase names).
    """
    val = os.environ.get(canonical, "").strip()
    if val:
        return val
    for env_path in _ENV_FILES:
        if not env_path.is_file():
            continue
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
            elif ":" in line:
                k, v = line.split(":", 1)
            else:
                continue
            if k.strip().lower() == canonical.lower():
                v = v.strip().strip('"').strip("'")
                if v:
                    return v
    return None


def _load_token() -> str | None:
    """Load Qobuz user auth token from env or .env files."""
    return _env_file_value("QOBUZ_USER_AUTH_TOKEN")


def _load_user_id() -> str | None:
    return _env_file_value("QOBUZ_USER_ID")


class QobuzAuth:
    """Scrape app_id and secrets from the Qobuz web bundle."""

    _BUNDLE_URL_RE = re.compile(
        r'<script src="(/resources/\d+\.\d+\.\d+-[a-z]\d{3}/bundle\.js)"></script>'
    )
    _APP_ID_RE = re.compile(
        r'production:\{api:\{appId:"(\d{9})",appSecret:"\w{32}"'
    )
    _SEED_TZ_RE = re.compile(
        r'[a-z]\.initialSeed\("([\w=]+)",window\.utimezone\.([a-z]+)\)',
        re.IGNORECASE,
    )

    def __init__(self) -> None:
        self._app_id: str | None = None
        self._secrets: list[str] | None = None
        self._token: str | None = _load_token()
        self._user_id: str | None = _load_user_id()

    @property
    def available(self) -> bool:
        return bool(self._token)

    @property
    def token(self) -> str:
        if not self._token:
            raise RuntimeError("QOBUZ_USER_AUTH_TOKEN not set")
        return self._token

    def _ensure(self) -> None:
        if self._app_id and self._secrets:
            return
        log.info("Scraping Qobuz bundle for app_id + secrets ...")
        resp = requests.get(LOGIN_PAGE, headers={"User-Agent": UA}, timeout=30)
        resp.raise_for_status()
        m = self._BUNDLE_URL_RE.search(resp.text)
        if not m:
            raise RuntimeError("Could not find bundle URL in Qobuz login page")
        bundle_url = "https://play.qobuz.com" + m.group(1)
        log.debug("Bundle URL: %s", bundle_url)
        resp = requests.get(bundle_url, headers={"User-Agent": UA}, timeout=60)
        resp.raise_for_status()
        js = resp.text

        m = self._APP_ID_RE.search(js)
        if not m:
            raise RuntimeError("Could not extract app_id from bundle")
        self._app_id = m.group(1)
        log.info("Qobuz app_id: %s", self._app_id)

        # Extract seed/timezone pairs
        tz_seeds: dict[str, list[str]] = {}
        for sm in self._SEED_TZ_RE.finditer(js):
            seed, tz = sm.group(1), sm.group(2).lower()
            tz_seeds.setdefault(tz, []).append(seed)
        if not tz_seeds:
            raise RuntimeError("No seed/timezone pairs found in bundle")

        # Reorder: move second key to first
        keys = list(tz_seeds.keys())
        if len(keys) > 1:
            second = keys[1]
            reordered = {second: tz_seeds.pop(second)}
            reordered.update(tz_seeds)
            tz_seeds = reordered

        # Extract info/extras per timezone
        tz_names = "|".join(k[0].upper() + k[1:] for k in tz_seeds)
        info_re = re.compile(
            rf'name:"\w+/(?P<tz>{tz_names})",info:"([\w=]+)",extras:"([\w=]+)"',
            re.IGNORECASE,
        )
        for im in info_re.finditer(js):
            tz = im.group("tz").lower()
            if tz in tz_seeds:
                tz_seeds[tz].append(im.group(2))
                tz_seeds[tz].append(im.group(3))

        # Decode secrets: concat, drop last 44, base64 decode
        self._secrets = []
        for tz, parts in tz_seeds.items():
            concatenated = "".join(parts)
            if len(concatenated) > 44:
                concatenated = concatenated[: len(concatenated) - 44]
            try:
                decoded = base64.b64decode(concatenated).decode("utf-8")
                self._secrets.append(decoded)
                log.debug("Secret for %s: %d chars", tz, len(decoded))
            except Exception as e:
                log.warning("Failed to decode secret for %s: %s", tz, e)

        if not self._secrets:
            raise RuntimeError("No secrets decoded from Qobuz bundle")
        log.info("Extracted %d secret(s)", len(self._secrets))

    @property
    def app_id(self) -> str:
        self._ensure()
        return self._app_id  # type: ignore

    @property
    def secrets(self) -> list[str]:
        self._ensure()
        return self._secrets  # type: ignore

    def _headers(self) -> dict[str, str]:
        h = {
            "User-Agent": UA,
            "X-App-Id": self.app_id,
        }
        if self._token:
            h["X-User-Auth-Token"] = self._token
        return h


def _norm(text: str | None) -> str:
    return " ".join(unicodedata.normalize("NFKC", text or "").casefold().split())


def _md5_sign(track_id: int, format_id: int, ts: int, secret: str) -> str:
    to_sign = f"trackgetFileUrlformat_id{format_id}intentstreamtrack_id{track_id}{ts}{secret}"
    return hashlib.md5(to_sign.encode("utf-8")).hexdigest()


class QobuzClient:
    """High-level Qobuz search + download."""

    def __init__(self) -> None:
        self.auth = QobuzAuth()
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": UA})

    def search_tracks(self, query: str, limit: int = 10) -> list[dict]:
        """Search Qobuz for tracks. Returns list of dicts with id, name, artist, album, duration."""
        self.auth._ensure()
        url = f"{BASE_URL}track/search"
        params = {
            "query": query,
            "limit": limit,
            "app_id": self.auth.app_id,
            "country_code": "AU",  # Australian library
        }
        r = self._session.get(url, params=params, headers=self.auth._headers(), timeout=30)
        r.raise_for_status()
        data = r.json()
        tracks = data.get("tracks", {}).get("items", [])
        results = []
        for t in tracks:
            performer = t.get("performer") or {}
            album = t.get("album") or {}
            results.append({
                "id": t["id"],
                "title": t.get("title") or t.get("name") or "",
                "artist": performer.get("name") or "",
                "album": album.get("title") or album.get("name") or "",
                "duration": t.get("duration", 0),  # seconds
                "track_number": t.get("track_number"),
                "is_downloadable": t.get("downloadable", False),
                "image": (album.get("image") or {}).get("large"),
            })
        return results

    def search_albums(self, query: str, limit: int = 10) -> list[dict]:
        """Search Qobuz albums. Returns [{id (base62), title, artist, image, tracks_count}].

        NB: the API also exposes a numeric `qobuz_id`, but album/get accepts
        the base62 `id` (verified 2026-11-14) and the album data comes back
        at the response TOP LEVEL, not nested under an "album" key.
        """
        self.auth._ensure()
        r = self._session.get(
            f"{BASE_URL}album/search",
            params={"query": query, "limit": limit,
                    "app_id": self.auth.app_id, "country_code": "AU"},
            headers=self.auth._headers(), timeout=30,
        )
        r.raise_for_status()
        items = r.json().get("albums", {}).get("items", [])
        results = []
        for a in items:
            img = a.get("image") or {}
            results.append({
                "id": a.get("id"),
                "title": a.get("title") or "",
                "artist": (a.get("artist") or {}).get("name") or "",
                "image": img.get("large"),
                "tracks_count": a.get("tracks_count", 0),
            })
        return results

    def get_album(self, album_id: str) -> dict:
        """Full album with track list. `album_id` is the base62 id from search.

        Response is the album object at the top level:
        {id, title, artist:{name}, image:{large}, tracks:{items:[{id,title,
        track_number,duration,downloadable}]}}
        """
        self.auth._ensure()
        r = self._session.get(
            f"{BASE_URL}album/get",
            params={"album_id": album_id, "app_id": self.auth.app_id,
                    "country_code": "AU"},
            headers=self.auth._headers(), timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def get_download_url(self, track_id: int) -> dict:
        """Get a signed download URL for a track. Tries best quality first.

        Returns dict with keys: url, mime_type, format_id, bit_depth, sampling_rate, is_sample.
        """
        self.auth._ensure()
        last_exc: Exception | None = None
        for secret in self.auth.secrets:
            for fmt in FORMAT_PRIORITY:
                try:
                    ts = int(time.time())
                    sig = _md5_sign(track_id, fmt, ts, secret)
                    url = (
                        f"{BASE_URL}track/getFileUrl"
                        f"?format_id={fmt}&intent=stream"
                        f"&request_ts={ts}&track_id={track_id}"
                        f"&request_sig={sig}"
                    )
                    r = self._session.get(
                        url, headers=self.auth._headers(), timeout=30
                    )
                    r.raise_for_status()
                    data = r.json()
                    dl_url = data.get("url")
                    if not dl_url:
                        raise ValueError("No download URL in response")
                    is_sample = data.get("sample", False)
                    sr = data.get("sampling_rate", 0)
                    if sr == 0:
                        is_sample = True
                    mime = data.get("mime_type", "")
                    result = {
                        "url": dl_url,
                        "mime_type": mime,
                        "format_id": fmt,
                        "bit_depth": data.get("bit_depth", 16),
                        "sampling_rate": sr,
                        "is_sample": is_sample,
                    }
                    # Check for downgrade
                    for restr in data.get("restrictions", []):
                        if restr.get("code") == "FormatRestrictedByFormatAvailability":
                            result["downgraded"] = True
                            break
                    return result
                except Exception as e:
                    last_exc = e
                    log.debug(
                        "getFileUrl failed (fmt=%d): %s", fmt, e
                    )
        raise RuntimeError(f"Failed to get Qobuz download URL for track {track_id}: {last_exc}")

    def download_file(self, url: str, dest: Path) -> Path:
        """Stream a download URL to a local file."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._session.get(url, stream=True, timeout=300) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
        return dest


def _pick_best_album(albums: list[dict], query: str) -> dict | None:
    """Score albums against 'Artist - Album' (or free text) query."""
    nq = _norm(query)
    n_artist = n_title = None
    if " - " in query:
        a, t = query.split(" - ", 1)
        n_artist, n_title = _norm(a), _norm(t)
    best, best_score = None, -1
    for a in albums:
        score = 0
        na, nt = _norm(a["artist"]), _norm(a["title"])
        if n_artist and n_title:
            if na == n_artist and nt == n_title:
                score = 100
            elif na == n_artist:
                score = 60
            elif nt == n_title:
                score = 40
            elif nq == _norm(f"{a['artist']} - {a['title']}"):
                score = 90
        elif _norm(f"{a['artist']} {a['title']}") == nq:
            score = 90
        if score > best_score:
            best, best_score = a, score
    if best is None or best_score < 40:
        return None
    return best


def _tag_flac(path: Path, artist: str, title: str, album: str,
              track_number: int | None, cover_url: str | None = None) -> None:
    """Write basic tags (+ cover for FLAC). Best-effort: never fails the download."""
    try:
        from mutagen import File as MutagenFile
        m = MutagenFile(str(path), easy=True)
        if m is None:
            return
        m["artist"] = artist
        m["albumartist"] = artist
        m["title"] = title
        m["album"] = album
        if track_number:
            m["tracknumber"] = str(track_number)
        if path.suffix.lower() == ".flac" and cover_url:
            from mutagen.flac import FLAC, Picture
            r = requests.get(cover_url, timeout=30)
            r.raise_for_status()
            pic = Picture()
            pic.type = 3  # cover front
            pic.mime = r.headers.get("Content-Type", "image/jpeg").split(";")[0]
            pic.desc = "cover"
            pic.data = r.content
            m["picture"] = [pic]
        m.save()
    except Exception as e:
        log.warning("tagging failed for %s: %s", path.name, e)


def download_album(query: str, library_dir: str) -> list[Path]:
    """Manual whole-album pull (user-triggered): Qobuz search -> album/get ->
    every track to <library_dir>/<Artist>/<Album>/NN. Title.<ext>.

    Idempotent: existing destination files are skipped. Returns the list of
    files actually downloaded this call (empty if the album was already there).
    """
    client = QobuzClient()
    albums = client.search_albums(query, limit=15)
    if not albums:
        raise RuntimeError(f"No Qobuz album results for '{query}'")
    album = _pick_best_album(albums, query)
    if not album:
        raise RuntimeError(
            f"No good album match for '{query}' (best: {albums[0]['artist']} - {albums[0]['title']})"
        )
    log.info("Album match: %s - %s (%d tracks)", album["artist"], album["title"],
             album["tracks_count"])
    full = client.get_album(album["id"])
    tracks = full.get("tracks", {}).get("items", [])
    if not tracks:
        raise RuntimeError(f"album/get returned no tracks for album {album['id']}")
    artist = full.get("artist", {}).get("name") or album["artist"]
    album_title = full.get("title") or album["title"]

    artist_s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", artist).strip()[:80]
    album_s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", album_title).strip()[:80]
    album_dir = Path(library_dir).expanduser() / artist_s / album_s
    album_dir.mkdir(parents=True, exist_ok=True)

    downloaded: list[Path] = []
    for t in tracks:
        title = t.get("title") or ""
        tno = t.get("track_number") or 0
        ext_guess = ".flac"
        stem = f"{tno:02d}. {re.sub(r'[<>:\"/\\\\|?*\x00-\x1f]', '_', title).strip()}" \
            if tno else title
        dest = album_dir / f"{stem}{ext_guess}"
        # idempotency: allow either flac/mp3 already present for this stem
        if dest.exists() or (album_dir / f"{stem}.mp3").exists():
            log.info("skip (exists): %s/%s", album_s, dest.name)
            continue
        dl = client.get_download_url(t["id"])
        if dl["is_sample"]:
            raise RuntimeError(f"only a sample available for {artist} - {title}")
        ext = ".flac" if "flac" in dl.get("mime_type", "") else ".mp3"
        dest = album_dir / f"{stem}{ext}"
        client.download_file(dl["url"], dest)
        _tag_flac(dest, artist, title, album_title, tno, album.get("image"))
        downloaded.append(dest)
        log.info("downloaded: %s (%.1f KB, %s/%s)", dest.name,
                 dest.stat().st_size / 1024,
                 dl.get("bit_depth"), dl.get("sampling_rate"))
    return downloaded


def qobuz_download(
    artist: str,
    title: str,
    incoming_dir: str,
    preferred_quality: str | None = None,
) -> tuple[Path, int, dict]:
    """Search Qobuz and download a single track.

    Returns (file_path, qobuz_track_id, track_info).
    Raises on failure (caller handles strikes).
    """
    client = QobuzClient()

    # Search
    query = f"{title} {artist}"
    log.info("Qobuz search: %s", query)
    tracks = client.search_tracks(query, limit=15)
    if not tracks:
        raise RuntimeError(f"No Qobuz results for '{artist} - {title}'")

    # Pick best match: exact artist+title (normalized), playable, prefer non-remix
    na, nt = _norm(artist), _norm(title)
    best = None
    best_score = -1
    for t in tracks:
        score = 0
        ta, tt = _norm(t["artist"]), _norm(t["title"])
        # exact artist+title = best
        if ta == na and tt == nt:
            score = 100
        elif ta == na:
            score = 60
        elif tt == nt:
            score = 40
        # prefer downloadable
        if t.get("is_downloadable"):
            score += 10
        # penalise remixes/lives
        combined = f"{t['title']} {t['artist']}".lower()
        if any(w in combined for w in ("remix", "live", "cover", "karaoke", "demo")):
            score -= 50
        if score > best_score:
            best_score = score
            best = t
    if not best or best_score < 40:
        raise RuntimeError(
            f"No good Qobuz match for '{artist} - {title}' "
            f"(best: {best['artist'] if best else 'N/A'} - {best['title'] if best else 'N/A'}, score={best_score})"
        )

    log.info("Qobuz match: %s - %s (track %d, album: %s)",
             best["artist"], best["title"], best["id"], best.get("album", "?"))

    # Get download URL
    dl = client.get_download_url(best["id"])
    if dl["is_sample"]:
        raise RuntimeError(f"Qobuz only has a sample for {artist} - {title}")

    ext = ".flac" if "flac" in dl.get("mime_type", "") else ".mp3"
    safe_artist = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", artist).strip()[:80]
    safe_title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip()[:80]
    stem = f"{safe_artist} - {safe_title}"
    dest = Path(incoming_dir) / f"{stem}{ext}"

    # Avoid overwrite
    if dest.exists():
        dest = dest.with_stem(stem + "_q")

    log.info("Downloading: %s -> %s (%s, %s)",
             best["title"], dest.name,
             dl.get("bit_depth", "?"), dl.get("sampling_rate", "?"))
    client.download_file(dl["url"], dest)
    log.info("Downloaded: %s (%.1f KB)", dest.name, dest.stat().st_size / 1024)

    return dest, best["id"], best
