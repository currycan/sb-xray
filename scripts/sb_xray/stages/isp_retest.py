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
import subprocess
import time
from pathlib import Path

from sb_xray.events import emit_event

logger = logging.getLogger(__name__)

_SUPERVISOR_SOCKET = Path("/var/run/supervisor.sock")
_DEFAULT_DELTA_PCT: float = 15.0


def _enabled() -> bool:
    return os.environ.get("ISP_RETEST_ENABLED", "true").strip().lower() != "false"


def _delta_threshold() -> float:
    raw = os.environ.get("ISP_RETEST_DELTA_PCT", "").strip()
    if not raw:
        return _DEFAULT_DELTA_PCT
    try:
        return max(0.0, float(raw))
    except ValueError:
        logger.warning(
            "invalid ISP_RETEST_DELTA_PCT=%r — falling back to %.1f",
            raw,
            _DEFAULT_DELTA_PCT,
        )
        return _DEFAULT_DELTA_PCT


def _load_previous_speeds() -> dict[str, float]:
    raw = os.environ.get("_ISP_SPEEDS_JSON", "").strip()
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(decoded, dict):
        return {}
    return {str(k): float(v) for k, v in decoded.items()}


def _top_tag(speeds: dict[str, float]) -> str:
    if not speeds:
        return ""
    return max(speeds.items(), key=lambda kv: kv[1])[0]


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
    threshold_pct: float,
) -> tuple[bool, str]:
    if set(old) != set(new):
        return True, "composition_changed"
    if _top_tag(old) != _top_tag(new):
        return True, "top_tag_changed"
    if _max_delta_pct(old, new) > threshold_pct:
        return True, "delta_exceeded"
    return False, "no_delta"


def _restart_daemons(
    *,
    socket_path: Path = _SUPERVISOR_SOCKET,
    runner: object = subprocess,
) -> bool:
    """Restart xray + sing-box through supervisorctl.

    Mirrors :func:`sb_xray.geo._restart_xray_if_running`: no-op when
    supervisord isn't up yet (i.e. we're inside the boot pipeline).
    Returns ``True`` on attempted restart, ``False`` on skip.
    """
    if not socket_path.is_socket():
        logger.info("supervisord socket absent — skipping restart (likely boot-time call)")
        return False
    for svc in ("xray", "sing-box"):
        try:
            runner.run(  # type: ignore[attr-defined]
                ["supervisorctl", "restart", svc],
                check=False,
                timeout=10,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.warning("重启 %s 失败: %s", svc, exc)
    logger.info("xray/sing-box 已重启以加载新 balancer 配置")
    return True


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

    from sb_xray.speed_test import run_isp_speed_tests

    old_speeds = _load_previous_speeds()
    old_top = _top_tag(old_speeds)

    try:
        run_isp_speed_tests(force=True)
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("isp-retest: speed-test failed")
        emit_event("isp.retest.error", {"error": repr(exc)})
        return 1

    new_speeds = _load_previous_speeds()
    new_top = _top_tag(new_speeds)
    threshold = _delta_threshold()
    delta_pct = _max_delta_pct(old_speeds, new_speeds)
    reload_needed, reason = _should_reload(
        old=old_speeds,
        new=new_speeds,
        threshold_pct=threshold,
    )

    _write_status_timestamps(delta_pct=delta_pct, top_tag=new_top)

    if not reload_needed:
        logger.info(
            "isp-retest: noop (reason=%s top=%s delta=%.2f%% threshold=%.2f%%)",
            reason,
            new_top,
            delta_pct,
            threshold,
        )
        emit_event(
            "isp.retest.noop",
            {
                "reason": reason,
                "top_tag": new_top,
                "delta_pct": round(delta_pct, 2),
                "threshold_pct": threshold,
            },
        )
        return 0

    # Composition changed — rebuild the balancer JSON and re-render the
    # daemon configs, then restart xray + sing-box.
    try:
        from sb_xray.config_builder import create_config
        from sb_xray.routing.isp import build_client_and_server_configs

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
    emit_event(
        "isp.retest.completed",
        {
            "reason": reason,
            "old_top_tag": old_top,
            "new_top_tag": new_top,
            "delta_pct": round(delta_pct, 2),
            "threshold_pct": threshold,
            "restarted": restarted,
        },
    )
    logger.info(
        "isp-retest: completed (reason=%s old_top=%s new_top=%s delta=%.2f%%)",
        reason,
        old_top,
        new_top,
        delta_pct,
    )
    return 0
