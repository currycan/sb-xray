"""Tests for scripts/sb_xray/stages/cert_renew.py."""

from __future__ import annotations

from pathlib import Path

import pytest
from sb_xray.cert import CertStatus
from sb_xray.stages import cert_renew


def test_run_renews_with_domain_params(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOMAIN", "example.test")
    monkeypatch.setenv("CDNDOMAIN", "cdn.example.test")
    secret = tmp_path / "secret"
    secret.write_text('ACMESH_REGISTER_EMAIL="a@b.test"\n', encoding="utf-8")
    monkeypatch.setenv("SECRET_FILE", str(secret))

    calls: dict[str, object] = {}

    def fake_ensure(*, name: str, params: str, ssl_path: Path | None = None) -> CertStatus:
        calls["name"] = name
        calls["params"] = params
        return CertStatus.SKIPPED

    monkeypatch.setattr(cert_renew, "ensure_certificate", fake_ensure)
    rc = cert_renew.run()
    assert rc == 0
    assert calls["name"] == "sb_xray_bundle"
    assert calls["params"] == "example.test:ali|cdn.example.test:cf"
    # SECRET_FILE 凭据须被载入 env 后才调 ensure_certificate
    import os
    assert os.environ.get("ACMESH_REGISTER_EMAIL") == "a@b.test"


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
