"""2. DOWNLOAD — yt-dlp one candidate at a time into the incoming folder."""

from __future__ import annotations

import glob
import logging
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import mutagen
import mutagen.id3
import mutagen.mp4
import requests

from . import state as st
from . import qobuz as qobuz_mod
from .config import Config

log = logging.getLogger(__name__)

_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
AUDIO_EXTS = {".m4a", ".mp3", ".opus", ".flac", ".ogg", ".wav", ".aac", ".webm"}
YTDLP = shutil.which("yt-dlp")


def safe_name(text: str) -> str:
    return _BAD_CHARS.sub("_", text).strip(" .")[:120]


_VERSION_WORDS = re.compile(r"(?i)\b(live|cover|remix|karaoke|instrumental|acoustic)\b")


def _match_filters(artist: str, title: str, duration: int | None) -> str | None:
    """Reject obvious wrong-version uploads (live/cover/...) before they cost
    a download, and clamp the length to the official track's ±35% (the Last.fm
    duration is also a cheap guard against picking a 2h compilation upload)."""
    filters = []
    if not _VERSION_WORDS.search(f"{artist} {title}"):
        filters.append(r"title!~=(?i)\b(live|cover|remix|karaoke|instrumental|acoustic)\b")
    if duration and duration > 30:
        filters.append(f"duration>={int(duration * 0.65)}&duration<={int(duration * 1.35)}")
    return "&".join(filters) or None


def _read_report(report: Path) -> tuple[Path | None, str | None]:
    """yt-dlp's `--print-to-file` writes '<video_id>\\t<final path>' per line."""
    try:
        lines = [ln.strip() for ln in report.read_text(encoding="utf-8").splitlines()]
    except OSError:
        return None, None
    for line in reversed(lines):
        if line:
            vid: str | None = None
            if "\t" in line:  # older reports (or fakes) contain only the path
                vid, line = line.split("\t", 1)
                line = line.strip()
            p = Path(line)
            if p.is_file():
                return p, vid or None
    return None, None


def _discover_written(before: set[Path], incoming: Path, stem: str) -> Path | None:
    """Fallback for older yt-dlp builds / unexpected output names."""
    added = [
        p for p in incoming.iterdir()
        if p.is_file() and p not in before
        and not p.name.startswith(".") and p.suffix.lower() in AUDIO_EXTS
    ]
    if not added:
        return None
    preferred = [p for p in added if p.stem.lower().startswith(stem.lower())]
    pool = preferred or added
    return max(pool, key=lambda p: p.stat().st_mtime)


def apply_basic_tags(path: Path, artist: str, title: str) -> bool:
    """Write artist/title *before* the audition week.

    Navidrome shows these tags, so a starred audition track can be matched back
    to the candidate even when we only know the YouTube channel name. Failing
    to tag is not fatal: matching also falls back to the file name.
    """
    try:
        m = mutagen.File(path, easy=True)
        if m is None:
            return False
        m["artist"] = artist
        m["title"] = title
        m.save()
        return True
    except Exception as e:  # corrupt container, missing plugin, ...
        log.warning("could not tag %s: %s", path, e)
        return False


def apply_full_tags(path: Path, artist: str, title: str, lfm,
                    info: dict | None = None) -> bool:
    """Full tags BEFORE the audition week: real artist/title/album/track# plus
    album art (Deezer high-res -> Last.fm album image), falling back to the
    yt-dlp thumbnail when both lookups miss. Never fatal: the download is kept.
    `info` may be a pre-fetched track_album_info() result (avoids a second call).
    """
    if not apply_basic_tags(path, artist, title):
        return False
    try:
        if not info:
            info = (lfm.track_album_info(artist, title) if lfm else None) or {}
        m = mutagen.File(path, easy=True)
        if m is not None:
            if info.get("album"):
                m["album"] = info["album"]
            if info.get("track_number"):
                m["tracknumber"] = str(info["track_number"])
            m.save()
        url = _deezer_cover_url(artist, title) or info.get("image")
        if not url:
            return True  # thumbnail embedded by yt-dlp stays
        jpg = requests.get(url, timeout=60)
        jpg.raise_for_status()
        _embed_cover(path, jpg.content)
        return True
    except Exception as e:  # enrichment must not kill download bookkeeping
        log.warning("full tags failed for %s: %s", path.name, e)
        return False


DEEZER_API = "https://api.deezer.com/search/track"


def _norm(text: str | None) -> str:
    return " ".join((text or "").casefold().split())


def _pick_deezer(results: list, artist: str, title: str) -> str | None:
    """Deezer cover_xl (1000px) for an exact artist+title match only.
    Wrong-track art is worse than the YouTube thumbnail we already have,
    so a loose search win is not trusted on its own."""
    a, t = _norm(artist), _norm(title)
    for r in results:
        if (_norm((r.get("artist") or {}).get("name")) == a
                and _norm(r.get("title")) == t):
            return (r.get("album") or {}).get("cover_xl")
    return None


def _deezer_cover_url(artist: str, title: str) -> str | None:
    try:
        r = requests.get(DEEZER_API, params={"q": f"{artist} {title}", "limit": 5},
                         timeout=30)
        r.raise_for_status()
        return _pick_deezer(r.json().get("data") or [], artist, title)
    except Exception as e:
        log.warning("Deezer cover lookup failed for %s - %s: %s", artist, title, e)
        return None


def _embed_cover(path: Path, data: bytes) -> bool:
    """Embed JPEG bytes as artwork (MP4 + ID3; other containers keep the
    yt-dlp thumbnail)."""
    audio = mutagen.File(path)
    if audio is None:
        return False
    try:
        if isinstance(audio, mutagen.mp4.MP4):
            audio["covr"] = [mutagen.mp4.MP4Cover(
                data, imageformat=mutagen.mp4.MP4Cover.FORMAT_JPEG)]
        elif isinstance(audio, mutagen.id3.ID3FileType):
            audio.tags.add(mutagen.id3.APIC(encoding=3, mime="image/jpeg",
                                            type=3, data=data))
        else:
            return False
        audio.save()
        return True
    except Exception as e:
        log.warning("could not embed cover in %s: %s", path.name, e)
        return False


def _existing_file(incoming: Path, stem: str) -> Path | None:
    """A finished file from an interrupted run (yt-dlp renames .part only when done)."""
    # glob.escape: a stem with "[Remastered]" must not read as a char class
    matches = [
        p for p in incoming.glob(f"{glob.escape(stem)}.*")
        if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in AUDIO_EXTS
    ]
    return max(matches, key=lambda p: p.stat().st_mtime) if matches else None


def download(cfg: Config, artist: str, title: str,
             duration: int | None = None, banned_ids: list[str] | None = None
             ) -> tuple[Path, str | None]:
    """Download exactly one track into incoming/. Returns (file, external_id).

    Dispatches on cfg.downloader: 'ytdlp' (YouTube via yt-dlp) or 'qobuz'
    (FLAC via Qobuz API). `duration` and `banned_ids` only apply to yt-dlp.
    """
    if cfg.downloader == "qobuz":
        path, track_id, info = qobuz_mod.qobuz_download(
            artist, title, cfg.incoming_dir)
        return path, str(track_id)

    # --- ytdlp path (original) ---
    if not YTDLP:
        raise RuntimeError("yt-dlp not found on PATH")
    incoming = Path(cfg.incoming_dir)
    incoming.mkdir(parents=True, exist_ok=True)
    stem = f"{safe_name(artist)} - {safe_name(title)}"

    existing = _existing_file(incoming, stem)
    if existing is not None:
        log.info("already downloaded, adopting: %s", existing.name)
        return existing, None

    before = {p for p in incoming.iterdir() if p.is_file()}
    report = incoming / f".starkeep-{uuid.uuid4().hex}.path"

    audio_format = "best" if cfg.audio_format == "native" else cfg.audio_format
    cmd = [
        YTDLP,
        "--extract-audio", "--audio-format", audio_format, "--audio-quality", "0",
        "--embed-thumbnail", "--convert-thumbnails", "jpg",
        "-o", str(incoming / f"{stem}.%(ext)s"),
        "--no-playlist", "--quiet", "--no-warnings",
        "--print-to-file", "after_move:%(id)s\t%(filepath)s", str(report),
    ]
    mf = _match_filters(artist, title, duration)
    if banned_ids:
        # ids come straight from yt-dlp's extractor: [A-Za-z0-9_-]{11}, regex-safe
        exclude = "|".join(banned_ids)
        mf = (mf + "&" if mf else "") + f"id!~^({exclude})$"
    if mf:
        cmd += ["--match-filters", mf]
    # 5 candidates + --max-downloads 1: a filtered-out top hit falls through
    # to the next result instead of failing the track. rc 101 = limit reached.
    cmd += ["--max-downloads", "1", f"ytsearch5:{artist} - {title} audio"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        written, video_id = _read_report(report)
        if written is None:
            written = _discover_written(before, incoming, stem)
        if written is None:
            if result.returncode not in (0, 101):  # 101 = stopped at the limit
                raise RuntimeError(
                    f"yt-dlp failed (rc={result.returncode}): {result.stderr[-400:]}")
            raise RuntimeError(
                f"no result passed the filters for '{artist} - {title}' "
                f"(live/cover/duration); nothing downloaded")
        return written, video_id
    finally:
        report.unlink(missing_ok=True)


def download_all(cfg: Config, candidates: list[dict], stt: dict,
                 lfm=None) -> int:
    """Serial downloads with strike bookkeeping. Returns count downloaded now."""
    ok = 0
    for cand in candidates:
        artist, title = cand["artist"], cand["title"]
        try:
            # one Last.fm call feeds both the length filter and the tags
            info = lfm.track_album_info(artist, title) if lfm else None
            path, video_id = download(
                cfg, artist, title, duration=(info or {}).get("duration"),
                banned_ids=list(stt.get("banned_ids", [])))
            apply_full_tags(path, artist, title, lfm, info=info)
            c = st.find_candidate(stt, artist, title)
            if c:
                c["status"] = "downloaded"
                c["file_path"] = str(path)
                c["video_id"] = video_id
                c["downloaded_at"] = st.utcnow()
            log.info("downloaded %s - %s -> %s", artist, title, path.name)
            ok += 1
        except Exception as e:  # one bad candidate must not kill the batch
            log.error("download failed for %s - %s: %s", artist, title, e)
            st.strike(stt, artist, title)
            c = st.find_candidate(stt, artist, title)
            if c:
                c["last_error"] = str(e)[:200]  # shown on the status page
        finally:  # persist after every candidate: crash mid-batch is fine
            st.save(cfg.state_file, stt)
    return ok
