"""Last.fm response parsing: scrobbles and similar tracks use different shapes."""

from __future__ import annotations

from starkeep.lastfm import LastfmClient


def _client(monkeypatch, payload):
    c = LastfmClient("key", "user")
    monkeypatch.setattr(c, "_call", lambda method, **params: payload)
    return c


def test_similar_tracks_uses_name_field(monkeypatch):
    payload = {
        "similartracks": {
            "track": [
                {"name": "Other Song", "artist": {"name": "Other Artist"}},
                {"name": "Third Song", "artist": {"#text": "Third Artist"}},
            ]
        }
    }
    client = _client(monkeypatch, payload)
    assert client.similar_tracks("Artist", "Song") == [
        ("Other Artist", "Other Song"),
        ("Third Artist", "Third Song"),
    ]


def test_similar_tracks_failure_is_not_fatal(monkeypatch):
    client = LastfmClient("key", "user")

    def boom(method, **params):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(client, "_call", boom)
    assert client.similar_tracks("Artist", "Song") == []


def test_recent_seeds_use_text_field(monkeypatch):
    payload = {
        "recenttracks": {
            "track": [
                {"name": "Song", "artist": {"#text": "Artist"}},
                {"name": "Now", "artist": {"#text": "Artist"},
                 "@attr": {"nowplaying": "true"}},
            ]
        }
    }
    client = _client(monkeypatch, payload)
    assert client.recent_track_seeds() == [("Artist", "Song")]
