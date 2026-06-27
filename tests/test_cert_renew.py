"""Tests for scripts/sb_xray/stages/cert_renew.py."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from sb_xray.cert import CertStatus
from sb_xray.stages import cert_renew


def test_run_renews_with_domain_params(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOMAIN", "example.test")
    monkeypatch.setenv("CDNDOMAIN", "cdn.example.test")
    monkeypatch.delenv("ACMESH_REGISTER_EMAIL", raising=False)
    secret = tmp_path / "secret"
    secret.write_text('ACMESH_REGISTER_EMAIL="a@b.test"\n', encoding="utf-8")
    monkeypatch.setenv("SECRET_FILE", str(secret))

    calls: dict[str, object] = {}

    def fake_ensure(*, name: str, params: str, ssl_path: Path | None = None) -> CertStatus:
        calls["name"] = name
        calls["params"] = params
        # SECRET_FILE 凭据须被载入 env 后才调 ensure_certificate
        assert os.environ.get("ACMESH_REGISTER_EMAIL") == "a@b.test"
        return CertStatus.SKIPPED

    monkeypatch.setattr(cert_renew, "ensure_certificate", fake_ensure)
    monkeypatch.setattr(cert_renew, "_reload_nginx", lambda: pytest.fail("must not reload on SKIPPED"))
    rc = cert_renew.run()
    assert rc == 0
    assert calls["name"] == "sb_xray_bundle"
    assert calls["params"] == "example.test:ali|cdn.example.test:cf"


def test_run_aborts_when_domain_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOMAIN", raising=False)
    monkeypatch.delenv("CDNDOMAIN", raising=False)
    monkeypatch.setattr(
        cert_renew, "ensure_certificate",
        lambda **kw: pytest.fail("must not call ensure_certificate without DOMAIN"),
    )
    assert cert_renew.run() == 1


def test_run_returns_1_on_ensure_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOMAIN", "example.test")
    monkeypatch.setenv("CDNDOMAIN", "cdn.example.test")
    monkeypatch.setenv("SECRET_FILE", str(tmp_path / "missing"))

    def boom(**kw: object) -> CertStatus:
        raise RuntimeError("acme exploded")

    monkeypatch.setattr(cert_renew, "ensure_certificate", boom)
    assert cert_renew.run() == 1


def test_run_reloads_nginx_on_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """nginx -s reload must be called when ensure_certificate returns INSTALLED."""
    monkeypatch.setenv("DOMAIN", "example.test")
    monkeypatch.setenv("CDNDOMAIN", "cdn.example.test")
    monkeypatch.setenv("SECRET_FILE", str(tmp_path / "missing"))

    monkeypatch.setattr(cert_renew, "ensure_certificate", lambda **kw: CertStatus.INSTALLED)

    reload_calls: list[bool] = []
    monkeypatch.setattr(cert_renew, "_reload_nginx", lambda: reload_calls.append(True) or True)

    rc = cert_renew.run()
    assert rc == 0
    assert reload_calls == [True], "expected exactly one nginx reload on INSTALLED"


def test_run_does_not_reload_nginx_on_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """nginx must NOT be reloaded when cert is still fresh (SKIPPED)."""
    monkeypatch.setenv("DOMAIN", "example.test")
    monkeypatch.setenv("CDNDOMAIN", "cdn.example.test")
    monkeypatch.setenv("SECRET_FILE", str(tmp_path / "missing"))

    monkeypatch.setattr(cert_renew, "ensure_certificate", lambda **kw: CertStatus.SKIPPED)
    monkeypatch.setattr(cert_renew, "_reload_nginx", lambda: pytest.fail("must not reload on SKIPPED"))

    assert cert_renew.run() == 0


def test_run_does_not_reload_nginx_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """nginx must NOT be reloaded when ensure_certificate raises."""
    monkeypatch.setenv("DOMAIN", "example.test")
    monkeypatch.setenv("CDNDOMAIN", "cdn.example.test")
    monkeypatch.setenv("SECRET_FILE", str(tmp_path / "missing"))

    monkeypatch.setattr(cert_renew, "ensure_certificate", lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(cert_renew, "_reload_nginx", lambda: pytest.fail("must not reload on failure"))

    assert cert_renew.run() == 1


def test_reload_nginx_returns_true_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_reload_nginx returns True when nginx -s reload exits 0."""
    fake_bin = tmp_path / "nginx"
    fake_bin.touch(mode=0o755)
    monkeypatch.setattr(cert_renew, "_NGINX_BIN", fake_bin)

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(cmd, returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert cert_renew._reload_nginx() is True


def test_reload_nginx_returns_false_on_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_reload_nginx returns False (non-fatal) when nginx exits non-zero."""
    fake_bin = tmp_path / "nginx"
    fake_bin.touch(mode=0o755)
    monkeypatch.setattr(cert_renew, "_NGINX_BIN", fake_bin)

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(cmd, returncode=1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert cert_renew._reload_nginx() is False


def test_reload_nginx_returns_false_when_binary_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_reload_nginx returns False gracefully when the binary does not exist."""
    monkeypatch.setattr(cert_renew, "_NGINX_BIN", tmp_path / "no_nginx_here")
    assert cert_renew._reload_nginx() is False
