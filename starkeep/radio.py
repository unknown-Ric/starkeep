"""3. RADIO — "related music radio".

Seed a track ("Artist - Title"). Discover related songs via Last.fm
(track.getSimilar + artist.getSimilar + artist.getTopTracks), fetch the
ones you don't already own from Qobuz as FLAC into the library, rescan
Navidrome, and build a shuffled-ready Navidrome playlist you can play on
loop in your phone client to keep the music going.

This is the on-demand counterpart to the weekly pipeline: same sources
(Last.fm for similarity, Qobuz for audio, Navidrome for the playlist), but
driven by a single seed instead of a week of scrobbles.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import requests

from . import qobuz as qobuz_mod
from .config import Config
from .lastfm import LastfmClient
from .subsonic import SubsonicClient

log = logging.getLogger(__name__)

_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _norm(text: str | None) -> str:
    return " ".join((text or "").casefold().split())


def _safe(text: str | None) -> str:
    return _BAD_CHARS.sub("_", (text or "")).strip(" .")[:120]


def _parse_seed(seed: str) -> tuple[str, str]:
    """'Artist - Title' -> (artist, title). No ' - ' -> artist empty, title = seed."""
    seed = seed.strip()
    if " - " in seed:
        a, t = seed.split(" - ", 1)
        return a.strip(), t.strip()
    return "", seed


def _resolve_artist(client: "qobuz_mod.QobuzClient", artist: str, title: str) -> str:
    """If the seed had no 'Artist - ' prefix, infer the artist from a Qobuz search."""
    if artist:
        return artist
    try:
        hits = client.search_tracks(title, limit=10)
        if hits:
            # best = first non-empty performer (Qobuz ranks relevance)
            for h in hits:
                if h.get("artist"):
                    log.info("resolved seed artist from Qobuz: %s", h["artist"])
                    return h["artist"]
    except Exception as e:
        log.warning("artist resolution failed for %r: %s", title, e)
    return ""


def discover_related(
    lfm: LastfmClient,
    artist: str,
    title: str,
    similar_limit: int = 12,
    similar_artists: int = 5,
    top_per_artist: int = 2,
) -> list[dict]:
    """Ordered (artist, title, source) candidates related to the seed.

    Order = a decent radio flow: closest similar tracks first, then other
    tracks from the seed artist, then top tracks from similar artists.
    The seed itself is excluded; duplicates (normalized) are dropped.
    """
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    seed_key = (_norm(artist), _norm(title))

    def add(a: str, t: str, src: str) -> None:
        if not a or not t:
            return
        key = (_norm(a), _norm(t))
        if key in seen or key == seed_key:
            return
        seen.add(key)
        out.append({"artist": a, "title": t, "source": src})

    # 1) closest: similar *tracks* to the seed song
    for a, t in lfm.similar_tracks(artist, title, limit=similar_limit):
        add(a, t, "similar-track")

    # 2) more from the same artist
    for a, t in lfm.artist_top_tracks(artist, limit=3):
        add(a, t, "seed-artist")

    # 3) top tracks from similar artists
    for sa in lfm.similar_artists(artist, limit=similar_artists):
        for a, t in lfm.artist_top_tracks(sa, limit=top_per_artist):
            add(a, t, "similar-artist")

    return out


def _qobuz_download_to_library(
    client: "qobuz_mod.QobuzClient", artist: str, title: str, library_dir: str
) -> Path | None:
    """Search Qobuz, pick the best match, download a single track into
    <library_dir>/<Artist>/<Album>/<Title>.<ext> with tags.

    Returns the file path (existing or freshly downloaded), or None if no
    acceptable match. Raises on download/sample-only failure.
    """
    tracks = client.search_tracks(f"{title} {artist}", limit=15)
    if not tracks:
        raise RuntimeError(f"No Qobuz results for '{artist} - {title}'")

    na, nt = _norm(artist), _norm(title)
    ranked = []
    for t in tracks:
        score = 0
        ta, tt = _norm(t["artist"]), _norm(t["title"])
        if ta == na and tt == nt:
            score = 100
        elif ta == na:
            score = 60
        elif tt == nt:
            score = 40
        if t.get("is_downloadable"):
            score += 10
        combined = f"{t['title']} {t['artist']}".lower()
        if any(w in combined for w in ("remix", "live", "cover", "karaoke", "demo")):
            score -= 50
        ranked.append((score, t))
    ranked.sort(key=lambda st: st[0], reverse=True)

    # Try the top matches in rank order. A single track's download URL can be
    # transiently broken (Qobuz CDN 403 / malformed HTTP response), and the
    # top hit may be sample-only — both are recoverable via the next match.
    last_err: Exception | None = None
    sample_only = False
    for score, best in ranked[:5]:
        if score < 40:
            break
        try:
            dl = client.get_download_url(best["id"])
        except Exception as e:
            last_err = e
            log.warning("radio: get_download_url failed for %s - %s (track %s): %s",
                        best["artist"], best["title"], best["id"], e)
            continue
        if dl["is_sample"]:
            sample_only = True
            log.warning("radio: only a sample for %s - %s (track %s); trying next match",
                        best["artist"], best["title"], best["id"])
            continue

        ext = ".flac" if "flac" in dl.get("mime_type", "") else ".mp3"
        artist_s = _safe(best["artist"]) or _safe(artist)
        album_s = _safe(best.get("album") or "Singles")
        title_s = _safe(best["title"])
        album_dir = Path(library_dir) / artist_s / album_s
        album_dir.mkdir(parents=True, exist_ok=True)

        dest = album_dir / f"{title_s}{ext}"
        # Album-mode files are named "NN - Title.ext" — match on the title
        # portion so a track already in the album folder is never downloaded
        # twice.
        existing = [p for p in album_dir.glob(f"*{title_s}*.flac")] or \
                   [p for p in album_dir.glob(f"*{title_s}*.mp3")]
        if existing:
            log.info("already present in album folder, skipping: %s", existing[0].name)
            return existing[0]
        try:
            client.download_file(dl["url"], dest)
        except Exception as e:
            last_err = e
            log.warning("radio: download failed for %s - %s (track %s): %s",
                        best["artist"], best["title"], best["id"], e)
            continue
        qobuz_mod._tag_flac(dest, best["artist"], best["title"],
                            best.get("album") or "", best.get("track_number"),
                            best.get("image"))
        log.info("radio download: %s - %s -> %s (%s/%s)",
                 best["artist"], best["title"], dest.name,
                 dl.get("bit_depth"), dl.get("sampling_rate"))
        return dest

    if last_err is not None:
        raise last_err
    if sample_only:
        raise RuntimeError(f"Qobuz only has a sample for {artist} - {title}")
    log.warning("no good Qobuz match for %s - %s", artist, title)
    return None


def run_radio(cfg: Config, seed: str, count: int = 15,
              playlist: str | None = None) -> dict:
    """Full radio build. Returns a JSON-serialisable report."""
    artist, title = _parse_seed(seed)
    qb = qobuz_mod.QobuzClient()
    if not artist:
        artist = _resolve_artist(qb, artist, title)
        if not artist:
            raise RuntimeError(
                f"could not resolve an artist for seed {seed!r} — "
                "pass it as 'Artist - Title'")

    sub = SubsonicClient(cfg.subsonic_url, cfg.subsonic_user, cfg.subsonic_password)
    lfm = LastfmClient(cfg.lastfm_api_key, cfg.lastfm_user)

    # 1. discover
    candidates = discover_related(lfm, artist, title)
    to_fetch = candidates[:count]
    log.info("radio: %d related candidates (fetching up to %d)",
             len(candidates), len(to_fetch))

    # 2. download the ones we don't already own, straight into the library
    downloaded: list[dict] = []
    already_owned: list[dict] = []
    failed: list[dict] = []
    for cand in to_fetch:
        a, t = cand["artist"], cand["title"]
        try:
            if sub.song_exists(a, t):
                already_owned.append(cand)
                continue
            path = _qobuz_download_to_library(qb, a, t, cfg.library_dir)
            if path is None:
                failed.append({**cand, "error": "no Qobuz match"})
            else:
                downloaded.append({**cand, "file": str(path)})
        except Exception as e:
            log.error("radio download failed for %s - %s: %s", a, t, e)
            failed.append({**cand, "error": str(e)[:200]})

    # 3. rescan so Navidrome indexes the fresh files, then resolve song ids
    sub.wait_for_scan(timeout=240)

    # 4. build the playlist: seed first, then every candidate we have a local id for
    song_ids: list[str] = []

    def _add_ids(a: str, t: str) -> None:
        for hit in sub.search_songs(a, t, limit=5):
            sid = hit.get("id")
            if sid and sid not in song_ids:
                song_ids.append(sid)
                return

    _add_ids(artist, title)  # the seed
    for cand in to_fetch:
        _add_ids(cand["artist"], cand["title"])

    name = playlist or f"Radio: {artist} - {title}"
    playlist_id = sub.replace_playlist(name, song_ids) if song_ids else None

    # 5. best-effort playlist cover from the seed's album art
    if playlist_id:
        try:
            info = lfm.track_album_info(artist, title) or {}
            img_url = info.get("image")
            if img_url:
                img = requests.get(img_url, timeout=30)
                img.raise_for_status()
                media_type = img.headers.get("Content-Type", "image/jpeg").split(";")[0]
                sub.set_playlist_image(playlist_id, img.content, media_type)
        except Exception as e:
            log.warning("radio playlist cover failed: %s", e)

    return {
        "seed": {"artist": artist, "title": title},
        "playlist": name,
        "playlist_id": playlist_id,
        "tracks_in_playlist": len(song_ids),
        "downloaded": downloaded,
        "already_owned": already_owned,
        "failed": failed,
        "total_candidates": len(candidates),
    }
