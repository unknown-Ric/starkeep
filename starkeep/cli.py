"""CLI: starkeep run | dry-run | status | doctor | show-state"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import replace
from pathlib import Path

import requests

from . import config as config_mod
from . import blacklist
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

    entries = blacklist.load(cfg.blacklist_file)
    if cfg.blacklist_file:
        print(f"[ OK ] blacklist: {len(entries)} entries ({cfg.blacklist_file})")
    else:
        print("[INFO] no blacklist — create blacklist.txt next to the config")

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


def _ping(url: str, ok: bool) -> None:
    """healthchecks.io-style ping: success hits url, failure hits url/fail.
    Best-effort: monitoring must never mask or break the real result."""
    if not url:
        return
    try:
        requests.get(url if ok else f"{url}/fail", timeout=10)
    except Exception:
        pass


def main() -> None:
    ap = argparse.ArgumentParser(prog="starkeep", description=__doc__)
    ap.add_argument("--config-dir", default=".", help="directory holding config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run", help="full pipeline (reconcile, decide, harvest, download)")
    run_p.add_argument("--cap", type=int, default=None,
                       help="override config cap for this run (smoke tests)")
    run_p.add_argument("--no-expire", action="store_true",
                       help="promotion-only pass: promote stars, skip expiry/deletion "
                            "(cleaning happens on the weekly full run)")
    run_p.add_argument("--since", metavar="YYYY-MM-DD", default=None,
                       help="harvest scrobbles starting from this date instead "
                            "of the default harvest_days window (spot runs)")
    dry_p = sub.add_parser("dry-run", help="show what promote/delete would do, touch nothing")
    dry_p.add_argument("--cap", type=int, default=None, help="override config cap")
    dry_p.add_argument("--no-expire", action="store_true",
                       help="show what a promotion-only pass would do")
    forget_p = sub.add_parser("forget", help="remove a candidate from state so it can be re-harvested")
    forget_p.add_argument("artist", help="artist as shown in the state/playlist")
    forget_p.add_argument("title", help="title as shown in the state/playlist")
    sub.add_parser("status", help="summary of last run from state.json")
    sub.add_parser("doctor", help="validate config, connectivity and dependencies")
    sub.add_parser("show-state", help="print raw state.json")
    args = ap.parse_args()

    cfg = config_mod.load(args.config_dir) or config_mod.first_run_setup(args.config_dir)
    import datetime as dt
    cfg = cfg.with_api_key()
    if not cfg.blacklist_file:
        probe = Path(args.config_dir) / blacklist.FILENAME
        if probe.is_file():
            cfg = replace(cfg, blacklist_file=str(probe))
    if getattr(args, "cap", None) is not None:
        cfg = replace(cfg, cap=args.cap)
    if getattr(args, "since", None):
        try:
            start = dt.date.fromisoformat(args.since)
        except ValueError:
            ap.error(f"--since needs an ISO date (YYYY-MM-DD), got {args.since!r}")
        if start > dt.date.today():
            ap.error(f"--since {args.since} is in the future")
        cfg = replace(cfg, harvest_days=(dt.date.today() - start).days + 1)

    if args.cmd == "status":
        sys.exit(status(cfg))
    if args.cmd == "forget":
        config_mod.validate(cfg, require_lastfm=False)  # still want a sane config
        stt = st.load(cfg.state_file)
        removed = st.forget_candidate(stt, args.artist, args.title)
        if removed:
            st.save(cfg.state_file, stt)
            print(f"forgot {removed} candidate(s): {args.artist} - {args.title}")
            sys.exit(0)
        print(f"no candidate in state matches: {args.artist} - {args.title}", file=sys.stderr)
        sys.exit(1)
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
            result = runner.run(cfg, dry_run=(args.cmd == "dry-run"),
                                expire=not getattr(args, "no_expire", False))
        except Exception as e:  # cron-friendly: one clear line, non-zero exit
            print(f"starkeep {args.cmd} failed: {e}", file=sys.stderr)
            _ping(cfg.ping_url, ok=False)
            sys.exit(1)
        if args.cmd == "run":
            _ping(cfg.ping_url, ok=True)
        print(json.dumps(result, indent=2, default=str))

if __name__ == "__main__":
    main()
