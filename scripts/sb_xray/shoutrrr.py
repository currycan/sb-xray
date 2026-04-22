"""SB-Xray shoutrrr 事件总线接收器。

监听 ``127.0.0.1:${SHOUTRRR_FORWARDER_PORT}``，接收来自 Xray ``rules.webhook``
的 HTTP POST（JSON body），把事件转发给 ``shoutrrr`` CLI 推送到
Telegram / Discord / Slack / Gotify 等 20+ 通道。

由 supervisord 通过 ``python3 /scripts/entrypoint.py shoutrrr-forward`` 拉起
(参见 ``templates/supervisord/daemon.ini`` 里的 ``[program:shoutrrr-forwarder]``)。

环境变量:
    SHOUTRRR_URLS           分号分隔的 shoutrrr URL 列表
                            空值时进入 dry-run，仅日志不外推
    SHOUTRRR_FORWARDER_PORT 监听端口，默认 18085
    SHOUTRRR_TITLE_PREFIX   推送标题前缀，默认 ``[sb-xray]``

Xray webhook payload 字段见 v26.3.27 PR #5722: email / level / protocol /
network / source / destination / routeTarget / originalTarget / inboundTag /
inboundName / inboundLocal / outboundTag / ts。

历史注记: v26.3.27 之前此逻辑位于 ``scripts/shoutrrr-forwarder.py`` 独立脚本,
为与 sb_xray 包内其他模块（geo / cert / display ...）统一形态而迁入。
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Final

DEFAULT_PORT: Final[int] = 18085
DEFAULT_TITLE_PREFIX: Final[str] = "[sb-xray]"
_SHOUTRRR_TIMEOUT_SEC: Final[int] = 10

logger = logging.getLogger(__name__)


def _parse_urls(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [u for u in raw.split(";") if u.strip()]


def _send(urls: list[str], title_prefix: str, event: str, payload: dict) -> None:
    if not urls:
        logger.info(
            "dry-run event=%s payload=%s",
            event,
            json.dumps(payload, ensure_ascii=False),
        )
        return
    title = f"{title_prefix} {event}"
    body = "\n".join(f"{k}: {v}" for k, v in payload.items())
    for url in urls:
        # URL 只用 scheme 作为日志识别符,不暴露 token
        url_scheme = url.split("://", 1)[0] if "://" in url else "?"
        cmd = ["shoutrrr", "send", "--url", url, "--title", title, "--message", body]
        try:
            result = subprocess.run(
                cmd,
                check=False,
                timeout=_SHOUTRRR_TIMEOUT_SEC,
                capture_output=True,
                text=True,
            )
        except Exception as exc:
            logger.error("send crashed scheme=%s err=%s", url_scheme, exc)
            continue
        if result.returncode != 0:
            # 静默失败过去是本项目最大的盲区;把 shoutrrr 的 stderr 前 400 字符
            # 直接打进 forwarder 日志,省掉 "204 但没消息" 的排查回合。
            stderr_tail = (result.stderr or result.stdout or "").strip()[:400]
            logger.error(
                "send failed scheme=%s exit=%d stderr=%s",
                url_scheme,
                result.returncode,
                shlex.quote(stderr_tail),
            )
        else:
            logger.info("send ok scheme=%s event=%s", url_scheme, event)


def _make_handler(urls: list[str], title_prefix: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # silence default access log
            return

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length else b"{}"
                payload = json.loads(raw.decode("utf-8") or "{}")
            except Exception as exc:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(f"bad json: {exc}".encode())
                return
            event = self.headers.get("X-Event") or self.path.strip("/") or "unknown"
            _send(urls, title_prefix, event, payload)
            self.send_response(204)
            self.end_headers()

        def do_GET(self):
            if self.path == "/healthz":
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"ok")
                return
            self.send_response(404)
            self.end_headers()

    return Handler


def run(
    port: int | None = None,
    urls: list[str] | None = None,
    title_prefix: str | None = None,
) -> int:
    """Start the event-bus HTTP receiver; blocks until KeyboardInterrupt.

    Args are ``None`` by default so callers can fall back to env vars; pytest
    passes explicit values to avoid global-state contamination.
    """
    if port is None:
        port = int(os.environ.get("SHOUTRRR_FORWARDER_PORT", str(DEFAULT_PORT)))
    if urls is None:
        urls = _parse_urls(os.environ.get("SHOUTRRR_URLS"))
    if title_prefix is None:
        title_prefix = os.environ.get("SHOUTRRR_TITLE_PREFIX", DEFAULT_TITLE_PREFIX)

    # Initialise Python logging the same way entrypoint.main() does so
    # this standalone supervisord-managed process emits the unified
    # format (supervisord then redirects stderr to its per-program log
    # file per daemon.ini).
    from sb_xray.log_config import setup_logging

    setup_logging()

    logger.info("listening on 127.0.0.1:%d urls=%d", port, len(urls))
    server = ThreadingHTTPServer(("127.0.0.1", port), _make_handler(urls, title_prefix))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("shutting down")
    finally:
        server.server_close()
    return 0
