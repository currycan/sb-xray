"""Tests for change-aware secret refresh (secrets.refresh_remote_secrets +
the secrets-refresh cron orchestrator)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import sb_xray.secrets as secrets
import sb_xray.stages.secrets_refresh as sr
from sb_xray.secrets import RefreshStatus


def _fake_fetch(content: str):
    def _f(out_path: Path, *, blob_path: Path) -> None:
        out_path.write_text(content, encoding="utf-8")

    return _f


# --------------------------------------------------------------------------- #
# refresh_remote_secrets                                                       #
# --------------------------------------------------------------------------- #
@pytest.fixture
def decode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECODE", "passphrase")


def test_cold_decrypt_when_missing(tmp_path: Path, decode: None, monkeypatch) -> None:
    sf = tmp_path / "secret"
    monkeypatch.setattr(secrets, "_fetch_and_decrypt", _fake_fetch("A='1'\n"))
    res = secrets.refresh_remote_secrets(secret_file=sf)
    assert res.status is RefreshStatus.COLD_DECRYPTED
    assert res.content_changed
    assert sf.read_text(encoding="utf-8") == "A='1'\n"


def test_unchanged_is_noop(tmp_path: Path, decode: None, monkeypatch) -> None:
    sf = tmp_path / "secret"
    sf.write_text("A='1'\n", encoding="utf-8")
    monkeypatch.setattr(secrets, "_fetch_and_decrypt", _fake_fetch("A='1'\n"))
    res = secrets.refresh_remote_secrets(secret_file=sf)
    assert res.status is RefreshStatus.UNCHANGED
    assert not res.content_changed
    assert not (tmp_path / "secret.new").exists()  # candidate cleaned up


def test_updated_computes_changed_and_removed(tmp_path: Path, decode: None, monkeypatch) -> None:
    sf = tmp_path / "secret"
    sf.write_text("A='1'\nB='2'\n", encoding="utf-8")
    monkeypatch.setattr(secrets, "_fetch_and_decrypt", _fake_fetch("A='9'\nC='3'\n"))
    res = secrets.refresh_remote_secrets(secret_file=sf)
    assert res.status is RefreshStatus.UPDATED
    assert res.changed_keys == frozenset({"A", "C"})  # A changed, C added
    assert res.removed_keys == frozenset({"B"})  # B dropped
    assert "A='9'" in sf.read_text(encoding="utf-8")


def test_offline_keeps_cached_file(tmp_path: Path, decode: None, monkeypatch) -> None:
    sf = tmp_path / "secret"
    sf.write_text("A='1'\n", encoding="utf-8")

    def _boom(out_path: Path, *, blob_path: Path) -> None:
        raise RuntimeError("download failed")

    monkeypatch.setattr(secrets, "_fetch_and_decrypt", _boom)
    res = secrets.refresh_remote_secrets(secret_file=sf)
    assert res.status is RefreshStatus.SKIPPED_OFFLINE
    assert sf.read_text(encoding="utf-8") == "A='1'\n"
    assert not (tmp_path / "secret.new").exists()


def test_offline_cold_raises(tmp_path: Path, decode: None, monkeypatch) -> None:
    sf = tmp_path / "secret"

    def _boom(out_path: Path, *, blob_path: Path) -> None:
        raise RuntimeError("download failed")

    monkeypatch.setattr(secrets, "_fetch_and_decrypt", _boom)
    with pytest.raises(RuntimeError):
        secrets.refresh_remote_secrets(secret_file=sf)


def test_no_decode_keeps_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DECODE", raising=False)
    sf = tmp_path / "secret"
    sf.write_text("A='1'\n", encoding="utf-8")
    res = secrets.refresh_remote_secrets(secret_file=sf)
    assert res.status is RefreshStatus.SKIPPED_NO_DECODE


def test_no_decode_cold_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DECODE", raising=False)
    sf = tmp_path / "secret"
    with pytest.raises(RuntimeError):
        secrets.refresh_remote_secrets(secret_file=sf)


# --------------------------------------------------------------------------- #
# secrets-refresh cron orchestrator                                           #
# --------------------------------------------------------------------------- #
def test_apply_env_overrides_and_pops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sf = tmp_path / "secret"
    sf.write_text("A_ISP_SECRET='fresh'\n", encoding="utf-8")
    monkeypatch.setenv("A_ISP_SECRET", "stale")  # boot-frozen old value
    monkeypatch.setenv("B_ISP_IP", "9.9.9.9")  # removed node
    sr._apply_env(frozenset({"A_ISP_SECRET"}), frozenset({"B_ISP_IP"}), sf)
    assert os.environ["A_ISP_SECRET"] == "fresh"  # forced over setdefault freeze
    assert "B_ISP_IP" not in os.environ  # removed key popped


def test_run_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_REFRESH_ENABLED", "false")
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(sr, "emit_event", lambda n, p: events.append((n, p)))
    refresh = MagicMock()
    monkeypatch.setattr(sr, "refresh_remote_secrets", refresh)
    assert sr.run() == 0
    refresh.assert_not_called()
    assert events[-1] == ("secret.refresh.noop", {"reason": "disabled"})


def test_run_noop_when_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SECRET_REFRESH_ENABLED", raising=False)
    monkeypatch.setattr(
        sr,
        "refresh_remote_secrets",
        lambda **_kw: secrets.SecretRefresh(RefreshStatus.UNCHANGED),
    )
    speed = MagicMock()
    restart = MagicMock()
    monkeypatch.setattr("sb_xray.speed_test.run_isp_speed_tests", speed)
    monkeypatch.setattr(sr, "restart_daemons", restart)
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(sr, "emit_event", lambda n, p: events.append((n, p)))
    assert sr.run() == 0
    speed.assert_not_called()
    restart.assert_not_called()
    assert events[-1][0] == "secret.refresh.noop"


def test_run_completed_reconfigures_and_restarts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sf = tmp_path / "secret"
    sf.write_text("X_ISP_SECRET='new'\n", encoding="utf-8")
    monkeypatch.setenv("SECRET_FILE", str(sf))
    monkeypatch.delenv("SECRET_REFRESH_ENABLED", raising=False)
    monkeypatch.setenv("X_ISP_SECRET", "old")  # boot-frozen; expect override
    monkeypatch.setenv("OLD_ISP_IP", "1.2.3.4")  # removed; expect pop

    monkeypatch.setattr(
        sr,
        "refresh_remote_secrets",
        lambda **_kw: secrets.SecretRefresh(
            RefreshStatus.UPDATED,
            changed_keys=frozenset({"X_ISP_SECRET"}),
            removed_keys=frozenset({"OLD_ISP_IP"}),
        ),
    )
    speed, build, create = MagicMock(), MagicMock(), MagicMock()
    restart, media = MagicMock(return_value=True), MagicMock()
    monkeypatch.setattr("sb_xray.speed_test.run_isp_speed_tests", speed)
    monkeypatch.setattr("sb_xray.routing.isp.build_client_and_server_configs", build)
    monkeypatch.setattr("sb_xray.config_builder.create_config", create)
    monkeypatch.setattr(sr, "restart_daemons", restart)
    monkeypatch.setattr(sr, "restore_media_routing", media)
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(sr, "emit_event", lambda n, p: events.append((n, p)))

    assert sr.run() == 0
    speed.assert_called_once()
    build.assert_called_once()
    create.assert_called_once()
    restart.assert_called_once()
    assert os.environ["X_ISP_SECRET"] == "new"  # changed key forced over freeze
    assert "OLD_ISP_IP" not in os.environ  # removed key popped
    name, payload = events[-1]
    assert name == "secret.refresh.completed"
    assert payload["changed"] == 1 and payload["removed"] == 1 and payload["restarted"] is True


def test_reconfigure_calls_nginx_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """credentials-changed reconfigure must nginx -s reload after restart."""
    sf = tmp_path / "secret"
    sf.write_text("X_ISP_SECRET='new'\n", encoding="utf-8")
    monkeypatch.setenv("SECRET_FILE", str(sf))
    monkeypatch.delenv("SECRET_REFRESH_ENABLED", raising=False)
    monkeypatch.setenv("X_ISP_SECRET", "old")

    monkeypatch.setattr(
        sr,
        "refresh_remote_secrets",
        lambda **_kw: secrets.SecretRefresh(
            RefreshStatus.UPDATED,
            changed_keys=frozenset({"X_ISP_SECRET"}),
            removed_keys=frozenset(),
        ),
    )
    speed, build, create = MagicMock(), MagicMock(), MagicMock()
    restart = MagicMock(return_value=True)
    media = MagicMock()
    nginx = MagicMock(return_value=True)
    monkeypatch.setattr("sb_xray.speed_test.run_isp_speed_tests", speed)
    monkeypatch.setattr("sb_xray.routing.isp.build_client_and_server_configs", build)
    monkeypatch.setattr("sb_xray.config_builder.create_config", create)
    monkeypatch.setattr(sr, "restart_daemons", restart)
    monkeypatch.setattr(sr, "restore_media_routing", media)
    monkeypatch.setattr(sr, "reload_nginx", nginx)
    monkeypatch.setattr(sr, "emit_event", lambda n, p: None)

    rc = sr.run()

    assert rc == 0
    nginx.assert_called_once_with()


def test_noop_does_not_call_nginx_reload(monkeypatch: pytest.MonkeyPatch) -> None:
    """noop path (unchanged secrets) must NOT call reload_nginx."""
    monkeypatch.delenv("SECRET_REFRESH_ENABLED", raising=False)
    monkeypatch.setattr(
        sr,
        "refresh_remote_secrets",
        lambda **_kw: secrets.SecretRefresh(RefreshStatus.UNCHANGED),
    )
    nginx = MagicMock()
    monkeypatch.setattr(sr, "reload_nginx", nginx)
    monkeypatch.setattr(sr, "restart_daemons", MagicMock())
    monkeypatch.setattr(sr, "emit_event", lambda n, p: None)

    rc = sr.run()

    assert rc == 0
    nginx.assert_not_called()
