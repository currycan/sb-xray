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
    monkeypatch.delenv("GEOIP_CC", raising=False)  # force the name-regex fallback path
    monkeypatch.setenv("GEOIP_INFO", geoip)
    assert net.is_restricted_region() is expected


def test_is_restricted_region_no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEOIP_INFO", raising=False)
    monkeypatch.delenv("GEOIP_CC", raising=False)
    assert net.is_restricted_region() is False


def test_is_restricted_region_explicit_arg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEOIP_INFO", raising=False)
    monkeypatch.delenv("GEOIP_CC", raising=False)
    assert net.is_restricted_region("CN|1.2.3.4") is True
    assert net.is_restricted_region("US|1.2.3.4") is False
    assert net.is_restricted_region() is False  # no-arg falls back to env (back-compat)


@pytest.mark.parametrize(
    "cc,expected",
    [("CN", True), ("HK", True), ("MO", True), ("RU", True), ("us", False), ("JP", False)],
)
def test_is_restricted_region_by_iso_cc(cc: str, expected: bool) -> None:
    # ISO code is authoritative — decided purely on cc, regardless of info text.
    assert net.is_restricted_region("Tokyo JP|1.1.1.1", cc=cc) is expected


def test_iso_cc_overrides_name_regex(monkeypatch: pytest.MonkeyPatch) -> None:
    # GEOIP_CC present → name regex on GEOIP_INFO is ignored.
    monkeypatch.setenv("GEOIP_CC", "US")
    monkeypatch.setenv("GEOIP_INFO", "香港 HK|1.2.3.4")
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


# ---- get_geo_info / get_geo_cc (ipapi.is JSON) ------------------------------

_IPAPI_US: dict = {
    "ip": "198.46.142.117",
    "location": {"country_code": "US", "country": "United States", "city": "Buffalo"},
    "datacenter": {"country": "US"},
    "asn": {"type": "hosting", "country": "us"},
}


@respx.mock
def test_get_geo_info_from_ipapi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # region 取国家级中文名（via _CC_TO_ZH），city 不进 GEOIP_INFO；cc 为 ISO。
    monkeypatch.setattr(net, "_DEFAULT_CACHE", tmp_path / "ipapi.json")
    respx.get("https://api.ipapi.is/").mock(return_value=httpx.Response(200, json=_IPAPI_US))
    assert net.get_geo_info() == "美国|198.46.142.117"
    assert net.get_geo_cc() == "US"


@respx.mock
def test_get_geo_info_unmapped_cc_falls_back_to_english(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(net, "_DEFAULT_CACHE", tmp_path / "ipapi.json")
    data = {"ip": "1.2.3.4", "location": {"country_code": "ZZ", "country": "Neverland"}}
    respx.get("https://api.ipapi.is/").mock(return_value=httpx.Response(200, json=data))
    assert net.get_geo_info() == "Neverland|1.2.3.4"
    assert net.get_geo_cc() == "ZZ"


@respx.mock
def test_get_geo_info_empty_when_both_sources_fail_no_country(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ipapi.is 给不出国家码 → 落到 ip-api 回退；回退也失败 → ""。
    monkeypatch.setattr(net, "_DEFAULT_CACHE", tmp_path / "ipapi.json")
    monkeypatch.setattr(net, "_DEFAULT_IP_API_CACHE", tmp_path / "ip-api.json")
    respx.get("https://api.ipapi.is/").mock(return_value=httpx.Response(200, json={"ip": "1.2.3.4"}))
    respx.get(host="ip-api.com").mock(side_effect=httpx.ConnectError("x"))
    assert net.get_geo_info() == ""


@respx.mock
def test_get_geo_info_empty_when_both_sources_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(net, "_DEFAULT_CACHE", tmp_path / "ipapi.json")
    monkeypatch.setattr(net, "_DEFAULT_IP_API_CACHE", tmp_path / "ip-api.json")
    respx.get("https://api.ipapi.is/").mock(side_effect=httpx.ConnectError("x"))
    respx.get(host="ip-api.com").mock(side_effect=httpx.ConnectError("x"))
    assert net.get_geo_info() == ""
    assert net.get_geo_cc() == ""


@respx.mock
def test_get_geo_info_falls_back_to_ip_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ipapi.is 失败 → ip-api.com 二级回退（ISO 码 + 中文国名，无城市）。
    monkeypatch.setattr(net, "_DEFAULT_CACHE", tmp_path / "ipapi.json")
    monkeypatch.setattr(net, "_DEFAULT_IP_API_CACHE", tmp_path / "ip-api.json")
    respx.get("https://api.ipapi.is/").mock(side_effect=httpx.ConnectError("x"))
    ip_api = respx.get(host="ip-api.com").mock(
        return_value=httpx.Response(
            200,
            json={"status": "success", "countryCode": "JP", "country": "日本", "query": "203.0.113.7"},
        )
    )
    assert net.get_geo_info() == "日本|203.0.113.7"
    assert net.get_geo_cc() == "JP"
    assert ip_api.called


@respx.mock
def test_geo_and_iptype_share_single_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 一次 boot 只抓一次 ipapi.is：geo + cc + ip_type 共享同一缓存文件。
    monkeypatch.setattr(net, "_DEFAULT_CACHE", tmp_path / "ipapi.json")
    route = respx.get("https://api.ipapi.is/").mock(
        return_value=httpx.Response(200, json=_IPAPI_US)
    )
    assert net.get_geo_info() == "美国|198.46.142.117"  # fetch #1, writes cache
    assert net.get_geo_cc() == "US"  # cache reuse
    assert net.check_ip_type() == "hosting"  # cache reuse
    assert route.call_count == 1
