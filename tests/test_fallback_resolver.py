"""Tests for sb_xray.routing.isp._resolve_fallback_tags (Phase 5)."""

from __future__ import annotations

import pytest
from sb_xray.routing import isp


@pytest.mark.parametrize(
    ("strategy", "restricted", "has_warp", "expected"),
    [
        # direct — byte-compatible with Phases 1–4
        ("direct", False, False, ["direct"]),
        ("direct", True, False, ["direct"]),
        ("direct", True, True, ["direct"]),
        ("direct", False, True, ["direct"]),
        # block — fail-closed regardless of region
        ("block", False, False, ["block"]),
        ("block", True, False, ["block"]),
        ("block", True, True, ["block"]),
        ("block", False, True, ["block"]),
        # warp — only materialises when restricted + has_warp
        ("warp", True, True, ["warp", "direct"]),
        ("warp", True, False, ["direct"]),
        ("warp", False, True, ["direct"]),
        ("warp", False, False, ["direct"]),
    ],
)
def test_resolve_fallback_tags_matrix(
    strategy: str, restricted: bool, has_warp: bool, expected: list[str]
) -> None:
    assert (
        isp._resolve_fallback_tags(
            strategy=strategy,
            is_restricted=restricted,
            has_warp=has_warp,
        )
        == expected
    )


def test_unknown_strategy_falls_back_to_direct(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    caplog.set_level(logging.WARNING, logger="sb_xray.routing.isp")
    result = isp._resolve_fallback_tags(strategy="nonsense", is_restricted=False, has_warp=False)
    assert result == ["direct"]
    assert any("unknown ISP_FALLBACK_STRATEGY" in r.message for r in caplog.records)


def test_env_drives_strategy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ISP_FALLBACK_STRATEGY", "block")
    assert isp._resolve_fallback_tags(is_restricted=False, has_warp=False) == ["block"]


def test_env_has_warp_reads_warp_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ISP_FALLBACK_STRATEGY", "warp")
    monkeypatch.setenv("WARP_ENABLED", "true")
    monkeypatch.setenv("GEOIP_INFO", "HongKong|192.0.2.1")
    assert isp._resolve_fallback_tags() == ["warp", "direct"]


def test_xray_fallback_tag_follows_strategy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ISP_FALLBACK_STRATEGY", "block")
    _, bal = isp.build_xray_balancer({"proxy-hk": 100.0})
    assert '"fallbackTag": "block"' in bal


def test_sb_urltest_chain_with_warp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ISP_FALLBACK_STRATEGY", "warp")
    monkeypatch.setenv("WARP_ENABLED", "true")
    monkeypatch.setenv("GEOIP_INFO", "HongKong|192.0.2.1")
    import json

    out = isp.build_sb_urltest({"proxy-hk": 100.0})
    data = json.loads(out.rstrip(","))
    assert data["outbounds"] == ["proxy-hk", "warp", "direct"]


def test_network_get_fallback_proxy_delegates_to_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sb_xray import network

    monkeypatch.delenv("HAS_ISP_NODES", raising=False)
    monkeypatch.setenv("ISP_FALLBACK_STRATEGY", "block")
    assert network.get_fallback_proxy() == "block"
    monkeypatch.setenv("HAS_ISP_NODES", "true")
    assert network.get_fallback_proxy() == "isp-auto"
