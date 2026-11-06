"""Configuration loading, first-run interactive setup, and validation."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field, replace
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
    # Paths
    incoming_dir: str = ""  # audition folder; created if missing
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
    return Config(**{k: v for k, v in data.items() if k in Config.__dataclass_fields__})


def first_run_setup(directory: str | Path = ".") -> Config:
    """Interactive walkthrough when config.yaml is missing."""
    print("No config.yaml found — first-run setup (values are stored locally, never committed)\n")
    def ask(prompt: str, default: str = "") -> str:
        val = input(f"  {prompt}" + (f" [{default}]" if default else "") + ": ").strip()
        return val or default

    cfg = Config(
        lastfm_user=ask("Last.fm username", ""),
        subsonic_url=ask("Subsonic/Navidrome base URL", "http://localhost:4533"),
        subsonic_user=ask("Subsonic username", ""),
        subsonic_password=input("  Subsonic password (input hidden? no — plain input): ").strip(),
        incoming_dir=ask("Incoming/audition folder (absolute path)", str(Path.home() / "music-incoming")),
        library_dir=ask("Library root (promote target)", str(Path.home() / "music-library")),
        state_file=ask("State file path", str(Path.home() / ".starkeep" / "state.json")),
        log_dir=ask("Log directory", str(Path.home() / ".starkeep" / "logs")),
    )
    save(directory, cfg)
    print(f"\nSaved to {config_path(directory)}. Put your Last.fm API key in the"
          f" LASTFM_API_KEY environment variable (free key: https://www.last.fm/api).\n")
    return cfg


def save(directory: str | Path, cfg: Config) -> None:
    p = config_path(directory)
    p.write_text(yaml.dump(cfg.__dict__, sort_keys=False), encoding="utf-8")


def validate(cfg: Config) -> list[str]:
    """Return a list of human-readable problems (empty = OK)."""
    problems = []
    if not cfg.lastfm_api_key:
        problems.append("Last.fm API key missing — set the LASTFM_API_KEY env var")
    for name in ("subsonic_url", "subsonic_user", "subsonic_password"):
        if not getattr(cfg, name):
            problems.append(f"{name} is not set")
    if cfg.cap < 1:
        problems.append("cap must be >= 1")
    if Path(cfg.incoming_dir).resolve() == Path(cfg.library_dir).resolve():
        problems.append("incoming_dir and library_dir must differ")
    return problems


def ensure_dirs(cfg: Config) -> None:
    Path(cfg.incoming_dir).mkdir(parents=True, exist_ok=True)
    Path(cfg.library_dir).mkdir(parents=True, exist_ok=True)
    if cfg.state_file:
        Path(cfg.state_file).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg.log_dir).mkdir(parents=True, exist_ok=True)
