"""2. DOWNLOAD — yt-dlp one candidate at a time into the incoming folder."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import mutagen

from . import state as st
from .config import Config

log = logging.getLogger(__name__)

_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
AUDIO_EXTS = {".m4a", ".mp3", ".opus", ".flac", ".ogg", ".wav", ".aac", ".webm"}
YTDLP = shutil.which("yt-dlp")


def safe_name(text: str) -> str:
    return _BAD_CHARS.sub("_", text).strip(" .")[:120]


def yt_search_query(artist: str, title: str) -> str:
    # Bias toward audio uploads (avoids most music-video/live junk).
    return f"ytsearch1:{artist} - {title} audio"


def _read_report(report: Path) -> Path | None:
    """yt-dlp's `--print-to-file after_move:filepath` writes the final path."""
    try:
        lines = [ln.strip() for ln in report.read_text(encoding="utf-8").splitlines()]
    except OSError:
        return None
    for line in reversed(lines):
        if line:
            p = Path(line)
            if p.is_file():
                return p
    return None


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


def download(cfg: Config, artist: str, title: str) -> Path:
    """Download exactly one track into incoming/. Returns the file actually written."""
    if not YTDLP:
        raise RuntimeError("yt-dlp not found on PATH")
    incoming = Path(cfg.incoming_dir)
    incoming.mkdir(parents=True, exist_ok=True)
    stem = f"{safe_name(artist)} - {safe_name(title)}"
    before = {p for p in incoming.iterdir() if p.is_file()}
    report = incoming / f".starkeep-{uuid.uuid4().hex}.path"

    cmd = [
        YTDLP,
        "--extract-audio", "--audio-format", cfg.audio_format, "--audio-quality", "0",
        "-o", str(incoming / f"{stem}.%(ext)s"),
        "--no-playlist", "--quiet", "--no-warnings",
        "--print-to-file", "after_move:filepath", str(report),
        yt_search_query(artist, title),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if result.returncode != 0:
            raise RuntimeError(f"yt-dlp failed (rc={result.returncode}): {result.stderr[-400:]}")
        written = _read_report(report) or _discover_written(before, incoming, stem)
        if written is None:
            raise RuntimeError("yt-dlp reported success but no new audio file was found")
        return written
    finally:
        report.unlink(missing_ok=True)


def download_all(cfg: Config, candidates: list[dict], stt: dict) -> int:
    """Serial downloads with strike bookkeeping. Returns count downloaded now."""
    ok = 0
    for cand in candidates:
        artist, title = cand["artist"], cand["title"]
        try:
            path = download(cfg, artist, title)
            apply_basic_tags(path, artist, title)
            c = st.find_candidate(stt, artist, title)
            if c:
                c["status"] = "downloaded"
                c["file_path"] = str(path)
                c["downloaded_at"] = st.utcnow()
            log.info("downloaded %s - %s -> %s", artist, title, path.name)
            ok += 1
        except Exception as e:  # one bad candidate must not kill the batch
            log.error("download failed for %s - %s: %s", artist, title, e)
            st.strike(stt, artist, title)
        finally:  # persist after every candidate: crash mid-batch is fine
            st.save(cfg.state_file, stt)
    return ok
