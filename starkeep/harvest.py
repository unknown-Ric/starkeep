"""1. HARVEST — build this week's candidate list."""

from __future__ import annotations

import logging

from . import state as st
from . import blacklist
from .config import Config
from .lastfm import LastfmClient
from .subsonic import SubsonicClient, _strip_feat

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
            if age >= cooldown:
                released.append(f'{c["artist"]} - {c["title"]}')
                continue  # cold enough: forget it, harvest may re-propose
        keep.append(c)
    if released:
        stt["candidates"] = keep
        log.info("requeued after %dd cooldown: %s", cooldown, ", ".join(released))
    return len(released)


def _skey(artist: str, title: str) -> tuple[str, str]:
    """Variant-tolerant key: 'X (feat. Y)' and 'X featuring Y' collapse to 'X'."""
    return _strip_feat(artist), _strip_feat(title)


def harvest(cfg: Config, lfm: LastfmClient | None, sub: SubsonicClient, stt: dict,
            lb=None) -> list[dict]:
    """Return up to cfg.cap queued candidates, freshly added/updated in state.

    Sources (in order):
      - Last.fm: most-played tracks of the last N days -> track.getSimilar
      - ListenBrainz (optional): the current week's algorithm playlists
        (Weekly Exploration / Weekly Jams)
    Dedupes against:
      - candidates already in state (terminal ones and strike-hot ones skipped,
        transient failures retried while strikes < max_strikes)
      - guest-credit variants of known candidates ("X (feat. Y)" vs "X")
      - the actual library (Subsonic search3)
    Also caps picks per artist (cfg.max_per_artist) for variety, and records
    the seed each candidate came from.
    """
    if cfg.cap <= 0:  # --cap 0 = decide-only run: no Last.fm calls at all
        return []
    sources: list[tuple[str, str, str]] = []  # (artist, title, seed_label)
    if lfm is not None:
        seeds = lfm.recent_track_seeds(days=cfg.harvest_days, limit=100)
        if not seeds:
            log.warning("no recent scrobbles — Last.fm source produced nothing")
        for seed_artist, seed_title in seeds:
            for artist, title in lfm.similar_tracks(seed_artist, seed_title,
                                                    cfg.similar_per_seed):
                sources.append((artist, title, _seed_label(seed_artist, seed_title)))
    if lb is not None:
        try:
            for t in lb.weekly_candidates(cfg.listenbrainz_tracks):
                sources.append((t["artist"], t["title"],
                                f"listenbrainz {t['source']}"))
        except Exception as e:
            log.warning("ListenBrainz harvest failed: %s", e)
    if not sources:
        log.warning("no discovery candidates (no scrobbles, no LB playlists) — "
                    "run skipped safely")
        return []
    release_expired(cfg, stt)
    blocklist = blacklist.load(cfg.blacklist_file)

    # variant map of everything already in state (see the variant skip below)
    variants: dict[tuple[str, str], list[dict]] = {}
    for c in stt["candidates"]:
        variants.setdefault(_skey(c["artist"], c["title"]), []).append(c)

    picked: list[dict] = []
    seen: set[tuple[str, str]] = set()
    artist_picks: dict[str, int] = {}
    for artist, title, seed in sources:
        if not artist or not title:
            continue
        key = st.candidate_key(artist, title)
        skey = _skey(artist, title)
        if key in seen or skey in seen:
            continue
        seen.add(key)
        seen.add(skey)

        existing = variants.get(skey, [])
        exact = [e for e in existing if st.candidate_key(e["artist"], e["title"]) == key]
        if not exact and existing:
            # a differently-credited version of a known candidate, not the
            # same exact track (that case is queue_decision's job) — don't
            # queue both
            log.info("variant of known candidate, skipping: %s - %s",
                     artist, title)
            continue

        if artist_picks.get(skey[0], 0) >= cfg.max_per_artist:
            log.info("per-artist cap %d reached, skipping: %s - %s",
                     cfg.max_per_artist, artist, title)
            continue

        if blacklist.blacklisted(blocklist, artist, title):
            log.info("blacklisted, skipping: %s - %s", artist, title)
            continue

        decision = st.queue_decision(stt, artist, title, cfg.max_strikes)
        if decision == "skip":
            continue
        if decision == "new" and sub.song_exists(artist, title):
            log.info("already in library, skipping: %s - %s", artist, title)
            st.add_candidate(stt, artist, title, status="skipped-owned",
                             seed=seed)
            st.save(cfg.state_file, stt)
            continue
        if decision == "retry":
            c = st.find_candidate(stt, artist, title)
            if c:
                c["status"] = "queued"
                c["seed"] = seed
                log.info("retrying (strike %d/%d): %s - %s",
                         c.get("strikes", 0), cfg.max_strikes, artist, title)
        elif decision == "new":
            st.add_candidate(stt, artist, title, status="queued", seed=seed)

        picked.append({"artist": artist, "title": title})
        artist_picks[skey[0]] = artist_picks.get(skey[0], 0) + 1
        if len(picked) >= cfg.cap:
            break

    st.save(cfg.state_file, stt)
    return picked


def _seed_label(seed_artist: str, seed_title: str) -> str:
    return f"similar to {seed_artist} — {seed_title}"
