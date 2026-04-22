"""Tests for sb_xray.routing.isp._resolve_probe_config (Phase 1)."""

from __future__ import annotations

import pytest
from sb_xray.routing import isp


def _clear_env(mp: pytest.MonkeyPatch) -> None:
    for k in ("ISP_PROBE_URL", "ISP_PROBE_INTERVAL", "ISP_PROBE_TOLERANCE_MS"):
        mp.delenv(k, raising=False)


def test_default_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    cfg = isp._resolve_probe_config()
    assert cfg.url == "https://speed.cloudflare.com/__down?bytes=1048576"
    assert cfg.interval == "1m"
    assert cfg.tolerance_ms == 300


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("ISP_PROBE_URL", "https://example.test/probe")
    monkeypatch.setenv("ISP_PROBE_INTERVAL", "45s")
    monkeypatch.setenv("ISP_PROBE_TOLERANCE_MS", "800")
    cfg = isp._resolve_probe_config()
    assert cfg.url == "https://example.test/probe"
    assert cfg.interval == "45s"
    assert cfg.tolerance_ms == 800


def test_empty_env_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("ISP_PROBE_URL", "")
    monkeypatch.setenv("ISP_PROBE_INTERVAL", "")
    monkeypatch.setenv("ISP_PROBE_TOLERANCE_MS", "")
    cfg = isp._resolve_probe_config()
    assert cfg.url == "https://speed.cloudflare.com/__down?bytes=1048576"
    assert cfg.interval == "1m"
    assert cfg.tolerance_ms == 300


def test_kwargs_override_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ISP_PROBE_URL", "https://env.test/probe")
    cfg = isp._resolve_probe_config(
        url="https://kwarg.test/probe", interval="10s", tolerance_ms=150
    )
    assert cfg.url == "https://kwarg.test/probe"
    assert cfg.interval == "10s"
    assert cfg.tolerance_ms == 150


def test_invalid_tolerance_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("ISP_PROBE_TOLERANCE_MS", "not-a-number")
    cfg = isp._resolve_probe_config()
    assert cfg.tolerance_ms == 300
