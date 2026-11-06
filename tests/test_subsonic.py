"""Subsonic client: star shape and exact library matching."""

from __future__ import annotations

from starkeep.subsonic import SubsonicClient


def _client(monkeypatch, payload):
    c = SubsonicClient("http://navidrome.test", "u", "p")
    monkeypatch.setattr(c, "_call", lambda endpoint, **params: payload)
    return c


def test_song_exists_requires_artist_and_title(monkeypatch):
    payload = {
        "searchResult3": {
            "song": [
                {"artist": "Artist", "title": "Title"},
                {"artist": "Other", "title": "Title"},
            ]
        }
    }
    client = _client(monkeypatch, payload)
    assert client.song_exists("Artist", "Title") is True
    assert client.song_exists("Artist", "Different") is False
    assert client.song_exists("Nope", "Title") is False


def test_starred_songs_shape(monkeypatch):
    payload = {
        "starred2": {
            "song": [{
                "id": "1", "title": "Title", "artist": "Artist",
                "path": "Artist/Album/01 Title.m4a", "starred": "2026-11-01T10:00:00Z",
            }]
        }
    }
    client = _client(monkeypatch, payload)
    stars = client.starred_songs()
    assert stars[0]["path"].endswith("01 Title.m4a")
    assert stars[0]["starred"] == "2026-11-01T10:00:00Z"
