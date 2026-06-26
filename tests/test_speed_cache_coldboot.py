"""Tests for Phase 5 cold-boot speed cache."""

from __future__ import annotations

import json
import os
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
    # Drop any *_ISP_IP left over so node-backed cache validation is deterministic.
    for key in [k for k in os.environ if k.endswith("_ISP_IP")]:
        monkeypatch.delenv(key, raising=False)


def _back_nodes(monkeypatch: pytest.MonkeyPatch, *prefixes: str) -> None:
    """Register live ISP nodes in env so their proxy-<slug> tags are 'backed'.

    ``_back_nodes(monkeypatch, "CN2")`` sets ``CN2_ISP_IP`` / ``CN2_ISP_PORT``,
    which discovery resolves to the tag ``proxy-cn2-isp``.
    """
    for i, prefix in enumerate(prefixes):
        monkeypatch.setenv(f"{prefix}_ISP_IP", f"10.0.0.{i + 1}")
        monkeypatch.setenv(f"{prefix}_ISP_PORT", "1080")


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
    _write_status(
        status, ts=int(time.time()), speeds={"proxy-cn2-isp": 100.0}, isp_tag="proxy-cn2-isp"
    )
    monkeypatch.setenv("STATUS_FILE", str(status))
    monkeypatch.setenv("ISP_SPEED_CACHE_ASYNC", "false")
    _back_nodes(monkeypatch, "CN2")

    measure = MagicMock()
    monkeypatch.setattr(speed_test, "measure_isp_speeds", measure)

    speed_test.run_isp_speed_tests()

    measure.assert_not_called()
    assert json.loads(os.environ["_ISP_SPEEDS_JSON"]) == {"proxy-cn2-isp": 100.0}


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
    _back_nodes(monkeypatch, "US")  # proxy-us-isp must be a live node for the cache to hold

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


def test_speed_cache_hit_rejects_stale_isp_tags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: STATUS_FILE caches a node (proxy-us-isp) whose *_ISP_IP was
    dropped from SECRET_FILE. The TTL cache path must reject the cache and fall
    through to a live measure — otherwise the stale tag reaches the isp-auto
    urltest / xray balancer with no matching outbound and the engine crashes
    with 'dependency proxy-us-isp not found for outbound isp-auto'."""
    status = tmp_path / "status"
    _write_status(
        status,
        ts=int(time.time()),
        speeds={"proxy-us-isp": 30.0, "proxy-cn2-isp": 80.0},
        isp_tag="proxy-cn2-isp",
    )
    monkeypatch.setenv("STATUS_FILE", str(status))
    monkeypatch.setenv("ISP_SPEED_CACHE_ASYNC", "false")
    _back_nodes(monkeypatch, "CN2")  # US_ISP removed → proxy-us-isp is now stale

    assert speed_test._try_speed_cache_hit() is False
    # Rejected before mutating env — no stale speeds leak downstream.
    assert "_ISP_SPEEDS_JSON" not in os.environ


def test_speed_cache_hit_accepts_when_all_tags_backed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The node check must not over-reject: a cache whose every tag still has a
    backing node stays a hit."""
    status = tmp_path / "status"
    _write_status(
        status,
        ts=int(time.time()),
        speeds={"proxy-cn2-isp": 80.0, "proxy-hk-isp": 120.0},
        isp_tag="proxy-hk-isp",
    )
    monkeypatch.setenv("STATUS_FILE", str(status))
    monkeypatch.setenv("ISP_SPEED_CACHE_ASYNC", "false")
    _back_nodes(monkeypatch, "CN2", "HK")

    assert speed_test._try_speed_cache_hit() is True
    assert json.loads(os.environ["_ISP_SPEEDS_JSON"]) == {
        "proxy-cn2-isp": 80.0,
        "proxy-hk-isp": 120.0,
    }


def test_speed_cache_hit_rejects_non_dict_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-object _ISP_SPEEDS_JSON (e.g. a JSON array) is treated as a miss."""
    status = tmp_path / "status"
    status.write_text(
        "\n".join(
            [
                f"export ISP_LAST_RETEST_TS='{int(time.time())}'",
                "export _ISP_SPEEDS_JSON='[1, 2, 3]'",
                "export ISP_TAG='proxy-cn2-isp'",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("STATUS_FILE", str(status))
    _back_nodes(monkeypatch, "CN2")
    assert speed_test._try_speed_cache_hit() is False


def test_current_isp_tags_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    """_current_isp_tags derives proxy-<slug> from each *_ISP_IP prefix."""
    _back_nodes(monkeypatch, "CN2", "HK")
    assert speed_test._current_isp_tags() == {"proxy-cn2-isp", "proxy-hk-isp"}


# ---------------------------------------------------------------------------
# C2: run_isp_speed_tests_budgeted — wall-clock budget cap + last-known fallback
# ---------------------------------------------------------------------------

from sb_xray import speed_test as st  # noqa: E402 — module alias for budgeted tests


def test_budgeted_falls_back_to_last_known_on_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C2: when a cold-cache measurement overruns the wall-clock budget, the
    budgeted wrapper stops waiting, loads last-known ISP_TAG from STATUS_FILE,
    and returns None — boot is never blocked for the full measurement."""
    status = tmp_path / "status"
    status.write_text(
        "export ISP_TAG='proxy-kr-isp'\n"
        "export _ISP_SPEEDS_JSON='{\"proxy-kr-isp\": 88.0}'\n"
        "export IS_8K_SMOOTH='true'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("STATUS_FILE", str(status))
    monkeypatch.setenv("ISP_SPEED_BOOT_BUDGET_SEC", "1")
    monkeypatch.delenv("ISP_TAG", raising=False)
    monkeypatch.delenv("_ISP_SPEEDS_JSON", raising=False)

    def _slow(**_kw: object) -> None:
        time.sleep(5.0)  # far exceeds the 1s budget
        return None

    monkeypatch.setattr(st, "run_isp_speed_tests", _slow)

    t0 = time.monotonic()
    out = st.run_isp_speed_tests_budgeted()
    elapsed = time.monotonic() - t0

    assert out is None
    assert elapsed < 3.0  # returned well before the 5s sleep finished
    assert os.environ["ISP_TAG"] == "proxy-kr-isp"
    assert os.environ["_ISP_SPEEDS_JSON"] == '{"proxy-kr-isp": 88.0}'
    assert os.environ["IS_8K_SMOOTH"] == "true"


def test_budgeted_returns_real_outcome_within_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fast path: when the measurement completes inside the budget the real
    outcome is returned and no fallback happens."""
    monkeypatch.setenv("STATUS_FILE", str(tmp_path / "status"))
    monkeypatch.setenv("ISP_SPEED_BOOT_BUDGET_SEC", "10")
    sentinel = object()

    def _fast(**_kw: object) -> object:
        return sentinel

    monkeypatch.setattr(st, "run_isp_speed_tests", _fast)
    assert st.run_isp_speed_tests_budgeted() is sentinel


def test_budgeted_zero_budget_is_synchronous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ISP_SPEED_BOOT_BUDGET_SEC=0 disables the budget (legacy synchronous
    behaviour) — the wrapper simply delegates to run_isp_speed_tests."""
    monkeypatch.setenv("STATUS_FILE", str(tmp_path / "status"))
    monkeypatch.setenv("ISP_SPEED_BOOT_BUDGET_SEC", "0")
    sentinel = object()
    monkeypatch.setattr(st, "run_isp_speed_tests", lambda **_kw: sentinel)
    assert st.run_isp_speed_tests_budgeted() is sentinel
