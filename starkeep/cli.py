"""CLI: starkeep run | dry-run | status | doctor | show-state"""

from __future__ import annotations

import argparse
import json
import sys

from . import config as config_mod
from . import state as st
from . import runner
from .lastfm import LastfmClient
from .subsonic import SubsonicClient


def status(cfg) -> int:
    stt = st.load(cfg.state_file)
    print(f"last run        : {stt.get('last_run')}")
    print(f"candidates seen : {len(stt.get('candidates', []))}")
    for n in ("queued", "downloaded", "promoted", "failed", "expired", "skipped-owned"):
        k = sum(1 for c in stt.get("candidates", []) if c["status"] == n)
        print(f"  {n:<14}: {k}")
    print(f"moves recorded  : {len(stt.get('moved', []))}")
    print(f"deletes recorded: {len(stt.get('deleted', []))}")
    return 0


def doctor(cfg) -> int:
    ok = True
    for problem in config_mod.validate(cfg):
        print(f"[FAIL] {problem}")
        ok = False
    if cfg.lastfm_api_key:
        try:
            lfm = LastfmClient(cfg.lastfm_api_key, cfg.lastfm_user)
            lfm.recent_track_seeds(days=7, limit=5)
            print("[ OK ] Last.fm API reachable, history readable")
        except Exception as e:
            print(f"[FAIL] Last.fm API: {e}")
            ok = False
    try:
        sub = SubsonicClient(cfg.subsonic_url, cfg.subsonic_user, cfg.subsonic_password)
        sub.ping()
        print("[ OK ] Subsonic reachable + authenticated")
        stars = sub.starred_songs()
        if stars and not stars[0].get("starred"):
            print("[WARN] getStarred2 returned no 'starred' timestamps — decide's expiry math will be approximate")
        print(f"[ OK ] {len(stars)} starred songs visible")
    except Exception as e:
        print(f"[FAIL] Subsonic: {e}")
        ok = False
    import shutil
    if shutil.which("yt-dlp"):
        print("[ OK ] yt-dlp found on PATH")
    else:
        print("[FAIL] yt-dlp not on PATH (pipx install yt-dlp)")
        ok = False
    return 0 if ok else 1


def main() -> None:
    ap = argparse.ArgumentParser(prog="starkeep", description=__doc__)
    ap.add_argument("--config-dir", default=".", help="directory holding config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="full pipeline (harvest, download, decide)")
    sub.add_parser("dry-run", help="show what promote/delete would do, touch nothing")
    sub.add_parser("status", help="summary of last run from state.json")
    sub.add_parser("doctor", help="validate config and connectivity")
    sub.add_parser("show-state", help="print raw state.json")
    args = ap.parse_args()

    cfg = config_mod.load(args.config_dir) or config_mod.first_run_setup(args.config_dir)
    cfg = cfg.with_api_key()

    if args.cmd == "status":
        sys.exit(status(cfg))
    if args.cmd == "show-state":
        print(json.dumps(st.load(cfg.state_file).get("path") and
              {k: v for k, v in st.load(cfg.state_file).items()}, indent=2, default=str))
        sys.exit(0)
    if args.cmd == "doctor":
        sys.exit(doctor(cfg))
    if args.cmd in ("run", "dry-run"):
        config_mod.ensure_dirs(cfg)
        problems = config_mod.validate(cfg)
        if problems:
            print("config problems (run `starkeep doctor` for details):")
            for p in problems:
                print(f"  - {p}")
            sys.exit(1)
        result = runner.run(cfg, dry_run=(args.cmd == "dry-run"))
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
