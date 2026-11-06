"""3. DECIDE — starred keeps, expired orphans, untouched young tracks."""

from __future__ import annotations

import logging
import mutagen
import mutagen.flac
from pathlib import Path
from shutil import move

from .config import Config
from .lastfm import LastfmClient
from . import state as st
from .subsonic import SubsonicClient

log = logging.getLogger(__name__)


def promote_file(cfg: Config, src: Path, artist: str, title: str,
                 lfm: LastfmClient, stt: dict) -> str:
    """Enrich tags (Last.fm album/track#), file into <Artist>/<Album>/NN. Title.

    Reads existing tags first; Last.fm is only used to fill gaps and to repair
    bogus yt-dlp channel-name artist tags. Falls back to <Artist>/Singles/.
    """
    info = lfm.track_album_info(artist, title) or {}
    album = info.get("album")
    track_no = info.get("track_number")
    m = mutagen.File(src, easy=True)
    if m is not None:
        if not m.get("artist"):
            m["artist"] = artist
        if not m.get("title"):
            m["title"] = title
        if album:
            m["album"] = album
        m["tracknumber"] = str(track_no) if track_no else m.get("tracknumber") or ""
        m.save()
    target_dir = Path(cfg.library_dir) / artist / (album or "Singles")
    target_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{track_no:02d}. {title}" if track_no else f"{artist} - {title}"
    dst = target_dir / f"{stem}{src.suffix}"
    move(src, dst)  # overwriting keeps would be a danger; names should be unique per album
    stt.setdefault("moved", []).append({"from": str(src), "to": str(dst), "at": st.utcnow()})
    return str(dst)


def delete_expired(cfg: Config, stt: dict, expiry_days: int,
                   starred_paths: set[str]) -> list[str]:
    """Delete orphan candidates older than expiry_days, unless starred.

    SAFETY: only candidates whose file_path is recorded in state are eligible
    (never gar-glob-delete). Paths are re-verified to live under incoming_dir.
    """
    incoming_root = Path(cfg.incoming_dir).resolve()
    deleted = []
    for c in stt["candidates"]:
        if c["status"] != "downloaded":
            continue
        if st.days_since(c.get("downloaded_at") or c.get("added_at")) < expiry_days:
            continue  # young & unstarred -> rolls over to next week
        p = c.get("file_path") and Path(c["file_path"])
        if not p or not p.exists():
            continue
        if p.resolve().is_relative_to(incoming_root) is False:
            log.error("refusing to delete %s: not under incoming_dir", p)
            continue
        if c["file_path"] in starred_paths:
            continue
        p.unlink(missing_ok=True)
        c["status"] = "expired"
        deleted.append(c["file_path"])
        stt["deleted"].append({"path": c["file_path"], "at": st.utcnow()})
    if deleted:
        st.save(cfg.state_file, stt)
    return deleted


def decide(cfg: Config, sub: SubsonicClient, lfm: LastfmClient, stt: dict,
           dry_run: bool = False) -> dict:
    """The keep/expire pass: read stars from Subsonic, act on incoming files."""
    starred = sub.starred_songs()
    starred_paths = {s["path"] for s in starred}

    keeps, deletes = [], []
    incoming_root = Path(cfg.incoming_dir).resolve()
    for c in stt["candidates"]:
        if c["status"] != "downloaded":
            continue
        absolute = str(Path(c["file_path"]).resolve()) if c["file_path"] else None
        is_starred = any(
            (s["artist"] or "").lower() == c["artist"].lower()
            and (s["title"] or "").lower() == c["title"].lower()
            for s in starred
        ) or (absolute and absolute in starred_paths)
        if is_starred:
            src = Path(resolve_file(c))
            if src and src.is_file() and src.resolve().is_relative_to(incoming_root):
                keeps.append((c, src))
            else:
                log.warning("starred but file unusable: %s - %s", c["artist"], c["title"])
        elif st.days_since(c.get("downloaded_at") or c.get("added_at")) >= cfg.expiry_days:
            deletes.append((c, Path(c["file_path"])))
        # else: young unstarred -> untouched, rolls over

    if dry_run:
        return {
            "keeps": [{"artist": c["artist"], "title": c["title"], "from": str(src)} for c, src in keeps],
            "deletes": [{"artist": c["artist"], "title": c["title"], "path": c["file_path"]} for c, _ in deletes],
            "dry_run": True,
        }

    promoted = []
    for c, src in keeps:
        dst = promote_file(cfg, src, c["artist"], c["title"], lfm, stt)
        c["status"] = "promoted"
        log.info("promoted %s - %s -> %s", c["artist"], c["title"], dst)
        promoted.append(dst)

    deleted_paths = []
    for c, _ in deletes:
        # only delete files that are really coming from us + exist
        p = Path(c["file_path"])
        if p.is_file() and p.resolve().is_relative_to(incoming_root):
            if c["file_path"] in starred_paths:
                continue  # starred at the last moment; give it a promotion cycle
            p.unlink(missing_ok=True)
            c["status"] = "expired"
            stt["deleted"].append({"path": c["file_path"], "at": st.utcnow()})
            deleted_paths.append(c["file_path"])
    st.save(cfg.state_file, stt)
    return {"keeps": promoted, "deletes": deleted_paths, "dry_run": False}
