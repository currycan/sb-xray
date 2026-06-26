"""Tests for the ISP speed-test + switching optimisation.

Covers the new units: sample-count env alias, median central tendency,
cross-run leader hysteresis, and the notify edge-trigger.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sb_xray import speed_test as st

_MBPS_TO_BPS = 1024 * 1024 / 8


# ---- sample count ----------------------------------------------------------


def test_resolve_sample_count_default_is_three(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ISP_SPEED_SAMPLES", raising=False)
    monkeypatch.delenv("SPEED_SAMPLES", raising=False)
    assert st._resolve_sample_count(None) == 3


def test_resolve_sample_count_honors_isp_speed_samples(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ISP_SPEED_SAMPLES", "5")
    monkeypatch.setenv("SPEED_SAMPLES", "2")  # legacy must lose to the canonical knob
    assert st._resolve_sample_count(None) == 5


def test_resolve_sample_count_legacy_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ISP_SPEED_SAMPLES", raising=False)
    monkeypatch.setenv("SPEED_SAMPLES", "4")
    assert st._resolve_sample_count(None) == 4


def test_resolve_sample_count_explicit_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ISP_SPEED_SAMPLES", "9")
    assert st._resolve_sample_count(2) == 2


# ---- median central tendency -----------------------------------------------


def test_median_rejects_outlier_for_n5() -> None:
    # mbps [10,20,30,31,32] → median 30, NOT the truncated mean (≈27).
    bps = [v * _MBPS_TO_BPS for v in (10.0, 20.0, 30.0, 31.0, 32.0)]
    central, _stddev, _label = st._truncated_mean_with_stability(bps)
    assert central == 30.0


def test_median_equals_truncated_mean_at_n3() -> None:
    bps = [v * _MBPS_TO_BPS for v in (80.0, 90.0, 100.0)]
    central, _stddev, _label = st._truncated_mean_with_stability(bps)
    assert central == 90.0


# ---- leader hysteresis -----------------------------------------------------


def _ctx(speeds: dict[str, float], leader: str) -> st.IspSpeedContext:
    return st.IspSpeedContext(speeds=dict(speeds), fastest_tag=leader, fastest_speed=speeds[leader])


def test_hysteresis_keeps_incumbent_within_margin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ISP_LEADER_HYSTERESIS", raising=False)
    ctx = _ctx({"proxy-us": 10.0, "proxy-la": 10.5}, "proxy-la")
    tag, speed = st._leader_with_hysteresis(ctx, {"proxy-us": 9.0, "proxy-la": 1.0})
    # incumbent proxy-us (10.0) holds: challenger 10.5 < 10.0*1.15
    assert tag == "proxy-us"
    assert speed == 10.0


def test_hysteresis_switches_beyond_margin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ISP_LEADER_HYSTERESIS", raising=False)
    ctx = _ctx({"proxy-us": 10.0, "proxy-la": 12.0}, "proxy-la")
    tag, speed = st._leader_with_hysteresis(ctx, {"proxy-us": 9.0, "proxy-la": 1.0})
    # challenger 12.0 > 10.0*1.15 → switch
    assert tag == "proxy-la"
    assert speed == 12.0


def test_hysteresis_ignores_dead_incumbent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ISP_LEADER_HYSTERESIS", raising=False)
    ctx = _ctx({"proxy-us": 0.0, "proxy-la": 8.0}, "proxy-la")
    tag, speed = st._leader_with_hysteresis(ctx, {"proxy-us": 50.0, "proxy-la": 1.0})
    # previous leader proxy-us is dead this run → don't keep it
    assert tag == "proxy-la"
    assert speed == 8.0


def test_hysteresis_no_prev_returns_ctx_leader() -> None:
    ctx = _ctx({"proxy-us": 5.0}, "proxy-us")
    assert st._leader_with_hysteresis(ctx, {}) == ("proxy-us", 5.0)


# ---- notify edge-trigger ---------------------------------------------------


def _prev(speeds: dict[str, float], isp_tag: str) -> dict[str, str]:
    return {"_ISP_SPEEDS_JSON": json.dumps(speeds), "ISP_TAG": isp_tag}


def test_notify_first_run() -> None:
    assert st._should_notify(prev={}, new_speeds={"a": 5.0}, new_isp_tag="a", new_fastest_mbps=5.0)


def test_notify_on_membership_change() -> None:
    prev = _prev({"a": 5.0, "b": 1.0}, "a")
    assert st._should_notify(
        prev=prev, new_speeds={"a": 5.0, "b": 0.0}, new_isp_tag="a", new_fastest_mbps=5.0
    )


def test_notify_on_tag_change() -> None:
    prev = _prev({"a": 5.0, "b": 4.0}, "a")
    assert st._should_notify(
        prev=prev, new_speeds={"a": 5.0, "b": 4.0}, new_isp_tag="b", new_fastest_mbps=5.0
    )


def test_notify_on_rating_tier_flip() -> None:
    prev = _prev({"a": 30.0}, "a")  # 4K tier
    assert st._should_notify(
        prev=prev, new_speeds={"a": 12.0}, new_isp_tag="a", new_fastest_mbps=12.0
    )  # 1080P tier


def test_no_notify_on_pure_jitter() -> None:
    prev = _prev({"a": 30.0, "b": 12.0}, "a")
    # same usable set, same tag, same 4K tier → silent
    assert not st._should_notify(
        prev=prev, new_speeds={"a": 31.5, "b": 13.0}, new_isp_tag="a", new_fastest_mbps=31.5
    )


# ---- status snapshot reader ------------------------------------------------


def test_read_status_snapshot_parses_exports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "status"
    p.write_text(
        "export ISP_TAG='proxy-us'\nexport _ISP_SPEEDS_JSON='{\"proxy-us\": 5.0}'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("STATUS_FILE", str(p))
    snap = st._read_status_snapshot()
    assert snap["ISP_TAG"] == "proxy-us"
    assert json.loads(snap["_ISP_SPEEDS_JSON"]) == {"proxy-us": 5.0}


def test_read_status_snapshot_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STATUS_FILE", str(tmp_path / "nope"))
    assert st._read_status_snapshot() == {}


def test_usable_speed_tags_filters_dead() -> None:
    assert st._usable_speed_tags({"a": 5.0, "b": 0.0, "c": 1.0}) == {"a", "c"}


# ---- routing: dead lines KEPT (runtime leastPing + direct fallback) --------


def test_balancer_keeps_dead_line_in_selector() -> None:
    from sb_xray.routing import isp

    obs, bal = isp.build_xray_balancer({"proxy-us": 50.0, "proxy-la": 0.0})
    # 0-speed line stays so isp-auto is stable (a flap doesn't churn reloads);
    # runtime leastPing skips it.
    assert "proxy-us" in bal and "proxy-la" in bal
    assert "proxy-la" in obs
    assert '"fallbackTag": "direct"' in bal


def test_balancer_all_dead_still_emits_isp_auto_with_fallback() -> None:
    from sb_xray.routing import isp

    # All lines at 0 Mbps must STILL emit isp-auto (with direct fallback) — else
    # ${ISP_OUT}=isp-auto / *_OUT refs dangle → "outbound not found".
    obs, bal = isp.build_xray_balancer({"proxy-us": 0.0, "proxy-la": 0.0})
    assert '"tag": "isp-auto"' in bal
    assert '"fallbackTag": "direct"' in bal
    assert obs  # observatory still generated


def test_sb_urltest_all_dead_keeps_direct_tail() -> None:
    from sb_xray.routing import isp

    frag = isp.build_sb_urltest({"proxy-us": 0.0, "proxy-la": 0.0})
    assert '"tag": "isp-auto"' in frag
    assert '"direct"' in frag  # direct tail member = graceful all-dead fallback


# ---- media-routing leak fix (C: sb.json ${*_OUT} → direct) -----------------


def test_patch_unresolved_service_outs_falls_back_to_direct(tmp_path: Path) -> None:
    from sb_xray import config_builder as cb

    p = tmp_path / "sb.json"
    p.write_text(
        '{"rules":[{"o":"${GEMINI_OUT}"},{"o":"${ISP_OUT}"},{"o":"direct"}]}',
        encoding="utf-8",
    )
    cb._patch_unresolved_service_outs(p)
    text = p.read_text(encoding="utf-8")
    assert "_OUT}" not in text  # no literal placeholder survives
    assert text.count('"direct"') == 3  # 2 patched + 1 pre-existing


def test_patch_unresolved_service_outs_noop_when_clean(tmp_path: Path) -> None:
    from sb_xray import config_builder as cb

    p = tmp_path / "sb.json"
    original = '{"rules":[{"o":"direct"},{"o":"proxy-us-isp"}]}'
    p.write_text(original, encoding="utf-8")
    cb._patch_unresolved_service_outs(p)
    assert p.read_text(encoding="utf-8") == original  # untouched


def test_patch_unresolved_service_outs_missing_file_is_noop(tmp_path: Path) -> None:
    from sb_xray import config_builder as cb

    cb._patch_unresolved_service_outs(tmp_path / "nope.json")  # must not raise


# ---- leader hysteresis: sentinel exclusion + ISP_TAG priority ---------------


def test_hysteresis_excludes_999_sentinel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ISP_LEADER_HYSTERESIS", raising=False)
    # prev_speeds carries the 999.0 cache-hit sentinel on proxy-la; the real
    # prior leader (by genuine measurement) was proxy-us at 9.0. The sentinel
    # must NOT make proxy-la the incumbent.
    ctx = _ctx({"proxy-us": 10.0, "proxy-la": 8.0}, "proxy-us")
    tag, speed = st._leader_with_hysteresis(
        ctx, {"proxy-us": 9.0, "proxy-la": 999.0}
    )
    # incumbent resolves to proxy-us (sentinel filtered) which is also ctx leader
    assert tag == "proxy-us"
    assert speed == 10.0


def test_hysteresis_prefers_persisted_isp_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ISP_LEADER_HYSTERESIS", raising=False)
    # Persisted ISP_TAG names proxy-us as last routed leader. Challenger
    # proxy-la wins this run at 10.5 but < proxy-us(10.0)*1.15 → hold proxy-us.
    ctx = _ctx({"proxy-us": 10.0, "proxy-la": 10.5}, "proxy-la")
    tag, speed = st._leader_with_hysteresis(
        ctx, {"proxy-us": 999.0, "proxy-la": 1.0}, prev_isp_tag="proxy-us"
    )
    assert tag == "proxy-us"
    assert speed == 10.0


def test_hysteresis_default_prev_isp_tag_back_compat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No prev_isp_tag → behaves like the old argmax path (sentinel-free input).
    monkeypatch.delenv("ISP_LEADER_HYSTERESIS", raising=False)
    ctx = _ctx({"proxy-us": 10.0, "proxy-la": 10.5}, "proxy-la")
    tag, speed = st._leader_with_hysteresis(ctx, {"proxy-us": 9.0, "proxy-la": 1.0})
    assert tag == "proxy-us"
    assert speed == 10.0
