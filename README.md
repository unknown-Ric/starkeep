# starkeep

> A cron-powered music discovery pipeline for [Navidrome](https://navidrome.org)
> (or any Subsonic-compatible server): weekly Last.fm-backed candidates are
> auto-downloaded into an audition folder, **starring a track keeps it into your
> permanent library**, everything else quietly expires.

No daemon, no web UI, no database install. It composes tools you likely already
run: Navidrome, [yt-dlp](https://github.com/yt-dlp/yt-dlp), cron — plus a free
Last.fm API key.

```
Last.fm scrobbles ──▶ similar tracks ──▶ yt-dlp ──▶ incoming/ (audition)
                                                       │
        star in any Subsonic app (offline OK) ─────────┤
                                                       ├─ starred → library/<Artist>/<Album>/NN. Title
        orphan for N days ─────────────────────────────┴─ deleted
```

## Install

1. `pipx install starkeep` (or `pip install .` from a clone)
2. `yt-dlp` and `ffmpeg` on PATH
3. `starkeep doctor` — first run walks you through config, then validates everything

## How "star to keep" works

Listen in whatever Subsonic app you prefer. When a track in the audition folder
is worth keeping, **star it** — offline stars sync later, so this works in the
car. On the next scheduled run, starred tracks are promoted to your permanent
library, enriched with real album/track tags (via Last.fm), filed as
`<Artist>/<Album>/<NN>. Title` — or `<Artist>/Singles/` when no album is known.

Unstarred tracks older than `expiry_days` (default 14) are deleted. Only files
`starkeep` downloaded itself are ever eligible.

## Configuration & commands

- `starkeep run` — the pipeline full pass (intended for cron / a systemd timer)
- `starkeep dry-run` — print the promote/delete plan without touching the disk
- `starkeep status` — summary of last run
- `starkeep doctor` — validate config, Last.fm key, Subsonic auth, yt-dlp

## Safety invariants (promises, not suggestions)

- The script only ever *downloads* into `incoming/`, and *moves or deletes* files
  **from** `incoming/` that it recorded itself.
- Deletions only happen for paths take from the state file — it never glob-deletes.
- Your library is only ever *added to*. It is never modified otherwise.
- `state.json` is written atomically after every transition; a crash kills the
  inter-pass at worst, never corrupts state and never double-deletes.
- Failed downloads accumulate strikes; after N (default 2), never retried.

## Known limitations

- `ytsearch1:"…"` can fetch covers/live/wrong versions; bad picks simply expire in
  14 days. YouTube API breakage is an eternal hazard.
- One user, one server: it's designed for a personal homelab, not multi-user.

## License

MIT
