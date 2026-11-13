"""Harvest: cap, dedupe, library-owned skip, and transient-failure retry."""

from __future__ import annotations

from starkeep import harvest, state as st
from dataclasses import replace
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


def _expired(stt, artist, title, days_ago):
    import datetime as dt
    c = st.add_candidate(stt, artist, title, status="expired")
    c["expired_at"] = (st.utcnow_dt() - dt.timedelta(days=days_ago)).isoformat(timespec="seconds")
    return c


def test_cooldown_releases_expired_candidates_for_reharvest(cfg):
    lfm = FakeLastfm(seeds=[("Seed", "Song")], similar=[("A", "One")])
    stt = st.load(cfg.state_file)
    _expired(stt, "A", "Old", 99)      # cold enough
    _expired(stt, "B", "Fresh", 1)     # still cooling down
    cfg = replace(cfg, requeue_after_days=21)

    picked = harvest.harvest(cfg, lfm, FakeSubsonic(), stt)

    assert picked == [{"artist": "A", "title": "One"}]  # A forgotten -> re-queued
    assert st.find_candidate(stt, "A", "One")["status"] == "queued"
    assert st.find_candidate(stt, "B", "Fresh")["status"] == "expired"


def test_zero_cooldown_keeps_expired_candidates_config(cfg):
    lfm = FakeLastfm(seeds=[("Seed", "Song")], similar=[("A", "One")])
    stt = st.load(cfg.state_file)
    _expired(stt, "A", "One", 999)

    picked = harvest.harvest(cfg, lfm, FakeSubsonic(), stt)

    assert picked == []  # permanent rejection: default stays the old behavior


def test_expired_without_stamp_is_never_released(cfg):
    # legacy rows (before the cooldown feature) stay permanently rejected
    lfm = FakeLastfm(seeds=[("Seed", "Song")], similar=[("A", "One")])
    stt = st.load(cfg.state_file)
    c = st.add_candidate(stt, "A", "One", status="expired")  # no expired_at
    cfg = replace(cfg, requeue_after_days=21)

    assert harvest.harvest(cfg, lfm, FakeSubsonic(), stt) == []
    assert st.find_candidate(stt, "A", "One")["status"] == "expired"


def test_blacklisted_track_is_never_queued(cfg, tmp_path):
    bl = tmp_path / "blacklist.txt"
    bl.write_text("Blocked - Song\nDoomed\n", encoding="utf-8")
    cfg = replace(cfg, blacklist_file=str(bl))
    lfm = FakeLastfm(seeds=[("Seed", "Song")],
                     similar=[("Blocked", "Song"), ("Doomed, Others", "Hit"), ("A", "One")])
    stt = st.load(cfg.state_file)

    picked = harvest.harvest(cfg, lfm, FakeSubsonic(), stt)

    assert picked == [{"artist": "A", "title": "One"}]
    assert [c["artist"] for c in stt["candidates"]] == ["A"]


def test_cap_zero_is_decide_only(cfg):
    lfm = FakeLastfm(seeds=[("Seed", "Song")], similar=[("A", "One")])
    cfg = cfg.__class__(**{**cfg.__dict__, "cap": 0})
    stt = st.load(cfg.state_file)

    assert harvest.harvest(cfg, lfm, FakeSubsonic(), stt) == []
    assert stt["candidates"] == []


def test_guest_credit_variants_are_deduped(cfg):
    lfm = FakeLastfm(
        seeds=[("Seed", "Song")],
        similar=[("A", "One"), ("B", "One featuring Q-Tip"), ("B", "One (feat. Someone)"),
                 ("A", "Two")],
    )
    stt = st.load(cfg.state_file)

    picked = harvest.harvest(cfg, lfm, FakeSubsonic(), stt)

    assert [(p["artist"], p["title"]) for p in picked] == [
        ("A", "One"), ("B", "One featuring Q-Tip"), ("A", "Two")]
    titles = [c["title"] for c in stt["candidates"]]
    assert "One (feat. Someone)" not in titles  # variant of B/One, skipped


def test_variant_of_known_candidate_is_skipped(cfg):
    lfm = FakeLastfm(seeds=[("Seed", "Song")], similar=[("A", "One featuring Q-Tip")])
    stt = st.load(cfg.state_file)
    st.add_candidate(stt, "A", "One", status="promoted")

    picked = harvest.harvest(cfg, lfm, FakeSubsonic(), stt)

    assert picked == []  # "One" is owned; its feat-variant must not queue


def test_per_artist_cap_keeps_variety(cfg):
    lfm = FakeLastfm(
        seeds=[("Seed", "Song")],
        similar=[("A", "One"), ("A", "Two"), ("A", "Three"), ("B", "Four")],
    )
    stt = st.load(cfg.state_file)

    picked = harvest.harvest(cfg, lfm, FakeSubsonic(), stt)

    assert [(p["artist"], p["title"]) for p in picked] == [("A", "One"), ("A", "Two"),
                                                           ("B", "Four")]


def test_seed_is_recorded_for_the_status_page(cfg):
    lfm = FakeLastfm(seeds=[("Franz Ferdinand", "Walk Away")], similar=[("A", "One")])
    stt = st.load(cfg.state_file)

    harvest.harvest(cfg, lfm, FakeSubsonic(), stt)

    c = st.find_candidate(stt, "A", "One")
    assert c["seed"] == "similar to Franz Ferdinand — Walk Away"


def test_harvest_keeps_seeding_until_the_cap_is_full(cfg):
    """30 cap + 3-per-seed used to end after the first 10 seeds: keep going."""
    lfm = FakeLastfm(
        seeds=[(f"Seed{i}", "Song") for i in range(20)],
        similar=[(f"A{i}", f"Track{i}") for i in range(30)],  # 3 per seed
    )
    stt = st.load(cfg.state_file)

    cfg = replace(cfg, cap=12, similar_per_seed=3)
    picked = harvest.harvest(cfg, lfm, FakeSubsonic(), stt)

    assert len(picked) == 12  # not capped at one seed-batch worth


def test_harvest_cap_zero_makes_no_lastfm_calls(cfg):
    """--cap 0 runs must not depend on Last.fm at all."""
    class Boom:
        def recent_track_seeds(self, **kw):
            raise RuntimeError("lastfm down")

        def similar_tracks(self, *a, **k):
            raise RuntimeError("lastfm down")

    stt = st.load(cfg.state_file)
    assert harvest.harvest(replace(cfg, cap=0), Boom(), FakeSubsonic(), stt) == []
    assert stt["candidates"] == []
