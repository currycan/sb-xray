"""Tests for sb_xray.cert (entrypoint.sh §12 equivalent)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from sb_xray import cert


class _FakeCompleted:
    def __init__(self, returncode: int = 0, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout


def test_skip_renew_when_cert_valid_gt_7d(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ssl_path = tmp_path / "ssl"
    ssl_path.mkdir()
    for suffix in (".crt", ".key", "-ca.crt"):
        (ssl_path / f"cdn{suffix}").write_text("placeholder", encoding="utf-8")

    captured: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeCompleted:
        captured.append(cmd)
        if cmd[0] == "openssl":
            return _FakeCompleted(returncode=0)  # valid > 7d
        pytest.fail(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = cert.ensure_certificate(name="cdn", params="vpn.example.com:ali", ssl_path=ssl_path)
    assert result is cert.CertStatus.SKIPPED
    assert all(c[0] == "openssl" for c in captured)


def test_issue_when_cert_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ssl_path = tmp_path / "ssl"
    ssl_path.mkdir()

    commands: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeCompleted:
        commands.append(cmd)
        if cmd[0] == "acme.sh" and "--list" in cmd:
            return _FakeCompleted(returncode=0, stdout="")
        return _FakeCompleted(returncode=0)

    for env in (
        "ACMESH_REGISTER_EMAIL",
        "ACMESH_SERVER_NAME",
        "ALI_KEY",
        "ALI_SECRET",
        "CF_TOKEN",
        "CF_ZONE_ID",
        "CF_ACCOUNT_ID",
    ):
        monkeypatch.setenv(env, f"fake-{env.lower()}")
    monkeypatch.setattr(subprocess, "run", fake_run)

    cert.ensure_certificate(name="cdn", params="vpn.example.com:ali", ssl_path=ssl_path)
    acme_flags = [c[1] for c in commands if c[0] == "acme.sh" and len(c) > 1]
    assert "--list" in acme_flags
    assert "--register-account" in acme_flags
    assert "--issue" in acme_flags
    assert "--install-cert" in acme_flags


def test_issue_raises_on_missing_required_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ssl_path = tmp_path / "ssl"
    ssl_path.mkdir()

    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeCompleted:
        if cmd[0] == "acme.sh" and "--list" in cmd:
            return _FakeCompleted(returncode=0, stdout="")
        return _FakeCompleted(returncode=0)

    for env in (
        "ACMESH_REGISTER_EMAIL",
        "ACMESH_SERVER_NAME",
        "ALI_KEY",
        "ALI_SECRET",
        "CF_TOKEN",
        "CF_ZONE_ID",
        "CF_ACCOUNT_ID",
    ):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="required environment variables"):
        cert.ensure_certificate(name="cdn", params="vpn.example.com:ali", ssl_path=ssl_path)


def test_google_ca_requires_eab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ssl_path = tmp_path / "ssl"
    ssl_path.mkdir()

    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeCompleted:
        return _FakeCompleted(returncode=0, stdout="")

    for env in (
        "ACMESH_REGISTER_EMAIL",
        "ALI_KEY",
        "ALI_SECRET",
        "CF_TOKEN",
        "CF_ZONE_ID",
        "CF_ACCOUNT_ID",
    ):
        monkeypatch.setenv(env, "v")
    monkeypatch.setenv("ACMESH_SERVER_NAME", "google")
    monkeypatch.delenv("ACMESH_EAB_KID", raising=False)
    monkeypatch.delenv("ACMESH_EAB_HMAC_KEY", raising=False)
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="EAB"):
        cert.ensure_certificate(name="cdn", params="vpn.example.com:ali", ssl_path=ssl_path)


def test_wildcard_expansion_for_domain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ssl_path = tmp_path / "ssl"
    ssl_path.mkdir()
    issue_cmd: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeCompleted:
        if cmd[0] == "acme.sh" and len(cmd) > 1 and cmd[1] == "--issue":
            issue_cmd.append(cmd)
        if cmd[0] == "acme.sh" and "--list" in cmd:
            return _FakeCompleted(returncode=0, stdout="")
        return _FakeCompleted(returncode=0)

    for env in (
        "ACMESH_REGISTER_EMAIL",
        "ACMESH_SERVER_NAME",
        "ALI_KEY",
        "ALI_SECRET",
        "CF_TOKEN",
        "CF_ZONE_ID",
        "CF_ACCOUNT_ID",
    ):
        monkeypatch.setenv(env, "v")
    monkeypatch.setattr(subprocess, "run", fake_run)

    cert.ensure_certificate(name="cdn", params="vpn.example.com:ali", ssl_path=ssl_path)
    flat = " ".join(issue_cmd[0])
    assert "vpn.example.com" in flat
    assert "*.vpn.example.com" in flat
