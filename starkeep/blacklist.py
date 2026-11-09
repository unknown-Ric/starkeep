"""Manual blocklist: blacklist.txt next to the config (optional).

Entry shapes, one per line (`#` comments, blank lines ignored):

    Artist - Title    -> that one track
    Artist            -> any track naming that artist

An artist entry must match a *named artist* of the song, so multi-artist
candidates ("A, B - T", "A & B", "A feat. B", "A with B") are split into
artist tokens before matching. Words like "and" are names, not separators
("Mumford and Sons"), and are therefore never split on.

Matching is case/whitespace-insensitive; the blacklist file is the durable
memory, so skipped tracks are not recorded in state.
"""

from __future__ import annotations

import re
from pathlib import Path

FILENAME = "blacklist.txt"  # lives next to config.yaml

# The separator set is deliberately tight: ',', '&', '/', and the explicit
# featuring words. 'and' is the most common substring inside real artist
# names, so it is excluded on purpose.
SEP = re.compile(
    r"\s*[,/&]\s*|\s+(?:feat\.?|ft\.?|featuring|with)\s+", re.IGNORECASE
)


def _norm(text: str) -> str:
    return " ".join((text or "").casefold().split())


def artists_of(artist: str) -> list[str]:
    """The distinct artists named in a candidate's artist field."""
    return [t for t in (part.strip() for part in SEP.split(_norm(artist))) if t]


def load(path: str | Path | None) -> list[tuple[str, str]]:
    """Parse the blacklist into (artist, title) pairs; empty title = artist rule.

    Unknown/missing file is not an error — the feature is optional by default.
    """
    if not path:
        return []
    p = Path(path)
    if not p.is_file():
        return []
    entries: list[tuple[str, str]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if " - " in line:
            artist, _, title = line.partition(" - ")
            entries.append((_norm(artist), _norm(title)))
        else:
            entries.append((_norm(line), ""))
    return entries


def blacklisted(entries: list[tuple[str, str]], artist: str, title: str) -> bool:
    """True when the candidate's artist+title hits any blacklist entry.

    A track entry needs the exact artist+title pair; an artist-only entry
    hits when the named artist appears anywhere in the artist field, exact
    or as one of the split tokens.
    """
    if not entries:
        return False
    full_artist, title_n = _norm(artist), _norm(title)
    named = artists_of(artist)
    for entry_artist, entry_title in entries:
        if entry_title:  # track entry: exact artist+title
            if full_artist == entry_artist and title_n == entry_title:
                return True
        elif full_artist == entry_artist or entry_artist in named:
            return True
    return False
