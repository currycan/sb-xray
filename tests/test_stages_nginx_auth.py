"""Tests for scripts/sb_xray/stages/nginx_auth.py."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sb_xray.stages import nginx_auth as sbauth


def test_skips_when_credentials_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PUBLIC_USER", raising=False)
    monkeypatch.delenv("PUBLIC_PASSWORD", raising=False)

    def must_not_call(*args: object, **kwargs: object) -> object:
        raise AssertionError("_apr1 should not run when credentials missing")

    monkeypatch.setattr(sbauth, "_apr1", must_not_call)
    assert sbauth.setup_basic_auth(path=tmp_path / "htpasswd") is False


def test_writes_htpasswd_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "htpasswd"
    monkeypatch.setattr(sbauth, "_apr1", lambda pw: f"$apr1$abc${pw}-enc")
    assert sbauth.setup_basic_auth(user="alice", password="secret", path=target) is True
    content = target.read_text(encoding="utf-8")
    assert content == "alice:$apr1$abc$secret-enc\n"
    assert oct(target.stat().st_mode)[-3:] == "644"


def test_apr1_uses_openssl_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(
        cmd: list[str], check: bool, capture_output: bool, text: bool
    ) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="$apr1$xy$zz\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert sbauth._apr1("password") == "$apr1$xy$zz"
    assert captured["cmd"] == ["openssl", "passwd", "-apr1", "password"]
