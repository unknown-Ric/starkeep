"""2. DOWNLOAD — yt-dlp one candidate at a time into the incoming folder."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess

from . import state as st
from .config import Config

log = logging.getLogger(__name__)

_BAD_CHARS = r'<>:"/\\|?*'
YTDLP = shutil.which("yt-dlp")


def safe_name(text: str) -> str:
    return re.sub(_BAD_CHARS, "_", text).strip()[:120]


def yt_search_query(artist: str, title: str) -> str:
    # audio-bias (your v2 idea, applied from day one)
    return f'ytsearch1:"{artist} - {title} audio"'


def download(cfg: Config, artist: str, title: str) -> str | None:
    """Download exactly one track. Returns file path, raises on failure."""
    if not YTDLP:
        raise RuntimeError("yt-dlp not found on PATH")
    out_tmpl = str(cfg.incoming_dir / f"{safe_name(artist)} - {safe_name(title)}.%(ext)s") \
        if hasattr(cfg.incoming_dir, "__truediv__") else \
        str(cfg.incoming_dir) + f"/{safe_name(artist)} - {safe_name(title)}.%(ext)s"
    cmd = [
        YTDLP,
        "--audio-format", cfg.audio_format, "--extract-audio",
        "-o", out_tmpl,
        "--no-playlist", "--quiet", "--no-warnings",
        yt_search_query(artist, title.lstrip("")),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed (rc={result.returncode}): {result.stderr[-400:]}")
    return None  # filled in by caller via state (file name is deterministic)


def download_all(cfg: Config, candidates: list[dict], stt: dict) -> int:
    """Serial downloads with strike bookkeeping. Returns count downloaded now."""
    ok = 0
    for cand in candidates:
        artist, title = cand["artist"], cand["title"]
        try:
            download(cfg, artist, title)
            fname = f"{safe_name(artist)} - {safe_name(title)}.{cfg.audio_format}"
            path = str(cfg.incoming_dir) + "/" + fname
            c = st.find_candidate(stt, artist, title)
            if c:
                c["status"], c["file_path"], c["downloaded_at"] = "downloaded", path, st.utcnow()
            log.info("downloaded %s - %s", artist, title)
            ok += 1
        except Exception as e:  # one bad candidate must not kill the batch
            log.error("download failed for %s - %s: %s", artist, title, e)
            st.strike(stt, artist, title)
        finally:  # persist after every candidate: crash mid-batch is fine
            st.save(cfg.state_file, stt)
    return ok
