"""Tests for sb_xray.stages.keys (G1: xray subprocess timeout)."""

from __future__ import annotations

import subprocess

import pytest
from sb_xray.stages import keys


def test_run_xray_passes_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def _fake_run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        seen["timeout"] = kw.get("timeout")
        return subprocess.CompletedProcess(cmd, 0, stdout="Private key: a\nPublic key: b\n", stderr="")

    monkeypatch.setattr(keys.subprocess, "run", _fake_run)
    lines = keys._run_xray(["xray", "x25519"])
    assert seen["timeout"] == keys._XRAY_TIMEOUT_SEC
    assert lines == ["Private key: a", "Public key: b"]


def test_run_xray_raises_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))

    monkeypatch.setattr(keys.subprocess, "run", _boom)
    with pytest.raises(subprocess.TimeoutExpired):
        keys._run_xray(["xray", "x25519"])
