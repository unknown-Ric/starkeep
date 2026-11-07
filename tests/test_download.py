"""Download must record the file yt-dlp actually wrote, not an assumed name."""

from __future__ import annotations

import subprocess
from pathlib import Path

from starkeep import download, state as st


def _fake_ytdlp(cmd, capture_output=True, text=True, timeout=0):
    template = Path(cmd[cmd.index("-o") + 1])
    written = Path(str(template).replace("%(ext)s", "m4a"))
    written.write_bytes(b"audio")
    report = Path(cmd[cmd.index("--print-to-file") + 2])
    report.write_text(str(written), encoding="utf-8")
    return subprocess.CompletedProcess(cmd, 0, "", "")


def test_download_returns_actual_written_file(cfg, monkeypatch):
    monkeypatch.setattr(download, "YTDLP", "yt-dlp")
    monkeypatch.setattr(download.subprocess, "run", _fake_ytdlp)

    path = download.download(cfg, "AC/DC", "Back: In Black")
    assert path == Path(cfg.incoming_dir) / "AC_DC - Back_ In Black.m4a"
    assert path.is_file()
    assert not list(Path(cfg.incoming_dir).glob(".starkeep-*"))


def test_download_falls_back_to_scanning_when_report_is_missing(cfg, monkeypatch):
    def fake(cmd, capture_output=True, text=True, timeout=0):
        template = Path(cmd[cmd.index("-o") + 1])
        written = Path(str(template).replace("%(ext)s", "m4a"))
        written.write_bytes(b"audio")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(download, "YTDLP", "yt-dlp")
    monkeypatch.setattr(download.subprocess, "run", fake)

    path = download.download(cfg, "Artist", "Title")
    assert path.name == "Artist - Title.m4a"
    assert path.is_file()


def test_failed_download_raises(cfg, monkeypatch):
    monkeypatch.setattr(download, "YTDLP", "yt-dlp")
    monkeypatch.setattr(
        download.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", "boom"),
    )
    try:
        download.download(cfg, "Artist", "Title")
    except RuntimeError as e:
        assert "boom" in str(e)
    else:
        raise AssertionError("expected RuntimeError")


class _FakeEasy:
    """Stand-in for easy-mode mutagen files (tests never have real audio)."""

    def __init__(self):
        self.tags = {}
        self.saved = False

    def __setitem__(self, key, value):
        self.tags[key] = value

    def get(self, key):
        return self.tags.get(key)

    def save(self):
        self.saved = True


def test_download_all_records_path_and_applies_tags(cfg, monkeypatch):
    monkeypatch.setattr(download, "YTDLP", "yt-dlp")
    monkeypatch.setattr(download.subprocess, "run", _fake_ytdlp)
    tagged = []
    monkeypatch.setattr(download, "apply_basic_tags",
                        lambda path, artist, title: tagged.append((path, artist, title)) or True)

    stt = st.load(cfg.state_file)
    st.add_candidate(stt, "Artist", "Title", status="queued")
    count = download.download_all(cfg, [{"artist": "Artist", "title": "Title"}], stt)

    assert count == 1
    c = st.find_candidate(stt, "Artist", "Title")
    assert c["status"] == "downloaded"
    assert Path(c["file_path"]).is_file()
    assert tagged and tagged[0][1:] == ("Artist", "Title")
    assert st.load(cfg.state_file)["candidates"][0]["status"] == "downloaded"


def test_status_strike_on_download_failure(cfg, monkeypatch):
    monkeypatch.setattr(download, "YTDLP", "yt-dlp")
    monkeypatch.setattr(
        download.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", "nope"),
    )
    stt = st.load(cfg.state_file)
    st.add_candidate(stt, "Artist", "Title", status="queued")

    assert download.download_all(cfg, [{"artist": "Artist", "title": "Title"}], stt) == 0
    c = st.find_candidate(stt, "Artist", "Title")
    assert c["status"] == "failed"
    assert c["strikes"] == 1


def test_download_embeds_thumbnail(cfg, monkeypatch):
    seen = {}

    def fake(cmd, capture_output=True, text=True, timeout=0):
        seen["cmd"] = cmd
        template = Path(cmd[cmd.index("-o") + 1])
        written = Path(str(template).replace("%(ext)s", "m4a"))
        written.write_bytes(b"audio")
        report = Path(cmd[cmd.index("--print-to-file") + 2])
        report.write_text(str(written), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(download, "YTDLP", "yt-dlp")
    monkeypatch.setattr(download.subprocess, "run", fake)

    download.download(cfg, "Artist", "Title")

    assert "--embed-thumbnail" in seen["cmd"]
    assert "jpg" in seen["cmd"]


def test_complete_file_from_interrupted_run_is_adopted(cfg, monkeypatch):
    """yt-dlp renames .part only when finished, so a matching file is complete."""
    incoming = Path(cfg.incoming_dir)
    incoming.mkdir(parents=True)
    leftover = incoming / "Artist - Title.m4a"
    leftover.write_bytes(b"complete audio")

    monkeypatch.setattr(download, "YTDLP", "yt-dlp")

    def must_not_run(*a, **k):
        raise AssertionError("yt-dlp should not be called for an existing file")

    monkeypatch.setattr(download.subprocess, "run", must_not_run)
    assert download.download(cfg, "Artist", "Title") == leftover


def test_pick_deezer_requires_exact_artist_title_match():
    results = [
        {"artist": {"name": "Other Artist"}, "title": "Wrong Song",
         "album": {"cover_xl": "http://wrong.jpg"}},
        {"artist": {"name": "AC/DC"}, "title": "Back In Black",
         "album": {"cover_xl": "http://right.jpg"}},
    ]
    assert download._pick_deezer(results, "AC/DC", "Back In Black ") == "http://right.jpg"
    # A loose-search miss must never yield art for the wrong song.
    assert download._pick_deezer(results[:1], "AC/DC", "Back In Black") is None


def test_apply_full_tags_sets_album_tracknumber_and_cover(cfg, monkeypatch):
    fake = _FakeEasy()
    monkeypatch.setattr(download.mutagen, "File",
                        lambda *a, **k: fake)
    monkeypatch.setattr(
        download, "_deezer_cover_url", lambda a, t: "http://art.jpg")
    jpeg = bytes(range(256))  # bytes, not a real image (fake path never parses)
    monkeypatch.setattr(download.requests, "get",
                        lambda url, timeout: type("R", (), {
                            "raise_for_status": lambda s: None,
                            "content": jpeg})())
    embedded = []
    monkeypatch.setattr(download, "_embed_cover",
                        lambda path, data: embedded.append(data) or True)

    class FakeLfm:
        def track_album_info(self, artist, title):
            return {"album": "Back In Black", "track_number": 4}

    assert download.apply_full_tags(Path("x.m4a"), "Artist", "Title", FakeLfm())
    assert fake.tags["artist"] == "Artist"
    assert fake.tags["title"] == "Title"
    assert fake.tags["album"] == "Back In Black"
    assert fake.tags["tracknumber"] == "4"
    assert embedded == [jpeg]


def test_apply_full_tags_without_lfm_degrades_to_basic_tags(cfg):
    assert download.apply_full_tags(Path("nope.m4a"), "Artist", "Title", None) is False


def test_download_all_passes_lfm_into_full_tags(cfg, monkeypatch):
    monkeypatch.setattr(download, "YTDLP", "yt-dlp")
    monkeypatch.setattr(download.subprocess, "run", _fake_ytdlp)
    seen = {}
    monkeypatch.setattr(download, "apply_full_tags",
                        lambda path, artist, title, lfm: seen.setdefault("lfm", lfm) or True)
    stt = st.load(cfg.state_file)
    download.download_all(cfg, [{"artist": "Artist", "title": "Title"}], stt, lfm="LFM")
    assert seen["lfm"] == "LFM"
