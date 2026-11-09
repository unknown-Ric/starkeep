"""3. DECIDE — starred keeps, expired orphans, untouched young tracks."""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from shutil import move

import mutagen

from . import state as st
from . import blacklist
from .config import Config
from .lastfm import LastfmClient
from .subsonic import SubsonicClient


log = logging.getLogger(__name__)


def _pc_key(artist: str, title: str) -> str:
    """Key matching subsonic.playlist_playcounts()."""
    return _norm(artist) + "|" + _norm(title)


def _norm(text: str | None) -> str:
    return " ".join((text or "").casefold().split())


def _under(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root)
    except OSError:  # dangling symlink / unreadable path
        return False


def starred_match(candidate: dict, starred: list[dict]) -> bool:
    """Match a starred library song to one of our audition candidates.

    Primary key is artist+title (we tag downloads with the candidate's real
    artist/title). The file-name match covers tracks starred before the tag
    pass ran, and servers whose starred path is relative to a music folder.
    """
    artist, title = _norm(candidate.get("artist")), _norm(candidate.get("title"))
    file_path = candidate.get("file_path")
    basename = Path(file_path).name.casefold() if file_path else ""
    for song in starred:
        if _norm(song.get("artist")) == artist and _norm(song.get("title")) == title:
            return True
        song_path = song.get("path") or ""
        if basename and Path(song_path).name.casefold() == basename:
            return True
    return False


def _audition_age(c: dict, now: dt.datetime) -> int:
    """Age in whole days, counted from midnight (UTC) of the download day.

    Second-for-second age would race the schedule: the weekly batch is born
    minutes *after* the cleaning run fires, so a strict 24h-clock would let
    every Monday batch survive one cleaning pass too long. From the download
    calendar day, "Monday's candidates die the next cleaning run" holds.
    """
    ts = c.get("downloaded_at") or c.get("added_at")
    if not ts:
        return 1 << 30  # undocumented candidate: treated as ancient, expires
    t = dt.datetime.fromisoformat(ts)
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    start = t.replace(hour=0, minute=0, second=0, microsecond=0)
    return (now - start).days


def plan(cfg: Config, starred: list[dict], stt: dict, playcounts: dict[str, int] | None = None,
         now: dt.datetime | None = None, expire: bool = True,
         blocklist: list[tuple[str, str]] | None = None
         ) -> tuple[list[tuple[dict, Path]], list[tuple[dict, Path]]]:
    """Pure keep/expire decision over downloaded candidates (no disk changes).

    Lifetime is per-run (`expiry_runs`), not per-day: an unstarred batch is
    expired once heard, unheard tracks get `carry_over_runs` extra passes.
    With `expiry_runs == 0` the calendar-day `expiry_days` applies instead:
    unstarred candidates are deleted at the first cleaning run after their
    download day + `expiry_days` (Monday batch -> next Monday).
    The per-candidate `auditions` counter is bumped here (in-memory only).

    `expire=False` is a promotion-only pass: unstarred candidates are skipped
    entirely — nothing deleted, and the run budget is not consumed, so frequent
    promotion runs cannot bring a cleanup forward.

    `blocklist` (see blacklist.py) deletes immediately on a cleaning pass,
    even young tracks — it is an explicit keep-out order that must not wait
    out the audition week. Starred candidates still win: the star is deliberate.
    """
    now = now or st.utcnow_dt()
    incoming_root = Path(cfg.incoming_dir).resolve()
    blocklist = blocklist or []
    playcounts = playcounts or {}
    keeps: list[tuple[dict, Path]] = []
    deletes: list[tuple[dict, Path]] = []

    for c in stt["candidates"]:
        if c.get("status") != "downloaded":
            continue
        if not c.get("file_path"):
            continue
        src = Path(c["file_path"])
        if starred_match(c, starred):
            if src.is_file() and _under(src, incoming_root):
                keeps.append((c, src))
            else:
                log.warning("starred but file unusable: %s - %s (%s)",
                            c["artist"], c["title"], src)
            continue
        if not expire:
            continue  # promotion pass: only starred candidates may move
        if blocklist and blacklist.blacklisted(blocklist, c["artist"], c["title"]):
            c["_expire_reason"] = "blacklisted"
            deletes.append((c, src))
            continue
        heard = playcounts.get(_pc_key(c["artist"], c["title"]), 0) >= cfg.min_plays
        if cfg.expiry_runs:
            c["auditions"] = int(c.get("auditions", 0)) + 1
            if c["auditions"] >= cfg.expiry_runs and heard:
                c["_expire_reason"] = "heard-unstarred"
            elif c["auditions"] > cfg.expiry_runs + cfg.carry_over_runs:
                c["_expire_reason"] = "never-played"
            else:
                continue  # still inside its runway
        elif _audition_age(c, now) >= cfg.expiry_days:
            c["_expire_reason"] = "age"
        else:
            continue  # young unstarred -> untouched, rolls over to next week
        deletes.append((c, src))
    return keeps, deletes


def promote_file(cfg: Config, src: Path, artist: str, title: str,
                 lfm: LastfmClient, stt: dict) -> str:
    """Enrich tags (Last.fm album/track#), file into <Artist>/<Album>/NN. Title.

    Reads existing tags first; Last.fm is only used to fill gaps and to repair
    bogus yt-dlp channel-name artist tags. Falls back to <Artist>/Singles/.
    Never overwrites an existing file.
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
        if track_no:
            m["tracknumber"] = str(track_no)
        m.save()

    target_dir = Path(cfg.library_dir) / artist / (album or "Singles")
    target_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{track_no:02d}. {title}" if track_no else f"{artist} - {title}"
    dst = target_dir / f"{stem}{src.suffix}"
    if dst.exists():
        # Same track already promoted (state was reset, or a duplicate star).
        # Do not clobber the library copy: leave the incoming file for expiry.
        raise FileExistsError(f"promotion target already exists: {dst}")
    move(src, dst)
    stt.setdefault("moved", []).append({"from": str(src), "to": str(dst), "at": st.utcnow()})
    return str(dst)


def decide(cfg: Config, sub: SubsonicClient, lfm: LastfmClient | None, stt: dict,
           dry_run: bool = False, now: dt.datetime | None = None,
           expire: bool = True) -> dict:
    """The keep/expire pass: read stars from Subsonic, act on incoming files."""
    starred = sub.starred_songs()
    playcounts = sub.playlist_playcounts(cfg.playlist_name) if cfg.expiry_runs else {}
    keeps, deletes = plan(cfg, starred, stt, playcounts=playcounts, now=now,
                          expire=expire, blocklist=blacklist.load(cfg.blacklist_file))

    if dry_run:
        return {
            "dry_run": True,
            "starred": len(starred),
            "keeps": [
                {"artist": c["artist"], "title": c["title"], "from": str(src)}
                for c, src in keeps
            ],
            "deletes": [
                {"artist": c["artist"], "title": c["title"], "path": str(src)}
                for c, src in deletes
            ],
        }

    promoted: list[str] = []
    for c, src in keeps:
        try:
            dst = promote_file(cfg, src, c["artist"], c["title"], lfm, stt)
        except Exception as e:
            # Leave it downloaded: a later run can retry (or it expires).
            log.error("promotion failed for %s - %s: %s", c["artist"], c["title"], e)
            c.pop("_expire_reason", None)
            continue
        c["status"] = "promoted"
        log.info("promoted %s - %s -> %s", c["artist"], c["title"], dst)
        promoted.append(dst)
        st.save(cfg.state_file, stt)

    incoming_root = Path(cfg.incoming_dir).resolve()
    deleted: list[str] = []
    for c, src in deletes:
        # SAFETY: only delete a path recorded in state, that still exists, and
        # that resolves inside incoming_dir. Never glob, never follow a move.
        if not (src.is_file() and _under(src, incoming_root)):
            log.warning("not deleting %s: missing or outside incoming_dir", src)
            continue
        if starred_match(c, starred):  # starred between planning and acting
            c.pop("_expire_reason", None)
            continue
        src.unlink(missing_ok=True)
        reason = c.pop("_expire_reason", "age")
        c["status"] = "expired"
        c["expired_at"] = st.utcnow()  # the requeue_after_days cooldown runs from here
        stt["deleted"].append({"path": str(src), "at": st.utcnow()})
        deleted.append(str(src))
        log.info("expired (%s): %s - %s", reason, c["artist"], c["title"])
        st.save(cfg.state_file, stt)

    return {
        "dry_run": False,
        "starred": len(starred),
        "keeps": promoted,
        "deletes": deleted,
    }
