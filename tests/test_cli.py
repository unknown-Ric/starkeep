"""Read-only commands must not trigger the interactive first-run wizard."""

from __future__ import annotations

import sys

import pytest

from starkeep import cli
from starkeep import config as config_mod
from dataclasses import replace


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


def test_bare_read_only_command_falls_back_to_server_config(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli.config_mod, "CLI_FALLBACK_CONFIG_DIR", str(tmp_path))
    config_mod.save(tmp_path, replace(config_mod.Config(), cap=5))
    monkeypatch.setattr(sys, "argv", ["starkeep", "config"])
    monkeypatch.chdir(tmp_path.parent)  # no config.yaml in the cwd

    with pytest.raises(SystemExit):
        cli.main()

    out = capsys.readouterr().out
    assert "using" in out
    assert "config (default: 30)" in out  # the fallback config's cap=5, not defaults
