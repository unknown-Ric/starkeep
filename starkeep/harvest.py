"""1. HARVEST — build this week's candidate list."""

from __future__ import annotations

import logging

from . import state as st
from . import blacklist
from .config import Config
from .lastfm import LastfmClient
from .subsonic import SubsonicClient

log = logging.getLogger(__name__)


def release_expired(cfg: Config, stt: dict) -> int:
    """Drop expired candidates whose requeue_after_days cooldown has passed.

    Drops them from state so they read as *never seen*: the next harvest may
    propose them again like any fresh candidate (library dedupe still guards
    anything promoted). With requeue_after_days == 0 or no expired_at stamp,
    nothing changes — old records stay permanently rejected.
    """
    cooldown = cfg.requeue_after_days
    if cooldown <= 0:
        return 0
    now = st.utcnow_dt()
    keep: list[dict] = []
    released: list[str] = []
    for c in stt["candidates"]:
        ts = c.get("expired_at")
        if c.get("status") == "expired" and ts:
            age = st.days_since(ts, now)
            if 0 <= age >= cooldown:
                released.append(f'{c["artist"]} - {c["title"]}')
                continue  # cold enough: forget it, harvest may re-propose
        keep.append(c)
    if released:
        stt["candidates"] = keep
        log.info("requeued after %dd cooldown: %s", cooldown, ", ".join(released))
    return len(released)


def harvest(cfg: Config, lfm: LastfmClient, sub: SubsonicClient, stt: dict) -> list[dict]:
    """Return up to cfg.cap queued candidates, freshly added/updated in state.

    Source: most-played tracks of the last 7 days -> track.getSimilar for each.
    Dedupes against:
      - candidates already in state (terminal ones and strike-hot ones skipped,
        transient failures retried while strikes < max_strikes)
      - the actual library (Subsonic search3)
    """
    seeds = lfm.recent_track_seeds(days=cfg.harvest_days, limit=100)
    if not seeds:
        log.warning("no recent scrobbles — harvest produced nothing (run skipped safely)")
        return []
    if cfg.cap <= 0:  # --cap 0 = decide-only smoke run
        return []
    release_expired(cfg, stt)
    blocklist = blacklist.load(cfg.blacklist_file)

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

        if blacklist.blacklisted(blocklist, artist, title):
            log.info("blacklisted, skipping: %s - %s", artist, title)
            continue

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
