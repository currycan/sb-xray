"""Tests for sb_xray.node_meta (show-config.sh §30-75 derivation)."""

from __future__ import annotations

import os

import pytest
from sb_xray import node_meta


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "DOMAIN",
        "GEOIP_INFO",
        "ISP_TAG",
        "IS_8K_SMOOTH",
        "IP_TYPE",
        "NODE_NAME",
        "NODE_IP",
        "REGION_INFO",
        "FLAG_INFO",
        "FLAG_PREFIX",
        "NODE_SUFFIX",
    ):
        monkeypatch.delenv(key, raising=False)


def test_derive_sets_node_name_from_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("DOMAIN", "jp.ansandy.com")
    monkeypatch.setenv("GEOIP_INFO", "Tokyo Japan|203.0.113.1")
    node_meta.derive_and_export()
    assert os.environ["NODE_NAME"] == "jp"
    assert os.environ["NODE_IP"] == "203.0.113.1"
    assert os.environ["REGION_INFO"] == "Tokyo Japan"
    assert os.environ["FLAG_INFO"] == "🇯🇵"
    assert os.environ["FLAG_PREFIX"] == "🇯🇵 "


def test_flag_prefix_empty_when_region_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("DOMAIN", "vpn.example.com")
    monkeypatch.setenv("GEOIP_INFO", "Nowhere|198.51.100.2")
    node_meta.derive_and_export()
    assert os.environ["FLAG_INFO"] == ""
    assert os.environ["FLAG_PREFIX"] == ""


def test_fast_domain_prefix_prepends_speed_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("DOMAIN", "dmit.example.com")
    monkeypatch.setenv("GEOIP_INFO", "|")
    node_meta.derive_and_export()
    assert " ✈ 高速" in os.environ["NODE_SUFFIX"]


def test_good_tag_when_proxied_isp_8k(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("DOMAIN", "vpn.example.com")
    monkeypatch.setenv("GEOIP_INFO", "|")
    monkeypatch.setenv("ISP_TAG", "socks5-1")
    monkeypatch.setenv("IS_8K_SMOOTH", "true")
    monkeypatch.setenv("IP_TYPE", "hosting")
    node_meta.derive_and_export()
    assert "✈ good" in os.environ["NODE_SUFFIX"]
    assert "✈ hosting" in os.environ["NODE_SUFFIX"]


def test_super_tag_when_residential_and_8k(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("DOMAIN", "vpn.example.com")
    monkeypatch.setenv("GEOIP_INFO", "|")
    monkeypatch.setenv("ISP_TAG", "direct")
    monkeypatch.setenv("IS_8K_SMOOTH", "true")
    monkeypatch.setenv("IP_TYPE", "isp")
    node_meta.derive_and_export()
    assert "✈ super" in os.environ["NODE_SUFFIX"]
    assert "✈ isp" in os.environ["NODE_SUFFIX"]


def test_good_wins_over_super_when_proxied(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("DOMAIN", "vpn.example.com")
    monkeypatch.setenv("GEOIP_INFO", "|")
    monkeypatch.setenv("ISP_TAG", "wireguard-hk")
    monkeypatch.setenv("IS_8K_SMOOTH", "true")
    monkeypatch.setenv("IP_TYPE", "isp")
    node_meta.derive_and_export()
    suffix = os.environ["NODE_SUFFIX"]
    assert "✈ good" in suffix
    assert "✈ super" not in suffix


def test_no_8k_no_good_super_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("DOMAIN", "vpn.example.com")
    monkeypatch.setenv("GEOIP_INFO", "|")
    monkeypatch.setenv("ISP_TAG", "socks5-1")
    monkeypatch.setenv("IS_8K_SMOOTH", "false")
    monkeypatch.setenv("IP_TYPE", "hosting")
    node_meta.derive_and_export()
    suffix = os.environ["NODE_SUFFIX"]
    assert "✈ good" not in suffix
    assert "✈ super" not in suffix
    assert "✈ hosting" in suffix
