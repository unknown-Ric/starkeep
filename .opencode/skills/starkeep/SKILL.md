---
name: Starkeep Architecture
description: Map of the starkeep codebase — pipeline stages, module responsibilities, state file, and the safety invariants that must never break. Load this BEFORE reading source files when working on starkeep.
---

# Starkeep architecture

Starkeep is a cron-powered music discovery pipeline for Navidrome/Subsonic:
Last.fm candidates → yt-dlp → an `incoming/` audition folder → user stars what
they want to keep → starred tracks are promoted into the library, the rest
expires and is deleted. Full user docs live in `README.md` — read that, not the
source, for behavior questions.

## Pipeline (one run = `runner.py`, in this order)

1. **reconcile** — read-only scan of `incoming/` for files state doesn't know
   about (orphans from a crash). Orphans are only *reported*, never deleted.
2. **decide** — starred audition tracks are promoted into
   `<library>/<Artist>/<Album>/NN. Title`; candidates past `expiry_days`
   (default 14) that were never starred are deleted.
3. **harvest** — Last.fm `track.getSimilar` seeded by this week's most-played
   distinct tracks; deduped against the real library (`search3`) and prior
   candidates; capped at `cap` (default 30).
4. **download** — yt-dlp one match per candidate (`ytsearch1:Artist - Title
   audio`) into `incoming/`, retagged immediately with real Last.fm
   artist/title/album/track data; cover art: Deezer (exact match only) →
   Last.fm image → video thumbnail.
5. **playlist** — rebuild the fixed `Starkeep Audition` playlist (delete +
   create) to mirror `incoming/`. The only non-file write.

## Modules

| File | Role |
|---|---|
| `starkeep/cli.py` | argparse entry point; `run`, `dry-run`, `status`, `doctor`, `show-state` |
| `starkeep/runner.py` | orchestrates one pass, lockfile, daily log file |
| `starkeep/harvest.py` | builds this week's candidate list |
| `starkeep/download.py` | yt-dlp + retagging into `incoming/` |
| `starkeep/decide.py` | promote starred / expire stale, all deletions happen here |
| `starkeep/playlist.py` | rebuilds the audition playlist |
| `starkeep/reconcile.py` | reports orphans/missing in `incoming/` |
| `starkeep/state.py` | atomic state file + lockfile (crash safety) |
| `starkeep/subsonic.py` | minimal Subsonic client (ping, search3, getStarred2, unstar, playlist endpoints) |
| `starkeep/lastfm.py` | Last.fm API wrapper |
| `starkeep/config.py` | YAML config, validation, first-run interactive setup |

## State file (`state.json`)

Single source of truth for every candidate and downloaded path. Written
atomically (tmp file + `os.replace`) after every candidate transition.
Deletions only ever happen for paths recorded here, still existing, and
resolving inside `incoming_dir`.

## Safety invariants — DO NOT BREAK

These are promises documented in the README; any change touching
`decide.py`, `state.py`, `download.py`, or `reconcile.py` must preserve them:

- Downloads only land in `incoming_dir`.
- Deletions are strictly state-file-driven; there is no glob-delete anywhere.
- The library is only ever added to; existing target files are never
  overwritten (the audition copy expires instead).
- Only the `Starkeep Audition` playlist is ever touched.
- A crash loses at most the in-flight step, never the state file, and never
  double-deletes.
- Orphans in `incoming/` are reported, never auto-deleted.
- Failed downloads get strikes; after `max_strikes` (default 2) they are never
  retried, and expired tracks are never re-queued.
