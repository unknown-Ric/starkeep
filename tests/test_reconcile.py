"""Orphans are reported, never deleted; vanished files are marked, not guessed."""

from __future__ import annotations

from pathlib import Path

from starkeep import reconcile, state as st


def test_orphan_is_reported_and_never_deleted(cfg):
    incoming = Path(cfg.incoming_dir)
    incoming.mkdir(parents=True)
    orphan = incoming / "Some Channel - unknown upload.m4a"
    orphan.write_bytes(b"audio")

    stt = st.load(cfg.state_file)
    report = reconcile.scan(cfg, stt)

    assert report["orphans"] == [str(orphan)]
    assert orphan.is_file()
    assert stt["candidates"] == []


def test_known_files_are_not_orphans(cfg):
    incoming = Path(cfg.incoming_dir)
    incoming.mkdir(parents=True)
    known = incoming / "Artist - Title.m4a"
    known.write_bytes(b"audio")
    (incoming / "notes.txt").write_text("ignore me", encoding="utf-8")

    stt = st.load(cfg.state_file)
    c = st.add_candidate(stt, "Artist", "Title", status="downloaded")
    c["file_path"] = str(known)

    assert reconcile.scan(cfg, stt)["orphans"] == []


def test_vanished_download_is_rejected_not_retried(cfg):
    """README advice 'delete the file to reject' must stick: no silent re-harvest."""
    stt = st.load(cfg.state_file)
    c = st.add_candidate(stt, "Artist", "Title", status="downloaded")
    c["file_path"] = str(Path(cfg.incoming_dir) / "gone.m4a")

    report = reconcile.scan(cfg, stt)
    assert report["missing"] == [c["file_path"]]

    reconcile.apply_missing(stt, report["missing"])
    assert c["status"] == "expired"
    assert c["expired_at"]  # requeue cooldown starts here
