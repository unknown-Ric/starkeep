"""Safety invariants of the state file."""

from __future__ import annotations

import json

import pytest

from starkeep import state as st


def test_atomic_write_roundtrip(tmp_path):
    path = str(tmp_path / "state.json")
    data = st.load(path)
    st.add_candidate(data, "Artist", "Title", status="queued")
    st.save(path, data)
    st.save(path, data)  # overwrite is atomic, no temp files left behind

    assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["candidates"]
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_failed_replace_leaves_previous_state_intact(tmp_path, monkeypatch):
    path = str(tmp_path / "state.json")
    data = st.load(path)
    st.add_candidate(data, "Artist", "Title", status="queued")
    st.save(path, data)
    original = (tmp_path / "state.json").read_text(encoding="utf-8")

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(st.os, "replace", boom)
    with pytest.raises(OSError):
        st.save(path, data)

    assert (tmp_path / "state.json").read_text(encoding="utf-8") == original
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_queue_decision_lifecycle(tmp_path):
    data = st.load(str(tmp_path / "state.json"))

    assert st.queue_decision(data, "A", "T") == "new"

    st.add_candidate(data, "A", "T", status="queued")
    assert st.queue_decision(data, "A", "T") == "requeue"

    st.strike(data, "A", "T")
    assert st.queue_decision(data, "A", "T", max_strikes=2) == "retry"

    st.strike(data, "A", "T")
    assert st.queue_decision(data, "A", "T", max_strikes=2) == "skip"
    assert st.is_retryable(data, "A", "T", max_strikes=2) is False

    expired = st.find_candidate(data, "A", "T")
    expired["status"] = "expired"
    assert st.queue_decision(data, "A", "T", max_strikes=5) == "skip"


def test_missing_file_below_strike_limit_is_retryable(tmp_path):
    data = st.load(str(tmp_path / "state.json"))
    c = st.add_candidate(data, "A", "T", status="downloaded")
    c["file_path"] = str(tmp_path / "gone.m4a")
    c["status"] = "missing"
    assert st.queue_decision(data, "A", "T") == "retry"


def test_forget_removes_all_matching_candidates(tmp_path):
    data = st.load(str(tmp_path / "state.json"))
    st.add_candidate(data, "A", "T", status="expired")
    st.add_candidate(data, "A", "OTHER", status="failed")
    dup = st.add_candidate(data, "  A ", " T", status="queued")  # same, normalized

    assert st.forget_candidate(data, "A", "T") == 2
    assert st.find_candidate(data, "A", "OTHER")["status"] == "failed"


def test_forget_nothing_removes_nothing(tmp_path):
    data = st.load(str(tmp_path / "state.json"))
    assert st.forget_candidate(data, "Nobody", "Nowhere") == 0
