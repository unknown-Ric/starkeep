"""Audition playlist — rebuilt at the end of every run from downloaded candidates.

Replaces (delete + create) one fixed playlist so any player can pin it by name;
starred/promoted/expired tracks drop out naturally because the source is the
set of candidates that are still `downloaded`.
"""

from __future__ import annotations

import logging

from pathlib import Path

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


def sync_audition(sub: SubsonicClient, stt: dict) -> dict:
    """Server playlist = exactly the tracks still awaiting a star decision."""
    cands = [c for c in stt["candidates"] if c.get("status") == "downloaded"]
    song_ids: list[str] = []
    for c in cands:
        sid = _song_id_for(sub, c)
        if sid is None:
            log.info("audition playlist: no server song found for %s - %s",
                     c["artist"], c["title"])
            continue
        song_ids.append(sid)
    sub.replace_playlist(AUDITION_NAME, song_ids)
    return {"playlist": AUDITION_NAME, "songs": len(song_ids), "candidates": len(cands)}
