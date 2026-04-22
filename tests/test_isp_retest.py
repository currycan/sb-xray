"""Tests for sb_xray.stages.isp_retest (Phase 3)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from sb_xray.stages import isp_retest


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in (
        "_ISP_SPEEDS_JSON",
        "ISP_RETEST_ENABLED",
        "ISP_RETEST_DELTA_PCT",
        "SHOUTRRR_URLS",
    ):
        monkeypatch.delenv(k, raising=False)


def _install_speed_test_stub(
    monkeypatch: pytest.MonkeyPatch,
    *,
    new_speeds: dict[str, float],
) -> MagicMock:
    """Replace run_isp_speed_tests so it installs new_speeds in env."""
    stub = MagicMock()

    def _fake(**_kw: object) -> None:
        import os

        os.environ["_ISP_SPEEDS_JSON"] = json.dumps(new_speeds)
        stub(**_kw)

    monkeypatch.setattr("sb_xray.speed_test.run_isp_speed_tests", _fake)
    return stub


def _install_reconfig_stubs(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    build = MagicMock()
    create = MagicMock()
    monkeypatch.setattr(
        "sb_xray.routing.isp.build_client_and_server_configs",
        build,
    )
    monkeypatch.setattr("sb_xray.config_builder.create_config", create)
    return build, create


def test_noop_when_top_tag_and_delta_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("_ISP_SPEEDS_JSON", json.dumps({"proxy-cn2": 100.0, "proxy-hk": 80.0}))
    stub = _install_speed_test_stub(
        monkeypatch,
        new_speeds={"proxy-cn2": 102.0, "proxy-hk": 79.0},  # < 15% delta
    )
    build, create = _install_reconfig_stubs(monkeypatch)
    restart = MagicMock()
    monkeypatch.setattr(isp_retest, "_restart_daemons", restart)
    # Avoid STATUS_FILE writes against read-only tmpfs.
    monkeypatch.setattr(isp_retest, "_write_status_timestamps", MagicMock())

    rc = isp_retest.run()

    assert rc == 0
    stub.assert_called_once()
    build.assert_not_called()
    create.assert_not_called()
    restart.assert_not_called()


def test_reloads_when_top_tag_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("_ISP_SPEEDS_JSON", json.dumps({"proxy-cn2": 100.0, "proxy-hk": 80.0}))
    _install_speed_test_stub(
        monkeypatch,
        new_speeds={"proxy-cn2": 50.0, "proxy-hk": 120.0},  # top-1 changed
    )
    build, create = _install_reconfig_stubs(monkeypatch)
    restart = MagicMock(return_value=True)
    monkeypatch.setattr(isp_retest, "_restart_daemons", restart)
    monkeypatch.setattr(isp_retest, "_write_status_timestamps", MagicMock())

    rc = isp_retest.run()

    assert rc == 0
    build.assert_called_once()
    create.assert_called_once()
    restart.assert_called_once()


def test_reloads_when_composition_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("_ISP_SPEEDS_JSON", json.dumps({"proxy-cn2": 100.0}))
    _install_speed_test_stub(
        monkeypatch,
        new_speeds={"proxy-cn2": 100.0, "proxy-aws": 95.0},
    )
    build, create = _install_reconfig_stubs(monkeypatch)
    restart = MagicMock(return_value=True)
    monkeypatch.setattr(isp_retest, "_restart_daemons", restart)
    monkeypatch.setattr(isp_retest, "_write_status_timestamps", MagicMock())

    rc = isp_retest.run()

    assert rc == 0
    build.assert_called_once()


def test_reloads_when_delta_exceeds_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("_ISP_SPEEDS_JSON", json.dumps({"proxy-cn2": 100.0, "proxy-hk": 80.0}))
    monkeypatch.setenv("ISP_RETEST_DELTA_PCT", "10")
    _install_speed_test_stub(
        monkeypatch,
        new_speeds={"proxy-cn2": 120.0, "proxy-hk": 80.0},  # 20% delta on cn2
    )
    build, _ = _install_reconfig_stubs(monkeypatch)
    restart = MagicMock(return_value=True)
    monkeypatch.setattr(isp_retest, "_restart_daemons", restart)
    monkeypatch.setattr(isp_retest, "_write_status_timestamps", MagicMock())

    rc = isp_retest.run()

    assert rc == 0
    build.assert_called_once()


def test_disabled_flag_skips_speed_test(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ISP_RETEST_ENABLED", "false")
    stub = MagicMock()
    monkeypatch.setattr("sb_xray.speed_test.run_isp_speed_tests", stub)

    rc = isp_retest.run()

    assert rc == 0
    stub.assert_not_called()


def test_speed_test_exception_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(**_kw: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("sb_xray.speed_test.run_isp_speed_tests", _boom)
    build, _ = _install_reconfig_stubs(monkeypatch)
    restart = MagicMock()
    monkeypatch.setattr(isp_retest, "_restart_daemons", restart)

    rc = isp_retest.run()

    assert rc == 1
    build.assert_not_called()
    restart.assert_not_called()


def test_max_delta_pct_with_new_tag() -> None:
    # Adding a tag counts as a composition change handled by _should_reload,
    # but _max_delta_pct itself must surface 100% for the missing tag.
    assert isp_retest._max_delta_pct({}, {"new": 10.0}) == 100.0
    assert isp_retest._max_delta_pct({"old": 10.0}, {}) == 100.0


def test_top_tag_breaks_ties_deterministically() -> None:
    assert isp_retest._top_tag({"a": 10.0, "b": 20.0}) == "b"
    assert isp_retest._top_tag({}) == ""


def test_should_reload_table() -> None:
    old = {"a": 10.0, "b": 5.0}
    assert isp_retest._should_reload(old=old, new=old, threshold_pct=15.0) == (False, "no_delta")
    assert isp_retest._should_reload(
        old=old, new={"a": 10.0, "b": 5.0, "c": 1.0}, threshold_pct=15.0
    ) == (True, "composition_changed")
    assert isp_retest._should_reload(old=old, new={"a": 3.0, "b": 5.0}, threshold_pct=15.0) == (
        True,
        "top_tag_changed",
    )
    assert isp_retest._should_reload(old=old, new={"a": 13.0, "b": 5.0}, threshold_pct=15.0) == (
        True,
        "delta_exceeded",
    )
