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


# ---------------------------------------------------------------------------
# _probe_rtt + measure_detailed wiring (opt-in via ISP_SPEED_RTT_ADAPTIVE)
# ---------------------------------------------------------------------------


def test_probe_rtt_returns_elapsed_seconds() -> None:
    class _Resp:
        def close(self) -> None:
            pass

    class _C:
        def head(self, url: str) -> _Resp:
            return _Resp()

    rtt = st._probe_rtt(_C(), "https://x/")  # type: ignore[arg-type]
    assert rtt is not None
    assert rtt >= 0.0


def test_probe_rtt_returns_none_on_httperror() -> None:
    import httpx as _httpx

    class _C:
        def head(self, url: str):
            raise _httpx.ConnectError("down")

    assert st._probe_rtt(_C(), "https://x/") is None  # type: ignore[arg-type]


def test_measure_detailed_respects_rtt_adaptive_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ISP_SPEED_RTT_ADAPTIVE=true extends warmup for high-RTT links."""
    monkeypatch.setenv("ISP_SPEED_RTT_ADAPTIVE", "true")
    monkeypatch.setenv("ISP_SPEED_WARMUP_SEC", "1.5")

    captured: dict[str, float] = {}

    class _C:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def head(self, url):
            class _R:
                def close(self):
                    pass

            return _R()

    def _fake_probe_rtt(_c, _u):
        return 0.3  # 300ms RTT → warmup should extend to max(1.5, 3.0) = 3.0

    def _fake_stream(_c, _u, *, warmup_sec, **_kw):
        captured["warmup_sec"] = warmup_sec
        return st.SampleResult(mbps=50.0, status="ok", bytes_read=10_000_000, window_sec=1.0)

    monkeypatch.setattr(st, "_probe_rtt", _fake_probe_rtt)
    monkeypatch.setattr(st, "_stream_measure", _fake_stream)
    monkeypatch.setattr(st, "_httpx_client", lambda **_: _C())

    st.measure_detailed("https://x/", samples=1)
    assert captured["warmup_sec"] == 3.0


def test_measure_detailed_skips_rtt_probe_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ISP_SPEED_RTT_ADAPTIVE", raising=False)
    monkeypatch.setenv("ISP_SPEED_WARMUP_SEC", "1.5")

    probed = {"called": False}

    class _C:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    def _fake_probe_rtt(_c, _u):
        probed["called"] = True
        return 0.3

    def _fake_stream(*a, warmup_sec=0.0, **kw):
        return st.SampleResult(mbps=50.0, status="ok", bytes_read=10**7, window_sec=1.0)

    monkeypatch.setattr(st, "_probe_rtt", _fake_probe_rtt)
    monkeypatch.setattr(st, "_stream_measure", _fake_stream)
    monkeypatch.setattr(st, "_httpx_client", lambda **_: _C())

    st.measure_detailed("https://x/", samples=1)
    assert probed["called"] is False
