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
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Final

DEFAULT_PORT: Final[int] = 18085
DEFAULT_TITLE_PREFIX: Final[str] = "[sb-xray]"
_SHOUTRRR_TIMEOUT_SEC: Final[int] = 10

# X-Event → 人话标题。命中的事件用摘要正文（见 _format_message），
# 未登记的事件退回 key:value 列表兜底，新事件不至于变成乱码。
_BAN_TITLES: Final[dict[str, str]] = {
    "ban_bt": "🚫 BT 下载已拦截",
    "ban_geoip_cn": "🇨🇳 国内目标访问已拦截",
    "ban_ads": "🛡️ 广告/追踪已拦截",
    "ban_private_ip": "🔒 内网地址访问已拦截",
}
_TS_FORMAT: Final[str] = "%m-%d %H:%M:%S"

# speed_test diag.status → 人话标签（词表见 speed_test._aggregate_diag）
_SPEED_STATUS_LABEL: Final[dict[str, str]] = {
    "low_speed": "速率过低",
    "timeout": "超时",
    "connect_fail": "连接失败",
    "zero_body": "空响应",
    "mixed": "部分失败",
}


def _fmt_mbps(value: object) -> str | None:
    """数字 → 去尾零的 Mbps 文本；非数字返回 None。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return f"{value:g}"


def _rating_line(value: object) -> str | None:
    """``fastest_mbps`` → 评级梯子标签（如「评级: 流畅 4K，8K 可能卡顿」）。

    Replaces the old binary「8K: ✅/⚠️」which, with the 100 Mbps bar, read as
    a scary "⚠️ 不流畅" almost permanently. The ladder (8K-HDR/8K/4K/1080P/
    网络较慢) reuses speed_test.rate so a healthy 4K-capable line says so
    instead of failing a bar it was never going to clear.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    from sb_xray.speed_test import _RATING_LABEL, rate

    label = _RATING_LABEL.get(rate(float(value)), "—")
    return f"评级: {label}"


def _format_speed_test(payload: dict, title_prefix: str) -> tuple[str, str]:
    """isp.speed_test.result → 人话摘要：选定线路 / 评级 / 各线路逐行。"""
    title = f"{title_prefix} 📊 ISP 测速结果"

    isp_tag = str(payload.get("isp_tag") or "?")
    headline = f"选定线路: {isp_tag}"
    fastest = _fmt_mbps(payload.get("fastest_mbps"))
    if fastest is not None:
        headline += f" · {fastest} Mbps"

    summary = [headline]
    rating = _rating_line(payload.get("fastest_mbps"))
    if rating is not None:
        summary.append(rating)
    direct = _fmt_mbps(payload.get("direct_mbps"))
    if direct is not None:
        summary.append(f"直连基准: {direct} Mbps")
    blocks = ["\n".join(summary)]

    speeds = payload.get("speeds")
    diag = payload.get("diag")
    diag = diag if isinstance(diag, dict) else {}
    if isinstance(speeds, dict) and speeds:
        detail = ["各线路:"]
        for tag, mbps in speeds.items():
            d = diag.get(tag)
            d = d if isinstance(d, dict) else {}
            status = d.get("status")
            mark = "✓" if status == "ok" else ("✗" if status else "·")
            mbps_str = _fmt_mbps(mbps) or str(mbps)
            line = f"{mark} {tag}  {mbps_str} Mbps"
            if status and status != "ok":
                label = _SPEED_STATUS_LABEL.get(status, status)
                ok, total = d.get("ok"), d.get("total")
                if isinstance(ok, int) and isinstance(total, int):
                    line += f"  ({label} {ok}/{total})"
                else:
                    line += f"  ({label})"
            detail.append(line)
        blocks.append("\n".join(detail))

    return title, "\n\n".join(blocks)


def _format_substore_failure(payload: dict, title_prefix: str) -> tuple[str, str]:
    """substore.sub_fetch.failed → 哪几条订阅今日拉取失败 + 失败原因。"""
    title = f"{title_prefix} 🔴 订阅拉取失败"
    items = payload.get("items")
    lines: list[str] = []
    if isinstance(items, list):
        for it in items:
            if not isinstance(it, dict):
                continue
            name = str(it.get("name") or "?")
            tag = " (机场)" if it.get("airport") else ""
            reason = str(it.get("reason") or "")
            line = f"✗ {name}{tag}"
            if reason:
                line += f" — {reason}"
            lines.append(line)
    blocks: list[str] = []
    if lines:
        blocks.append("\n".join(lines))
    failed, total = payload.get("failed"), payload.get("total")
    if isinstance(failed, int) and isinstance(total, int):
        blocks.append(f"共 {failed}/{total} 条失败")
    return title, "\n\n".join(blocks) or "订阅拉取失败"


# isp.retest.completed reason → 人话（取值见 isp_retest._should_reload）
_RETEST_REASON_LABEL: Final[dict[str, str]] = {
    "composition_changed": "节点集合变化",
    "routing_class_changed": "路由模式切换（直连 ↔ 代理）",
}


def _format_retest_completed(payload: dict, title_prefix: str) -> tuple[str, str]:
    """isp.retest.completed → 人话：线路切换结论 + 触发原因 + 重启状态。

    payload 只带路由决策元数据（reason / old_top_tag / new_top_tag /
    delta_pct / restarted），不含测速数值，所以原先落到通用 key:value 兜底,
    与 ``isp.speed_test.result`` 的中文卡片观感割裂。delta_pct 在 composition
    变化时恒为 100%（新节点旧值为 0），属噪声,故不展示。
    """
    title = f"{title_prefix} 🔄 ISP 线路已更新"

    old_top = str(payload.get("old_top_tag") or "")
    new_top = str(payload.get("new_top_tag") or "")
    if old_top and new_top and old_top != new_top:
        headline = f"线路切换: {old_top} → {new_top}"
    elif new_top:
        headline = f"当前线路: {new_top}"
    elif old_top:
        headline = f"线路切换: {old_top} → 无可用线路"
    else:
        headline = "线路配置已更新"

    details: list[str] = []
    reason_label = _RETEST_REASON_LABEL.get(str(payload.get("reason") or ""))
    if reason_label:
        details.append(f"原因: {reason_label}")
    details.append(
        "已重启 xray/sing-box 生效" if payload.get("restarted") else "未重启（启动阶段）"
    )

    return title, "\n\n".join([headline, "\n".join(details)])


_CANARY_ROLE_LABEL: Final[dict[str, str]] = {
    "canary": "canary（错峰先行）",
    "worker": "worker（本台）",
}


def _format_canary_failed(payload: dict, title_prefix: str) -> tuple[str, str]:
    """watchtower.canary.failed → 自动更新后业务自检失败的中文卡片。"""
    title = f"{title_prefix} 🔴 自动更新自检失败"
    role = _CANARY_ROLE_LABEL.get(str(payload.get("role") or ""), str(payload.get("role") or "?"))
    head = [f"节点角色: {role}"]
    fails = str(payload.get("fails") or "")
    if fails:
        head.append(f"失败项: {fails}")
    head.append(f"镜像构建: {payload.get('built') or payload.get('image') or '未知'}")
    blocks = ["\n".join(head)]
    runbook = str(payload.get("runbook") or "")
    if runbook:
        blocks.append(f"⚠️ 处置\n{runbook}")
    return title, "\n\n".join(blocks)


def _format_canary_updated(payload: dict, title_prefix: str) -> tuple[str, str]:
    """watchtower.canary.updated → 自动更新成功且自检通过的中文卡片。"""
    title = f"{title_prefix} ✅ 已自动更新"
    built = str(payload.get("built") or payload.get("new") or "未知")
    return title, "\n\n".join([f"镜像构建: {built}", "四项自检全部通过"])


logger = logging.getLogger(__name__)


def _parse_urls(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [u for u in raw.split(";") if u.strip()]


def _format_ts(value: object) -> str | None:
    """Unix 秒 → 本地时间（容器 TZ）；解析不了返回 None，绝不抛异常。"""
    try:
        return datetime.fromtimestamp(int(value)).strftime(_TS_FORMAT)  # type: ignore[arg-type]
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _format_message(event: str, payload: dict, title_prefix: str) -> tuple[str, str]:
    """把 webhook payload 拼成人话 (title, body)。

    已知 ban 事件 → emoji 标题 + 摘要正文（谁连了什么 + 来源/入站/时间），
    空值字段整行省略；未知事件 → 原 key:value 列表，但剔除空值、ts 转可读。
    """
    if event == "isp.speed_test.result":
        return _format_speed_test(payload, title_prefix)
    if event == "isp.retest.completed":
        return _format_retest_completed(payload, title_prefix)
    if event == "watchtower.canary.failed":
        return _format_canary_failed(payload, title_prefix)
    if event == "watchtower.canary.updated":
        return _format_canary_updated(payload, title_prefix)
    if event == "substore.sub_fetch.failed":
        return _format_substore_failure(payload, title_prefix)
    if event in _BAN_TITLES:
        title = f"{title_prefix} {_BAN_TITLES[event]}"
        user = str(payload.get("email") or "").split("@", 1)[0]
        destination = str(payload.get("destination") or "")
        blocks: list[str] = []
        if user and destination:
            blocks.append(f"用户 {user} 尝试连接\n{destination}")
        elif user:
            blocks.append(f"用户 {user}")
        elif destination:
            blocks.append(f"目标 {destination}")

        details: list[str] = []
        source = str(payload.get("source") or "")
        if source:
            details.append(f"来源: {source.rsplit(':', 1)[0]}")
        inbound = str(payload.get("inboundTag") or "")
        transport = "/".join(
            str(payload.get(k) or "") for k in ("protocol", "network") if payload.get(k)
        )
        if inbound and transport:
            details.append(f"入站: {inbound} · {transport}")
        elif inbound:
            details.append(f"入站: {inbound}")
        ts_readable = _format_ts(payload.get("ts"))
        if ts_readable:
            details.append(f"时间: {ts_readable}")
        if details:
            blocks.append("\n".join(details))
        # payload 全空时给个非空正文，避免 shoutrrr 拒发空消息
        return title, "\n\n".join(blocks) or event

    title = f"{title_prefix} {event}"
    lines: list[str] = []
    for k, v in payload.items():
        if k == "event":  # 已是标题，避免正文重复
            continue
        if v is None or v == "":
            continue
        if k == "ts":
            readable = _format_ts(v)
            if readable:
                lines.append(f"ts: {readable}")
                continue
        lines.append(f"{k}: {v}")
    return title, "\n".join(lines) or event


def _send(urls: list[str], title_prefix: str, event: str, payload: dict) -> None:
    # Edge-triggered alerting: speed_test results carry a ``notify`` flag set
    # by _persist_routing_decision. Only a notable change (membership flip,
    # tag change, rating-tier flip, first run) sets it true — pure bandwidth
    # jitter stays silent. Absent key → push (back-compat with old payloads).
    if event == "isp.speed_test.result" and payload.get("notify") is False:
        logger.info("speed_test result not notable — skipping push (notify=false)")
        return
    if not urls:
        logger.info(
            "dry-run event=%s payload=%s",
            event,
            json.dumps(payload, ensure_ascii=False),
        )
        return
    title, body = _format_message(event, payload, title_prefix)
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
            # X-Event 头优先（Xray webhook 走这条）；events.py 把事件名包在
            # body 的 "event" 字段里 POST 到 /xray，无头时退回读它，再退回 URL 路径。
            event = self.headers.get("X-Event")
            if not event and isinstance(payload, dict):
                event = payload.get("event")
            event = event or self.path.strip("/") or "unknown"
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
