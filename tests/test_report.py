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
