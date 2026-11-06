"""Crash-safe, atomic state file.

Invariants (documented in README, do not break):
- Every download/promotion/deletion is recorded here BEFORE/AS the action happens;
  deletions only ever happen for paths present in this state.
- Writes are atomic (tmp file + os.replace).
- A lockfile prevents overlapping cron runs from stomping each other.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from pathlib import Path
from typing import Any

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - Windows dev machines
    fcntl = None  # type: ignore[assignment]

if os.name == "nt":  # pragma: no cover - Windows dev machines
    import msvcrt

STATE_VERSION = 1

# Candidate lifecycle:
#   queued -> downloaded -> promoted | expired
#   queued -> failed (strike) -> queued (retry) -> failed ... or promoted/expired
#   skipped-owned: already in the library, never downloaded
#   missing: file vanished from incoming/ (never deleted by us)
TERMINAL_STATUSES = {"promoted", "expired", "skipped-owned"}


def _empty() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "last_run": None,
        "candidates": [],  # artist,title,status,added_at,added_run,file_path,strikes,downloaded_at
        "moved": [],
        "deleted": [],
    }


class StateLock:
    """Exclusive lock around a state file (fcntl on POSIX, msvcrt on Windows).

    The lock file is intentionally kept around: unlinking it while another
    process waits would let two processes lock different inodes.
    """

    def __init__(self, state_path: str) -> None:
        self.lock_path = Path(state_path).with_suffix(".lock")
        self._fh: Any = None

    def __enter__(self) -> "StateLock":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.lock_path, "a+")
        try:
            if fcntl is not None:
                fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as e:
            self._fh.close()
            raise RuntimeError("another starkeep run seems active (lockfile held)") from e
        return self

    def __exit__(self, *exc: Any) -> None:
        try:
            if fcntl is not None:
                fcntl.flock(self._fh, fcntl.LOCK_UN)
            else:
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self._fh.close()


def utcnow() -> str:
    return utcnow_dt().isoformat(timespec="seconds")


def utcnow_dt() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def days_since(iso: str | None, now: dt.datetime | None = None) -> float:
    """Age in (fractional) days; +inf when the timestamp is absent."""
    if not iso:
        return float("inf")
    t = dt.datetime.fromisoformat(iso)
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    now = now or utcnow_dt()
    return (now - t).total_seconds() / 86400.0


def load(state_path: str) -> dict[str, Any]:
    p = Path(state_path)
    if not p.is_file():
        st = _empty()
        st["path"] = state_path
        return st
    st = json.loads(p.read_text(encoding="utf-8"))
    st.setdefault("version", STATE_VERSION)
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
    return (artist.strip().casefold(), title.strip().casefold())


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
        "status": status,
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
        c["strikes"] = int(c.get("strikes", 0)) + 1
        c["status"] = "failed"


def queue_decision(st: dict, artist: str, title: str, max_strikes: int = 2) -> str:
    """What should harvest do with this candidate?

    new     : never seen -> check library, then queue
    retry   : a previous failure/missing file below the strike limit -> queue again
    requeue : was queued when a run crashed -> keep it queued
    skip    : downloaded/promoted/expired/owned, or out of strikes
    """
    c = find_candidate(st, artist, title)
    if c is None:
        return "new"
    status = c.get("status")
    strikes = int(c.get("strikes", 0))
    if status == "queued":
        return "requeue"
    if status in ("failed", "missing") and strikes < max_strikes:
        return "retry"
    return "skip"


def is_retryable(st: dict, artist: str, title: str, max_strikes: int = 2) -> bool:
    """Kept for readability at call sites; expired/promoted tracks never return."""
    return queue_decision(st, artist, title, max_strikes) != "skip"
