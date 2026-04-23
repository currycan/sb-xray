"""Tests for sb_xray.speed_test.run_isp_speed_tests (§ Stage 2 orchestration)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sb_xray import speed_test as sbspeed


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STATUS_FILE", str(tmp_path / "status"))
    # Purge any globals that leak between tests.
    for key in (
        "ISP_TAG",
        "IS_8K_SMOOTH",
        "DIRECT_SPEED",
        "HAS_ISP_NODES",
        "FASTEST_PROXY_TAG",
        "proxy_max_speed",
        "_ISP_SPEEDS_JSON",
        "DEFAULT_ISP",
        "IP_TYPE",
        "GEOIP_INFO",
    ):
        monkeypatch.delenv(key, raising=False)
    # Drop any *_ISP_IP env left over
    for key in [k for k in os.environ if k.endswith("_ISP_IP")]:
        monkeypatch.delenv(key, raising=False)


def test_cache_hit_rebuilds_state_and_skips_measure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ISP_TAG", "proxy-cn2-isp")
    monkeypatch.setenv("CN2_ISP_IP", "1.2.3.4")
    monkeypatch.setenv("CN2_ISP_PORT", "1080")

    def must_not_call(*args: object, **kwargs: object) -> object:
        raise AssertionError("measure must not run on ISP_TAG cache hit")

    monkeypatch.setattr(sbspeed, "measure", must_not_call)
    monkeypatch.setattr(sbspeed, "measure_detailed", must_not_call)
    sbspeed.run_isp_speed_tests()

    assert os.environ["HAS_ISP_NODES"] == "true"
    speeds = sbspeed.load_isp_speeds()
    assert "proxy-cn2-isp" in speeds
    # Cached winner gets the sentinel 999 so downstream sort surfaces it first.
    assert speeds["proxy-cn2-isp"] == 999.0


def test_cache_hit_invalidated_when_node_missing_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: operator removes US_ISP_* from SECRET_FILE but
    STATUS_FILE still has ISP_TAG=proxy-us-isp. Cache-hit path must
    detect the stale reference and fall through to a fresh measure;
    otherwise xray later crashes with 'outbound tag proxy-us-isp not
    found' because build_client_and_server_configs doesn't emit an
    outbound the env doesn't back."""
    monkeypatch.setenv("ISP_TAG", "proxy-us-isp")  # cached, but US_ISP_IP absent
    monkeypatch.setenv("CN2_ISP_IP", "1.2.3.4")
    monkeypatch.setenv("CN2_ISP_PORT", "1080")
    monkeypatch.setenv("IP_TYPE", "hosting")

    measured: list[str | None] = []

    def _fake_measure(url, *, samples=1, proxy=None, proxy_auth=None, timeout=5.0, name=None):
        measured.append(proxy)
        return 50.0 if proxy else 10.0

    def _fake_measure_detailed(
        url, *, samples=1, proxy=None, proxy_auth=None, timeout=5.0, name=None
    ):
        measured.append(proxy)
        return (50.0 if proxy else 10.0), {"status": "ok", "ok": samples, "total": samples}

    monkeypatch.setattr(sbspeed, "measure", _fake_measure)
    monkeypatch.setattr(sbspeed, "measure_detailed", _fake_measure_detailed)
    monkeypatch.setattr(sbspeed, "show_report", lambda *a, **kw: None)
    sbspeed.run_isp_speed_tests(samples=1)

    # Fell through → direct baseline + CN2 proxy both measured.
    assert measured, "must have run measure() after cache invalidation"
    # Fresh ISP_TAG selection — must NOT still be the stale proxy-us-isp.
    assert os.environ["ISP_TAG"] != "proxy-us-isp"


def test_no_isp_env_falls_back_to_direct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_measure(url, **kwargs):
        calls.append((url, kwargs))
        return 42.0

    monkeypatch.setattr(sbspeed, "measure", fake_measure)
    monkeypatch.setattr(sbspeed, "show_report", lambda *a, **kw: None)
    sbspeed.run_isp_speed_tests(samples=1)

    # One direct-baseline measurement, no proxy call.
    assert len(calls) == 1
    assert calls[0][1].get("proxy") is None
    assert os.environ["ISP_TAG"] == "direct"
    assert os.environ["DIRECT_SPEED"] == "42.00"


def test_isp_nodes_tested_and_fastest_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CN2_ISP_IP", "1.1.1.1")
    monkeypatch.setenv("CN2_ISP_PORT", "1080")
    monkeypatch.setenv("CN2_ISP_USER", "u1")
    monkeypatch.setenv("CN2_ISP_SECRET", "p1")
    monkeypatch.setenv("HK_ISP_IP", "2.2.2.2")
    monkeypatch.setenv("HK_ISP_PORT", "1080")
    monkeypatch.setenv("IP_TYPE", "hosting")  # triggers proxy path

    speed_map = {
        None: 10.0,  # direct baseline
        "socks5h://1.1.1.1:1080": 80.0,
        "socks5h://2.2.2.2:1080": 120.0,
    }

    def fake_measure(url, *, samples=1, proxy=None, proxy_auth=None, timeout=5.0, name=None):
        return speed_map[proxy]

    def fake_measure_detailed(
        url, *, samples=1, proxy=None, proxy_auth=None, timeout=5.0, name=None
    ):
        return speed_map[proxy], {"status": "ok", "ok": samples, "total": samples}

    monkeypatch.setattr(sbspeed, "measure", fake_measure)
    monkeypatch.setattr(sbspeed, "measure_detailed", fake_measure_detailed)
    monkeypatch.setattr(sbspeed, "show_report", lambda *a, **kw: None)
    sbspeed.run_isp_speed_tests(samples=1)

    assert os.environ["FASTEST_PROXY_TAG"] == "proxy-hk-isp"
    assert os.environ["ISP_TAG"] == "proxy-hk-isp"
    assert os.environ["IS_8K_SMOOTH"] == "true"
    assert os.environ["HAS_ISP_NODES"] == "true"
    speeds = sbspeed.load_isp_speeds()
    assert speeds["proxy-hk-isp"] == 120.0
    assert speeds["proxy-cn2-isp"] == 80.0


def test_status_file_is_written(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sbspeed, "measure", lambda *a, **kw: 5.0)
    monkeypatch.setattr(
        sbspeed,
        "measure_detailed",
        lambda *a, **kw: (5.0, {"status": "ok", "ok": 1, "total": 1}),
    )
    monkeypatch.setattr(sbspeed, "show_report", lambda *a, **kw: None)
    sbspeed.run_isp_speed_tests(samples=1)
    status = (tmp_path / "status").read_text(encoding="utf-8")
    assert "export ISP_TAG='direct'" in status
    assert "export IS_8K_SMOOTH='false'" in status
