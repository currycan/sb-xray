"""Tests for sb_xray.cert (entrypoint.sh §12 equivalent)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from sb_xray import cert


class _FakeCompleted:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_default_ssl_path_reads_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: default ``ssl_path=Path('/ssl')`` was wrong — templates
    render ``${SSL_PATH}`` (``/pki`` by Dockerfile) so nginx / xray /
    sing-box look for certs under ``/pki/sb_xray_bundle.crt`` while the
    old Python default wrote them to ``/ssl/`` → infinite nginx/xray
    restart loop observed on cn2 prod."""
    ssl_path = tmp_path / "pki"
    ssl_path.mkdir()
    for suffix in (".crt", ".key", "-ca.crt"):
        (ssl_path / f"bundle{suffix}").write_text("placeholder", encoding="utf-8")

    monkeypatch.setenv("SSL_PATH", str(ssl_path))

    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeCompleted:
        if cmd[0] == "openssl":
            return _FakeCompleted(returncode=0)
        pytest.fail(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)
    # NOT passing ssl_path → reads $SSL_PATH
    result = cert.ensure_certificate(name="bundle", params="vpn.example.com:ali")
    assert result is cert.CertStatus.SKIPPED


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
    # New flow: always register + issue; no '--list' short-circuit
    # (the old guard was fragile — acme.sh --list lied about holding
    # a cert whose files on disk were gone, causing --install-cert to
    # silently fail).
    assert "--register-account" in acme_flags
    assert "--issue" in acme_flags
    assert "--install-cert" in acme_flags
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


def test_acme_env_strips_log_level_to_avoid_integer_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: acme.sh L347/381/414 compare LOG_LEVEL numerically.

    Dockerfile sets LOG_LEVEL="warning" (string) for xray/sing-box; leaving
    it in the subprocess env makes acme.sh log `integer expected` warnings.
    """
    monkeypatch.setenv("LOG_LEVEL", "warning")
    monkeypatch.setenv("PATH", "/acme.sh:/usr/bin")
    env = cert._acme_env()
    assert "LOG_LEVEL" not in env
    assert env["PATH"].startswith("/acme.sh")


def test_install_purges_stale_nginx_conf_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: entrypoint.sh:899 purges /etc/nginx/conf.d/* and stream.d/*
    before acme.sh --install-cert; the Python port must do the same to avoid
    acme.sh's reload-nginx step loading orphaned upstream blocks.
    """
    ssl_path = tmp_path / "ssl"
    nginx_root = tmp_path / "etc" / "nginx"
    conf_dir = nginx_root / "conf.d"
    stream_dir = nginx_root / "stream.d"
    conf_dir.mkdir(parents=True)
    stream_dir.mkdir(parents=True)
    stale_http = conf_dir / "stale.conf"
    stale_stream = stream_dir / "stale.conf"
    stale_http.write_text("server { }\n", encoding="utf-8")
    stale_stream.write_text("server { }\n", encoding="utf-8")

    # Rebind the hard-coded paths used inside ensure_certificate.
    import sb_xray.cert as cert_mod

    orig_path = cert_mod.Path
    purged: list[Path] = []

    class _Path(orig_path):  # type: ignore[valid-type,misc]
        def iterdir(self):  # type: ignore[override]
            for p in orig_path(self).iterdir():
                purged.append(p)
                yield p

    # Monkeypatch the two literal targets: redirect /etc/nginx/... -> tmp_path/etc/nginx/...
    def fake_path(*args: object, **kwargs: object) -> Path:
        if args == ("/etc/nginx/conf.d",):
            return conf_dir
        if args == ("/etc/nginx/stream.d",):
            return stream_dir
        return orig_path(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(cert_mod, "Path", fake_path)
    # Return 0 for every subprocess call — the assertion we care about
    # is the on-disk purge side-effect between --issue and
    # --install-cert, not subprocess exit codes.
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _FakeCompleted(returncode=0, stdout=""))
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

    cert.ensure_certificate(name="bundle", params="vpn.example.com:ali", ssl_path=ssl_path)
    assert not stale_http.exists()
    assert not stale_stream.exists()


def test_install_quits_reloadcmd_nginx_and_clears_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: entrypoint.sh:903 runs `nginx -s quit` + `rm nginx.pid`
    after acme.sh --install-cert so supervisord can fork a clean nginx.
    """
    ssl_path = tmp_path / "ssl"
    pid_dir = tmp_path / "run" / "nginx"
    pid_dir.mkdir(parents=True)
    pid_file = pid_dir / "nginx.pid"
    pid_file.write_text("12345\n", encoding="utf-8")

    import sb_xray.cert as cert_mod

    orig_path = cert_mod.Path

    def fake_path(*args: object, **kwargs: object) -> Path:
        if args == ("/var/run/nginx/nginx.pid",):
            return pid_file
        if args in (("/etc/nginx/conf.d",), ("/etc/nginx/stream.d",)):
            return tmp_path / "nonexistent"
        return orig_path(*args, **kwargs)  # type: ignore[arg-type]

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeCompleted:
        calls.append(list(cmd))
        if cmd[:2] == ["/usr/sbin/nginx", "-s"]:
            return _FakeCompleted(returncode=0)
        if cmd[0] == "openssl":
            return _FakeCompleted(returncode=1)
        if cmd[0] == "acme.sh" and cmd[1:2] == ["--list"]:
            return _FakeCompleted(returncode=0, stdout="vpn.example.com")
        return _FakeCompleted(returncode=0)

    monkeypatch.setattr(cert_mod, "Path", fake_path)
    monkeypatch.setattr(subprocess, "run", fake_run)
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

    cert.ensure_certificate(name="bundle", params="vpn.example.com:ali", ssl_path=ssl_path)

    assert ["/usr/sbin/nginx", "-s", "quit"] in calls
    assert not pid_file.exists()


def test_issue_nonzero_exit_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: acme.sh --issue returning a bad code must abort the
    stage. Observed on cn2 prod — the acme.sh store was stale (leftover
    Main_Domain entry but no ca.cer), --issue silently failed and
    --install-cert then 'installed' non-existent files."""
    ssl_path = tmp_path / "ssl"
    ssl_path.mkdir()

    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeCompleted:
        if cmd[0] == "acme.sh" and "--issue" in cmd:
            return _FakeCompleted(returncode=1)  # simulate DNS cred failure
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

    with pytest.raises(RuntimeError, match=r"acme\.sh --issue exited 1"):
        cert.ensure_certificate(name="bundle", params="vpn.example.com:ali", ssl_path=ssl_path)


def test_issue_failure_hint_rate_limit() -> None:
    """CA rate-limit (retryafter=) → hint mentions wildcard-capable CA swap."""
    acme_out = "[...] The retryafter=86400 value is too large (> 600), will not retry anymore."
    hint = cert._issue_failure_hint(acme_out, server="zerossl", first_domain="a.b")
    assert "rate-limited" in hint
    assert "ACMESH_SERVER_NAME=letsencrypt" in hint
    # The alternatives we suggest must all support wildcard via DNS-01;
    # buypass doesn't, so the hint must NOT suggest it.
    assert "buypass" not in hint.lower() or "do not switch to buypass" in hint.lower()


def test_issue_failure_hint_dns_credentials() -> None:
    """Missing DNS creds → hint mentions ALI_KEY/CF_TOKEN."""
    acme_out = "You don't specify aliyun api key and secret yet."
    hint = cert._issue_failure_hint(acme_out, server="zerossl", first_domain="a.b")
    assert "ALI_KEY" in hint and "CF_TOKEN" in hint


def test_issue_failure_hint_generic_fallback() -> None:
    hint = cert._issue_failure_hint("random failure text", server="zerossl", first_domain="a.b")
    assert "DNS credentials" in hint  # generic-but-actionable fallback


def test_acme_env_translates_dns_credential_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression (cn2 prod): acme.sh's dns_ali / dns_cf plugins read
    mixed-case names (Ali_Key / Ali_Secret / CF_Token / CF_Zone_ID /
    CF_Account_ID) but the SECRET_FILE convention — and bash
    entrypoint.sh's own SECRET_FILE — uses upper-case ALI_KEY etc.
    Bash did the translation manually before --issue; the Python port
    missed it → dns_ali logged 'You don't specify aliyun api key and
    secret yet' and acme.sh exited 1."""
    monkeypatch.setenv("ALI_KEY", "ali-key-123")
    monkeypatch.setenv("ALI_SECRET", "ali-secret-abc")
    monkeypatch.setenv("CF_TOKEN", "cf-token")
    monkeypatch.setenv("CF_ZONE_ID", "zone-id")
    monkeypatch.setenv("CF_ACCOUNT_ID", "account-id")
    # Drop any incidental mixed-case entries so the test proves the
    # translation fired (not inheritance from the shell).
    for k in ("Ali_Key", "Ali_Secret", "CF_Token", "CF_Zone_ID", "CF_Account_ID"):
        monkeypatch.delenv(k, raising=False)

    env = cert._acme_env()
    assert env["Ali_Key"] == "ali-key-123"
    assert env["Ali_Secret"] == "ali-secret-abc"
    assert env["CF_Token"] == "cf-token"
    assert env["CF_Zone_ID"] == "zone-id"
    assert env["CF_Account_ID"] == "account-id"
    # Originals preserved (acme.sh reads only the mixed-case form, but
    # leaving the upper-case variants around has no cost).
    assert env["ALI_KEY"] == "ali-key-123"


def test_install_cert_nonzero_exit_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: acme.sh --install-cert failure (e.g. source .cer
    missing from acme.sh's own store) must abort, not silently return
    'installed'. Before this, nginx/xray restart-looped forever
    referencing non-existent /pki/sb_xray_bundle.crt."""
    ssl_path = tmp_path / "ssl"
    ssl_path.mkdir()

    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeCompleted:
        if cmd[0] == "acme.sh" and "--install-cert" in cmd:
            return _FakeCompleted(returncode=1)
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

    with pytest.raises(RuntimeError, match=r"acme\.sh --install-cert exited 1"):
        cert.ensure_certificate(name="bundle", params="vpn.example.com:ali", ssl_path=ssl_path)
