"""status.html snapshot: no credentials, escaping intact, orphans visible."""

from __future__ import annotations

from pathlib import Path

from starkeep import report
from starkeep import state as st


def test_snapshot_lists_audition_tracks_and_orphans(cfg):
    stt = st.load(cfg.state_file)
    st.add_candidate(stt, "Artist", "Title", status="downloaded")
    st.add_candidate(stt, "Gone", "Track", status="expired")
    stt["moved"].append({"from": "/x/a.m4a", "to": "/lib/Artist/03. Title.m4a",
                         "at": st.utcnow()})

    path = report.write(cfg, stt, ["/music/_incoming/mystery.m4a"])
    text = Path(path).read_text(encoding="utf-8")

    assert "Artist" in text and "Title" in text
    assert "expired" in text           # status counts shown
    assert "mystery.m4a" in text       # orphans must be visible
    assert "never deleted" in text     # safety note next to the warning
    assert "03. Title.m4a" not in text  # promoted names lose the extension
    assert '<td class="title">Title</td>' in text  # split from the artist


def test_snapshot_is_mobile_readable(cfg):
    stt = st.load(cfg.state_file)
    path = report.write(cfg, stt, [])
    text = Path(path).read_text(encoding="utf-8")
    assert 'name="viewport"' in text            # no desktop-zoom rendering
    assert "@media" in text                     # tighter padding on phones
    assert "display:block" in text              # cells stack instead of tables


def test_snapshot_escapes_html_and_holds_no_secrets(cfg):
    stt = st.load(cfg.state_file)
    st.add_candidate(stt, "<script>alert(1)</script>", "B & W", status="downloaded")

    path = report.write(cfg, stt, [])
    text = Path(path).read_text(encoding="utf-8")

    assert "<script>alert" not in text
    assert "&lt;script&gt;" in text
    assert "B &amp; W" in text
    # the file is meant to be shareable: nothing about the config's secrets
    assert cfg.subsonic_password not in text
    assert cfg.lastfm_api_key not in text


def test_add_run_caps_history():
    stt = {"runs": []}
    for i in range(35):
        st.add_run(stt, {"at": str(i), "full": False})
    assert len(stt["runs"]) == 30
    assert stt["runs"][-1]["at"] == "34"  # newest kept


def test_two_periods_rendered(cfg):
    stt = st.load(cfg.state_file)
    st.add_candidate(stt, "New", "Track", status="downloaded")
    stt["runs"] = [
        {"at": "2026-11-02T04:30:00+00:00", "full": True,
         "downloaded": [["Old", "Track"]],
         "promoted": ["01. Old Track.m4a"], "expired": ["02. Old One.m4a"],
         "orphans": 0},
        {"at": "2026-11-05T04:17:00+00:00", "full": False,
         "promoted": ["03. Second Star.m4a"], "downloaded": [],
         "expired": [], "orphans": 0},
        {"at": "2026-11-09T04:30:00+00:00", "full": True,
         "downloaded": [["New", "Track"]],
         "promoted": [["Kept", "Song", "2026-11-10T12:00:00+00:00"]],
         "expired": [], "orphans": 0},
        {"at": "2026-11-11T04:17:00+00:00", "full": False,
         "promoted": ["04. New Star.m4a"], "downloaded": [],
         "expired": [], "orphans": 0},
    ]

    text = Path(report.write(cfg, stt, [])).read_text(encoding="utf-8")

    assert "Running period" in text and "Previous period" in text
    assert 'class="title">New Star' in text  # current: promoted so-far, no ext
    assert 'class="title">Old Track</td>' in text    # previous: legacy filename
    assert 'class="title">Second Star</td>' in text  # previous: promote-pass
    assert 'class="title">Old Track</td>' in text    # previous: legacy filename
    assert 'class="title">Second Star</td>' in text  # previous: promote-pass
    assert '<tr><td class="artist">Kept</td><td class="title">Song</td></tr>' in text
    assert "expired at period end: 1" in text
    # current period must NOT contain the previous batch's promotion
    running, previous = text.split("Previous period")
    assert "Old Track" not in running
    assert "New Star" not in previous


def test_audition_grouped_by_expiry_soonest_first(cfg):
    stt = st.load(cfg.state_file)
    # added now -> expires expiry_days (14) out; fudge one candidate older
    soon = st.add_candidate(stt, "Soon", "One", status="downloaded")
    soon["downloaded_at"] = "2026-01-01T00:00:00+00:00"   # long past expiry
    late = st.add_candidate(stt, "Late", "Two", status="downloaded")
    late["downloaded_at"] = "2026-01-10T00:00:00+00:00"   # less far past
    fresh = st.add_candidate(stt, "Fresh", "Three", status="downloaded")
    fresh["downloaded_at"] = st.utcnow()                  # inside its runway

    text = Path(report.write(cfg, stt, [])).read_text(encoding="utf-8")

    assert "Past expiry — cleans at next full run (2)" in text
    assert "Expires in" in text
    soon_i = text.index("Soon")
    late_i = text.index("Late")
    fresh_i = text.index("Fresh")
    assert soon_i < late_i < fresh_i  # soonest expiry first


def test_starred_waiting_and_failed_details_shown(cfg):
    stt = st.load(cfg.state_file)
    keep = st.add_candidate(stt, "Star", "Me", status="downloaded")
    keep["starred_at"] = st.utcnow()
    bad = st.add_candidate(stt, "Broken", "Thing", status="failed")
    bad["strikes"] = 1
    bad["last_error"] = "yt-dlp failed (rc=1): video unavailable"

    text = Path(report.write(cfg, stt, [])).read_text(encoding="utf-8")

    assert "Starred — will be promoted at the next run (1)" in text
    assert "Failed downloads (1)" in text
    assert '<td class="meta">1/2</td>' in text
    assert "video unavailable" in text
    assert 'title="yt-dlp failed (rc=1): video unavailable"' in text


def test_failed_out_of_strikes_is_marked_skipped(cfg):
    stt = st.load(cfg.state_file)
    dead = st.add_candidate(stt, "Dead", "Track", status="failed")
    dead["strikes"] = 2

    text = Path(report.write(cfg, stt, [])).read_text(encoding="utf-8")
    assert "2/2 — skipped" in text


def test_zero_counters_dimmed_and_relative_js_present(cfg):
    stt = st.load(cfg.state_file)
    text = Path(report.write(cfg, stt, [])).read_text(encoding="utf-8")

    assert 'class="zero">queued 0<' in text      # zeros muted
    assert 'data-ts=' in text                    # relative dates via JS
    assert "STALE_H" in text                     # staleness threshold wired
    assert 'name="color-scheme" content="dark"' in text
    assert "#8a8a8a" in text                     # footer contrast bump
    assert "(two-period view starts at the next full run)" not in text


def test_header_shows_promotion_and_harvest_schedule(cfg, monkeypatch):
    import datetime as dt
    fixed = dt.datetime(2026, 11, 12, 20, 0, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(st, "utcnow_dt", lambda: fixed)
    stt = st.load(cfg.state_file)
    stt["runs"] = [
        {"at": "2026-11-02T04:30:00+00:00", "full": True},
        {"at": "2026-11-05T04:17:00+00:00", "full": False},
        {"at": "2026-11-09T04:30:00+00:00", "full": True},
        {"at": "2026-11-11T04:17:00+00:00", "full": False},
    ]

    text = Path(report.write(cfg, stt, [])).read_text(encoding="utf-8")

    assert "Last promotion run: <strong data-ts=\"2026-11-11T04:17:00+00:00\">" in text
    assert "Last harvest run: <strong data-ts=\"2026-11-09T04:30:00+00:00\">" in text
    assert "Next harvest run: <strong data-ts=\"2026-11-16T04:17:00+00:00\">" in text
    # a full run promotes too: no promote pass ever -> it is the fallback
    stt["runs"] = [{"at": "2026-11-09T04:30:00+00:00", "full": True}]
    text = Path(report.write(cfg, stt, [])).read_text(encoding="utf-8")
    assert "Last promotion run: <strong data-ts=\"2026-11-09T04:30:00+00:00\">" in text
    # next harvest comes from the timer schedule, not from history
    stt["runs"] = [{"at": "2026-11-05T04:17:00+00:00", "full": False}]
    text = Path(report.write(cfg, stt, [])).read_text(encoding="utf-8")
    assert "Last harvest run: <strong data-ts=\"\">?</strong>" in text
    assert "Next harvest run: <strong data-ts=\"2026-11-16T04:17:00+00:00\">" in text


def test_seed_column_rendered(cfg):
    stt = st.load(cfg.state_file)
    st.add_candidate(stt, "New", "Track", status="downloaded",
                     seed="similar to Franz Ferdinand — Walk Away")

    text = Path(report.write(cfg, stt, [])).read_text(encoding="utf-8")
    assert "similar to Franz Ferdinand — Walk Away" in text


def test_groups_collapse_and_share_dates(cfg, monkeypatch):
    import datetime as dt
    monkeypatch.setattr(st, "utcnow_dt",
                        lambda: dt.datetime(2026, 11, 12, 20, 0, tzinfo=dt.timezone.utc))
    stt = st.load(cfg.state_file)
    past = st.add_candidate(stt, "Old", "One", status="downloaded")
    past["downloaded_at"] = "2026-01-01T00:00:00+00:00"     # expires 01-15, past
    soon = st.add_candidate(stt, "Soon", "Two", status="downloaded")
    soon["downloaded_at"] = "2026-10-31T00:00:00+00:00"     # expires 11-14
    noseed = st.add_candidate(stt, "Bare", "Three", status="downloaded")
    noseed["downloaded_at"] = "2026-10-31T00:00:00+00:00"

    text = Path(report.write(cfg, stt, [])).read_text(encoding="utf-8")

    assert "<details open><summary>Past expiry" in text      # soon groups open
    assert "Expires in 2 days (2) · Sat 11-14" in text       # date lives in heading
    assert "<details><summary>Expires in 2 days" in text     # future groups closed
    assert "<td>11-14</td>" not in text                      # no per-row date
    assert "expires ?" not in text                           # dated rows: no cell
    assert '<td class="artist">Soon</td><td class="title">Two</td>' in text
    assert '<td class="seed"></td>' in text                  # empty seed, still :empty
    assert "Because you played" not in text                  # header dropped
