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
    monkeypatch.setattr(speed_test, "_measure_direct_baseline", measure)
    monkeypatch.setattr(speed_test, "_measure_isp_nodes", MagicMock())

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

    measure = MagicMock(return_value=50.0)
    monkeypatch.setattr(speed_test, "_measure_direct_baseline", measure)
    monkeypatch.setattr(
        speed_test,
        "_measure_isp_nodes",
        MagicMock(return_value=speed_test.IspSpeedContext()),
    )

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
