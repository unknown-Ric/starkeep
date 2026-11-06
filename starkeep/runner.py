"""4. RUN — orchestrate harvest -> download -> decide for one weekly cron pass."""

from __future__ import annotations

import logging
import time
from datetime import datetime

from . import state as st
from . import harvest, download, decide
from .config import Config
from .lastfm import LastfmClient
from .subsonic import SubsonicClient

log = logging.getLogger(__name__)


def setup_logging(cfg: Config) -> None:
    logfile = f"{datetime.now():%Y-%m-%d}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(f"{cfg.log_dir}/{logfile}", encoding="utf-8"),
        ],
    )


def run(cfg: Config, dry_run: bool = False) -> dict:
    setup_logging(cfg)
    with st.StateLock(cfg.state_file):  # prevent overlapping cron runs
        stt = st.load(cfg.state_file)
        lfm = LastfmClient(cfg.lastfm_api_key, cfg.lastfm_user) if cfg.lastfm_api_key else None
        sub = SubsonicClient(cfg.subsonic_url, cfg.subsonic_user, cfg.subsonic_password)
        sub.ping()
        stt["last_run"] = st.utcnow()

        if dry_run:
            return decide.decide(cfg, sub, lfm, stt, dry_run=True)

        result: dict = {}
        candidates = harvest.harvest(cfg, lfm, sub, stt)
        st.save(cfg.state_file, stt)
        result["candidates"] = len(candidates)

        result["downloaded"] = download.download_all(cfg, candidates, stt)
        result["decide"] = decide.decide(cfg, sub, lfm, stt)
        st.save(cfg.state_file, stt)

        log.info(
            "run done: %d candidates, %d downloaded, %d promoted, %d deleted",
            result["candidates"], result["downloaded"],
            len(result["decide"]["keeps"]), len(result["decide"]["deletes"]),
        )
        return {
            "candidates": result["candidates"],
            "downloaded": result["downloaded"],
            "promoted": result["decide"]["keeps"],
            "expires": result["decide"]["deletes"],
        }
