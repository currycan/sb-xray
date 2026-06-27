"""ISP bandwidth re-test orchestrator (Phase 3).

Invoked by ``/scripts/entrypoint.py isp-retest`` on the cron schedule
installed by :mod:`sb_xray.stages.cron`. Re-measures every configured
ISP node, diffs against the previous STATUS_FILE snapshot, and only
rebuilds configs + restarts daemons when the balancer composition or
ranking actually changed. Pure RTT fluctuations are left to the
running ``urltest`` / ``leastPing`` to handle in place.

Emits one of three structured events:

- ``isp.retest.completed`` — composition / top-1 changed, daemons reloaded
- ``isp.retest.noop``      — delta below threshold, nothing restarted
- ``isp.retest.error``     — speed-test or reload raised; daemons untouched
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from typing import Any

from sb_xray.events import emit_event
from sb_xray.stages.reload_util import reload_nginx as _reload_nginx
from sb_xray.stages.reload_util import restart_daemons as _restart_daemons
from sb_xray.stages.reload_util import restore_media_routing as _restore_media_routing

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    return os.environ.get("ISP_RETEST_ENABLED", "true").strip().lower() != "false"


def _load_speeds_from_snapshot(snapshot: dict[str, str]) -> dict[str, float]:
    raw = snapshot.get("_ISP_SPEEDS_JSON", "").strip()
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(decoded, dict):
        return {}
    return {str(k): float(v) for k, v in decoded.items()}


def _load_previous_speeds() -> dict[str, float]:
    """Previous persisted speeds, read straight from STATUS_FILE.

    The cron retest runs as a fresh process that never sources STATUS_FILE
    into env, and the env ``_ISP_SPEEDS_JSON`` used to be a frozen boot-time
    snapshot — comparing against it made every retest see a 100% delta and
    restart. Reading the file makes old/new reflect the actual prior retest.
    """
    from sb_xray.speed_test import _read_status_snapshot

    return _load_speeds_from_snapshot(_read_status_snapshot())


def _top_tag(speeds: dict[str, float]) -> str:
    if not speeds:
        return ""
    return max(speeds.items(), key=lambda kv: kv[1])[0]


def _routing_class(isp_tag: str) -> str:
    """direct/block vs balancer-backed proxy — only a class flip needs a rebuild."""
    return "direct" if isp_tag in ("", "direct", "block") else "proxy"


def _max_delta_pct(old: dict[str, float], new: dict[str, float]) -> float:
    """Maximum per-tag speed change as a percentage.

    Tags present in one dict but not the other contribute 100% (composition
    change). Empty → 0.0.
    """
    tags = set(old) | set(new)
    if not tags:
        return 0.0
    worst = 0.0
    for t in tags:
        o = old.get(t, 0.0)
        n = new.get(t, 0.0)
        if o <= 0.0:
            worst = max(worst, 100.0)
            continue
        pct = abs(n - o) / o * 100.0
        worst = max(worst, pct)
    return worst


def _should_reload(
    *,
    old: dict[str, float],
    new: dict[str, float],
    old_isp_tag: str,
    new_isp_tag: str,
) -> tuple[bool, str]:
    """Rebuild + restart only when it actually changes routing.

    xray ``leastPing`` re-ranks the live selector by RTT every minute, so a
    pure bandwidth re-ordering of the *same* nodes yields identical runtime
    routing — restarting for it just drops every connection for nothing (the
    old ``top_tag_changed`` / ``delta_exceeded`` triggers). A line merely going
    to 0 Mbps is handled live too (``leastPing`` skips it, ``fallbackTag``/
    ``direct`` tail covers all-dead), so it is NOT a reload trigger either —
    keying on the *configured* set (which includes 0-speed tags) keeps a flaky
    line's flapping from churning restarts. We therefore reload only when:

    - the **configured membership** changes (operator added/removed a node →
      the selector pool itself must change), or
    - the **routing class** flips (direct/block ↔ balancer-backed proxy).
    """
    if set(old) != set(new):
        return True, "composition_changed"
    if _routing_class(old_isp_tag) != _routing_class(new_isp_tag):
        return True, "routing_class_changed"
    return False, "no_change"


def _speed_summary(outcome: object) -> dict[str, Any] | None:
    """Speed-test fields the merged retest card renders, or ``None`` if absent.

    The retest card folds in the same numbers a standalone
    ``isp.speed_test.result`` would show (its push is suppressed in this path),
    so the data is not lost. ``outcome`` is ``SpeedOutcome | None`` — ``None`` on
    a cache hit or a stubbed test run; typed loosely to dodge a speed_test
    import at module top. Fields mirror shoutrrr._speed_blocks' expectations.
    """
    if outcome is None:
        return None
    summary: dict[str, object] = {
        "isp_tag": getattr(outcome, "isp_tag", ""),
        "fastest_mbps": round(getattr(outcome, "fastest_speed", 0.0), 2),
        "direct_mbps": round(getattr(outcome, "direct_mbps", 0.0), 2),
        "speeds": {t: round(v, 2) for t, v in (getattr(outcome, "speeds", {}) or {}).items()},
    }
    diag = getattr(outcome, "diag", None)
    if diag:
        summary["diag"] = diag
    return summary


def _run_signature_self_check() -> None:
    """Fire the B-class signature-rot self-check, fully isolated.

    Runs on the retest cron (a node-local periodic process — the right place to
    probe real services from this IP). Any failure is swallowed: a rotted-marker
    check must never fail the retest itself (routing already fails safe).
    """
    try:
        from sb_xray.routing.media import run_signature_self_check

        run_signature_self_check()
    except Exception:  # pragma: no cover — defensive isolation
        logger.exception("isp-retest: signature self-check failed (ignored)")


def _write_status_timestamps(*, delta_pct: float, top_tag: str) -> None:
    # Import locally to avoid speed_test import-cycle at module top.
    from sb_xray.speed_test import _write_status_line

    with contextlib.suppress(OSError):
        _write_status_line("ISP_LAST_RETEST_TS", str(int(time.time())))
        _write_status_line("ISP_LAST_RETEST_DELTA_PCT", f"{delta_pct:.2f}")
        _write_status_line("ISP_LAST_RETEST_TOP_TAG", top_tag)


def run() -> int:
    """Execute a single retest cycle — the cron entrypoint."""
    if not _enabled():
        logger.info("isp-retest: disabled via ISP_RETEST_ENABLED=false")
        emit_event("isp.retest.noop", {"reason": "disabled"})
        return 0

    from sb_xray.speed_test import _read_status_snapshot, run_isp_speed_tests

    old_snap = _read_status_snapshot()
    old_speeds = _load_speeds_from_snapshot(old_snap)
    old_isp_tag = old_snap.get("ISP_TAG", "")
    old_top = _top_tag(old_speeds)

    try:
        # Suppress the standalone speed_test push — its summary is folded into
        # the merged retest card below, so the two notifications become one.
        outcome = run_isp_speed_tests(force=True, suppress_result_push=True)
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("isp-retest: speed-test failed")
        emit_event("isp.retest.error", {"error": repr(exc)})
        return 1

    new_snap = _read_status_snapshot()
    new_speeds = _load_speeds_from_snapshot(new_snap)
    new_isp_tag = new_snap.get("ISP_TAG", "")
    new_top = _top_tag(new_speeds)
    delta_pct = _max_delta_pct(old_speeds, new_speeds)
    reload_needed, reason = _should_reload(
        old=old_speeds,
        new=new_speeds,
        old_isp_tag=old_isp_tag,
        new_isp_tag=new_isp_tag,
    )

    _write_status_timestamps(delta_pct=delta_pct, top_tag=new_top)
    _run_signature_self_check()

    if not reload_needed:
        logger.info(
            "isp-retest: noop (reason=%s top=%s delta=%.2f%%)",
            reason,
            new_top,
            delta_pct,
        )
        noop_payload: dict[str, object] = {
            "reason": reason,
            "top_tag": new_top,
            "delta_pct": round(delta_pct, 2),
        }
        speed = _speed_summary(outcome)
        if speed:
            noop_payload["speed"] = speed
        emit_event("isp.retest.noop", noop_payload)
        return 0

    # Configured membership or routing class changed — rebuild the balancer
    # JSON and re-render the daemon configs, then restart xray + sing-box.
    try:
        from sb_xray.config_builder import create_config
        from sb_xray.routing.isp import build_client_and_server_configs

        # Restore media routing env BEFORE re-rendering, else sb.json's
        # ${*_OUT} placeholders leak as literals (see _restore_media_routing).
        _restore_media_routing()
        build_client_and_server_configs(speeds=new_speeds)
        create_config()
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("isp-retest: reconfigure failed")
        emit_event(
            "isp.retest.error",
            {"error": repr(exc), "stage": "reconfigure"},
        )
        return 1

    restarted = _restart_daemons()
    _reload_nginx()
    completed_payload: dict[str, object] = {
        "reason": reason,
        "old_top_tag": old_top,
        "new_top_tag": new_top,
        "delta_pct": round(delta_pct, 2),
        "restarted": restarted,
    }
    speed = _speed_summary(outcome)
    if speed:
        completed_payload["speed"] = speed
    emit_event("isp.retest.completed", completed_payload)
    logger.info(
        "isp-retest: completed (reason=%s old_top=%s new_top=%s delta=%.2f%%)",
        reason,
        old_top,
        new_top,
        delta_pct,
    )
    return 0
