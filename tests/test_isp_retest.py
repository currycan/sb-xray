"""Tests for sb_xray.stages.isp_retest (Phase 3)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sb_xray.stages import isp_retest


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in (
        "_ISP_SPEEDS_JSON",
        "ISP_RETEST_ENABLED",
        "SHOUTRRR_URLS",
    ):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def status_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A STATUS_FILE the retest reads old/new state from (process-safe path)."""
    path = tmp_path / "status"
    monkeypatch.setenv("STATUS_FILE", str(path))
    return path


def _write_status(path: Path, *, speeds: dict[str, float], isp_tag: str) -> None:
    path.write_text(
        f"export _ISP_SPEEDS_JSON='{json.dumps(speeds)}'\nexport ISP_TAG='{isp_tag}'\n",
        encoding="utf-8",
    )


def _install_speed_test_stub(
    monkeypatch: pytest.MonkeyPatch,
    status_path: Path,
    *,
    new_speeds: dict[str, float],
    new_isp_tag: str,
) -> MagicMock:
    """Replace run_isp_speed_tests so it persists new state to STATUS_FILE."""
    stub = MagicMock()

    def _fake(**_kw: object) -> None:
        _write_status(status_path, speeds=new_speeds, isp_tag=new_isp_tag)
        stub(**_kw)

    monkeypatch.setattr("sb_xray.speed_test.run_isp_speed_tests", _fake)
    return stub


def _install_reconfig_stubs(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    build = MagicMock()
    create = MagicMock()
    monkeypatch.setattr("sb_xray.routing.isp.build_client_and_server_configs", build)
    monkeypatch.setattr("sb_xray.config_builder.create_config", create)
    return build, create


def test_noop_on_pure_bandwidth_jitter(status_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Same usable members + same routing class → no rebuild, no restart."""
    _write_status(status_file, speeds={"proxy-cn2": 100.0, "proxy-hk": 80.0}, isp_tag="proxy-cn2")
    stub = _install_speed_test_stub(
        monkeypatch,
        status_file,
        new_speeds={"proxy-cn2": 102.0, "proxy-hk": 79.0},
        new_isp_tag="proxy-cn2",
    )
    build, create = _install_reconfig_stubs(monkeypatch)
    restart = MagicMock()
    monkeypatch.setattr(isp_retest, "_restart_daemons", restart)
    monkeypatch.setattr(isp_retest, "_write_status_timestamps", MagicMock())

    rc = isp_retest.run()

    assert rc == 0
    stub.assert_called_once()
    build.assert_not_called()
    create.assert_not_called()
    restart.assert_not_called()


def test_noop_folds_speed_summary_and_suppresses_push(
    status_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Merged card: retest suppresses the standalone speed push and folds the
    speed summary into the isp.retest.noop payload (one notification, not two)."""
    from sb_xray.speed_test import SpeedOutcome

    _write_status(status_file, speeds={"proxy-us": 100.0, "proxy-la": 80.0}, isp_tag="proxy-us")
    outcome = SpeedOutcome(
        speeds={"proxy-us": 51.56, "proxy-la": 20.97},
        diag={"proxy-us": {"status": "ok", "ok": 2, "total": 2}},
        direct_mbps=91.91,
        fastest_tag="proxy-us",
        fastest_speed=51.56,
        isp_tag="proxy-us",
        is_8k_smooth=False,
        has_isp_nodes=True,
        notify=True,
    )
    captured_kw: dict[str, object] = {}

    def _fake(**kw: object) -> SpeedOutcome:
        captured_kw.update(kw)
        _write_status(status_file, speeds={"proxy-us": 51.56, "proxy-la": 20.97}, isp_tag="proxy-us")
        return outcome

    monkeypatch.setattr("sb_xray.speed_test.run_isp_speed_tests", _fake)
    monkeypatch.setattr(isp_retest, "_write_status_timestamps", MagicMock())
    emit = MagicMock()
    monkeypatch.setattr(isp_retest, "emit_event", emit)

    rc = isp_retest.run()

    assert rc == 0
    # standalone speed_test push is suppressed in the retest path
    assert captured_kw.get("suppress_result_push") is True
    # the noop event carries the folded speed summary
    emit.assert_called_once()
    name, payload = emit.call_args[0]
    assert name == "isp.retest.noop"
    assert payload["speed"]["fastest_mbps"] == 51.56
    assert payload["speed"]["direct_mbps"] == 91.91
    assert payload["speed"]["speeds"] == {"proxy-us": 51.56, "proxy-la": 20.97}


def test_no_reload_on_pure_reorder(status_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Top-1 tag flips but membership/class unchanged → leastPing handles it live."""
    _write_status(status_file, speeds={"proxy-cn2": 100.0, "proxy-hk": 80.0}, isp_tag="proxy-cn2")
    _install_speed_test_stub(
        monkeypatch,
        status_file,
        new_speeds={"proxy-cn2": 50.0, "proxy-hk": 120.0},  # hk overtakes cn2
        new_isp_tag="proxy-hk",  # still a proxy → same routing class
    )
    build, _create = _install_reconfig_stubs(monkeypatch)
    restart = MagicMock()
    monkeypatch.setattr(isp_retest, "_restart_daemons", restart)
    monkeypatch.setattr(isp_retest, "_write_status_timestamps", MagicMock())

    rc = isp_retest.run()

    assert rc == 0
    build.assert_not_called()
    restart.assert_not_called()


def test_no_reload_when_member_goes_to_zero(
    status_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A line dropping to 0 Mbps keeps the configured set → NO reload.

    Runtime leastPing skips the dead line and fallbackTag/direct covers it —
    rebuilding would just churn restarts on a flaky line's flapping.
    """
    _write_status(status_file, speeds={"proxy-cn2": 100.0, "proxy-hk": 80.0}, isp_tag="proxy-cn2")
    _install_speed_test_stub(
        monkeypatch,
        status_file,
        new_speeds={"proxy-cn2": 100.0, "proxy-hk": 0.0},  # hk dead but still a key
        new_isp_tag="proxy-cn2",
    )
    build, _create = _install_reconfig_stubs(monkeypatch)
    restart = MagicMock()
    monkeypatch.setattr(isp_retest, "_restart_daemons", restart)
    monkeypatch.setattr(isp_retest, "_write_status_timestamps", MagicMock())

    rc = isp_retest.run()

    assert rc == 0
    build.assert_not_called()
    restart.assert_not_called()


def test_reloads_when_member_added(status_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_status(status_file, speeds={"proxy-cn2": 100.0}, isp_tag="proxy-cn2")
    _install_speed_test_stub(
        monkeypatch,
        status_file,
        new_speeds={"proxy-cn2": 100.0, "proxy-aws": 95.0},
        new_isp_tag="proxy-cn2",
    )
    build, _ = _install_reconfig_stubs(monkeypatch)
    restart = MagicMock(return_value=True)
    monkeypatch.setattr(isp_retest, "_restart_daemons", restart)
    monkeypatch.setattr(isp_retest, "_write_status_timestamps", MagicMock())

    rc = isp_retest.run()

    assert rc == 0
    build.assert_called_once()


def test_reloads_on_routing_class_flip(status_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """proxy → direct (all lines unusable / policy change) → rebuild."""
    _write_status(status_file, speeds={"proxy-cn2": 100.0}, isp_tag="proxy-cn2")
    _install_speed_test_stub(
        monkeypatch,
        status_file,
        new_speeds={"proxy-cn2": 100.0},  # same membership...
        new_isp_tag="direct",  # ...but routing class flipped
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


def test_speed_test_exception_returns_error(
    status_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    # Retained for the event payload's delta reporting (no longer a trigger).
    assert isp_retest._max_delta_pct({}, {"new": 10.0}) == 100.0
    assert isp_retest._max_delta_pct({"old": 10.0}, {}) == 100.0


def test_top_tag_breaks_ties_deterministically() -> None:
    assert isp_retest._top_tag({"a": 10.0, "b": 20.0}) == "b"
    assert isp_retest._top_tag({}) == ""


def test_should_reload_table() -> None:
    old = {"a": 10.0, "b": 5.0}
    # same configured set + same class → no reload (pure reorder/jitter)
    assert isp_retest._should_reload(
        old=old, new={"a": 3.0, "b": 5.0}, old_isp_tag="proxy-a", new_isp_tag="proxy-b"
    ) == (False, "no_change")
    # a line dropping to 0 keeps the configured set → NO reload (runtime handles)
    assert isp_retest._should_reload(
        old=old, new={"a": 10.0, "b": 0.0}, old_isp_tag="proxy-a", new_isp_tag="proxy-a"
    ) == (False, "no_change")
    # member added (operator config change) → composition change
    assert isp_retest._should_reload(
        old=old, new={"a": 10.0, "b": 5.0, "c": 1.0}, old_isp_tag="proxy-a", new_isp_tag="proxy-a"
    ) == (True, "composition_changed")
    # member removed → composition change
    assert isp_retest._should_reload(
        old=old, new={"a": 10.0}, old_isp_tag="proxy-a", new_isp_tag="proxy-a"
    ) == (True, "composition_changed")
    # routing class flip → reload even with identical set
    assert isp_retest._should_reload(
        old=old, new=old, old_isp_tag="proxy-a", new_isp_tag="direct"
    ) == (True, "routing_class_changed")


def test_routing_class_buckets() -> None:
    assert isp_retest._routing_class("direct") == "direct"
    assert isp_retest._routing_class("block") == "direct"
    assert isp_retest._routing_class("") == "direct"
    assert isp_retest._routing_class("proxy-cn2") == "proxy"


def test_reload_restores_media_routing_before_reconfigure(
    status_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reload must re-run media probes so sb.json's ${*_OUT} resolve."""
    _write_status(status_file, speeds={"proxy-cn2": 100.0}, isp_tag="proxy-cn2")
    _install_speed_test_stub(
        monkeypatch,
        status_file,
        new_speeds={"proxy-cn2": 100.0, "proxy-aws": 95.0},  # membership grows → reload
        new_isp_tag="proxy-cn2",
    )
    media = MagicMock()
    monkeypatch.setattr(isp_retest, "_restore_media_routing", media)
    build, _ = _install_reconfig_stubs(monkeypatch)
    monkeypatch.setattr(isp_retest, "_restart_daemons", MagicMock(return_value=True))
    monkeypatch.setattr(isp_retest, "_write_status_timestamps", MagicMock())

    isp_retest.run()

    media.assert_called_once()
    build.assert_called_once()


def test_restore_media_routing_sets_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    monkeypatch.setattr(
        "sb_xray.routing.media.check_all",
        lambda: {"GEMINI_OUT": "proxy-us-isp", "NETFLIX_OUT": "direct"},
    )
    monkeypatch.delenv("GEMINI_OUT", raising=False)
    monkeypatch.setenv("HAS_ISP_NODES", "true")
    isp_retest._restore_media_routing()
    assert os.environ["GEMINI_OUT"] == "proxy-us-isp"
    assert os.environ["NETFLIX_OUT"] == "direct"
    # ISP_OUT (ecommerce) is restored too — not a media probe key
    assert os.environ["ISP_OUT"] == "isp-auto"


def test_restore_media_routing_isp_out_direct_when_no_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    monkeypatch.setattr("sb_xray.routing.media.check_all", lambda: {})
    monkeypatch.delenv("HAS_ISP_NODES", raising=False)
    isp_retest._restore_media_routing()
    assert os.environ["ISP_OUT"] == "direct"


def test_restore_media_routing_swallows_probe_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> dict[str, str]:
        raise RuntimeError("probe down")

    monkeypatch.setattr("sb_xray.routing.media.check_all", _boom)
    isp_retest._restore_media_routing()  # must not raise — C layer is the net
