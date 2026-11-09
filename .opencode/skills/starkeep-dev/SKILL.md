---
name: Starkeep Dev Workflow
description: How to install, test, and run starkeep locally — pytest setup, CLI commands, dry-runs, and systemd deployment. Load this before running or testing starkeep.
---

# Starkeep dev workflow

Pure-Python (3.10+), no services needed for tests. Deps: requests, PyYAML,
mutagen. External binaries `yt-dlp` and `ffmpeg` are only needed for real
downloads.

## Setup and tests

```bash
cd starkeep
pip install -e ".[dev]"
pytest
```

The safety-critical tests (
`tests/test_state.py`, `test_decide.py`, `test_reconcile.py`) cover atomic
state writes, the exact expiry boundary, and refusal to delete outside
`incoming_dir`. If you change `state.py` or `decide.py`, run those and add a
case for any new transition.

## CLI commands

```bash
starkeep doctor                  # validate config, connectivity, yt-dlp/ffmpeg
starkeep dry-run                 # show promote/expire plan, touches nothing
starkeep run --cap 0             # decide-only pass (no downloads)
starkeep status                  # last run, candidate counts, orphans
starkeep show-state              # raw state.json dump
```

All commands accept `--config-dir /path/to/dir` (default: cwd). Real runs
need `LASTFM_API_KEY` in the env and a working Navidrome — don't run `run`
against the user's real library without asking.

## Deployment

systemd units in `deploy/` (service + timer); plain cron works too — the
built-in lockfile makes overlapping runs harmless. After promotion, Navidrome
needs a library scan before new albums appear.
