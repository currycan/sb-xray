#!/usr/bin/env python3
"""
shoutrrr-forwarder.py — SB-Xray 事件总线接收器

监听 127.0.0.1:${SHOUTRRR_FORWARDER_PORT:-18085}，接收来自 Xray `rules.webhook`
的 HTTP POST（JSON body），把事件转发给 shoutrrr CLI 推送到 Telegram / Discord / Slack 等。

环境变量:
    SHOUTRRR_URLS           分号分隔的 shoutrrr URL 列表（如 "telegram://...;discord://..."）
                            未设置 or 空值时进入 dry-run，仅日志不推送
    SHOUTRRR_FORWARDER_PORT 监听端口，默认 18085
    SHOUTRRR_TITLE_PREFIX   推送标题前缀，默认 "[sb-xray]"

Xray webhook payload 字段见 v26.3.27 PR #5722:
    email / level / protocol / network / source / destination / routeTarget
    originalTarget / inboundTag / inboundName / inboundLocal / outboundTag / ts
"""

import json
import os
import shlex
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("SHOUTRRR_FORWARDER_PORT", "18085"))
URLS = [u for u in os.environ.get("SHOUTRRR_URLS", "").split(";") if u.strip()]
TITLE_PREFIX = os.environ.get("SHOUTRRR_TITLE_PREFIX", "[sb-xray]")


def log(msg: str) -> None:
    print(f"[shoutrrr-forwarder] {msg}", flush=True)


def send(event: str, payload: dict) -> None:
    if not URLS:
        log(f"dry-run event={event} payload={json.dumps(payload, ensure_ascii=False)}")
        return
    title = f"{TITLE_PREFIX} {event}"
    body_lines = [f"{k}: {v}" for k, v in payload.items()]
    body = "\n".join(body_lines)
    for url in URLS:
        cmd = ["shoutrrr", "send", "--url", url, "--title", title, "--message", body]
        try:
            subprocess.run(cmd, check=False, timeout=10, capture_output=True)
        except Exception as exc:  # noqa: BLE001
            log(f"send failed url={shlex.quote(url)} err={exc}")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # silence default access log
        return

    def do_POST(self):  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception as exc:  # noqa: BLE001
            self.send_response(400)
            self.end_headers()
            self.wfile.write(f"bad json: {exc}".encode())
            return
        event = self.headers.get("X-Event") or self.path.strip("/") or "unknown"
        send(event, payload)
        self.send_response(204)
        self.end_headers()

    def do_GET(self):  # noqa: N802
        if self.path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
            return
        self.send_response(404)
        self.end_headers()


def main() -> int:
    log(f"listening on 127.0.0.1:{PORT} urls={len(URLS)}")
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("shutting down")
    return 0


if __name__ == "__main__":
    sys.exit(main())
