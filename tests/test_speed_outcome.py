"""SpeedOutcome immutable result + measure/apply/persist split (race-fix Task 2-4)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sb_xray import speed_test as st


def _make_outcome(**overrides: object) -> st.SpeedOutcome:
    base: dict[str, object] = {
        "speeds": {"proxy-us-isp": 29.6},
        "diag": None,
        "direct_mbps": 50.0,
        "fastest_tag": "proxy-us-isp",
        "fastest_speed": 29.6,
        "isp_tag": "proxy-us-isp",
        "is_8k_smooth": False,
        "has_isp_nodes": True,
        "notify": True,
    }
    base.update(overrides)
    return st.SpeedOutcome(**base)  # type: ignore[arg-type]


def test_speed_outcome_is_frozen() -> None:
    o = _make_outcome()
    with pytest.raises(Exception):
        o.isp_tag = "x"  # type: ignore[misc]  # frozen


def test_apply_outcome_to_env_sets_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HAS_ISP_NODES", raising=False)
    o = _make_outcome(notify=False)
    st.apply_outcome_to_env(o)
    assert os.environ["HAS_ISP_NODES"] == "true"
    assert os.environ["ISP_TAG"] == "proxy-us-isp"
    assert os.environ["IS_8K_SMOOTH"] == "false"
    assert os.environ["FASTEST_PROXY_TAG"] == "proxy-us-isp"
    assert os.environ["DIRECT_SPEED"] == "50.00"  # M1: parity with _measure_direct_baseline


def test_apply_outcome_no_isp_clears_has_isp_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAS_ISP_NODES", "true")
    o = _make_outcome(
        speeds={}, fastest_tag=None, fastest_speed=0.0, isp_tag="direct", has_isp_nodes=False
    )
    st.apply_outcome_to_env(o)
    assert os.environ["HAS_ISP_NODES"] == ""
    assert os.environ["ISP_TAG"] == "direct"


def test_persist_outcome_writes_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STATUS_FILE", str(tmp_path / "status"))
    o = _make_outcome(is_8k_smooth=True, notify=False)
    st.persist_outcome_to_status(o)
    snap = st._read_status_snapshot()
    assert snap["ISP_TAG"] == "proxy-us-isp"
    assert snap["IS_8K_SMOOTH"] == "true"
    assert "proxy-us-isp" in snap["_ISP_SPEEDS_JSON"]


def test_measure_is_pure_no_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """measure_isp_speeds must not touch os.environ / STATUS_FILE, nor hit the net."""
    monkeypatch.setenv("STATUS_FILE", str(tmp_path / "status"))
    monkeypatch.setenv("IP_TYPE", "hosting")
    monkeypatch.delenv("HAS_ISP_NODES", raising=False)
    monkeypatch.delenv("GEOIP_INFO", raising=False)
    # M2: mock the direct-baseline network call (else measure() does real HTTP).
    monkeypatch.setattr(st, "measure", lambda *a, **k: 50.0)
    # No *_ISP_IP nodes → the ISP-node loop performs zero network IO.
    before = dict(os.environ)
    o = st.measure_isp_speeds(url="http://x/", sample_count=1)
    assert isinstance(o, st.SpeedOutcome)
    assert o.has_isp_nodes is False
    assert o.direct_mbps == 50.0
    assert os.environ.get("HAS_ISP_NODES") in (None, "")  # never written
    assert not (tmp_path / "status").exists()  # never persisted
    # measure neither added nor removed any env key
    # (depends on Task 0: apply_isp_routing_logic no longer transiently writes GEOIP_INFO).
    assert dict(os.environ) == before
