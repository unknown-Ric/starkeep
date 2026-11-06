"""Shared fixtures: temp config, fake API clients."""

from __future__ import annotations

import datetime as dt

import pytest

from starkeep.config import Config


@pytest.fixture
def cfg(tmp_path):
    return Config(
        lastfm_user="tester",
        lastfm_api_key="key",
        subsonic_url="http://navidrome.test",
        subsonic_user="tester",
        subsonic_password="secret",
        incoming_dir=str(tmp_path / "incoming"),
        library_dir=str(tmp_path / "library"),
        state_file=str(tmp_path / "state.json"),
        log_dir=str(tmp_path / "logs"),
        cap=5,
        expiry_days=14,
    )


@pytest.fixture
def now():
    return dt.datetime(2026, 11, 6, 12, 0, tzinfo=dt.timezone.utc)


class FakeSubsonic:
    def __init__(self, stars=None, owned=None):
        self.stars = stars or []
        self.owned = owned or set()

    def ping(self):
        return None

    def starred_songs(self):
        return self.stars

    def song_exists(self, artist, title):
        return (artist.casefold(), title.casefold()) in {
            (a.casefold(), t.casefold()) for a, t in self.owned
        }


class FakeLastfm:
    def __init__(self, info=None, seeds=None, similar=None):
        self.info = info or {}
        self.seeds = seeds or []
        self.similar = similar or []

    def track_album_info(self, artist, title):
        return self.info

    def recent_track_seeds(self, days=7, limit=100):
        return self.seeds

    def similar_tracks(self, artist, title, limit=3):
        return self.similar
