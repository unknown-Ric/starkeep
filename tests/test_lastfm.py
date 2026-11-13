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


def test_recent_tracks_send_from_and_paginate(monkeypatch):
    """The window parameter is `from` (not `from_`) and the whole window is read."""
    client = LastfmClient("key", "user")
    calls = []

    def fake(method, **params):
        calls.append(params)
        page = int(params["page"])
        return {"recenttracks": {
            "track": [{"name": f"S{page}", "artist": {"#text": "X"}}],
            "@attr": {"totalPages": "2"},
        }}

    monkeypatch.setattr(client, "_call", fake)
    seeds = client.recent_track_seeds(days=30)

    assert seeds == [("X", "S1"), ("X", "S2")]  # both pages harvested
    assert "from" in calls[0] and "from_" not in calls[0]
    assert calls[0]["limit"] == "200"          # max page size
    assert [c["page"] for c in calls] == ["1", "2"]


def test_recent_tracks_stop_after_enough_distinct_seeds(monkeypatch):
    client = LastfmClient("key", "user")
    pages = {"n": 0}

    def fake(method, **params):
        pages["n"] += 1
        return {"recenttracks": {
            "track": [{"name": f"S{pages['n']}", "artist": {"#text": "X"}}],
            "@attr": {"totalPages": "99"},
        }}

    monkeypatch.setattr(client, "_call", fake)
    seeds = client.recent_track_seeds(limit=2)
    assert seeds == [("X", "S1"), ("X", "S2")]
    assert pages["n"] == 2  # did not read all 99 pages
