"""Tests for sb_xray.routing.isp.build_client_and_server_configs (§ Stage 4)."""

from __future__ import annotations

import json
import os

import pytest
from sb_xray.routing import isp as sbisp


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.endswith(
            (
                "_ISP_IP",
                "_ISP_PORT",
                "_ISP_USER",
                "_ISP_SECRET",
            )
        ):
            monkeypatch.delenv(key, raising=False)
    for key in (
        "HAS_ISP_NODES",
        "FASTEST_PROXY_TAG",
        "CUSTOM_OUTBOUNDS",
        "SB_CUSTOM_OUTBOUNDS",
        "SB_ISP_URLTEST",
        "XRAY_OBSERVATORY_SECTION",
        "XRAY_BALANCERS_SECTION",
        "XRAY_SERVICE_RULES",
        "ISP_IP",
        "ISP_PORT",
        "ISP_USER",
        "ISP_SECRET",
        "CHATGPT_OUT",
        "NETFLIX_OUT",
        "DISNEY_OUT",
        "CLAUDE_OUT",
        "GEMINI_OUT",
        "YOUTUBE_OUT",
        "SOCIAL_MEDIA_OUT",
        "TIKTOK_OUT",
        "ISP_OUT",
        "_ISP_SPEEDS_JSON",
    ):
        monkeypatch.delenv(key, raising=False)


def test_empty_when_no_isp_nodes() -> None:
    out = sbisp.build_client_and_server_configs(speeds={})
    assert out["CUSTOM_OUTBOUNDS"] == ""
    assert out["SB_CUSTOM_OUTBOUNDS"] == ""
    assert out["SB_ISP_URLTEST"] == ""
    assert out["XRAY_OBSERVATORY_SECTION"] == ""
    assert out["XRAY_BALANCERS_SECTION"] == ""
    # service rules always set (media fall back to `direct`).
    assert "direct" in out["XRAY_SERVICE_RULES"]


def test_sorts_by_speed_and_sets_fastest_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAS_ISP_NODES", "true")
    monkeypatch.setenv("FASTEST_PROXY_TAG", "proxy-hk-isp")
    monkeypatch.setenv("CN2_ISP_IP", "1.1.1.1")
    monkeypatch.setenv("CN2_ISP_PORT", "1080")
    monkeypatch.setenv("CN2_ISP_USER", "u1")
    monkeypatch.setenv("CN2_ISP_SECRET", "p1")
    monkeypatch.setenv("HK_ISP_IP", "2.2.2.2")
    monkeypatch.setenv("HK_ISP_PORT", "1081")
    monkeypatch.setenv("HK_ISP_USER", "u2")
    monkeypatch.setenv("HK_ISP_SECRET", "p2")

    speeds = {"proxy-cn2-isp": 80.0, "proxy-hk-isp": 120.0}
    out = sbisp.build_client_and_server_configs(speeds=speeds)

    # HK first (fastest).
    assert "proxy-hk-isp" in out["CUSTOM_OUTBOUNDS"].splitlines()[0]
    # both entries present.
    assert "proxy-cn2-isp" in out["CUSTOM_OUTBOUNDS"]

    assert os.environ["ISP_IP"] == "2.2.2.2"
    assert os.environ["ISP_PORT"] == "1081"
    assert os.environ["ISP_USER"] == "u2"
    assert os.environ["ISP_SECRET"] == "p2"

    urltest = json.loads(out["SB_ISP_URLTEST"])
    assert urltest["type"] == "urltest"
    assert urltest["outbounds"][0] == "proxy-hk-isp"


def test_service_rules_use_env_or_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NETFLIX_OUT", "proxy-hk-isp")
    monkeypatch.setenv("CHATGPT_OUT", "isp-auto")
    out = sbisp.build_client_and_server_configs(speeds={})
    rules = out["XRAY_SERVICE_RULES"]
    assert '"outboundTag": "proxy-hk-isp"' in rules
    assert '"balancerTag": "isp-auto"' in rules
