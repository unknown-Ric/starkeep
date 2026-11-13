"""Configuration loading, first-run interactive setup, and validation."""

from __future__ import annotations

import getpass
import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

CONFIG_FILE = "config.yaml"

# Path fields that must be expanded (~) and resolved once at load time, so
# every later user (download, decide, StateLock, state.save) sees the same
# absolute path regardless of the cwd the cron run happens to start in.
_PATH_FIELDS = ("incoming_dir", "library_dir", "state_file", "log_dir",
                "blacklist_file")
_INT_FIELDS = ("cap", "expiry_days", "similar_per_seed", "max_strikes",
               "expiry_runs", "min_plays", "carry_over_runs",
               "max_per_artist", "harvest_days", "requeue_after_days")

# Bare read-only commands (config/status/show-state) fall back here when the
# current directory has no config and the dir itself is configured.
CLI_FALLBACK_CONFIG_DIR = "/etc/starkeep"

# Promotional image GitHub builds per repo; survives forks/renames via config.
# PNG lives next to starkeep-logo.svg so covers need no SVG rasterizer.
DEFAULT_PLAYLIST_ICON = "https://raw.githubusercontent.com/unknown-Ric/starkeep/main/starkeep-logo.png"

# Never written to config.yaml; they come from the environment (see save()).
_SECRETS = ("lastfm_api_key", "subsonic_password")


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
    expiry_days: int = 7          # audition lifetime: unstarred after this many
                                  # days is deleted at the next cleaning run
    similar_per_seed: int = 3     # similar tracks pulled per seed track
    max_strikes: int = 2          # >= this many failures -> never retry
    # Cleaning-cycle audition lifetime (alternative to day-based):
    expiry_runs: int = 0          # 0 = pure day-based (expiry_days); > 0 = an
                                  # unstarred batch expires after this many
                                  # cleaning passes (heard only, min_plays)
    min_plays: int = 1            # Navidrome playCount >= this counts as "heard"
    carry_over_runs: int = 1      # extra passes for tracks that were never played
    downloader: str = "ytdlp"     # only "ytdlp" supported for now (MeTube later)
    max_per_artist: int = 2       # variety cap: at most this many tracks per
                                  # artist in one run's batch
    audio_format: str = "m4a"
    harvest_days: int = 7         # scrobble window feeding the harvest
    # Days before an expired candidate becomes harvestable again. 0 = the old
    # permanent rejection (never add back); e.g. 21 = one Monday cycle of 3.
    requeue_after_days: int = 0
    # Manual blocklist, parsed by blacklist.py. Empty = use blacklist.txt next
    # to the config file if it exists. "no.txt" disables the feature entirely.
    blacklist_file: str = ""
    # Navidrome-native playlist cover: the repo logo (PNG). Any PNG/JPEG/
    # WEBP/GIF/SVG URL can substitute it (SVG is rasterized server-side, needs
    # cairosvg); empty string disables the upload entirely.
    playlist_icon: str = DEFAULT_PLAYLIST_ICON
    # The playlist starkeep rebuilds every run (in place, id stays stable) —
    # players pin it by this name. Changing it orphans the old playlist:
    # delete it manually.
    playlist_name: str = "Starkeep Audition"
    # Cron monitoring (healthchecks.io style): GET on success, GET /fail on
    # error. Empty = disabled.
    ping_url: str = ""

    def with_api_key(self) -> "Config":
        # credentials may live in the environment instead of config.yaml;
        # the environment wins so rotating a secret never needs a file edit
        env_key = os.environ.get("LASTFM_API_KEY", "")
        env_pw = os.environ.get("SUBSONIC_PASSWORD", "")
        updates = {}
        if env_key:
            updates["lastfm_api_key"] = env_key
        if env_pw:
            updates["subsonic_password"] = env_pw
        return replace(self, **updates) if updates else self


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
    for k in _INT_FIELDS:  # YAML happily yields cap: "30" as a string
        if k in data and not isinstance(data[k], int):
            try:
                data[k] = int(data[k])
            except (TypeError, ValueError):
                print(f"warning: {k} must be a number, using the default",
                      file=sys.stderr)
                del data[k]
    for k in _PATH_FIELDS:  # expand ~ and pin to one absolute path per config
        if data.get(k):
            pp = Path(str(data[k])).expanduser()
            if not pp.is_absolute():
                # relative to config.yaml, never to the cwd cron happens to use
                pp = Path(directory).resolve() / pp
            data[k] = str(pp.resolve())
    return Config(**{k: v for k, v in data.items() if k in Config.__dataclass_fields__})


def first_run_setup(directory: str | Path = ".") -> Config:
    """Interactive walkthrough when config.yaml is missing."""
    print("No config.yaml found — first-run setup")
    print("(stored locally; the file is gitignored and chmod 600)\n")

    def ask(prompt: str, default: str = "") -> str:
        val = input(f"  {prompt}" + (f" [{default}]" if default else "") + ": ").strip()
        return val or default

    home = Path.home()
    if Path("/config").is_dir():  # inside the Docker container: defaults must
        # land on the persistent mounts (/music, /config), not the container FS
        inc_def, lib_def, conf_def = "/music/_incoming", "/music/library", Path("/config")
    else:
        inc_def, lib_def, conf_def = (str(home / "music" / "_incoming"),
                                      str(home / "music" / "library"),
                                      home / ".starkeep")
    cfg = Config(
        lastfm_user=ask("Last.fm username"),
        subsonic_url=ask("Subsonic/Navidrome base URL", "http://localhost:4533"),
        subsonic_user=ask("Subsonic username"),
        subsonic_password=getpass.getpass("  Subsonic password: "),
        # The audition folder must be scanned by Navidrome, otherwise you cannot
        # star tracks during the week.
        incoming_dir=ask("Audition folder inside your Navidrome library", inc_def),
        library_dir=ask("Permanent library root", lib_def),
        state_file=ask("State file path", str(conf_def / "state.json")),
        log_dir=ask("Log directory", str(conf_def / "logs")),
    )
    save(directory, cfg)
    print(f"\nSaved to {config_path(directory)} (permissions 600 where supported).")
    print("Put your free Last.fm API key in the LASTFM_API_KEY environment variable:")
    print("  https://www.last.fm/api/account/create")
    print("Then run: starkeep doctor\n")
    return cfg


def save(directory: str | Path, cfg: Config) -> None:
    """Write only what differs from the defaults (behavior keys; secrets belong
    in the environment — see README). Load() fills the rest from defaults."""
    p = config_path(directory)
    p.parent.mkdir(parents=True, exist_ok=True)
    defaults = Config()
    data = {k: v for k, v in cfg.__dict__.items()
            if v != getattr(defaults, k) and k not in _SECRETS}
    header = ("# starkeep config - only values that differ from the defaults.\n"
              "# Every option is documented in config.example.yaml.\n"
              "# Secrets (LASTFM_API_KEY, SUBSONIC_PASSWORD) live in the env.\n")
    p.write_text(header + yaml.dump(data, sort_keys=False), encoding="utf-8")
    try:  # the file may hold the Subsonic password
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
    if cfg.expiry_runs < 0:
        problems.append("expiry_runs must be >= 0 (0 = day-based expiry)")
    if cfg.carry_over_runs < 0:
        problems.append("carry_over_runs must be >= 0")
    if cfg.min_plays < 1:
        problems.append("min_plays must be >= 1")
    if cfg.similar_per_seed < 1:
        problems.append("similar_per_seed must be >= 1")
    if cfg.max_strikes < 1:
        problems.append("max_strikes must be >= 1")
    if cfg.max_per_artist < 1:
        problems.append("max_per_artist must be >= 1")
    if cfg.harvest_days < 1:
        problems.append("harvest_days must be >= 1")
    if cfg.requeue_after_days < 0:
        problems.append("requeue_after_days must be >= 0 (0 = never requeue)")
    if cfg.downloader != "ytdlp":
        problems.append("downloader must be 'ytdlp' in v1")
    if not cfg.playlist_name.strip():
        problems.append("playlist_name must not be empty")

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
