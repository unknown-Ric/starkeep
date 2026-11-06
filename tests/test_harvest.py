"""Harvest: cap, dedupe, library-owned skip, and transient-failure retry."""

from __future__ import annotations

from starkeep import harvest, state as st
from tests.conftest import FakeLastfm, FakeSubsonic


def test_harvest_respects_cap_and_dedupes(cfg):
    lfm = FakeLastfm(
        seeds=[("Seed", "Song")],
        similar=[("A", "One"), ("A", "One"), ("B", "Two"), ("C", "Three")],
    )
    cfg = cfg.__class__(**{**cfg.__dict__, "cap": 2})
    stt = st.load(cfg.state_file)

    picked = harvest.harvest(cfg, lfm, FakeSubsonic(), stt)

    assert picked == [{"artist": "A", "title": "One"}, {"artist": "B", "title": "Two"}]
    assert sum(1 for c in stt["candidates"] if c["status"] == "queued") == 2
    assert st.load(cfg.state_file)["candidates"]


def test_harvest_skips_owned_tracks_but_remembers_them(cfg):
    lfm = FakeLastfm(seeds=[("Seed", "Song")], similar=[("Owned", "Track")])
    stt = st.load(cfg.state_file)

    picked = harvest.harvest(cfg, lfm, FakeSubsonic(owned={("Owned", "Track")}), stt)

    assert picked == []
    c = st.find_candidate(stt, "Owned", "Track")
    assert c["status"] == "skipped-owned"


def test_harvest_retries_a_single_strike(cfg):
    lfm = FakeLastfm(seeds=[("Seed", "Song")], similar=[("A", "One")])
    stt = st.load(cfg.state_file)
    st.add_candidate(stt, "A", "One", status="failed")
    st.strike(stt, "A", "One")

    picked = harvest.harvest(cfg, lfm, FakeSubsonic(), stt)

    assert picked == [{"artist": "A", "title": "One"}]
    assert st.find_candidate(stt, "A", "One")["status"] == "queued"


def test_harvest_never_retries_after_max_strikes(cfg):
    lfm = FakeLastfm(seeds=[("Seed", "Song")], similar=[("A", "One")])
    stt = st.load(cfg.state_file)
    st.add_candidate(stt, "A", "One", status="failed")
    st.strike(stt, "A", "One")
    st.strike(stt, "A", "One")

    assert harvest.harvest(cfg, lfm, FakeSubsonic(), stt) == []


def test_harvest_with_no_scrobbles_is_safe(cfg):
    stt = st.load(cfg.state_file)
    assert harvest.harvest(cfg, FakeLastfm(), FakeSubsonic(), stt) == []
