"""Reconcile: files in incoming/ that state does not know about.

A crash between "yt-dlp wrote the file" and "state saved the path" leaves an
orphan. Orphans are *reported*, never deleted: deleting files we cannot account
for would break the core safety promise. Run `starkeep status` to see them.
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import state as st
from .config import Config

log = logging.getLogger(__name__)

AUDIO_EXTS = {".m4a", ".mp3", ".opus", ".flac", ".ogg", ".wav", ".aac", ".webm"}


def scan(cfg: Config, stt: dict) -> dict:
    """Read-only: return {'orphans': [...], 'missing': [...]}."""
    incoming = Path(cfg.incoming_dir)
    orphans: list[str] = []
    missing: list[str] = []

    if incoming.is_dir():
        known = {
            str(Path(c["file_path"]).resolve())
            for c in stt.get("candidates", [])
            if c.get("file_path")
        }
        for path in sorted(incoming.rglob("*")):
            if not path.is_file() or path.name.startswith("."):
                continue
            if path.suffix.lower() not in AUDIO_EXTS:
                continue
            if str(path.resolve()) not in known:
                orphans.append(str(path))

    for c in stt.get("candidates", []):
        if c.get("status") != "downloaded" or not c.get("file_path"):
            continue
        if not Path(c["file_path"]).is_file():
            missing.append(str(c["file_path"]))
    return {"orphans": orphans, "missing": missing}


def apply_missing(stt: dict, missing: list[str]) -> None:
    """Mark candidates whose file vanished so they can be retried if eligible."""
    wanted = {str(Path(p)) for p in missing}
    for c in stt.get("candidates", []):
        if c.get("status") == "downloaded" and str(c.get("file_path")) in wanted:
            c["status"] = "missing"
            log.warning("incoming file vanished: %s (%s - %s)",
                        c["file_path"], c["artist"], c["title"])
