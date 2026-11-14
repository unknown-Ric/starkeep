"""Audition playlist — rebuilt at the end of every run from downloaded candidates.

Replaces one fixed playlist's songs in place (the playlist id stays stable, so
players can pin it); starred/promoted/expired tracks drop out naturally because
the source is the set of candidates that are still `downloaded`.
"""

from __future__ import annotations

import logging

from pathlib import Path

import requests

from . import state as st
from .subsonic import SubsonicClient, _norm, _strip_feat

log = logging.getLogger(__name__)
AUDITION_NAME = "Starkeep Audition"  # default; override via config.playlist_name


def _song_id_for(sub: SubsonicClient, cand: dict) -> str | None:
    """Map a downloaded candidate to its server song id.

    Primary key: unique file basename (`Artist - Title.m4a` is our download
    naming; promoted copies are `NN. Title.m4a`, so basename rarely collides).
    Fallback: artist+title. Server paths are container-mapped/synthesized, so
    only basename — never full path — is compared.
    """
    basename = Path(cand.get("file_path") or "").name.casefold()
    by_basename = by_name = None
    fa, ft = _strip_feat(cand["artist"]), _strip_feat(cand["title"])
    for s in sub.search_songs(cand["artist"], cand["title"]):
        sa, ss = _norm(s.get("artist")), _norm(s.get("title"))
        if not ((sa == _norm(cand["artist"]) and ss == _norm(cand["title"]))
                or (_strip_feat(s.get("artist")) == fa and _strip_feat(s.get("title")) == ft)):
            continue
        by_name = by_name if by_name is not None else s["id"]
        if basename and Path(s.get("path") or "").name.casefold() == basename:
            by_basename = s["id"]
    return by_basename or by_name


def sync_audition(sub: SubsonicClient, stt: dict, icon_url: str = "",
                  name: str = AUDITION_NAME) -> dict:
    """Server playlist = exactly the tracks still awaiting a star decision.

    The playlist is updated in place each run (same server id), so the
    Navidrome cover is re-uploaded to the unchanged id (harmless no-op).
    """
    cands = [c for c in stt["candidates"] if c.get("status") == "downloaded"]
    song_ids: list[str] = []
    for c in cands:
        sid = _song_id_for(sub, c)
        if sid is None:
            log.info("audition playlist: no server song found for %s - %s",
                     c["artist"], c["title"])
            continue
        song_ids.append(sid)
    pl_id = sub.replace_playlist(name, song_ids)
    icon = {"icon": "skipped"}  # no id / no url / server without native API
    if icon_url and pl_id:
        icon = _sync_icon(sub, pl_id, icon_url)
    return {"playlist": name, "songs": len(song_ids),
            "candidates": len(cands), **icon}


def _sync_icon(sub: SubsonicClient, pl_id: str, icon_url: str) -> dict:
    try:
        r = requests.get(icon_url, timeout=30)
        r.raise_for_status()
        media_type = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if media_type == "image/jpg":
            media_type = "image/jpeg"
        data = r.content
        if media_type == "image/svg+xml":
            data = _svg_to_png(data)  # Navidrome's /api/playlist/image rejects SVG (400)
            media_type = "image/png"
        sub.set_playlist_image(pl_id, data, media_type)
        return {"icon": "uploaded"}
    except Exception as e:
        log.warning("playlist icon upload failed (%s): %s", icon_url, e)
        return {"icon": "failed"}


def _svg_to_png(svg: bytes) -> bytes:
    import cairosvg  # optional dep; SVG cover art is not first-class on navidrome
    return cairosvg.svg2png(bytestring=svg, output_width=512, output_height=512)


def sync_mix(sub: SubsonicClient, stt: dict, name: str = "Starkeep Mix",
             top_limit: int = 50) -> dict:
    """Library Mix: top-played library songs + this run's new downloads.

    Blends the user's most-played tracks with freshly downloaded candidates,
    creating a Spotify-style personal mix that evolves each run.
    """
    new_ids: list[str] = []
    seen: set[str] = set()
    for c in stt["candidates"]:
        if c.get("status") != "downloaded":
            continue
        sid = _song_id_for(sub, c)
        if sid and sid not in seen:
            seen.add(sid)
            new_ids.append(sid)

    top = sub.get_top_songs(count=top_limit)
    top_ids = [s["id"] for s in top if s["id"] not in seen]

    # New downloads first (fresh stuff at the top), then top-played
    song_ids = (new_ids + top_ids)[: top_limit + len(new_ids)]
    if not song_ids:
        log.info("mix playlist: nothing to write (no new + no top songs)")
        return {"playlist": name, "songs": 0, "new": 0, "top": 0}

    pl_id = sub.replace_playlist(name, song_ids)
    log.info("Library Mix updated: %d new + %d top = %d songs",
             len(new_ids), len(top_ids), len(song_ids))
    return {"playlist": name, "songs": len(song_ids),
            "new": len(new_ids), "top": len(top_ids)}
