"""Tests for sb_xray.routing.isp.build_sb_urltest_set (Phase 4)."""

from __future__ import annotations

import json
import os

import pytest
from sb_xray.routing import isp
from sb_xray.routing.service_spec import SERVICE_SPECS


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear env used by build_client_and_server_configs so tests don't bleed."""
    for key in list(os.environ):
        if key.endswith(("_ISP_IP", "_ISP_PORT", "_ISP_USER", "_ISP_SECRET")):
            monkeypatch.delenv(key, raising=False)
    for key in (
        "HAS_ISP_NODES",
        "FASTEST_PROXY_TAG",
        "ISP_PER_SERVICE_SB",
        "ISP_PROBE_URL",
        "ISP_PROBE_INTERVAL",
        "ISP_PROBE_TOLERANCE_MS",
        "CHATGPT_OUT",
        "NETFLIX_OUT",
        "DISNEY_OUT",
        "CLAUDE_OUT",
        "GEMINI_OUT",
        "YOUTUBE_OUT",
        "SOCIAL_MEDIA_OUT",
        "TIKTOK_OUT",
        "ISP_OUT",
        "ISP_IP",
        "ISP_PORT",
        "ISP_USER",
        "ISP_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)


def _parse_fragments(out: str) -> list[dict]:
    """Split the trailing-comma-joined JSON fragments back into dicts."""
    assert out.endswith(",")
    body = out.rstrip(",")
    return [json.loads(chunk) for chunk in _iter_json_objects(body)]


def _iter_json_objects(s: str) -> list[str]:
    """Walk balanced braces; yields each top-level JSON object."""
    depth = 0
    start = 0
    out: list[str] = []
    for i, ch in enumerate(s):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                out.append(s[start : i + 1])
    return out


def test_empty_speeds_returns_empty_string() -> None:
    assert isp.build_sb_urltest_set({}) == ""


def test_emits_legacy_plus_per_service_tags() -> None:
    speeds = {"proxy-hk": 120.0, "proxy-cn2": 80.0}
    out = isp.build_sb_urltest_set(speeds)
    fragments = _parse_fragments(out)
    tags = [f["tag"] for f in fragments]
    # Legacy first, then one per service.
    assert tags[0] == "isp-auto"
    per_service_tags = {spec.sb_tag for spec in SERVICE_SPECS}
    assert per_service_tags.issubset(set(tags[1:]))
    assert len(fragments) == 1 + len(SERVICE_SPECS)


def test_each_fragment_has_service_probe_url() -> None:
    speeds = {"proxy-hk": 120.0}
    out = isp.build_sb_urltest_set(speeds)
    fragments = _parse_fragments(out)
    by_tag = {f["tag"]: f for f in fragments}
    for spec in SERVICE_SPECS:
        frag = by_tag[spec.sb_tag]
        assert frag["url"] == spec.probe_url
        assert frag["outbounds"][0] == "proxy-hk"
        assert frag["outbounds"][-1] == "direct"


def test_legacy_fragment_uses_configured_probe_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ISP_PROBE_URL", "https://custom.test/probe")
    out = isp.build_sb_urltest_set({"proxy-hk": 120.0})
    fragments = _parse_fragments(out)
    assert fragments[0]["url"] == "https://custom.test/probe"


def test_trailing_comma_contract() -> None:
    out = isp.build_sb_urltest_set({"proxy-hk": 120.0})
    assert out.endswith(",")
    # Valid JSON array when wrapped.
    arr = json.loads("[" + out.rstrip(",") + "]")
    assert len(arr) == 1 + len(SERVICE_SPECS)


def test_flag_off_uses_legacy_single_fragment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ISP_PER_SERVICE_SB", raising=False)
    monkeypatch.setenv("HAS_ISP_NODES", "true")
    # Seed one ISP node so build_client_and_server_configs emits outbounds.
    monkeypatch.setenv("HK_ISP_IP", "1.1.1.1")
    monkeypatch.setenv("HK_ISP_PORT", "1080")
    monkeypatch.setenv("HK_ISP_USER", "u")
    monkeypatch.setenv("HK_ISP_SECRET", "p")
    monkeypatch.setenv("FASTEST_PROXY_TAG", "proxy-hk-isp")
    out = isp.build_client_and_server_configs(speeds={"proxy-hk-isp": 80.0})
    urltest = out["SB_ISP_URLTEST"]
    # Exactly one urltest outbound tagged isp-auto.
    assert urltest.count('"type": "urltest"') == 1
    assert '"tag": "isp-auto"' in urltest
    assert '"isp-auto-netflix"' not in urltest


def test_flag_on_switches_to_per_service_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ISP_PER_SERVICE_SB", "true")
    monkeypatch.setenv("HAS_ISP_NODES", "true")
    monkeypatch.setenv("HK_ISP_IP", "1.1.1.1")
    monkeypatch.setenv("HK_ISP_PORT", "1080")
    monkeypatch.setenv("HK_ISP_USER", "u")
    monkeypatch.setenv("HK_ISP_SECRET", "p")
    monkeypatch.setenv("FASTEST_PROXY_TAG", "proxy-hk-isp")
    out = isp.build_client_and_server_configs(speeds={"proxy-hk-isp": 80.0})
    urltest = out["SB_ISP_URLTEST"]
    assert urltest.count('"type": "urltest"') == 1 + len(SERVICE_SPECS)
    for spec in SERVICE_SPECS:
        assert spec.sb_tag in urltest
