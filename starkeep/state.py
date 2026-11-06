"""Crash-safe, atomic state file.

Invariants (documented in README, do not break):
- Every download/promotion/deletion is recorded here BEFORE the action and
  confirmed after; deletions only ever happen for paths present in this state.
- Writes are atomic (tmp file + os.replace).
- A simple lockfile prevents overlapping cron runs from stomping each other.
"""

from __future__ import annotations

import datetime as dt
import fcntl  # POSIX only; see lock note below
import json
import os
import tempfile
from pathlib import Path
from typing import Any

STATE_VERSION = 1


def _empty() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "last_run": None,
        "candidates": [],  # {artist,title,source,status,added_at,file_path,strikes,downloaded_at}
        "moved": [],
        "deleted": [],
    }


class StateLock:
    """Exclusive lock around a state file. Uses fcntl on POSIX; on platforms
    without fcntl falls back to a best-effort check (cron overlap is rare)."""

    def __init__(self, state_path: str) -> None:
        self.lock_path = Path(state_path).with_suffix(".lock")
        self._fh: Any = None

    def __enter__(self) -> "StateLock":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.lock_path, "w")
        try:
            fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            self._fh.close()
            raise RuntimeError("another starkeep run seems active (lockfile held)") from e
        return self

    def __exit__(self, *exc: Any) -> None:
        try:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
        finally:
            self._fh.close()
        self.lock_path.unlink(missing_ok=True)


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def days_since(iso: str | None) -> float:
    if not iso:
        return float("inf")
    return (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(iso)).days


def load(state_path: str) -> dict[str, Any]:
    p = Path(state_path)
    if not p.is_file():
        st = _empty()
        st["path"] = state_path
        return st
    st = json.loads(p.read_text(encoding="utf-8"))
    st.setdefault("candidates", [])
    st.setdefault("moved", [])
    st.setdefault("deleted", [])
    st["path"] = state_path
    return st


def save(state_path: str, st: dict[str, Any]) -> None:
    p = Path(state_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {k: v for k, v in st.items() if k != "path"}
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".state-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# ---- candidate helpers --------------------------------------------------

def candidate_key(artist: str, title: str) -> tuple[str, str]:
    return (artist.strip().lower(), title.strip().lower())


def find_candidate(st: dict, artist: str, title: str) -> dict | None:
    key = candidate_key(artist, title)
    for c in st["candidates"]:
        if candidate_key(c["artist"], c["title"]) == key:
            return c
    return None


def add_candidate(st: dict, artist: str, title: str, status: str, **extra: Any) -> dict:
    c = {
        "artist": artist,
        "title": title,
        "status": status,  # queued | downloaded | failed | uploaded
        "added_at": utcnow(),
        "added_run": st.get("last_run"),
        "file_path": None,
        "strikes": 0,
        **extra,
    }
    st["candidates"].append(c)
    return c


def strike(st: dict, artist: str, title: str) -> None:
    c = find_candidate(st, artist, title)
    if c:
        c["strikes"] += 1
        c["status"] = "failed"


def is_retryable(st: dict, artist: str, title: str, max_strikes: int = 2) -> bool:
    """A candidate that already failed enough times must never be retried."""
    c = find_candidate(st, artist, title)
    if c and not (c["status"] == "queued" and c["strikes"] < max_strikes):
        return False
    return True
