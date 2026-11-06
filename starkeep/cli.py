"""CLI: starkeep run | dry-run | status | doctor | show-state"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import replace
from pathlib import Path

from . import config as config_mod
from . import reconcile
from . import runner
from . import state as st
from .lastfm import LastfmClient
from .subsonic import SubsonicClient


def status(cfg) -> int:
    stt = st.load(cfg.state_file)
    print(f"last run        : {stt.get('last_run')}")
    print(f"candidates seen : {len(stt.get('candidates', []))}")
    for name in ("queued", "downloaded", "promoted", "failed", "missing",
                 "expired", "skipped-owned"):
        count = sum(1 for c in stt.get("candidates", []) if c.get("status") == name)
        if count:
            print(f"  {name:<14}: {count}")
    print(f"moves recorded  : {len(stt.get('moved', []))}")
    print(f"deletes recorded: {len(stt.get('deleted', []))}")

    report = reconcile.scan(cfg, stt)
    if report["orphans"]:
        print(f"orphans         : {len(report['orphans'])} (never deleted; see below)")
        for path in report["orphans"][:10]:
            print(f"  ? {path}")
        if len(report["orphans"]) > 10:
            print(f"  ... and {len(report['orphans']) - 10} more")
    else:
        print("orphans         : 0")
    return 0


def _writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".starkeep-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def doctor(cfg) -> int:
    ok = True

    problems = config_mod.validate(cfg)
    for problem in problems:
        print(f"[FAIL] {problem}")
        ok = False
    if not problems:
        print("[ OK ] config complete and consistent")

    if cfg.lastfm_api_key:
        try:
            lfm = LastfmClient(cfg.lastfm_api_key, cfg.lastfm_user)
            seeds = lfm.recent_track_seeds(days=7, limit=5)
            print(f"[ OK ] Last.fm API reachable ({len(seeds)} recent distinct tracks)")
        except Exception as e:
            print(f"[FAIL] Last.fm API: {e}")
            ok = False

    try:
        sub = SubsonicClient(cfg.subsonic_url, cfg.subsonic_user, cfg.subsonic_password)
        sub.ping()
        print("[ OK ] Subsonic reachable + authenticated")
        stars = sub.starred_songs()
        print(f"[ OK ] {len(stars)} starred songs visible")
        bad = [s for s in stars if not s.get("artist") or not s.get("title")]
        if bad:
            print("[WARN] some starred songs have no artist/title; file-name matching will cover them")
    except Exception as e:
        print(f"[FAIL] Subsonic: {e}")
        ok = False

    if shutil.which("yt-dlp"):
        print("[ OK ] yt-dlp found on PATH")
    else:
        print("[FAIL] yt-dlp not on PATH (pipx install yt-dlp)")
        ok = False
    if shutil.which("ffmpeg"):
        print("[ OK ] ffmpeg found on PATH")
    else:
        print("[FAIL] ffmpeg not on PATH (required for --extract-audio)")
        ok = False

    for label, value in (("incoming_dir", cfg.incoming_dir),
                         ("library_dir", cfg.library_dir)):
        if value and _writable(Path(value).expanduser()):
            print(f"[ OK ] {label} writable")
        elif value:
            print(f"[FAIL] {label} is not writable: {value}")
            ok = False
    if cfg.state_file:
        state_parent = Path(cfg.state_file).expanduser().parent
        if _writable(state_parent):
            print("[ OK ] state_file directory writable")
        else:
            print(f"[FAIL] state_file directory not writable: {state_parent}")
            ok = False

    if cfg.incoming_dir:
        print("[INFO] Make sure incoming_dir is scanned by Navidrome, otherwise "
              "you cannot star audition tracks.")
    return 0 if ok else 1


def main() -> None:
    ap = argparse.ArgumentParser(prog="starkeep", description=__doc__)
    ap.add_argument("--config-dir", default=".", help="directory holding config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run", help="full pipeline (reconcile, decide, harvest, download)")
    run_p.add_argument("--cap", type=int, default=None,
                       help="override config cap for this run (smoke tests)")
    dry_p = sub.add_parser("dry-run", help="show what promote/delete would do, touch nothing")
    dry_p.add_argument("--cap", type=int, default=None, help="override config cap")
    sub.add_parser("status", help="summary of last run from state.json")
    sub.add_parser("doctor", help="validate config, connectivity and dependencies")
    sub.add_parser("show-state", help="print raw state.json")
    args = ap.parse_args()

    cfg = config_mod.load(args.config_dir) or config_mod.first_run_setup(args.config_dir)
    cfg = cfg.with_api_key()
    if getattr(args, "cap", None) is not None:
        cfg = replace(cfg, cap=args.cap)

    if args.cmd == "status":
        sys.exit(status(cfg))
    if args.cmd == "show-state":
        print(json.dumps(st.load(cfg.state_file), indent=2, default=str))
        sys.exit(0)
    if args.cmd == "doctor":
        sys.exit(doctor(cfg))
    if args.cmd in ("run", "dry-run"):
        config_mod.ensure_dirs(cfg)
        problems = config_mod.validate(cfg, require_lastfm=(args.cmd == "run"))
        if problems:
            print("config problems (run `starkeep doctor` for details):")
            for problem in problems:
                print(f"  - {problem}")
            sys.exit(1)
        try:
            result = runner.run(cfg, dry_run=(args.cmd == "dry-run"))
        except Exception as e:  # cron-friendly: one clear line, non-zero exit
            print(f"starkeep {args.cmd} failed: {e}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(result, indent=2, default=str))

if __name__ == "__main__":
    main()
