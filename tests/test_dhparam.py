"""Tests for sb_xray.stages.dhparam (G1: subprocess timeout)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sb_xray.stages import dhparam


def test_ensure_dhparam_skips_when_present(tmp_path: Path) -> None:
    f = tmp_path / "dhparam.pem"
    f.write_text("x", encoding="utf-8")
    assert dhparam.ensure_dhparam(path=f) is False


def test_ensure_dhparam_passes_timeout_to_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    def _fake_run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[bytes]:
        seen["cmd"] = cmd
        seen["timeout"] = kw.get("timeout")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(dhparam.subprocess, "run", _fake_run)
    out = tmp_path / "dhparam.pem"
    assert dhparam.ensure_dhparam(path=out) is True
    assert seen["timeout"] == dhparam._DHPARAM_TIMEOUT_SEC
    assert seen["cmd"][0] == "openssl"


def test_ensure_dhparam_raises_on_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))

    monkeypatch.setattr(dhparam.subprocess, "run", _boom)
    with pytest.raises(subprocess.TimeoutExpired):
        dhparam.ensure_dhparam(path=tmp_path / "dhparam.pem")
