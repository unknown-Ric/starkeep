"""starkeep — weekly music discovery pipeline for Subsonic/Navidrome servers.

Pipeline (one run, meant for cron):
 1. harvest   : Last.fm recent scrobbles + similar tracks -> candidate list
                (deduped against your library, capped, failure strikes respected)
 2. download  : yt-dlp each candidate into the incoming/audition folder
 3. decide    : tracks you starred -> promote into the library with fixed tags;
                candidates older than N days, never starred -> deleted
Everything is recorded in a single state file so runs are idempotent and
crash-safe. The script only ever touches files it downloaded itself.
"""

__version__ = "0.1.0"
