"""4. RUN — orchestrate reconcile -> decide -> harvest -> download for one cron pass."""

from __future__ import annotations

import logging
import os
from datetime import datetime

from . import decide, download, harvest, playlist, reconcile
from . import report as report_mod
from . import state as st
from .config import Config
from .lastfm import LastfmClient
from .subsonic import SubsonicClient

log = logging.getLogger(__name__)


def setup_logging(cfg: Config) -> None:
    os.makedirs(cfg.log_dir, exist_ok=True)
    logfile = f"{datetime.now():%Y-%m-%d}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(os.path.join(cfg.log_dir, logfile), encoding="utf-8"),
        ],
        force=True,
    )


def _restar_promoted(sub: SubsonicClient, stt: dict, promoted: list) -> int:
    """Moving the file creates a new server song: the star (and its playlist
    membership) does not follow. Re-apply the star after the scan so users get
    their star back without a manual step."""
    by_src = {c.get("file_path"): c for c in stt["candidates"]}
    re_starred = 0
    for mv in stt.get("moved", []):
        c = by_src.get(mv.get("from"))
        if not c or mv.get("to") not in promoted:
            continue
        try:
            sid = playlist._song_id_for(
                sub, {"artist": c["artist"], "title": c["title"], "file_path": mv["to"]})
            if sid:
                sub.star(sid)
                re_starred += 1
        except Exception as e:
            log.warning("re-star failed for %s - %s: %s", c["artist"], c["title"], e)
    if re_starred:
        log.info("re-starred %d promoted track(s)", re_starred)
    return re_starred


def run(cfg: Config, dry_run: bool = False, expire: bool = True) -> dict:
    setup_logging(cfg)
    with st.StateLock(cfg.state_file):  # prevent overlapping cron runs
        stt = st.load(cfg.state_file)
        lfm = LastfmClient(cfg.lastfm_api_key, cfg.lastfm_user) if cfg.lastfm_api_key else None
        sub = SubsonicClient(cfg.subsonic_url, cfg.subsonic_user, cfg.subsonic_password)
        sub.ping()
        stt["last_run"] = st.utcnow()

        report = reconcile.scan(cfg, stt)
        for path in report["orphans"]:
            log.warning("orphan in incoming/ (not in state, not touched): %s", path)

        if dry_run:
            result = decide.decide(cfg, sub, lfm, stt, dry_run=True, expire=expire)
            result["orphans"] = report["orphans"]
            return result

        if lfm is None:
            raise RuntimeError("Last.fm API key missing — set LASTFM_API_KEY")

        if report["missing"]:
            reconcile.apply_missing(stt, report["missing"])
            st.save(cfg.state_file, stt)

        # Decide first: last week's stars are promoted/deleted even if this
        # week's harvesting fails (Last.fm outage, empty history, ...).
        decision = decide.decide(cfg, sub, lfm, stt, expire=expire)

        candidates = harvest.harvest(cfg, lfm, sub, stt)
        downloaded = download.download_all(cfg, candidates, stt, lfm)
        st.save(cfg.state_file, stt)

        # New files (promoted + this week's downloads) have no server song id
        # until a scan runs. Trigger it and wait, so the audition playlist can
        # reference this run's files right away instead of lagging a week.
        if not dry_run and (decision["keeps"] or downloaded):
            try:
                sub.wait_for_scan()
            except Exception as e:
                log.warning("scan trigger failed (needs a Navidrome-compatible server): %s", e)
            _restar_promoted(sub, stt, decision["keeps"])

        try:  # playlist must not fail the run's bookkeeping
            pl = playlist.sync_audition(sub, stt, icon_url=cfg.playlist_icon,
                                        name=cfg.playlist_name)
        except Exception as e:
            log.warning("audition playlist sync failed: %s", e)
            pl = {}

        result = {
            "candidates": len(candidates),
            "downloaded": downloaded,
            "promoted": decision["keeps"],
            "expires": decision["deletes"],
            "orphans": report["orphans"],
            "playlist": pl,
        }
        try:  # a broken snapshot must not fail the run
            result["report"] = report_mod.write(cfg, stt, report["orphans"])
        except Exception as e:
            log.warning("status.html render failed: %s", e)

        log.info(
            "run done: %d candidates, %d downloaded, %d promoted, %d deleted, %d orphans, playlist %s",
            len(candidates), downloaded, len(decision["keeps"]),
            len(decision["deletes"]), len(report["orphans"]),
            pl.get("songs", "unchanged"),
        )
        return result
