"""Configuration loading, first-run interactive setup, and validation."""

from __future__ import annotations

import getpass
import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

CONFIG_FILE = "config.yaml"


@dataclass
class Config:
    # Last.fm
    lastfm_user: str = ""
    lastfm_api_key: str = ""  # read from env LASTFM_API_KEY if empty
    # Subsonic/Navidrome
    subsonic_url: str = ""  # e.g. http://127.0.0.1:4533
    subsonic_user: str = ""
    subsonic_password: str = ""
    # Paths (absolute)
    incoming_dir: str = ""  # audition folder; must be visible to Navidrome
    library_dir: str = ""   # promote target root (never touched except adds)
    state_file: str = ""    # state.json location
    log_dir: str = ""
    # Behavior
    cap: int = 30                 # candidates per run
    expiry_days: int = 14         # unstarred-orphan lifetime in incoming/
    similar_per_seed: int = 3     # similar tracks pulled per seed track
    max_strikes: int = 2          # >= this many failures -> never retry
    downloader: str = "ytdlp"     # only "ytdlp" supported for now (MeTube later)
    audio_format: str = "m4a"

    def with_api_key(self) -> "Config":
        if not self.lastfm_api_key:
            env = os.environ.get("LASTFM_API_KEY", "")
            if env:
                return replace(self, lastfm_api_key=env)
        return self


def config_path(directory: str | Path) -> Path:
    return Path(directory) / CONFIG_FILE


def load(directory: str | Path = ".") -> Config | None:
    """Load config, or return None if it doesn't exist (first run)."""
    p = config_path(directory)
    if not p.is_file():
        return None
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    unknown = set(data) - set(Config.__dataclass_fields__)
    if unknown:
        print(f"warning: ignoring unknown config keys: {', '.join(sorted(unknown))}",
              file=sys.stderr)
    return Config(**{k: v for k, v in data.items() if k in Config.__dataclass_fields__})


def first_run_setup(directory: str | Path = ".") -> Config:
    """Interactive walkthrough when config.yaml is missing."""
    print("No config.yaml found — first-run setup")
    print("(stored locally; the file is gitignored and chmod 600)\n")

    def ask(prompt: str, default: str = "") -> str:
        val = input(f"  {prompt}" + (f" [{default}]" if default else "") + ": ").strip()
        return val or default

    home = Path.home()
    cfg = Config(
        lastfm_user=ask("Last.fm username"),
        subsonic_url=ask("Subsonic/Navidrome base URL", "http://localhost:4533"),
        subsonic_user=ask("Subsonic username"),
        subsonic_password=getpass.getpass("  Subsonic password: "),
        # The audition folder must be scanned by Navidrome, otherwise you cannot
        # star tracks during the week.
        incoming_dir=ask("Audition folder inside your Navidrome library", str(home / "music" / "_incoming")),
        library_dir=ask("Permanent library root", str(home / "music" / "library")),
        state_file=ask("State file path", str(home / ".starkeep" / "state.json")),
        log_dir=ask("Log directory", str(home / ".starkeep" / "logs")),
    )
    save(directory, cfg)
    print(f"\nSaved to {config_path(directory)} (permissions 600 where supported).")
    print("Put your free Last.fm API key in the LASTFM_API_KEY environment variable:")
    print("  https://www.last.fm/api/account/create")
    print("Then run: starkeep doctor\n")
    return cfg


def save(directory: str | Path, cfg: Config) -> None:
    p = config_path(directory)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.dump(cfg.__dict__, sort_keys=False), encoding="utf-8")
    try:  # the file holds the Subsonic password
        os.chmod(p, 0o600)
    except OSError:
        pass


def validate(cfg: Config, require_lastfm: bool = True) -> list[str]:
    """Return a list of human-readable problems (empty = OK)."""
    problems: list[str] = []
    if require_lastfm:
        if not cfg.lastfm_api_key:
            problems.append("Last.fm API key missing — set the LASTFM_API_KEY env var")
        if not cfg.lastfm_user:
            problems.append("lastfm_user is not set")
    for name in ("subsonic_url", "subsonic_user", "subsonic_password"):
        if not getattr(cfg, name):
            problems.append(f"{name} is not set")
    for name in ("incoming_dir", "library_dir", "state_file", "log_dir"):
        if not getattr(cfg, name):
            problems.append(f"{name} is not set")

    if cfg.cap < 0:
        problems.append("cap must be >= 0 (0 = decide-only run)")
    if cfg.expiry_days < 1:
        problems.append("expiry_days must be >= 1")
    if cfg.similar_per_seed < 1:
        problems.append("similar_per_seed must be >= 1")
    if cfg.max_strikes < 1:
        problems.append("max_strikes must be >= 1")
    if cfg.downloader != "ytdlp":
        problems.append("downloader must be 'ytdlp' in v1")

    if cfg.incoming_dir and cfg.library_dir:
        incoming = Path(cfg.incoming_dir).expanduser().resolve()
        library = Path(cfg.library_dir).expanduser().resolve()
        if incoming == library:
            problems.append("incoming_dir and library_dir must differ")
        elif library.is_relative_to(incoming):
            problems.append("library_dir must not live inside incoming_dir "
                            "(deletes are restricted to incoming_dir)")
    return problems


def ensure_dirs(cfg: Config) -> None:
    Path(cfg.incoming_dir).expanduser().mkdir(parents=True, exist_ok=True)
    Path(cfg.library_dir).expanduser().mkdir(parents=True, exist_ok=True)
    if cfg.state_file:
        Path(cfg.state_file).expanduser().parent.mkdir(parents=True, exist_ok=True)
    if cfg.log_dir:
        Path(cfg.log_dir).expanduser().mkdir(parents=True, exist_ok=True)
