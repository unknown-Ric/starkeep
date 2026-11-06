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


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions only")
def test_saved_config_is_private(tmp_path, cfg):
    config_mod.save(tmp_path, cfg)
    mode = (tmp_path / "config.yaml").stat().st_mode & 0o777
    assert mode == 0o600
