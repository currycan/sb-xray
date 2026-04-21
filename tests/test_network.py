"""Tests for sb_xray.network (entrypoint.sh §7-8 equivalent)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from sb_xray import network as net

# ---- is_restricted_region ---------------------------------------------------


@pytest.mark.parametrize(
    "geoip,expected",
    [
        ("香港 HK|1.2.3.4", True),
        ("Hong Kong HK|1.2.3.4", True),
        ("中国 CN|5.6.7.8", True),
        ("China CN|5.6.7.8", True),
        ("Russia RU|9.9.9.9", True),
        ("澳门 MO|4.3.2.1", True),
        ("Tokyo JP|8.8.8.8", False),
        ("New York US|1.1.1.1", False),
        ("", False),
    ],
)
def test_is_restricted_region(geoip: str, expected: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEOIP_INFO", geoip)
    assert net.is_restricted_region() is expected


def test_is_restricted_region_no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEOIP_INFO", raising=False)
    assert net.is_restricted_region() is False


# ---- fallback / preferred strategy ------------------------------------------


def test_fallback_proxy_with_isp_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAS_ISP_NODES", "1")
    assert net.get_fallback_proxy() == "isp-auto"


def test_fallback_proxy_without_isp_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HAS_ISP_NODES", raising=False)
    assert net.get_fallback_proxy() == "direct"


def test_isp_preferred_strategy_with_isp_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HAS_ISP_NODES", "1")
    assert net.get_isp_preferred_strategy() == "isp-auto"


def test_isp_preferred_strategy_without_isp_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HAS_ISP_NODES", raising=False)
    assert net.get_isp_preferred_strategy() == "direct"


# ---- check_brutal_status ----------------------------------------------------


def test_check_brutal_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = tmp_path / "brutal"
    fake.mkdir()
    monkeypatch.setattr(net, "_BRUTAL_MODULE_PATH", fake)
    assert net.check_brutal_status() == "true"


def test_check_brutal_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(net, "_BRUTAL_MODULE_PATH", tmp_path / "missing")
    assert net.check_brutal_status() == "false"


# ---- detect_ip_strategy -----------------------------------------------------


def test_ip_strategy_dual_stack() -> None:
    assert net.detect_ip_strategy(v4_ok=True, v6_ok=True) == "prefer_ipv4"


def test_ip_strategy_v6_only() -> None:
    assert net.detect_ip_strategy(v4_ok=False, v6_ok=True) == "ipv6_only"


def test_ip_strategy_v4_only() -> None:
    assert net.detect_ip_strategy(v4_ok=True, v6_ok=False) == "ipv4_only"


def test_ip_strategy_no_network() -> None:
    # Bash defaulted to "ipv4_only" when both failed — keep parity.
    assert net.detect_ip_strategy(v4_ok=False, v6_ok=False) == "ipv4_only"


# ---- check_ip_type ----------------------------------------------------------


@respx.mock
def test_check_ip_type_returns_asn_type(tmp_path: Path) -> None:
    respx.get("https://api.ipapi.is/").mock(
        return_value=httpx.Response(200, json={"asn": {"type": "isp"}})
    )
    assert net.check_ip_type(cache_path=tmp_path / "ipapi.json") == "isp"


def test_check_ip_type_cache_hit(tmp_path: Path) -> None:
    cache = tmp_path / "cached.json"
    cache.write_text('{"asn":{"type":"hosting"}}', encoding="utf-8")
    assert net.check_ip_type(cache_path=cache) == "hosting"


@respx.mock
def test_check_ip_type_unknown_on_error(tmp_path: Path) -> None:
    respx.get("https://api.ipapi.is/").mock(side_effect=httpx.ConnectError("down"))
    assert net.check_ip_type(cache_path=tmp_path / "fail.json") == "unknown"


# ---- get_geo_info -----------------------------------------------------------


@respx.mock
def test_get_geo_info_returns_pipe_separated() -> None:
    html = (
        "<html><body><p>1.2.3.4 这是您访问国内网站所使用的IP</p>"
        "<p>5.6.7.8 您的IP来自 中国 上海</p></body></html>"
    )
    respx.get("https://ip111.cn/").mock(return_value=httpx.Response(200, text=html))
    result = net.get_geo_info()
    assert "|" in result


@respx.mock
def test_get_geo_info_empty_on_failure() -> None:
    respx.get("https://ip111.cn/").mock(side_effect=httpx.ConnectError("x"))
    assert net.get_geo_info() == ""
