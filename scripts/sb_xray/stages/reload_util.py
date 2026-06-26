"""Shared daemon-reload helpers for the cron reconfigure paths.

Both the ISP retest (:mod:`sb_xray.stages.isp_retest`) and the secrets
refresh (:mod:`sb_xray.stages.secrets_refresh`) cron entrypoints, after
re-rendering the xray / sing-box configs, need to:

1. restore the media-routing ``*_OUT`` env vars that only exist during the
   boot media stage (a fresh cron process lacks them), and
2. restart the daemons through ``supervisorctl``.

Centralised here so the two cron entrypoints share one implementation.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_SUPERVISOR_SOCKET = Path("/var/run/supervisor.sock")


def restore_media_routing() -> None:
    """Re-run media probes so sb.json's ``${*_OUT}`` placeholders resolve.

    The ``*_OUT`` media-routing vars (GEMINI_OUT / NETFLIX_OUT / ...) are only
    written to ``os.environ`` during the boot media stage. A cron reconfigure is
    a fresh process without them, so re-rendering sb.json here would otherwise
    bake literal ``${GEMINI_OUT}`` into the config - sing-box then drops all
    media (anytls/tuic) traffic with ``outbound not found``. Best-effort: on
    probe failure we leave the vars unset and rely on config_builder's
    direct-fallback safety net (so a flaky probe never aborts the reload).
    """
    try:
        from sb_xray.routing import media as sbmedia

        for key, value in sbmedia.check_all().items():
            os.environ[key] = value
        # ISP_OUT (电商: amazon/paypal/ebay；模板还用于 social/tiktok 规则) 不是
        # media 探针项，须按 boot (entrypoint._cache_media_probes) 同样推导，否则
        # reconfigure 后悬空 → 两核都退成 direct，电商走数据中心 IP 触发风控。
        # isp-auto 恒被生成（dead 线已不再过滤），故沿用 boot 的 HAS_ISP_NODES 判据即安全。
        os.environ["ISP_OUT"] = "isp-auto" if os.environ.get("HAS_ISP_NODES") else "direct"
    except Exception as exc:  # pragma: no cover - defensive, C-layer catches the rest
        logger.warning("media 探针恢复失败 (%s); 依赖 sb.json direct 兜底", exc)


def restart_daemons(
    *,
    socket_path: Path = _SUPERVISOR_SOCKET,
    runner: object = subprocess,
) -> bool:
    """Restart xray + sing-box through supervisorctl.

    No-op when supervisord isn't up yet (i.e. we're inside the boot pipeline,
    before ``exec supervisord``). Returns ``True`` on attempted restart,
    ``False`` on skip.
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
    logger.info("xray/sing-box 已重启以加载新配置")
    return True


def reload_nginx(
    *,
    socket_path: Path = _SUPERVISOR_SOCKET,
    runner: object = subprocess,
) -> bool:
    """Signal a running nginx to reload its freshly re-rendered config.

    The cron reconfigure paths re-run ``create_config()``, which re-renders the
    nginx configs alongside xray/sing-box. Restarting only the two cores leaves
    nginx serving the stale config until the container restarts, so emit a cheap
    ``nginx -s reload`` here. No-op when supervisord isn't up yet (boot-time
    call, before ``exec supervisord``). Returns ``True`` on attempted reload,
    ``False`` on skip; signal failures are swallowed (a transient reload error
    must never abort the reconfigure).
    """
    if not socket_path.is_socket():
        logger.info("supervisord socket absent — skipping nginx reload (boot-time call)")
        return False
    try:
        runner.run(  # type: ignore[attr-defined]
            ["nginx", "-s", "reload"],
            check=False,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("nginx reload 失败: %s", exc)
    logger.info("nginx 已 reload 以加载新渲染配置")
    return True
