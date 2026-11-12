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
    assert "03. Title.m4a" in text     # recently promoted


def test_snapshot_is_mobile_readable(cfg):
    stt = st.load(cfg.state_file)
    path = report.write(cfg, stt, [])
    text = Path(path).read_text(encoding="utf-8")
    assert 'name="viewport"' in text            # no desktop-zoom rendering
    assert "@media" in text                     # tighter padding on phones


def test_snapshot_escapes_html_and_holds_no_secrets(cfg):
    stt = st.load(cfg.state_file)
    st.add_candidate(stt, "<script>alert(1)</script>", "B & W", status="downloaded")

    path = report.write(cfg, stt, [])
    text = Path(path).read_text(encoding="utf-8")

    assert "<script>" not in text
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
         "downloaded": [["New", "Track"]], "promoted": [], "expired": [],
         "orphans": 0},
        {"at": "2026-11-11T04:17:00+00:00", "full": False,
         "promoted": ["04. New Star.m4a"], "downloaded": [], "expired": [],
         "orphans": 0},
    ]

    text = Path(report.write(cfg, stt, [])).read_text(encoding="utf-8")

    assert "Running period" in text and "Previous period" in text
    assert "04. New Star.m4a" in text      # current: promoted so-far
    assert "01. Old Track.m4a" in text     # previous: period's promotions
    assert "03. Second Star.m4a" in text   # previous: promote-pass accumulation
    assert "expired at period end: 1" in text
    # current period must NOT contain the previous batch's promotion
    running, previous = text.split("Previous period")
    assert "01. Old Track.m4a" not in running
    assert "04. New Star.m4a" not in previous
