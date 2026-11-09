"""Audition playlist — rebuilt at the end of every run from downloaded candidates.

Replaces (delete + create) one fixed playlist so any player can pin it by name;
starred/promoted/expired tracks drop out naturally because the source is the
set of candidates that are still `downloaded`.
"""

from __future__ import annotations

import logging

from pathlib import Path

import requests

from . import state as st
from .subsonic import SubsonicClient, _norm

log = logging.getLogger(__name__)
AUDITION_NAME = "Starkeep Audition"  # hardcoded until somebody asks for config


def _song_id_for(sub: SubsonicClient, cand: dict) -> str | None:
    """Map a downloaded candidate to its server song id.

    Primary key: unique file basename (`Artist - Title.m4a` is our download
    naming; promoted copies are `NN. Title.m4a`, so basename rarely collides).
    Fallback: artist+title. Server paths are container-mapped/synthesized, so
    only basename — never full path — is compared.
    """
    basename = Path(cand.get("file_path") or "").name.casefold()
    by_basename = by_name = None
    for s in sub.search_songs(cand["artist"], cand["title"]):
        if _norm(s.get("artist")) != _norm(cand["artist"]) \
                or _norm(s.get("title")) != _norm(cand["title"]):
            continue
        by_name = by_name if by_name is not None else s["id"]
        if basename and Path(s.get("path") or "").name.casefold() == basename:
            by_basename = s["id"]
    return by_basename or by_name


def sync_audition(sub: SubsonicClient, stt: dict, icon_url: str = "") -> dict:
    """Server playlist = exactly the tracks still awaiting a star decision.

    The playlist is deleted + recreated each run (server id changes), so a
    custom Navidrome cover is re-uploaded to the new id right after.
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
    pl_id = sub.replace_playlist(AUDITION_NAME, song_ids)
    icon = {"icon": "skipped"}  # no id / no url / server without native API
    if icon_url and pl_id:
        icon = _sync_icon(sub, pl_id, icon_url)
    return {"playlist": AUDITION_NAME, "songs": len(song_ids),
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
