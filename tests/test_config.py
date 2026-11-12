"""Config validation catches the layouts that would break the safety promises."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from starkeep import config as config_mod


def test_valid_config_has_no_problems(cfg):
    assert config_mod.validate(cfg) == []


def test_incoming_equal_library_rejected(cfg):
    bad = replace(cfg, library_dir=cfg.incoming_dir)
    assert any("must differ" in p for p in config_mod.validate(bad))


def test_library_inside_incoming_rejected(cfg, tmp_path):
    bad = replace(cfg, library_dir=str(Path(cfg.incoming_dir) / "library"))
    assert any("inside incoming_dir" in p for p in config_mod.validate(bad))


def test_incoming_inside_library_allowed(cfg):
    nested = replace(cfg, incoming_dir=str(Path(cfg.library_dir) / "_incoming"))
    assert config_mod.validate(nested) == []


def test_empty_paths_are_reported(cfg):
    bad = replace(cfg, state_file="", log_dir="")
    problems = config_mod.validate(bad)
    assert any("state_file" in p for p in problems)
    assert any("log_dir" in p for p in problems)


def test_dry_run_does_not_require_lastfm(cfg):
    bad = replace(cfg, lastfm_api_key="", lastfm_user="")
    assert any("Last.fm" in p for p in config_mod.validate(bad))
    assert config_mod.validate(bad, require_lastfm=False) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions only")
def test_saved_config_is_private(tmp_path, cfg):
    config_mod.save(tmp_path, cfg)
    mode = (tmp_path / "config.yaml").stat().st_mode & 0o777
    assert mode == 0o600


def test_save_writes_only_non_default_keys(tmp_path, cfg):
    config_mod.save(tmp_path, cfg)
    import yaml
    data = yaml.safe_load((tmp_path / "config.yaml").read_text())
    defaults = config_mod.Config()

    # every written key really differs from the default
    for k, v in data.items():
        assert v != getattr(defaults, k), k
    # pure-default keys are absent (behavior defaults live in the example file)
    assert "similar_per_seed" not in data and "max_strikes" not in data
    # ...and keys the fixture overrides ARE written
    assert "cap" in data and "expiry_days" in data


def test_save_load_roundtrip(tmp_path, cfg):
    config_mod.save(tmp_path, cfg)
    loaded = config_mod.load(tmp_path)
    assert loaded == cfg


def test_default_config_saves_empty_file(tmp_path):
    config_mod.save(tmp_path, config_mod.Config())
    import yaml
    assert yaml.safe_load((tmp_path / "config.yaml").read_text()) in (None, {})


def test_config_cmd_masks_secrets_and_marks_sources(cfg, capsys, monkeypatch):
    from dataclasses import replace as dreplace
    from starkeep.cli import config_cmd
    monkeypatch.delenv("LASTFM_API_KEY", raising=False)
    monkeypatch.delenv("SUBSONIC_PASSWORD", raising=False)
    cfg = dreplace(cfg, lastfm_api_key="lfsk-distinctive-42",
                   subsonic_password="pw-distinctive-42")
    out = config_cmd(cfg)
    text = capsys.readouterr().out
    assert "lfsk-distinctive-42" not in text
    assert "pw-distinctive-42" not in text
    assert "*** set ***" in text          # secrets present but masked
    assert "config (default: 30)" in text  # cap differs (cfg has cap=5)
    assert out == 0
