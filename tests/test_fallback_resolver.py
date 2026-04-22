"""Tests for sb_xray.routing.isp._resolve_fallback_tags (Phase 5)."""

from __future__ import annotations

import pytest
from sb_xray.routing import isp


@pytest.mark.parametrize(
    ("strategy", "expected"),
    [
        ("direct", ["direct"]),  # byte-compatible default
        ("block", ["block"]),  # fail-closed
    ],
)
def test_resolve_fallback_tags_matrix(strategy: str, expected: list[str]) -> None:
    assert isp._resolve_fallback_tags(strategy=strategy) == expected


def test_unknown_strategy_falls_back_to_direct(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    caplog.set_level(logging.WARNING, logger="sb_xray.routing.isp")
    result = isp._resolve_fallback_tags(strategy="nonsense")
    assert result == ["direct"]
    assert any("unknown ISP_FALLBACK_STRATEGY" in r.message for r in caplog.records)


def test_env_drives_strategy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ISP_FALLBACK_STRATEGY", "block")
    assert isp._resolve_fallback_tags() == ["block"]


def test_env_default_is_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ISP_FALLBACK_STRATEGY", raising=False)
    assert isp._resolve_fallback_tags() == ["direct"]


def test_xray_fallback_tag_follows_strategy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ISP_FALLBACK_STRATEGY", "block")
    _, bal = isp.build_xray_balancer({"proxy-hk": 100.0})
    assert '"fallbackTag": "block"' in bal


def test_sb_urltest_tail_with_block(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ISP_FALLBACK_STRATEGY", "block")
    import json

    out = isp.build_sb_urltest({"proxy-hk": 100.0})
    data = json.loads(out.rstrip(","))
    assert data["outbounds"] == ["proxy-hk", "block"]


def test_network_get_fallback_proxy_delegates_to_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sb_xray import network

    monkeypatch.delenv("HAS_ISP_NODES", raising=False)
    monkeypatch.setenv("ISP_FALLBACK_STRATEGY", "block")
    assert network.get_fallback_proxy() == "block"
    monkeypatch.setenv("HAS_ISP_NODES", "true")
    assert network.get_fallback_proxy() == "isp-auto"
