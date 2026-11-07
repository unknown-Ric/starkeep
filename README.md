# starkeep

<div align="center">
  <img src="starkeep-logo.svg" alt="starkeep logo" width="160">
</div>

> Cron-powered music discovery for [Navidrome](https://navidrome.org) (or any
> Subsonic-compatible server): Last.fm-backed candidates are downloaded into an
> audition folder, **star a track to keep it in your library**, everything else
> quietly expires.

No daemon, no web UI, no database. It composes tools you already run: Navidrome,
[yt-dlp](https://github.com/yt-dlp/yt-dlp), ffmpeg, cron — plus a free Last.fm
API key.

```
Last.fm scrobbles ──▶ similar tracks ──▶ yt-dlp ──▶ incoming/ (audition week)
                                                        │
        star in any Subsonic app (offline OK) ──────────┤
                                                        ├─ starred → library/<Artist>/<Album>/NN. Title
        not starred for 14 days ────────────────────────┴─ deleted
```

<div align="center">
  <img src="starkeep-how-it-works.gif" alt="How starkeep works: download into the audition folder, star to keep, the rest expires" width="560">
</div>

## Requirements

- Python 3.10+
- Navidrome (or another Subsonic-compatible server)
- `yt-dlp` and `ffmpeg` on `PATH`
- A free [Last.fm API key](https://www.last.fm/api/account/create)

**The audition folder must be visible to Navidrome**, typically as a subfolder
of your existing music library (`/music/_incoming`) or as its own Navidrome
music folder. Otherwise you cannot star audition tracks, and there is nothing
for starkeep to read back. The permanent library root should be scanned too.

## Install

```bash
pipx install git+https://github.com/unknown-Ric/starkeep
# or from a clone:
pipx install .
```

Then:

```bash
export LASTFM_API_KEY="..."     # put this in your shell/env file, not config.yaml
starkeep doctor                 # first run asks for the rest and validates everything
```

`starkeep doctor` checks config consistency, Last.fm, Subsonic auth, yt-dlp,
ffmpeg and folder permissions. It also warns whether your starred songs expose
artist/title fields. `config.yaml` stores the Subsonic password and is written
with mode `600`.

## Commands

| Command | Purpose |
|---|---|
| `starkeep run` | Full pass: reconcile, promote/expire, harvest, download, rebuild audition playlist |
| `starkeep dry-run` | Show what would be promoted/deleted; touches nothing |
| `starkeep status` | Last run, candidate counts, orphans in `incoming/` |
| `starkeep doctor` | Validate config, connectivity, dependencies |
| `starkeep show-state` | Raw `state.json` (debugging) |

All commands accept `--config-dir /path/to/dir` (default: current directory).
`run` and `dry-run` also accept `--cap N`; `run --cap 0` is a decide-only pass
(promote/expire without downloading anything).

## How "star to keep" works

1. **Harvest** — next week's most-played distinct tracks seed Last.fm
   `track.getSimilar`; results are deduped against your actual library
   (`search3`) and previously seen candidates, then capped.
2. **Download** — `yt-dlp` fetches one match per candidate
   (`ytsearch1:Artist - Title audio`) into `incoming/`. The file is retagged
   immediately with the real artist/title/album/track number (Last.fm), so
   Navidrome shows full info during the audition week and stars can be matched
   back reliably. Cover art: Deezer album art (1000px, exact artist+title match
   only) → Last.fm album image → the video thumbnail as last resort.
3. **Audition** — the **`Starkeep Audition`** playlist is rebuilt at the end of
   every run to match `incoming/` exactly (promoted and expired tracks drop out
   automatically). Listen in any Subsonic app and star what you want to keep
   (offline stars sync later, e.g. when you get home).
4. **Decide** — on the next run, starred tracks are promoted into
   `<library>/<Artist>/<Album>/NN. Title` (or `<Artist>/Singles/`). Last.fm
   album/track-number info fills gaps; existing tags are never overwritten
   blindly.
5. **Expire** — downloaded tracks older than `expiry_days` (default 14) that
   were never starred are deleted. With a weekly cron that means roughly two
   to three weeks of listening.

Promotion only appears in Navidrome after its next library scan. Configure a
scan schedule (or trigger one from a hook) that runs after `starkeep run`.

## Safety invariants (promises, not suggestions)

- Downloads only ever land in `incoming_dir`.
- Deletions are **state-file-driven**: only paths recorded by starkeep, still
  existing, and resolving *inside* `incoming_dir` are ever deleted. There is no
  glob-delete anywhere in the code.
- Your library is only ever **added to**. An existing target file is never
  overwritten; the audition copy is left to expire instead.
- The one non-file write is the **`Starkeep Audition` playlist**, rebuilt each
  run to mirror `incoming/` (delete + create). No other playlist is touched.
- `state.json` is written atomically after every candidate transition. A crash
  loses at most the in-flight step, never the state file, and never causes a
  double-delete.
- A lockfile prevents overlapping cron runs.
- Files in `incoming/` that are **not** recorded in state (a crash between
  download and save) are reported as *orphans* in `starkeep status` and the log
  — never deleted automatically.
- A failed download gets a strike; after `max_strikes` (default 2) it is never
  retried. Expired tracks are also never re-queued, so a rejected recommendation
  does not come back every week.

## Scheduling

systemd units are in [`deploy/`](deploy/): install both, adjust `User`,
`WorkingDirectory` and `ExecStart`, put the API key in `/etc/starkeep.env`:

```ini
LASTFM_API_KEY=...
```

```bash
sudo cp deploy/starkeep.service deploy/starkeep.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now starkeep.timer
```

Plain cron works too (the built-in lock makes overlaps harmless):

```cron
17 4 * * 1 cd /etc/starkeep && . /etc/starkeep.env && starkeep run
```

## Navidrome setup notes

- Add `incoming_dir` to a scanned music folder. Starkeep rebuilds a playlist
  named `Starkeep Audition` (fixed name) at the end of every `run`; pin it in
  your player and it is always your current audition list.
- After promotion, a rescan is required before the new album appears.
- Navidrome's scan may briefly show the audition copy and the promoted copy as
  two songs. They are the same file moved; a rescan reconciles it.
- Star IDs/paths are read through `getStarred2`; matching uses artist+title
  first and the file name as a fallback.

## Configuration

Every key is documented in [`config.example.yaml`](config.example.yaml). The
important ones:

- `incoming_dir` — audition folder, must be Navidrome-visible and writable
- `library_dir` — permanent target, must **not** be inside `incoming_dir`
- `cap` — candidates per run (default 30)
- `expiry_days` — audition lifetime (default 14)
- `max_strikes` — download failures before permanent skip (default 2)
- `audio_format` — `m4a` (default), `mp3`, `opus`, `flac`, …

## FAQ

**Does it delete music I already own?**
No. It only deletes files it downloaded itself, recorded in state, inside
`incoming_dir`. Your existing library is never a deletion candidate.

**I starred a track after it expired.**
Too late — the audition file is gone and that candidate stays permanently
rejected. Remove its entry from `state.json` (or reset the whole file) if you
want another attempt.

**The download is the wrong version (live/cover/remix).**
Let it expire, or delete it and remove the candidate from `state.json`. Audio
bias reduces but cannot eliminate bad matches.

**Can I run it more than once a week?**
Yes; the pipeline is idempotent. Expiry is measured in days, not runs.

**Does it work with Airsonic/other Subsonic servers?**
The client uses `ping`, `search3`, `getStarred2`, `unstar`, and
`getPlaylists`/`createPlaylist`/`deletePlaylist` (audition playlist) — all
standard Subsonic endpoints. Navidrome is the tested target.

**Multiple users?**
No. One config, one Navidrome user, one host — a personal-homelab tool.

## Roadmap

Only if there is demand: read-only status page, ntfy run notifications, MeTube
as an alternative downloader, ListenBrainz seeds, multiple sources. No plans to
become a Lidarr replacement or to manage whole albums.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The safety-critical tests cover atomic state writes, the exact expiry boundary,
and refusal to delete outside `incoming_dir`.

## License

MIT — see [LICENSE](LICENSE).
