"""Tests for scripts/sb_xray/stages/dhparam.py."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sb_xray.stages import dhparam as sbdh


def test_skips_when_file_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "dhparam.pem"
    target.write_text("-----BEGIN DH PARAMETERS-----\n", encoding="utf-8")

    def must_not_call(*args: object, **kwargs: object) -> object:
        raise AssertionError("subprocess.run should not run when file exists")

    monkeypatch.setattr(subprocess, "run", must_not_call)
    assert sbdh.ensure_dhparam(path=target) is False


def test_invokes_openssl_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "dhparam.pem"
    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        target.write_text("generated", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert sbdh.ensure_dhparam(path=target, bits=1024) is True
    assert captured["cmd"] == [
        "openssl",
        "dhparam",
        "-dsaparam",
        "-out",
        str(target),
        "1024",
    ]
    assert target.is_file()


def test_raises_on_openssl_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "dhparam.pem"

    def fake_run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 7)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="openssl dhparam exited with code 7"):
        sbdh.ensure_dhparam(path=target)
