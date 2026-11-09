"""Navidrome reports playlists under "name", not "playlistName" — lookups must match both."""

import starkeep.subsonic as su


class FakeClient(su.SubsonicClient):
    def __init__(self):  # no network
        self.calls = []

    def _call(self, endpoint, **params):
        self.calls.append((endpoint, params))
        if endpoint == "getPlaylists":
            return {"playlists": {"playlist":
                                  [{"id": "p1", "name": "Starkeep Audition"}]}}
        if endpoint == "createPlaylist":
            return {"status": "ok", "playlist": {"id": "new-id"}}
        return {"status": "ok"}


def test_playlists_allayes_name_between_fields():
    assert FakeClient().playlists() == [{
        "id": "p1", "name": "Starkeep Audition",
        "playlistName": "Starkeep Audition"}]


def test_replace_playlist_deletes_name_shaped_entry():
    c = FakeClient()
    c.replace_playlist("Starkeep Audition", ["s1"])
    assert ("deletePlaylist", {"id": "p1"}) in c.calls
