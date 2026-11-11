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


def test_start_scan_unsupported_server_is_a_noop():
    c = FakeClient()

    def unsupported(endpoint, **params):
        c.calls.append((endpoint, params))
        if endpoint == "startScan":
            raise RuntimeError("no such endpoint")
        return {"status": "ok"}

    c._call = unsupported
    assert c.start_scan() is False


def test_wait_for_scan_polls_until_scanning_false(monkeypatch):
    c = FakeClient()
    state = {"scanning": True}

    def fake_call(endpoint, **params):
        c.calls.append((endpoint, params))
        if endpoint == "getScanStatus":
            return {"scanStatus": {"scanning": state["scanning"]}}
        if endpoint == "startScan":
            return {"status": "ok"}
        return {"status": "ok"}

    c._call = fake_call
    monkeypatch.setattr("starkeep.subsonic.time.sleep", lambda s: None)
    state["scanning"] = False  # pretend the scan finished instantly

    assert c.wait_for_scan() is True
    assert ("startScan", {}) in c.calls
