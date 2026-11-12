"""Read-only commands must not trigger the interactive first-run wizard."""

from __future__ import annotations

import sys

import pytest

from starkeep import cli


def test_config_cmd_with_no_config_shows_defaults(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["starkeep", "config", "--config-dir", str(tmp_path)])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "no config.yaml" in out
    assert "Last.fm username" not in out      # no interactive setup
    assert "cap" in out                        # defaults table shown


def test_status_without_config_works_off_empty_state(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["starkeep", "status", "--config-dir", str(tmp_path)])
    with pytest.raises(SystemExit) as e:
        cli.main()  # status works off an empty config, prints zeros
    assert e.value.code == 0
    assert "candidates seen : 0" in capsys.readouterr().out
