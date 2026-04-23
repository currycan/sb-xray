"""Tests for Phase D: per-tag URL map + RTT adaptive warmup.

``ISP_SPEED_URL_MAP`` (JSON ``{tag: url}``) lets operators pin a geo-
appropriate probe URL per ISP tag. ``ISP_SPEED_RTT_ADAPTIVE=true``
measures one round-trip before each sample and extends the warmup
window proportionally so high-RTT links clear TCP slow-start.
"""

from __future__ import annotations

import json

import pytest
from sb_xray import speed_test as st

# ---------------------------------------------------------------------------
# URL map resolution
# ---------------------------------------------------------------------------


def test_url_map_returns_tag_specific_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "ISP_SPEED_URL_MAP",
        json.dumps({"proxy-kr-isp": "https://kr-speed.example/100mb"}),
    )
    resolved = st._resolve_tag_probe_url("proxy-kr-isp", "https://default/")
    assert resolved == "https://kr-speed.example/100mb"


def test_url_map_falls_back_to_default_when_tag_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "ISP_SPEED_URL_MAP",
        json.dumps({"proxy-jp-isp": "https://jp-speed.example/"}),
    )
    resolved = st._resolve_tag_probe_url("proxy-la-isp", "https://default/")
    assert resolved == "https://default/"


def test_url_map_falls_back_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ISP_SPEED_URL_MAP", raising=False)
    resolved = st._resolve_tag_probe_url("proxy-la-isp", "https://default/")
    assert resolved == "https://default/"


def test_url_map_falls_back_on_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed JSON must not crash the pipeline — just log + fall back."""
    monkeypatch.setenv("ISP_SPEED_URL_MAP", "{not valid json")
    resolved = st._resolve_tag_probe_url("proxy-la-isp", "https://default/")
    assert resolved == "https://default/"


# ---------------------------------------------------------------------------
# Adaptive warmup
# ---------------------------------------------------------------------------


def test_adaptive_warmup_extends_for_high_rtt() -> None:
    """RTT=200ms → warmup >= 2s (10 * RTT) capped at 5s."""
    result = st._adaptive_warmup_sec(base=1.5, rtt_sec=0.2)
    assert result == pytest.approx(2.0, rel=0.01)


def test_adaptive_warmup_keeps_base_for_low_rtt() -> None:
    """RTT=10ms → 10*RTT=100ms < base, keep base."""
    result = st._adaptive_warmup_sec(base=1.5, rtt_sec=0.01)
    assert result == 1.5


def test_adaptive_warmup_caps_at_5s() -> None:
    """Pathological RTT=1s → 10*RTT=10s, cap at 5s."""
    result = st._adaptive_warmup_sec(base=1.5, rtt_sec=1.0)
    assert result == 5.0


def test_adaptive_warmup_handles_zero_rtt() -> None:
    result = st._adaptive_warmup_sec(base=1.5, rtt_sec=0.0)
    assert result == 1.5
