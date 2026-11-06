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
