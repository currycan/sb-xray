"""Tests for sb_xray.secrets (entrypoint.sh §14 equivalent)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from sb_xray import secrets as sbsec


class _FakeCompleted:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode


def test_skip_when_secret_file_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = tmp_path / "secret.bin"
    secret.write_bytes(b"already-there")
    monkeypatch.setenv("DECODE", "key")

    called: list[str] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeCompleted:
        called.append(cmd[0])
        return _FakeCompleted(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = sbsec.decrypt_remote_secrets(secret_file=secret)
    assert result is sbsec.SecretStatus.SKIPPED
    assert called == []


def test_raises_when_decode_env_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DECODE", raising=False)
    with pytest.raises(RuntimeError, match="DECODE"):
        sbsec.decrypt_remote_secrets(secret_file=tmp_path / "secret.bin")


@respx.mock
def test_download_and_decrypt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECODE", "test-decode-key")
    secret = tmp_path / "secret.bin"
    tmp_bin = tmp_path / "tmp.bin"

    respx.get("https://raw.githubusercontent.com/currycan/key/master/tmp.bin").mock(
        return_value=httpx.Response(200, content=b"encrypted-blob")
    )

    captured: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeCompleted:
        captured.append(cmd)
        if cmd[0] == "crypctl":
            out_idx = cmd.index("-o") + 1
            Path(cmd[out_idx]).write_bytes(b"decrypted-secret")
        return _FakeCompleted(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = sbsec.decrypt_remote_secrets(secret_file=secret, tmp_path=tmp_bin)
    assert result is sbsec.SecretStatus.DECRYPTED
    assert secret.read_bytes() == b"decrypted-secret"
    crypctl_cmd = next(c for c in captured if c[0] == "crypctl")
    assert "--key-env" in crypctl_cmd
    assert "DECODE" in crypctl_cmd
    assert not tmp_bin.exists()


@respx.mock
def test_download_failure_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECODE", "key")
    respx.get("https://raw.githubusercontent.com/currycan/key/master/tmp.bin").mock(
        side_effect=httpx.ConnectError("down")
    )
    with pytest.raises(RuntimeError, match="download failed"):
        sbsec.decrypt_remote_secrets(secret_file=tmp_path / "s.bin", tmp_path=tmp_path / "t.bin")


@respx.mock
def test_decrypt_failure_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECODE", "key")
    respx.get("https://raw.githubusercontent.com/currycan/key/master/tmp.bin").mock(
        return_value=httpx.Response(200, content=b"blob")
    )

    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeCompleted:
        return _FakeCompleted(returncode=2)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="decrypt failed"):
        sbsec.decrypt_remote_secrets(secret_file=tmp_path / "s.bin", tmp_path=tmp_path / "t.bin")
