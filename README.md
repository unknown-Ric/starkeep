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
        not starred after expiry_days ───────┴─ deleted at the weekly
                                             cleaning run
```

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

A **spot run** outside the schedule is just `starkeep run` (the pipeline is
idempotent and lock-protected, so extra runs are safe). Use `--since
YYYY-MM-DD` to widen the scrobble window for that one pass — e.g. after a busy
week, or to seed the first run from a month of history:

```bash
starkeep run --since 2026-10-01
```

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
4. **Decide** — starred tracks are promoted into
   `<library>/<Artist>/<Album>/NN. Title` (or `<Artist>/Singles/`) by the
   frequent **promotion pass**. Last.fm
   album/track-number info fills gaps; existing tags are never overwritten
   blindly.
5. **Expire** — the weekly **cleaning run** deletes downloaded tracks that were
   never starred. With `expiry_runs: 0` (default) an unstarred track dies once
   it is `expiry_days` old (default 7 = Monday's playlist is gone by the next
   Monday). Alternatively a per-cleaning-run budget: `expiry_runs: 2` means
   "two cleaning passes once heard" (`min_plays` against the Navidrome
   playCount), with `carry_over_runs` extra passes for tracks never played.
   Promotion passes never delete and never consume the budget.

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

The recommended rhythm is two schedules (see "Run types"):

- **weekly cleaning + download** — `deploy/starkeep.{service,timer}`, Monday
  morning
- **frequent promotion** — `deploy/starkeep-promote.{service,timer}`, every
  4 h, `run --cap 0 --no-expire`

systemd units are in [`deploy/`](deploy/): install both, adjust `User`,
`WorkingDirectory` and `ExecStart`, put the API key in `/etc/starkeep.env`:

```ini
LASTFM_API_KEY=...
```

```bash
sudo cp deploy/starkeep.service deploy/starkeep.timer \
        deploy/starkeep-promote.service deploy/starkeep-promote.timer \
        /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now starkeep.timer starkeep-promote.timer
```

The timers are templates — edit `OnCalendar` to move Monday or change the
promotion cadence; the audition lifetime is the `expiry_days` config key.

Plain cron works too (the built-in lock makes overlaps harmless):

```cron
17 4 * * 1     cd /etc/starkeep && . /etc/starkeep.env && starkeep run
17 */4 * * *   cd /etc/starkeep && . /etc/starkeep.env && starkeep run --cap 0 --no-expire
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
- `expiry_days` — audition lifetime (default 7): unstarred candidates older
  than this are deleted at the next **cleaning run** (`starkeep run`, schedule
  it weekly for the Monday → next-Monday rhythm)
- `expiry_runs` / `min_plays` / `carry_over_runs` — alternative per-cleaning-
  cycle budget (`expiry_runs: 0` = day-based default); promotion passes
  (`--no-expire`) never delete and never consume a budget
- `max_strikes` — download failures before permanent skip (default 2)
- `harvest_days` — scrobble window feeding the harvest (default 7)
- `playlist_icon` — cover art for the `Starkeep Audition` playlist (Navidrome
  native API, requires Navidrome ≥ the 2026-03 release); defaults to the repo
  logo PNG (`raw.githubusercontent.com/.../starkeep-logo.png`). Substitute it
  with any image URL (an SVG URL works too — it gets rasterized, needs
  `pip install cairosvg`), or set `""` to keep whatever cover the server has
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
Yes — that is the point of promotion passes: `run --no-expire` is safe at any
cadence (every few hours is typical) because it only promotes and rebuilds the
audition playlist. Only a cleaning run can delete; how often you clean changes
when the `expiry_days` tick is spent, not how long tracks audition:
Monday's candidates die at the next Monday run.

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
