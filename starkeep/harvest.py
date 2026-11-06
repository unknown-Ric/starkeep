"""1. HARVEST — build this week's candidate list."""

from __future__ import annotations

import logging

from . import state as st
from .config import Config
from .lastfm import LastfmClient
from .subsonic import SubsonicClient

log = logging.getLogger(__name__)


def harvest(cfg: Config, lfm: LastfmClient, sub: SubsonicClient, stt: dict) -> list[dict]:
    """Return up to cfg.cap queued candidates, freshly added to state.

    Sources: most-played tracks of the last 7 days -> track.getSimilar for each,
    topped up with top tracks of artist.getSimilar. Dedupes against:
      - the existing library (Subsonic search of real files)
      - candidates already seen in state (incl. strike-hot ones -> skipped)
    """
    seeds = lfm.recent_track_seeds(days=7, limit=100)
    seen_in_state = {st.candidate_key(c["artist"], c["title"]) for c in stt["candidates"]}
    candidates: list[tuple[str, str]] = []
    for artist, title in seeds[:10]:
        for sim_artist, sim_title in lfm.similar_tracks(artist, title, cfg.similar_per_seed):
            candidates.append((sim_artist, sim_title))
        for sim_artist in lfm.similar_artists(artist, limit=2):
            candidates.append((sim_artist, ""))  # unknown track; resolve at download
        if len(candidates) > cfg.cap * 3:
            break

    # Prefer candidates with a usable title; drop dupes + known strikes
    picked: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for artist, title in candidates:
        # fill missing titles from library-free analogue (v1: skip untitled)
        if not title:
            continue
        key = st.candidate_key(artist, title)
        if key in seen or key in seen_in_state:
            continue
        if not st.is_retryable(stt, artist, title, cfg.max_strikes):
            continue
        picked.append((artist, title))
        seen.add(key)
        if len(picked) >= cfg.cap:
            break

    queued = []
    for artist, title in picked:
        if sub.song_exists(artist, title):
            log.info("already in library, skipping: %s - %s", artist, title)
            st.add_candidate(stt, artist, title, status="skipped-owned")
            st.save(cfg.state_file, stt)
            continue
        st.add_candidate(stt, artist, title, status="queued")
        queued.append({"artist": artist, "title": title})
    st.save(cfg.state_file, stt)

    if not queued and not seeds:
        log.warning("no recent scrobbles — harvest degraded; run skipped safely")
    return queued
