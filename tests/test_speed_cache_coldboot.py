"""Tests for Phase 5 cold-boot speed cache."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sb_xray import speed_test


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in (
        "STATUS_FILE",
        "ISP_SPEED_CACHE_TTL_MIN",
        "ISP_SPEED_CACHE_ASYNC",
        "ISP_TAG",
        "HAS_ISP_NODES",
        "_ISP_SPEEDS_JSON",
        "IS_8K_SMOOTH",
    ):
        monkeypatch.delenv(k, raising=False)


def _write_status(
    path: Path,
    *,
    ts: int,
    speeds: dict[str, float],
    isp_tag: str = "proxy-cn2",
    is_8k: str = "true",
) -> None:
    lines = [
        f"export ISP_LAST_RETEST_TS='{ts}'",
        f"export _ISP_SPEEDS_JSON='{json.dumps(speeds)}'",
        f"export ISP_TAG='{isp_tag}'",
        f"export IS_8K_SMOOTH='{is_8k}'",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_cache_hit_skips_live_measurement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    status = tmp_path / "status"
    _write_status(status, ts=int(time.time()), speeds={"proxy-cn2": 100.0})
    monkeypatch.setenv("STATUS_FILE", str(status))
    monkeypatch.setenv("ISP_SPEED_CACHE_ASYNC", "false")

    measure = MagicMock()
    monkeypatch.setattr(speed_test, "measure_isp_speeds", measure)

    speed_test.run_isp_speed_tests()

    measure.assert_not_called()
    assert json.loads(__import__("os").environ["_ISP_SPEEDS_JSON"]) == {"proxy-cn2": 100.0}


def test_cache_miss_when_no_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STATUS_FILE", str(tmp_path / "status"))
    assert speed_test._try_speed_cache_hit() is False


def test_cache_stale_beyond_ttl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    status = tmp_path / "status"
    # 2 hours ago, default TTL is 60 min.
    _write_status(status, ts=int(time.time()) - 7200, speeds={"proxy-cn2": 100.0})
    monkeypatch.setenv("STATUS_FILE", str(status))
    monkeypatch.setenv("ISP_SPEED_CACHE_TTL_MIN", "60")
    assert speed_test._try_speed_cache_hit() is False


def test_cache_ttl_zero_disables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    status = tmp_path / "status"
    _write_status(status, ts=int(time.time()), speeds={"proxy-cn2": 100.0})
    monkeypatch.setenv("STATUS_FILE", str(status))
    monkeypatch.setenv("ISP_SPEED_CACHE_TTL_MIN", "0")
    assert speed_test._try_speed_cache_hit() is False


def test_cache_rejects_invalid_speeds_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    status = tmp_path / "status"
    status.write_text(
        "\n".join(
            [
                f"export ISP_LAST_RETEST_TS='{int(time.time())}'",
                "export _ISP_SPEEDS_JSON='not json'",
                "export ISP_TAG='proxy-cn2'",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("STATUS_FILE", str(status))
    assert speed_test._try_speed_cache_hit() is False


def test_force_bypasses_cache_even_when_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    status = tmp_path / "status"
    _write_status(status, ts=int(time.time()), speeds={"proxy-cn2": 100.0})
    monkeypatch.setenv("STATUS_FILE", str(status))
    monkeypatch.setenv("ISP_SPEED_CACHE_ASYNC", "false")

    fake = speed_test.SpeedOutcome(
        speeds={},
        diag=None,
        direct_mbps=50.0,
        fastest_tag=None,
        fastest_speed=0.0,
        isp_tag="direct",
        is_8k_smooth=False,
        has_isp_nodes=False,
        notify=False,
    )
    measure = MagicMock(return_value=fake)
    monkeypatch.setattr(speed_test, "measure_isp_speeds", measure)

    speed_test.run_isp_speed_tests(force=True)

    measure.assert_called_once()


def test_async_refresh_starts_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """The daemon thread fires when ISP_SPEED_CACHE_ASYNC is not 'false'."""
    started: list[str] = []

    class _StubThread:
        def __init__(self, target, name, daemon) -> None:  # type: ignore[no-untyped-def]
            self.target = target
            self.name = name
            self.daemon = daemon

        def start(self) -> None:
            started.append(self.name)

    import threading

    monkeypatch.setattr(threading, "Thread", _StubThread)
    monkeypatch.delenv("ISP_SPEED_CACHE_ASYNC", raising=False)
    speed_test._spawn_async_refresh()
    assert started == ["isp-speed-refresh"]


def test_coldboot_cachehit_then_async_keeps_isp_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pin the race: after cache-hit sets HAS_ISP_NODES, an async refresh running
    before media routing reads it must NOT clear it — ISP_OUT stays isp-auto."""
    import os

    from sb_xray import speed_test as st

    status = tmp_path / "status"
    monkeypatch.setenv("STATUS_FILE", str(status))
    monkeypatch.setenv("IP_TYPE", "hosting")
    ts = int(time.time())
    status.write_text(
        f"export ISP_LAST_RETEST_TS='{ts}'\n"
        'export _ISP_SPEEDS_JSON=\'{"proxy-us-isp": 29.6}\'\n'
        "export ISP_TAG='proxy-us-isp'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ISP_SPEED_CACHE_ASYNC", "false")  # drive async timing manually

    # Main-thread cold boot: cache-hit must set HAS_ISP_NODES.
    st.run_isp_speed_tests()
    assert os.environ["HAS_ISP_NODES"] == "true"

    # Worst-case interleave: async refresh completes before media routing reads.
    fake = st.SpeedOutcome(
        speeds={"proxy-us-isp": 31.0},
        diag=None,
        direct_mbps=40.0,
        fastest_tag="proxy-us-isp",
        fastest_speed=31.0,
        isp_tag="proxy-us-isp",
        is_8k_smooth=False,
        has_isp_nodes=True,
        notify=False,
    )
    monkeypatch.setattr(st, "measure_isp_speeds", lambda url, sample_count: fake)
    st._async_refresh_once(url="http://x/", sample_count=1)

    # Main thread derives ISP_OUT (same expression as entrypoint.py) — still isp-auto.
    isp_out = "isp-auto" if os.environ.get("HAS_ISP_NODES") else "direct"
    assert isp_out == "isp-auto"

    # Media probes route through ISP too.
    from sb_xray.network import get_fallback_proxy

    assert get_fallback_proxy() == "isp-auto"
