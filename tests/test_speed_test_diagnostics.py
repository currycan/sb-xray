"""Tests for v2 diagnostic schema (``_ISP_SPEEDS_DIAG_JSON`` sibling).

The diagnostic schema exposes per-tag failure classifications without
breaking the ``_ISP_SPEEDS_JSON`` ``{tag: float}`` contract that
``isp_retest._max_delta_pct`` depends on.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sb_xray import speed_test as st

# ---------------------------------------------------------------------------
# measure_detailed() — returns (mbps, diag) in one call
# ---------------------------------------------------------------------------


def test_measure_detailed_returns_mbps_and_diag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ISP_SPEED_LEGACY", raising=False)
    results = iter(
        [
            st.SampleResult(mbps=80.0, status="ok", bytes_read=10_000_000, window_sec=1.0),
            st.SampleResult(mbps=90.0, status="ok", bytes_read=11_000_000, window_sec=1.0),
            st.SampleResult(mbps=100.0, status="ok", bytes_read=12_000_000, window_sec=1.0),
        ]
    )

    class _C:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def stream(self, m, u):
            raise RuntimeError("should not be called")

    monkeypatch.setattr(st, "_stream_measure", lambda *a, **kw: next(results))
    monkeypatch.setattr(st, "_httpx_client", lambda **_: _C())

    mbps, diag = st.measure_detailed("https://x/", samples=3)

    assert pytest.approx(mbps, rel=0.01) == 90.0  # trimmed mean of 80/90/100
    assert diag["status"] == "ok"
    assert diag["ok"] == 3
    assert diag["total"] == 3
    assert diag["bytes"] == 33_000_000
    assert diag["window_sec"] == 3.0


def test_measure_detailed_reports_connect_fail_for_all_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ISP_SPEED_LEGACY", raising=False)

    def _all_fail(*a, **kw):
        return st.SampleResult(mbps=0.0, status="connect_fail", bytes_read=0, window_sec=0.0)

    class _C:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    monkeypatch.setattr(st, "_stream_measure", _all_fail)
    monkeypatch.setattr(st, "_httpx_client", lambda **_: _C())

    mbps, diag = st.measure_detailed("https://x/", samples=3)
    assert mbps == 0.0
    assert diag["status"] == "connect_fail"
    assert diag["ok"] == 0


def test_measure_detailed_mixed_statuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ISP_SPEED_LEGACY", raising=False)
    results = iter(
        [
            st.SampleResult(mbps=80.0, status="ok", bytes_read=10_000_000, window_sec=1.0),
            st.SampleResult(mbps=0.0, status="timeout", bytes_read=0, window_sec=0.0),
            st.SampleResult(mbps=50.0, status="ok", bytes_read=6_000_000, window_sec=1.0),
        ]
    )

    class _C:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    monkeypatch.setattr(st, "_stream_measure", lambda *a, **kw: next(results))
    monkeypatch.setattr(st, "_httpx_client", lambda **_: _C())

    mbps, diag = st.measure_detailed("https://x/", samples=3)
    assert mbps > 0  # at least the ok samples contributed
    assert diag["status"] == "mixed"
    assert diag["ok"] == 2
    assert diag["total"] == 3
    assert "timeout" in diag["statuses"]


# ---------------------------------------------------------------------------
# IspSpeedContext.record() accepts optional diag
# ---------------------------------------------------------------------------


def test_isp_context_record_accepts_diag() -> None:
    ctx = st.IspSpeedContext()
    ctx.record("n1", 50.0, diag={"status": "ok", "ok": 3, "total": 3})
    assert ctx.speeds == {"n1": 50.0}
    assert ctx.diag == {"n1": {"status": "ok", "ok": 3, "total": 3}}


def test_isp_context_record_diag_optional() -> None:
    """Existing callers that pass no diag continue to work."""
    ctx = st.IspSpeedContext()
    ctx.record("n1", 50.0)
    assert ctx.diag == {}  # no diag registered


# ---------------------------------------------------------------------------
# _persist_routing_decision writes _ISP_SPEEDS_DIAG_JSON to STATUS_FILE
# ---------------------------------------------------------------------------


def test_persist_writes_diag_json_when_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("STATUS_FILE", str(tmp_path / "status"))
    monkeypatch.delenv("ISP_SPEED_DIAG_ENABLED", raising=False)

    # Fake routing decision so we don't touch the full xray pipeline.
    from sb_xray.routing import isp as isp_mod

    monkeypatch.setattr(
        isp_mod,
        "apply_isp_routing_logic",
        lambda ctx: isp_mod.IspDecision(isp_tag="proxy-la-isp", is_8k_smooth=False),
    )

    ctx = st.IspSpeedContext()
    ctx.record("proxy-la-isp", 21.0, diag={"status": "ok", "ok": 3, "total": 3})
    ctx.record(
        "proxy-kr-isp",
        0.0,
        diag={"status": "connect_fail", "ok": 0, "total": 3},
    )

    monkeypatch.setattr(st, "emit_event", MagicMock(), raising=False)
    from sb_xray import events as _events

    monkeypatch.setattr(_events, "emit_event", MagicMock())

    st._persist_routing_decision(100.0, ctx)

    status_raw = (tmp_path / "status").read_text(encoding="utf-8")
    assert "_ISP_SPEEDS_DIAG_JSON=" in status_raw
    # Extract and parse the JSON diag line
    diag_line = [line for line in status_raw.splitlines() if "_ISP_SPEEDS_DIAG_JSON=" in line][0]
    raw = diag_line.split("=", 1)[1].strip().strip("'\"")
    parsed = json.loads(raw)
    assert parsed["proxy-la-isp"]["status"] == "ok"
    assert parsed["proxy-kr-isp"]["status"] == "connect_fail"


def test_persist_omits_diag_json_when_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("STATUS_FILE", str(tmp_path / "status"))
    monkeypatch.setenv("ISP_SPEED_DIAG_ENABLED", "false")

    from sb_xray.routing import isp as isp_mod

    monkeypatch.setattr(
        isp_mod,
        "apply_isp_routing_logic",
        lambda ctx: isp_mod.IspDecision(isp_tag="direct", is_8k_smooth=True),
    )

    ctx = st.IspSpeedContext()
    ctx.record("proxy-la-isp", 21.0, diag={"status": "ok"})

    from sb_xray import events as _events

    monkeypatch.setattr(_events, "emit_event", MagicMock())

    st._persist_routing_decision(100.0, ctx)

    status_raw = (tmp_path / "status").read_text(encoding="utf-8")
    assert "_ISP_SPEEDS_DIAG_JSON=" not in status_raw


# ---------------------------------------------------------------------------
# emit_event payload gains 'diag'
# ---------------------------------------------------------------------------


def test_event_payload_includes_diag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STATUS_FILE", str(tmp_path / "status"))

    from sb_xray.routing import isp as isp_mod

    monkeypatch.setattr(
        isp_mod,
        "apply_isp_routing_logic",
        lambda c: isp_mod.IspDecision(isp_tag="proxy-la-isp", is_8k_smooth=False),
    )

    captured = {}

    def _cap(name, payload):
        captured["name"] = name
        captured["payload"] = payload

    from sb_xray import events as _events

    monkeypatch.setattr(_events, "emit_event", _cap)

    ctx = st.IspSpeedContext()
    ctx.record("proxy-la-isp", 21.0, diag={"status": "ok", "ok": 3, "total": 3})
    st._persist_routing_decision(100.0, ctx)

    assert captured["name"] == "isp.speed_test.result"
    assert "diag" in captured["payload"]
    assert captured["payload"]["diag"]["proxy-la-isp"]["status"] == "ok"


# ---------------------------------------------------------------------------
# Contract: isp_retest still parses _ISP_SPEEDS_JSON unchanged
# ---------------------------------------------------------------------------


def test_contract_isp_retest_load_roundtrip_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_ISP_SPEEDS_JSON schema stays {tag: float}; diag lives separately.

    Fuzz 200 random speed dicts; round-trip must be byte-identical so
    stages/isp_retest.py._load_previous_speeds sees the same values.
    """
    rng = random.Random(42)
    for _ in range(200):
        size = rng.randint(0, 10)
        speeds = {
            f"proxy-{rng.choice(['us', 'kr', 'la', 'jp'])}-{i}-isp": rng.uniform(0, 500)
            for i in range(size)
        }
        encoded = st._json_speeds(speeds)
        parsed = json.loads(encoded)

        # Keys preserved
        assert set(parsed.keys()) == set(speeds.keys())
        # Values agree to rounded 2 decimals (what _json_speeds does)
        for k, v in speeds.items():
            assert abs(parsed[k] - round(v, 2)) < 0.01


def test_load_isp_speeds_ignores_diag_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """load_isp_speeds() only reads _ISP_SPEEDS_JSON, not _ISP_SPEEDS_DIAG_JSON."""
    monkeypatch.setenv("_ISP_SPEEDS_JSON", json.dumps({"proxy-la-isp": 21.0}))
    monkeypatch.setenv(
        "_ISP_SPEEDS_DIAG_JSON",
        json.dumps({"proxy-la-isp": {"status": "ok"}}),
    )
    speeds = st.load_isp_speeds()
    assert speeds == {"proxy-la-isp": 21.0}
    # No {'status': ...} leakage
    for v in speeds.values():
        assert isinstance(v, float)
