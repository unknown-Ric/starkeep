"""The three promises that matter: star -> keep, expiry boundary, no stray deletes."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from pathlib import Path

import pytest

from starkeep import decide, state as st
from tests.conftest import FakeLastfm, FakeSubsonic


@pytest.fixture(autouse=True)
def no_real_audio_parser(monkeypatch):
    """Unit tests use dummy bytes; pretend mutagen cannot read them."""
    monkeypatch.setattr(decide.mutagen, "File", lambda *a, **k: None)


def _downloaded(cfg, stt, artist, title, age_days, now, filename=None):
    incoming = Path(cfg.incoming_dir)
    incoming.mkdir(parents=True, exist_ok=True)
    path = incoming / (filename or f"{artist} - {title}.m4a")
    path.write_bytes(b"fake audio")
    c = st.add_candidate(stt, artist, title, status="downloaded")
    c["file_path"] = str(path)
    c["downloaded_at"] = (now - dt.timedelta(days=age_days)).isoformat(timespec="seconds")
    return c, path


def test_expiry_boundary_is_exactly_expiry_days(cfg, now):
    stt = st.load(cfg.state_file)
    _, old = _downloaded(cfg, stt, "Old", "Track", 14, now)
    _, young = _downloaded(cfg, stt, "Young", "Track", 14 - 1 / 86400, now)

    result = decide.decide(cfg, FakeSubsonic(), FakeLastfm(), stt, now=now)

    assert str(old) in result["deletes"]
    assert not old.exists()
    assert young.exists()
    assert Path(young).is_file()


def test_never_deletes_outside_incoming(cfg, now):
    outside = Path(cfg.state_file).parent / "not-ours.m4a"
    outside.write_bytes(b"someone else's file")
    stt = st.load(cfg.state_file)
    c = st.add_candidate(stt, "Artist", "Title", status="downloaded")
    c["file_path"] = str(outside)
    c["downloaded_at"] = (now - dt.timedelta(days=99)).isoformat(timespec="seconds")

    result = decide.decide(cfg, FakeSubsonic(), FakeLastfm(), stt, now=now)

    assert result["deletes"] == []
    assert outside.exists()
    assert c["status"] == "downloaded"


def test_starred_track_is_promoted_with_tags(cfg, now):
    stt = st.load(cfg.state_file)
    c, src = _downloaded(cfg, stt, "Artist", "Title", 20, now)
    stars = [{"artist": "Artist", "title": "Title", "path": "x/Artist - Title.m4a"}]

    result = decide.decide(
        cfg, FakeSubsonic(stars), FakeLastfm({"album": "Album", "track_number": 3}),
        stt, now=now,
    )

    expected = str(Path(cfg.library_dir) / "Artist" / "Album" / "03. Title.m4a")
    assert result["keeps"] == [expected]
    assert not src.exists()
    assert Path(expected).is_file()
    assert c["status"] == "promoted"


def test_star_matches_by_file_name_when_tags_differ(cfg, now):
    stt = st.load(cfg.state_file)
    c, _ = _downloaded(cfg, stt, "Artist", "Title", 1, now)
    # Navidrome still shows the YouTube channel tags, but the starred path matches.
    stars = [{"artist": "Some Channel", "title": "Artist - Title (audio)",
              "path": "/music/_incoming/Artist - Title.m4a"}]

    result = decide.decide(cfg, FakeSubsonic(stars), FakeLastfm(), stt, now=now)

    assert len(result["keeps"]) == 1
    assert c["status"] == "promoted"


def test_promotion_never_overwrites_library_file(cfg, now):
    stt = st.load(cfg.state_file)
    c, src = _downloaded(cfg, stt, "Artist", "Title", 1, now)
    existing = Path(cfg.library_dir) / "Artist" / "Singles" / "Artist - Title.m4a"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_bytes(b"library copy")

    result = decide.decide(
        cfg, FakeSubsonic([{"artist": "Artist", "title": "Title", "path": ""}]),
        FakeLastfm(), stt, now=now,
    )

    assert result["keeps"] == []
    assert existing.read_bytes() == b"library copy"
    assert src.exists()
    assert c["status"] == "downloaded"


def test_dry_run_changes_nothing(cfg, now):
    stt = st.load(cfg.state_file)
    c, src = _downloaded(cfg, stt, "Artist", "Title", 30, now)

    result = decide.decide(cfg, FakeSubsonic(), FakeLastfm(), stt, dry_run=True, now=now)

    assert result["dry_run"] is True
    assert result["deletes"]
    assert src.exists()
    assert c["status"] == "downloaded"
    assert not Path(cfg.state_file).exists()


# ---- run-based lifetime (expiry_runs) --------------------------------------

def _runs_cfg(cfg, runs=2, min_plays=1, carry=1):
    return replace(cfg, expiry_runs=runs, min_plays=min_plays, carry_over_runs=carry)


def test_heard_unstarred_track_expires_at_the_run_budget(cfg, now):
    stt = st.load(cfg.state_file)
    c, src = _downloaded(cfg, stt, "Heard", "Track", 1, now)
    sub = FakeSubsonic(playcounts={"heard|track": 3})

    decide.decide(_runs_cfg(cfg), sub, FakeLastfm(), stt, now=now)  # pass 1: surviving
    assert src.exists() and c["status"] == "downloaded"
    decide.decide(_runs_cfg(cfg), sub, FakeLastfm(), stt, now=now)  # pass 2: expired

    assert not src.exists()
    assert c["status"] == "expired"


def test_never_played_track_carries_over_then_expires(cfg, now):
    stt = st.load(cfg.state_file)
    c, src = _downloaded(cfg, stt, "Never", "Heard", 1, now)
    sub = FakeSubsonic(playcounts={"never|heard": 0})
    cfg = _runs_cfg(cfg)  # 2 passes + 1 carry-over

    decide.decide(cfg, sub, FakeLastfm(), stt, now=now)  # pass 1
    decide.decide(cfg, sub, FakeLastfm(), stt, now=now)  # pass 2: unheard, stays
    assert src.exists() and c["status"] == "downloaded"

    decide.decide(cfg, sub, FakeLastfm(), stt, now=now)  # pass 3: carry-over spent
    decide.decide(cfg, sub, FakeLastfm(), stt, now=now)  # pass 4: deleted (2+1+1)

    assert not src.exists()
    assert c["status"] == "expired"


def test_min_plays_is_configurable(cfg, now):
    stt = st.load(cfg.state_file)
    c, src = _downloaded(cfg, stt, "Once", "Track", 1, now)
    sub = FakeSubsonic(playcounts={"once|track": 1})
    cfg = _runs_cfg(cfg, min_plays=2)  # 1 play is not "enough"

    decide.decide(cfg, sub, FakeLastfm(), stt, now=now)  # pass 1
    decide.decide(cfg, sub, FakeLastfm(), stt, now=now)  # pass 2: not heard enough, carries

    assert src.exists() and c["status"] == "downloaded"


def test_starred_track_is_immune_to_the_run_budget(cfg, now):
    stt = st.load(cfg.state_file)
    c, src = _downloaded(cfg, stt, "Loved", "Track", 99, now)
    stars = [{"artist": "Loved", "title": "Track", "path": "x/Loved - Track.m4a"}]
    sub = FakeSubsonic(stars, playcounts={"loved|track": 0})

    result = decide.decide(_runs_cfg(cfg), sub, FakeLastfm({"album": "Album"}), stt, now=now)

    assert len(result["keeps"]) == 1
    assert c["status"] == "promoted"
