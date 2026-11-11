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
| `starkeep forget ARTIST TITLE` | Remove a candidate from state so it can be harvested again |
| `starkeep status` | Last run, candidate counts, orphans in `incoming/` |
| `starkeep doctor` | Validate config, connectivity, dependencies |
| `starkeep show-state` | Raw `state.json` (debugging) |

All commands accept `--config-dir /path/to/dir` (default: current directory).
`run` and `dry-run` also accept `--cap N`; `run --cap 0` is a decide-only pass
(promote/expire without downloading anything).

Every `run` also renders a **static `status.html`** next to `state.json`:
the **running period** (since the last full run — auditioned tracks with
expiry dates, promoted so-far) and the **previous period** (finished, with
its final promoted/expired outcome), plus orphans. No server, no credentials,
no JavaScript — open it directly or serve it with anything you already run
(`python -m http.server`, nginx, …). Two-period history starts at the first
full run after the upgrade; before that the page shows a live fallback view.

A **spot run** outside the schedule is just `starkeep run` (the pipeline is
idempotent and lock-protected, so extra runs are safe). Use `--since
YYYY-MM-DD` to widen the scrobble window for that one pass — e.g. after a busy
week, or to seed the first run from a month of history:

```bash
starkeep run --since 2026-10-01
```

## How "star to keep" works

1. **Harvest** — last week's most-played distinct tracks seed Last.fm
   `track.getSimilar`; results are deduped against your actual library
   (`search3`) and previously seen candidates, then capped.
2. **Download** — `yt-dlp` fetches one match per candidate
   (`ytsearch1:Artist - Title audio`) into `incoming/`. Obvious wrong-version
   uploads are filtered out before they cost a download: video titles
   containing live/cover/remix/karaoke/instrumental/acoustic are rejected
   (unless the candidate title itself contains the word), and the length must
   fit the official track's duration from Last.fm (±35%). The file is retagged
   immediately with the real artist/title/album/track number (Last.fm), so
   Navidrome shows full info during the audition week and stars can be matched
   back reliably. Cover art: Deezer album art (1000px, exact artist+title match
   only) → Last.fm album image → the video thumbnail as last resort.

   Audio is YouTube-grade, not CD-grade. YouTube serves lossy audio (typically
   Opus or AAC); converting it to `m4a`/`mp3` adds a second lossy pass, and
   `flac` is just a lossless container around that lossy source — it will not
   restore quality. Set `audio_format: native` to keep the stream exactly as
   YouTube serves it (no re-encode at all), or keep the `m4a` default for
   maximum player compatibility.
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

Promotion only appears in Navidrome after its next library scan. Starkeep
triggers a scan itself (`startScan`, waits for `getScanStatus`) before
rebuilding the audition playlist, so this week's files and promoted tracks are
in the playlist immediately on Navidrome. Other Subsonic servers without
`startScan` need their own scan schedule/hook (e.g. a cron `scan` job), or the
playlist lags by one run.

Promotion moves the file, so Navidrome treats it as a new song: star and play
count do not follow automatically. Starkeep re-stars the promoted copy after
the scan (the star you gave the audition track is re-applied); the play count
resets — that is a server limitation.

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

- **weekly cleaning + download** — Monday morning, `starkeep run`
- **frequent promotion** — every 4 h, `starkeep run --cap 0 --no-expire`

**Plain per-user cron needs no sudo at all** (the built-in lock makes overlaps
harmless). If you installed with pipx:

```cron
17 4 * * 1     cd /etc/starkeep && . /etc/starkeep.env && starkeep run
17 */4 * * *   cd /etc/starkeep && . /etc/starkeep.env && starkeep run --cap 0 --no-expire
```

On Docker (see below), your own crontab schedules the container — no root
service, no systemd units to install:

```cron
17 4 * * 1     cd /opt/starkeep && docker compose run --rm starkeep run
17 */4 * * *   cd /opt/starkeep && docker compose run --rm starkeep run --cap 0 --no-expire
```

If you prefer systemd (bare-metal installs), units are in
[`deploy/`](deploy/): install both with sudo, adjust `User`, `WorkingDirectory`
and `ExecStart`, and put the API key in `/etc/starkeep.env`:

```ini
LASTFM_API_KEY=...
SUBSONIC_PASSWORD=...
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

## Navidrome setup notes

- Add `incoming_dir` to a scanned music folder. Starkeep rebuilds a playlist
  named `Starkeep Audition` (`playlist_name` in config) at the end of every
  `run`; pin it in
  your player and it is always your current audition list. Renaming the key
  orphans the old playlist on the server — delete that one by hand.
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
- `audio_format` — `m4a` (default), `mp3`, `opus`, `flac`, `native` (no
  re-encode, keeps the YouTube stream as-is; see the note above)
- `ping_url` — healthchecks.io-style cron monitoring: a successful `run` GETs
  the URL, a failed one GETs `<url>/fail`. Empty = disabled. A quiet cron
  failure otherwise means "no new music" and no signal.
- `playlist_icon` — cover art for the `Starkeep Audition` playlist (Navidrome
  native API, requires Navidrome ≥ the 2026-03 release); defaults to the repo
  logo PNG (`raw.githubusercontent.com/.../starkeep-logo.png`). Substitute it
  with any image URL (an SVG URL works too — it gets rasterized, needs
  `pip install cairosvg`), or set `""` to keep whatever cover the server has

## FAQ

**Does it delete music I already own?**
No. It only deletes files it downloaded itself, recorded in state, inside
`incoming_dir`. Your existing library is never a deletion candidate.

**How long does an expired track stay rejected?**
Forever by default (`requeue_after_days: 0`). Set e.g.
`requeue_after_days: 21` and candidates may come back three cleaning cycles
after deletion — still never earlier than the *next* cleaning run, and a
promoted copy in the library always wins (owned-dedupe). To resurrect a
specific candidate immediately: `starkeep forget ARTIST TITLE`, or delete the
entry from `state.json`.

**Can I block a song or artist for good?**
`blacklist.txt` next to the config: `Artist - Title` for one track, or a bare
`Artist` for any track naming that artist (works inside multi-artist credits
like `A, B - T`, `A & B`, `A feat. B`). Blacklisted audition tracks are
deleted on the next cleaning run even if young; blacklisted candidates are
never harvested.

**I starred a track after it expired.**
Too late — the audition file is gone and that candidate stays rejected
(`requeue_after_days: 0`). Two ways to soften that failure mode: set
`requeue_after_days: 21` so expired candidates may come back three cleaning
cycles later (never earlier than the next cleaning run, and a promoted library
copy always wins), or switch to the run-based budget (`expiry_runs`) which
only expires a track once it was actually heard. To resurrect a specific
candidate immediately: `starkeep forget ARTIST TITLE`, or remove its entry
from `state.json`.

**The download is the wrong version (live/cover/remix).**
Cheap guards are built in: YouTube videos whose title contains
live/cover/remix/karaoke/instrumental/acoustic are skipped during the search
(unless the candidate title itself contains the word), and the file's length
must fit the official track's Last.fm duration (±35%). Auto-generated
"Artist - Topic" channels usually win such searches on their own, but the
filter is title-based, not channel-based. What slips through: let it expire,
or delete it and remove the candidate from `state.json`. The audition week is
the human filter for the rest.

**Can I run it more than once a week?**
Yes — that is the point of promotion passes: `run --no-expire` is safe at any
cadence (every few hours is typical) because it only promotes and rebuilds the
audition playlist. Only a cleaning run can delete; how often you clean changes
when the `expiry_days` tick is spent, not how long tracks audition:
Monday's candidates die at the next Monday run.

**Does it work with Airsonic/other Subsonic servers?**
The client uses `ping`, `search3`, `getStarred2`, `star`, `unstar`, and
`getPlaylists`/`createPlaylist`/`deletePlaylist` (audition playlist), plus the
Navidrome extensions `startScan`/`getScanStatus` (skipped gracefully on
servers without them). All standard endpoints. Navidrome is the tested target.

**Multiple users?**
No. One config, one Navidrome user, one host — a personal-homelab tool.

## Credentials

The Subsonic password lives in `config.yaml` (mode `600`) or, preferably, in
the environment: `SUBSONIC_PASSWORD` is picked up automatically. Use a
**dedicated, non-admin Navidrome user** for starkeep — it only needs the
normal Subsonic endpoints and its own star/playlist state.

## Legal note

Downloading audio from YouTube via `yt-dlp` conflicts with YouTube's terms of
service and may raise copyright issues depending on your jurisdiction. This
tool is for personal use with music you already listen to; you are
responsible for what you download and keep.

## Keeping yt-dlp alive

yt-dlp breaks regularly when YouTube changes its site — that is normal and
fixed upstream within days. When downloads fail in bulk, upgrade first:

```bash
pipx upgrade yt-dlp    # or: pip install -U yt-dlp
```

## Docker

The repo ships a `Dockerfile` (ffmpeg + yt-dlp included) and a
`docker-compose.yml` that mounts `./config` (holds `config.yaml`, `state.json`,
logs) and your music root:

```bash
cp .env.example .env      # then fill in the secrets; .env is gitignored
cd /music && docker compose run --rm starkeep doctor   # first-run setup
```

All subcommands work the same way, e.g.
`docker compose run --rm starkeep status` or
`docker compose run --rm starkeep run --cap 0 --no-expire`.
Schedule the runs from **your own crontab** (see Scheduling) — no root
service needed. If files created in `incoming_dir` end up owned by root and
Navidrome cannot star them, add `user: "1000:1000"` to the compose service.

## Roadmap

Only if there is demand: a small web UI (guided first-run setup, config
editing, `status`/`dry-run` at a click — the pipeline is complex enough to
deserve it), read-only status page, ntfy run notifications, MeTube
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
