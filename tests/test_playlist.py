"""Audition playlist sync must equal the set of downloaded candidates exactly."""

from __future__ import annotations

from starkeep import playlist, state as st


class FakeSub:
    def __init__(self, songs, playlists=None):
        self.songs = songs  # flat search3 result pool
        self.playlists_l = playlists or [
            {"id": "pl-old", "playlistName": playlist.AUDITION_NAME}]
        self.created = []
        self.deleted = []
        self.images = []  # (playlist_id, data, media_type)

    def search_songs(self, artist, title, limit=10):
        return self.songs

    def playlists(self):
        return self.playlists_l

    def replace_playlist(self, name, song_ids):
        for pl in self.playlists_l:
            if pl["playlistName"] == name:
                self.deleted.append(pl["id"])
        self.created.append({"name": name, "song_ids": song_ids})
        return "new-pl"

    def set_playlist_image(self, playlist_id, data, media_type):
        self.images.append((playlist_id, data, media_type))


def _state(tmp_path, cands):
    stt = {"candidates": []}
    for artist, title, path, status in cands:
        stt["candidates"].append({
            "artist": artist, "title": title,
            "status": status, "file_path": path,
            "added_at": st.utcnow(),
        })
    return stt


SONGS = [
    {"id": "incoming-1", "title": "Title", "artist": "Artist",
     "path": "/music/incoming/starkeep/Artist - Title.m4a"},
    {"id": "promoted-1", "title": "Title", "artist": "Artist",
     "path": "/music/albums/Artist/Album/03. Title.m4a"},
    {"id": "unrelated", "title": "Title", "artist": "Someone Else",
     "path": "/music/albums/Someone Else/03. Title.m4a"},
]


def test_prefers_incoming_copy_over_promoted_copy(tmp_path):
    stt = _state(tmp_path, [("Artist", "Title", "/x/incoming/starkeep/Artist - Title.m4a",
                             "downloaded")])
    sub = FakeSub(SONGS)
    out = playlist.sync_audition(sub, stt)
    assert out["songs"] == 1
    assert out["playlist"] == playlist.AUDITION_NAME
    assert "incoming-1" in sub.created[-1]["song_ids"]


def test_replaces_existing_playlist_with_same_name(tmp_path):
    stt = _state(tmp_path, [("Artist", "Title", "/x/incoming/starkeep/Artist - Title.m4a",
                             "downloaded")])
    sub = FakeSub(SONGS, playlists=[{"id": "pl-old", "playlistName": playlist.AUDITION_NAME}])
    playlist.sync_audition(sub, stt)
    assert sub.deleted == ["pl-old"]


def test_basename_match_wins_over_fuzzy(tmp_path):
    stt = _state(tmp_path, [("Artist", "Title", "/x/incoming/starkeep/Artist - Title.m4a",
                             "downloaded")])
    wrong_copy = {"id": "wrong", "title": "Title", "artist": "Artist",
                  "path": "/music/incoming/other/Artist - Title (Live).m4a"}
    out = playlist.sync_audition(FakeSub([*SONGS, wrong_copy]), stt)
    assert out["songs"] == 1


def test_non_downloaded_candidates_are_excluded(tmp_path):
    stt = _state(tmp_path, [
        ("Artist", "Title", "/x/incoming/starkeep/Artist - Title.m4a", "downloaded"),
        ("Other", "Old", "/x/incoming/starkeep/Other - Old.m4a", "promoted"),
        ("Gone", "File", "/x/incoming/starkeep/Gone - File.m4a", "missing"),
    ])
    out = playlist.sync_audition(FakeSub(SONGS), stt)
    assert out["candidates"] == 1
    assert out["songs"] == 1


def test_unmatched_candidate_is_skipped_not_fatal(tmp_path):
    stt = _state(tmp_path, [
        ("Artist", "Title", "/x/incoming/starkeep/Artist - Title.m4a", "downloaded"),
        ("Nobody", "Knows", "/x/incoming/starkeep/Nobody - Knows.m4a", "downloaded"),
    ])
    songs = [s for s in SONGS if s["id"] != "unrelated"]  # no Nobody - Knows id
    out = playlist.sync_audition(FakeSub(songs), stt)
    assert out["candidates"] == 2
    assert out["songs"] == 1


def test_empty_state_creates_empty_playlist(tmp_path):
    sub = FakeSub(SONGS)
    out = playlist.sync_audition(sub, _state(tmp_path, []))
    assert out["songs"] == 0
    assert sub.created[-1]["song_ids"] == []


def test_falls_back_to_artist_title_when_basename_differs(tmp_path):
    # Promoted-style basename collides nothing; server renamed the file.
    stt = _state(tmp_path, [("Artist", "Title", "/x/incoming/starkeep/Artist - Title.opus",
                             "downloaded")])
    songs = [{"id": "srv-1", "title": "Title", "artist": "Artist",
              "path": "/music/incoming/starkeep/Artist - Title.m4a"}]
    sub = FakeSub(songs)
    out = playlist.sync_audition(sub, stt)
    assert sub.created[-1]["song_ids"] == ["srv-1"]
    assert out["icon"] == "skipped"  # no icon_url configured


def test_icon_uploaded_for_the_new_playlist_id(tmp_path, monkeypatch):
    stt = _state(tmp_path, [])
    sub = FakeSub(SONGS)
    monkeypatch.setattr(playlist.requests, "get", lambda url, timeout=30:
                        FakeResponse(b"IMG", "image/png"))
    out = playlist.sync_audition(sub, stt, icon_url="https://example/l.png")
    assert sub.images == [("new-pl", b"IMG", "image/png")]
    assert out["icon"] == "uploaded"


def test_icon_failure_never_breaks_the_playlist(tmp_path, monkeypatch):
    stt = _state(tmp_path, [])
    sub = FakeSub(SONGS)

    def boom(url, timeout=30):
        raise OSError("offline")
    monkeypatch.setattr(playlist.requests, "get", boom)
    out = playlist.sync_audition(sub, stt, icon_url="https://example/l.png")
    assert out["icon"] == "failed"
    assert out["playlist"] == playlist.AUDITION_NAME


class FakeResponse:
    def __init__(self, content, content_type):
        self.content, self._ct = content, content_type

    @property
    def headers(self):
        return {"Content-Type": self._ct}

    def raise_for_status(self):
        pass
