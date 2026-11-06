"""1. HARVEST — build this week's candidate list."""

from __future__ import annotations

import logging

from . import state as st
from .config import Config
from .lastfm import LastfmClient
from .subsonic import SubsonicClient

log = logging.getLogger(__name__)


def harvest(cfg: Config, lfm: LastfmClient, sub: SubsonicClient, stt: dict) -> list[dict]:
    """Return up to cfg.cap queued candidates, freshly added/updated in state.

    Source: most-played tracks of the last 7 days -> track.getSimilar for each.
    Dedupes against:
      - candidates already in state (terminal ones and strike-hot ones skipped,
        transient failures retried while strikes < max_strikes)
      - the actual library (Subsonic search3)
    """
    seeds = lfm.recent_track_seeds(days=7, limit=100)
    if not seeds:
        log.warning("no recent scrobbles — harvest produced nothing (run skipped safely)")
        return []
    if cfg.cap <= 0:  # --cap 0 = decide-only smoke run
        return []

    proposed: list[tuple[str, str]] = []
    for artist, title in seeds[:10]:
        proposed.extend(lfm.similar_tracks(artist, title, cfg.similar_per_seed))
        if len(proposed) >= cfg.cap * 3:
            break

    picked: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for artist, title in proposed:
        if not artist or not title:
            continue
        key = st.candidate_key(artist, title)
        if key in seen:
            continue
        seen.add(key)

        decision = st.queue_decision(stt, artist, title, cfg.max_strikes)
        if decision == "skip":
            continue
        if decision == "new" and sub.song_exists(artist, title):
            log.info("already in library, skipping: %s - %s", artist, title)
            st.add_candidate(stt, artist, title, status="skipped-owned")
            st.save(cfg.state_file, stt)
            continue
        if decision == "retry":
            c = st.find_candidate(stt, artist, title)
            if c:
                c["status"] = "queued"
                log.info("retrying (strike %d/%d): %s - %s",
                         c.get("strikes", 0), cfg.max_strikes, artist, title)
        elif decision == "new":
            st.add_candidate(stt, artist, title, status="queued")

        picked.append({"artist": artist, "title": title})
        if len(picked) >= cfg.cap:
            break

    st.save(cfg.state_file, stt)
    return picked
