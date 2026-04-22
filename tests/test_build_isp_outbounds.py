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

    # SB_ISP_URLTEST ends with a trailing comma for template splice;
    # strip it before re-parsing.
    assert out["SB_ISP_URLTEST"].endswith(",")
    urltest = json.loads(out["SB_ISP_URLTEST"].rstrip(","))
    assert urltest["type"] == "urltest"
    assert urltest["outbounds"][0] == "proxy-hk-isp"


def test_sb_json_template_round_trips_to_valid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end regression: splice ``SB_CUSTOM_OUTBOUNDS`` +
    ``SB_ISP_URLTEST`` into the exact sb.json outbounds skeleton and
    confirm ``json.loads`` accepts it. Catches the line-44 ',' delimiter
    crash observed on prod when either fragment drops its trailing comma."""
    import json
    from string import Template

    monkeypatch.setenv("HAS_ISP_NODES", "true")
    monkeypatch.setenv("FASTEST_PROXY_TAG", "proxy-hk-isp")
    monkeypatch.setenv("HK_ISP_IP", "1.1.1.1")
    monkeypatch.setenv("HK_ISP_PORT", "1081")
    monkeypatch.setenv("HK_ISP_USER", "u")
    monkeypatch.setenv("HK_ISP_SECRET", "p")
    monkeypatch.setenv("CN2_ISP_IP", "2.2.2.2")
    monkeypatch.setenv("CN2_ISP_PORT", "1080")
    monkeypatch.setenv("CN2_ISP_USER", "u")
    monkeypatch.setenv("CN2_ISP_SECRET", "p")

    env = sbisp.build_client_and_server_configs(
        speeds={"proxy-hk-isp": 120.0, "proxy-cn2-isp": 80.0}
    )

    # Minimal "outbounds" skeleton matching templates/sing-box/sb.json
    # lines 35-46 verbatim (array with direct/block sentinels + 2 splice
    # points).
    skeleton = Template(
        '{"outbounds":['
        '{"type":"direct","tag":"direct"},'
        "${SB_CUSTOM_OUTBOUNDS}"
        "${SB_ISP_URLTEST}"
        '{"type":"block","tag":"block"}'
        "]}"
    )
    rendered = skeleton.substitute(
        SB_CUSTOM_OUTBOUNDS=env["SB_CUSTOM_OUTBOUNDS"],
        SB_ISP_URLTEST=env["SB_ISP_URLTEST"],
    )
    # MUST be valid JSON — this was broken on prod.
    doc = json.loads(rendered)
    tags = [o["tag"] for o in doc["outbounds"]]
    assert tags == ["direct", "proxy-hk-isp", "proxy-cn2-isp", "isp-auto", "block"]


def test_service_rules_use_env_or_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NETFLIX_OUT", "proxy-hk-isp")
    monkeypatch.setenv("CHATGPT_OUT", "isp-auto")
    out = sbisp.build_client_and_server_configs(speeds={})
    rules = out["XRAY_SERVICE_RULES"]
    assert '"outboundTag": "proxy-hk-isp"' in rules
    assert '"balancerTag": "isp-auto"' in rules
