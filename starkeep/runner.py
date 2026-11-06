"""4. RUN — orchestrate reconcile -> decide -> harvest -> download for one cron pass."""

from __future__ import annotations

import logging
import os
from datetime import datetime

from . import decide, download, harvest, reconcile
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


def run(cfg: Config, dry_run: bool = False) -> dict:
    setup_logging(cfg)
    with st.StateLock(cfg.state_file):  # prevent overlapping cron runs
        stt = st.load(cfg.state_file)
        if not cfg.lastfm_api_key:
            raise RuntimeError("Last.fm API key missing — set LASTFM_API_KEY")
        lfm = LastfmClient(cfg.lastfm_api_key, cfg.lastfm_user)
        sub = SubsonicClient(cfg.subsonic_url, cfg.subsonic_user, cfg.subsonic_password)
        sub.ping()
        stt["last_run"] = st.utcnow()

        report = reconcile.scan(cfg, stt)
        for path in report["orphans"]:
            log.warning("orphan in incoming/ (not in state, not touched): %s", path)

        if dry_run:
            result = decide.decide(cfg, sub, lfm, stt, dry_run=True)
            result["orphans"] = report["orphans"]
            return result

        if report["missing"]:
            reconcile.apply_missing(stt, report["missing"])
            st.save(cfg.state_file, stt)

        # Decide first: last week's stars are promoted/deleted even if this
        # week's harvesting fails (Last.fm outage, empty history, ...).
        decision = decide.decide(cfg, sub, lfm, stt)

        candidates = harvest.harvest(cfg, lfm, sub, stt)
        downloaded = download.download_all(cfg, candidates, stt)
        st.save(cfg.state_file, stt)

        log.info(
            "run done: %d candidates, %d downloaded, %d promoted, %d deleted, %d orphans",
            len(candidates), downloaded, len(decision["keeps"]),
            len(decision["deletes"]), len(report["orphans"]),
        )
        return {
            "candidates": len(candidates),
            "downloaded": downloaded,
            "promoted": decision["keeps"],
            "expires": decision["deletes"],
            "orphans": report["orphans"],
        }
