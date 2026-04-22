"""Tests for scripts/sb_xray/stages/geoip.py."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sb_xray.stages import geoip as sbgeo


def test_skips_when_script_absent(tmp_path: Path) -> None:
    assert sbgeo.update_geo_data(script=tmp_path / "missing.sh") == 0


def test_invokes_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = tmp_path / "geo_update.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    rc = sbgeo.update_geo_data(script=script)
    assert rc == 0
    assert captured["cmd"][-1] == str(script)


def test_non_zero_exit_is_surfaced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = tmp_path / "geo_update.sh"
    script.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")

    def fake_run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 3)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert sbgeo.update_geo_data(script=script) == 3
