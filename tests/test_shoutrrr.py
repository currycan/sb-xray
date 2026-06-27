"""Tests for scripts/sb_xray/shoutrrr.py (event-bus HTTP receiver)."""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from datetime import datetime
from http.server import ThreadingHTTPServer
from unittest.mock import MagicMock

import pytest
from sb_xray import shoutrrr


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


_FULL_BAN_PAYLOAD = {
    "email": "user01@vless",
    "level": "0",
    "protocol": "vless",
    "network": "tcp",
    "source": "198.51.100.42:51234",
    "destination": "tracker.example.org:6881",
    "routeTarget": "",
    "originalTarget": "tracker.example.org:6881",
    "inboundTag": "reality-443",
    "inboundName": "",
    "inboundLocal": "",
    "outboundTag": "block",
    "ts": 1749307900,
}


def test_format_message_known_ban_event_summarises_payload():
    title, body = shoutrrr._format_message("ban_bt", _FULL_BAN_PAYLOAD, "[p]")
    assert title == "[p] 🚫 BT 下载已拦截"
    assert "用户 user01 尝试连接" in body
    assert "tracker.example.org:6881" in body
    # source shown without port
    assert "来源: 198.51.100.42" in body
    assert ":51234" not in body
    assert "入站: reality-443 · vless/tcp" in body
    expected_ts = datetime.fromtimestamp(1749307900).strftime("%m-%d %H:%M:%S")
    assert f"时间: {expected_ts}" in body
    # noise fields must not leak into the summary
    assert "level" not in body
    assert "routeTarget" not in body
    assert "outboundTag" not in body


@pytest.mark.parametrize(
    ("event", "expected_title"),
    [
        ("ban_bt", "🚫 BT 下载已拦截"),
        ("ban_geoip_cn", "🇨🇳 国内目标访问已拦截"),
        ("ban_ads", "🛡️ 广告/追踪已拦截"),
        ("ban_private_ip", "🔒 内网地址访问已拦截"),
    ],
)
def test_format_message_titles_for_all_ban_events(event, expected_title):
    title, _body = shoutrrr._format_message(event, _FULL_BAN_PAYLOAD, "[sb-xray]")
    assert title == f"[sb-xray] {expected_title}"


def test_format_message_omits_lines_for_missing_fields():
    title, body = shoutrrr._format_message(
        "ban_ads", {"email": "user02@trojan", "ts": "not-a-number"}, "[p]"
    )
    assert title == "[p] 🛡️ 广告/追踪已拦截"
    assert "用户 user02" in body
    # no destination / source / inbound / unparsable ts → those lines vanish
    assert "尝试连接" not in body
    assert "来源:" not in body
    assert "入站:" not in body
    assert "时间:" not in body


def test_format_message_known_event_empty_payload_falls_back_to_event_name():
    _title, body = shoutrrr._format_message("ban_bt", {}, "[p]")
    assert body == "ban_bt"


def test_format_message_unknown_event_keeps_kv_dump_without_blanks():
    payload = {"email": "demo", "routeTarget": "", "inboundName": None, "ts": 1749307900}
    title, body = shoutrrr._format_message("manual.test", payload, "[p]")
    assert title == "[p] manual.test"
    assert "email: demo" in body
    # blank/None values are dropped
    assert "routeTarget" not in body
    assert "inboundName" not in body
    # ts becomes human-readable
    expected_ts = datetime.fromtimestamp(1749307900).strftime("%m-%d %H:%M:%S")
    assert f"ts: {expected_ts}" in body
    assert "1749307900" not in body


def test_format_message_unknown_event_keeps_unparsable_ts_verbatim():
    _title, body = shoutrrr._format_message("manual.test", {"ts": "oops"}, "[p]")
    assert "ts: oops" in body


def test_format_message_unknown_event_skips_event_key():
    # events.py wraps payloads as {"event": name, ...}; the title already
    # carries the name, so it must not be repeated as a body line.
    _title, body = shoutrrr._format_message(
        "manual.test", {"event": "manual.test", "email": "demo"}, "[p]"
    )
    assert "email: demo" in body
    assert "event:" not in body


_SPEED_PAYLOAD = {
    "event": "isp.speed_test.result",
    "direct_mbps": 302.91,
    "fastest_tag": "proxy-us-isp",
    "fastest_mbps": 5.17,
    "speeds": {"proxy-us-isp": 5.17, "proxy-la-isp": 1.31},
    "isp_tag": "proxy-us-isp",
    "is_8k_smooth": False,
    "diag": {
        "proxy-us-isp": {"status": "ok", "ok": 2, "total": 2},
        "proxy-la-isp": {"status": "ok", "ok": 2, "total": 2},
    },
}


def test_format_message_speed_test_result_is_readable():
    title, body = shoutrrr._format_message("isp.speed_test.result", _SPEED_PAYLOAD, "[sb-xray:jp]")
    assert title == "[sb-xray:jp] 📊 ISP 测速结果"
    # 代理模式头部用「带宽最快」(领头者),不再用「选定线路」误导为单一已定线
    assert "带宽最快: proxy-us-isp · 5.17 Mbps" in body
    # 实际选路机制说明(leastPing 按延迟实时选)
    assert "leastPing" in body
    # rating ladder replaces the old binary 8K verdict (5.17 Mbps → 网络较慢)
    assert "评级: 网络较慢" in body
    assert "8K: ⚠️ 不流畅" not in body
    assert "直连基准: 302.91 Mbps" in body
    assert "✓ proxy-us-isp  5.17 Mbps" in body
    assert "✓ proxy-la-isp  1.31 Mbps" in body
    # raw python dict repr must be gone
    assert "{'status'" not in body
    assert "window_sec" not in body
    assert "event:" not in body


def test_format_message_speed_test_marks_failed_tag():
    payload = {
        "isp_tag": "proxy-us-isp",
        "fastest_mbps": 5.0,
        "is_8k_smooth": True,
        "speeds": {"proxy-us-isp": 5.0, "proxy-la-isp": 0.0},
        "diag": {
            "proxy-us-isp": {"status": "ok", "ok": 2, "total": 2},
            "proxy-la-isp": {"status": "timeout", "ok": 0, "total": 2},
        },
    }
    _title, body = shoutrrr._format_message("isp.speed_test.result", payload, "[p]")
    # display is rating-based on fastest_mbps (5.0 → 网络较慢), not the
    # internal is_8k_smooth flag.
    assert "评级: 网络较慢" in body
    assert "✓ proxy-us-isp  5 Mbps" in body
    assert "✗ proxy-la-isp  0 Mbps  (超时 0/2)" in body


def test_send_skips_non_notable_speed_test(monkeypatch, caplog):
    """notify=false speed_test results must not invoke shoutrrr."""
    import logging

    called = MagicMock()
    monkeypatch.setattr(shoutrrr.subprocess, "run", called)
    payload = {**_SPEED_PAYLOAD, "notify": False}
    with caplog.at_level(logging.INFO):
        shoutrrr._send(
            ["telegram://token@telegram?chats=1"], "[p]", "isp.speed_test.result", payload
        )
    called.assert_not_called()
    assert "skipping push" in caplog.text


def test_send_pushes_notable_speed_test(monkeypatch):
    """notify=true (or absent) must reach the shoutrrr CLI."""
    run = MagicMock(return_value=MagicMock(returncode=0, stderr="", stdout=""))
    monkeypatch.setattr(shoutrrr.subprocess, "run", run)
    payload = {**_SPEED_PAYLOAD, "notify": True}
    shoutrrr._send(["telegram://token@telegram?chats=1"], "[p]", "isp.speed_test.result", payload)
    run.assert_called_once()


def test_send_suppresses_secret_refresh_noop(monkeypatch, caplog):
    """secret.refresh.noop is hourly no-change noise — must not page anyone."""
    import logging

    called = MagicMock()
    monkeypatch.setattr(shoutrrr.subprocess, "run", called)
    with caplog.at_level(logging.INFO):
        shoutrrr._send(
            ["telegram://token@telegram?chats=1"],
            "[p]",
            "secret.refresh.noop",
            {"reason": "unchanged"},
        )
    called.assert_not_called()
    assert "skipping push" in caplog.text


def test_send_pushes_secret_refresh_completed(monkeypatch):
    """An actual credential rotation (completed) must still reach shoutrrr."""
    run = MagicMock(return_value=MagicMock(returncode=0, stderr="", stdout=""))
    monkeypatch.setattr(shoutrrr.subprocess, "run", run)
    shoutrrr._send(
        ["telegram://token@telegram?chats=1"],
        "[p]",
        "secret.refresh.completed",
        {"changed": 1, "removed": 0, "restarted": True},
    )
    run.assert_called_once()


def test_send_pushes_secret_refresh_error(monkeypatch):
    """A refresh failure (error) is actionable — must still reach shoutrrr."""
    run = MagicMock(return_value=MagicMock(returncode=0, stderr="", stdout=""))
    monkeypatch.setattr(shoutrrr.subprocess, "run", run)
    shoutrrr._send(
        ["telegram://token@telegram?chats=1"],
        "[p]",
        "secret.refresh.error",
        {"error": "decrypt failed", "stage": "fetch"},
    )
    run.assert_called_once()


def test_format_message_substore_failure_lists_failed_subs():
    payload = {
        "event": "substore.sub_fetch.failed",
        "failed": 2,
        "total": 10,
        "items": [
            {"name": "provider-c", "airport": True, "reason": "HTTP 403"},
            {"name": "node-jp", "airport": False, "reason": "0 节点"},
        ],
    }
    title, body = shoutrrr._format_message("substore.sub_fetch.failed", payload, "[sb-xray:dc99-3]")
    assert title == "[sb-xray:dc99-3] 🔴 订阅拉取失败"
    assert "✗ provider-c (机场) — HTTP 403" in body
    assert "✗ node-jp — 0 节点" in body
    assert "共 2/10 条失败" in body
    assert "event:" not in body


_RETEST_PAYLOAD = {
    "event": "isp.retest.completed",
    "reason": "composition_changed",
    "old_top_tag": "proxy-la-isp",
    "new_top_tag": "proxy-us-isp",
    "delta_pct": 100.0,
    "restarted": True,
}


def test_format_message_retest_completed_switch_is_readable():
    title, body = shoutrrr._format_message(
        "isp.retest.completed", _RETEST_PAYLOAD, "[sb-xray:dc99-3]"
    )
    assert title == "[sb-xray:dc99-3] 🔄 ISP 重测 · 线路已切换"
    assert "线路切换: proxy-la-isp → proxy-us-isp" in body
    assert "原因: 节点集合变化" in body
    assert "已重启 xray/sing-box 生效" in body
    # raw key:value dump must be gone — this is the whole point of the formatter
    assert "reason: composition_changed" not in body
    assert "delta_pct" not in body
    assert "restarted" not in body
    assert "event:" not in body


# Merged retest card: speed summary the cron folds in via payload["speed"]
# (the standalone isp.speed_test.result push is suppressed in the retest path).
_RETEST_SPEED = {
    "isp_tag": "proxy-us-isp",
    "fastest_mbps": 51.56,
    "direct_mbps": 91.91,
    "speeds": {"proxy-us-isp": 51.56, "proxy-la-isp": 20.97},
    "diag": {
        "proxy-us-isp": {"status": "ok", "ok": 2, "total": 2},
        "proxy-la-isp": {"status": "ok", "ok": 2, "total": 2},
    },
}


def test_format_message_retest_noop_merges_speed_and_conclusion():
    payload = {
        "event": "isp.retest.noop",
        "reason": "no_change",
        "top_tag": "proxy-us-isp",
        "delta_pct": 20.27,
        "speed": _RETEST_SPEED,
    }
    title, body = shoutrrr._format_message(
        "isp.retest.noop", payload, "[sb-xray:zgocloud]"
    )
    assert title == "[sb-xray:zgocloud] 🔁 ISP 重测 · 配置未变"
    # speed summary folded in
    assert "带宽最快: proxy-us-isp · 51.56 Mbps" in body
    assert "评级: " in body
    assert "直连基准: 91.91 Mbps" in body
    assert "✓ proxy-us-isp  51.56 Mbps" in body
    assert "✓ proxy-la-isp  20.97 Mbps" in body
    # decision conclusion — 结构性真相,不再凭空捏造「未达切换条件」
    assert "结论: 节点池与路由类别未变,无需重建配置(未重启)" in body
    assert "本次最大带宽波动 20.27%,已由 leastPing 在线吸收" in body
    # 实际选路机制说明
    assert "leastPing" in body
    # 不再出现捏造的切换条件措辞,也不再渲染与头部矛盾的第二个节点名 top_tag
    assert "未达切换条件" not in body
    assert "维持 proxy-la-isp" not in body
    # no raw key:value dump
    assert "top_tag:" not in body
    assert "delta_pct" not in body
    assert "event:" not in body


def test_format_message_retest_noop_without_speed_shows_conclusion_only():
    """disabled / cache-hit / pre-merge payloads carry no speed → conclusion only."""
    payload = {"reason": "no_change", "top_tag": "proxy-us-isp", "delta_pct": 5.0}
    title, body = shoutrrr._format_message("isp.retest.noop", payload, "[p]")
    assert title == "[p] 🔁 ISP 重测 · 配置未变"
    assert body == (
        "结论: 节点池与路由类别未变,无需重建配置(未重启)"
        ";本次最大带宽波动 5%,已由 leastPing 在线吸收"
    )


def test_format_message_retest_noop_disabled_shows_disabled_conclusion():
    """ISP_RETEST_ENABLED=false → noop payload {reason: disabled} → 明确禁用结论。"""
    title, body = shoutrrr._format_message(
        "isp.retest.noop", {"reason": "disabled"}, "[p]"
    )
    assert title == "[p] 🔁 ISP 重测 · 配置未变"
    assert body == "结论: ISP 重测已禁用"


def test_format_message_retest_completed_folds_in_speed():
    payload = {**_RETEST_PAYLOAD, "speed": _RETEST_SPEED}
    _title, body = shoutrrr._format_message("isp.retest.completed", payload, "[p]")
    # switch verdict still leads
    assert "线路切换: proxy-la-isp → proxy-us-isp" in body
    assert "原因: 节点集合变化 · 已重启 xray/sing-box 生效" in body
    # speed summary now appended
    assert "带宽最快: proxy-us-isp · 51.56 Mbps" in body
    assert "✓ proxy-la-isp  20.97 Mbps" in body


def test_format_message_retest_completed_first_run_shows_no_arrow():
    """Empty old_top (first run / lost snapshot) → headline without an arrow."""
    payload = {**_RETEST_PAYLOAD, "old_top_tag": ""}
    _title, body = shoutrrr._format_message("isp.retest.completed", payload, "[p]")
    assert "当前线路: proxy-us-isp" in body
    assert "→" not in body


def test_format_message_retest_completed_class_flip_not_restarted():
    payload = {
        **_RETEST_PAYLOAD,
        "reason": "routing_class_changed",
        "old_top_tag": "direct",
        "restarted": False,
    }
    _title, body = shoutrrr._format_message("isp.retest.completed", payload, "[p]")
    assert "线路切换: direct → proxy-us-isp" in body
    assert "原因: 路由模式切换（直连 ↔ 代理）" in body
    assert "未重启（启动阶段）" in body


def test_post_uses_payload_event_when_no_header(caplog):
    caplog.set_level("INFO", logger="sb_xray.shoutrrr")
    with _ServerThread() as srv:
        conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=2)
        body = json.dumps({"event": "isp.speed_test.result", "isp_tag": "proxy-us-isp"})
        conn.request("POST", "/xray", body=body, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 204
    messages = [r.getMessage() for r in caplog.records]
    assert any("dry-run event=isp.speed_test.result" in m for m in messages)


def test_parse_urls_splits_on_semicolons():
    assert shoutrrr._parse_urls(None) == []
    assert shoutrrr._parse_urls("") == []
    assert shoutrrr._parse_urls("telegram://a;discord://b") == [
        "telegram://a",
        "discord://b",
    ]
    # Empty tokens + whitespace-only tokens are filtered; outer whitespace
    # is preserved on kept tokens (shoutrrr CLI itself handles URL parsing).
    assert shoutrrr._parse_urls(";; telegram://a ;  ; discord://b ;") == [
        " telegram://a ",
        " discord://b ",
    ]


def test_send_dry_run_does_not_spawn_subprocess(monkeypatch, caplog):
    called: list[object] = []

    def _fail(*a, **kw):
        called.append((a, kw))
        raise AssertionError("subprocess.run must not be called in dry-run")

    monkeypatch.setattr(shoutrrr.subprocess, "run", _fail)
    caplog.set_level("INFO", logger="sb_xray.shoutrrr")
    shoutrrr._send(urls=[], title_prefix="[t]", event="ban_bt", payload={"k": "v"})

    assert called == []
    messages = [r.getMessage() for r in caplog.records]
    assert any("dry-run event=ban_bt" in m for m in messages)
    assert any('"k": "v"' in m for m in messages)


def test_send_invokes_shoutrrr_once_per_url(monkeypatch, caplog):
    caplog.set_level("INFO", logger="sb_xray.shoutrrr")
    captured: list[list[str]] = []

    def _fake_run(cmd, **kw):
        captured.append(cmd)

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        return _R()

    monkeypatch.setattr(shoutrrr.subprocess, "run", _fake_run)
    shoutrrr._send(
        urls=["telegram://A", "discord://B"],
        title_prefix="[p]",
        event="ban_ads",
        payload={"email": "x", "source": "1.2.3.4"},
    )

    assert len(captured) == 2
    for cmd in captured:
        assert cmd[0] == "shoutrrr"
        assert cmd[1] == "send"
        assert "--title" in cmd
        assert cmd[cmd.index("--title") + 1] == "[p] 🛡️ 广告/追踪已拦截"
        body = cmd[cmd.index("--message") + 1]
        assert "用户 x" in body
        assert "来源: 1.2.3.4" in body

    # success path should log scheme (not token) + event
    messages = [r.getMessage() for r in caplog.records]
    joined = "\n".join(messages)
    assert any("send ok scheme=telegram event=ban_ads" in m for m in messages)
    assert any("send ok scheme=discord event=ban_ads" in m for m in messages)
    # never leak the full URL (token) into logs
    assert "telegram://A" not in joined
    assert "discord://B" not in joined


def test_send_logs_non_zero_exit_and_stderr(monkeypatch, caplog):
    """Regression: shoutrrr's non-zero exit (e.g. Telegram 'need admin rights',
    exit 69) used to be silently swallowed. Must now surface in the log."""
    caplog.set_level("INFO", logger="sb_xray.shoutrrr")

    def _fake_run(cmd, **kw):
        class _R:
            returncode = 69
            stdout = ""
            stderr = "Bad Request: need administrator rights in the channel chat\n"

        return _R()

    monkeypatch.setattr(shoutrrr.subprocess, "run", _fake_run)
    shoutrrr._send(
        urls=["telegram://SECRET_TOKEN@telegram?chats=-100"],
        title_prefix="[p]",
        event="manual.test",
        payload={"k": "v"},
    )
    messages = [r.getMessage() for r in caplog.records]
    joined = "\n".join(messages)
    assert any("send failed scheme=telegram exit=69" in m for m in messages)
    assert "need administrator rights" in joined
    assert "SECRET_TOKEN" not in joined  # token must never hit the log


def test_send_logs_subprocess_crash(monkeypatch, caplog):
    caplog.set_level("INFO", logger="sb_xray.shoutrrr")

    def _boom(cmd, **kw):
        raise TimeoutError("shoutrrr CLI hung")

    monkeypatch.setattr(shoutrrr.subprocess, "run", _boom)
    shoutrrr._send(
        urls=["telegram://T@telegram?chats=-1"],
        title_prefix="[p]",
        event="e",
        payload={},
    )
    messages = [r.getMessage() for r in caplog.records]
    joined = "\n".join(messages)
    assert any("send crashed scheme=telegram" in m for m in messages)
    assert "shoutrrr CLI hung" in joined


class _ServerThread:
    """Spin up the forwarder in a background thread on a free port."""

    def __init__(self, urls: list[str] | None = None, prefix: str = "[t]") -> None:
        self.port = _free_port()
        self._urls = urls or []
        self._prefix = prefix
        handler = shoutrrr._make_handler(self._urls, self._prefix)
        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> _ServerThread:
        self._thread.start()
        # give the serve loop a moment to bind
        for _ in range(20):
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.05)
        return self

    def __exit__(self, *_exc):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


def test_healthz_returns_200_ok():
    with _ServerThread() as srv:
        conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=2)
        conn.request("GET", "/healthz")
        resp = conn.getresponse()
        body = resp.read()
        assert resp.status == 200
        assert body == b"ok"


def test_get_non_healthz_returns_404():
    with _ServerThread() as srv:
        conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=2)
        conn.request("GET", "/anything")
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 404


def test_post_json_dispatches_event_and_returns_204(caplog):
    caplog.set_level("INFO", logger="sb_xray.shoutrrr")
    with _ServerThread() as srv:
        conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=2)
        payload = {"email": "demo", "source": "198.51.100.42"}
        conn.request(
            "POST",
            "/ban_bt",
            body=json.dumps(payload),
            headers={"Content-Type": "application/json", "X-Event": "ban_bt"},
        )
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 204

    # dry-run path should have logged via sb_xray.shoutrrr logger.
    messages = [r.getMessage() for r in caplog.records]
    assert any("dry-run event=ban_bt" in m for m in messages)


def test_post_bad_json_returns_400():
    with _ServerThread() as srv:
        conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=2)
        conn.request(
            "POST",
            "/ban_bt",
            body="not-json",
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        body = resp.read()
        assert resp.status == 400
        assert b"bad json" in body


def test_run_honors_env_fallbacks(monkeypatch):
    """run() with no args should fall back to env, and we don't start the loop."""
    monkeypatch.setenv("SHOUTRRR_URLS", "telegram://X;discord://Y")
    monkeypatch.setenv("SHOUTRRR_FORWARDER_PORT", "18099")
    monkeypatch.setenv("SHOUTRRR_TITLE_PREFIX", "[envtest]")

    captured: dict[str, object] = {}

    class _FakeServer:
        def __init__(self, addr, handler):
            captured["addr"] = addr
            captured["handler"] = handler

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            captured["closed"] = True

    monkeypatch.setattr(shoutrrr, "ThreadingHTTPServer", _FakeServer)

    rc = shoutrrr.run()
    assert rc == 0
    assert captured["addr"] == ("127.0.0.1", 18099)
    assert captured["closed"] is True


def test_format_message_canary_failed_card():
    payload = {
        "role": "canary",
        "fails": "回国链路端到端",
        "built": "2026-06-09 10:43",
        "runbook": "立即叫停其余 15 台",
    }
    title, body = shoutrrr._format_message("watchtower.canary.failed", payload, "[sb-xray:dc99-3]")
    assert title == "[sb-xray:dc99-3] 🔴 自动更新自检失败"
    assert "节点角色: canary（错峰先行）" in body
    assert "失败项: 回国链路端到端" in body
    assert "镜像构建: 2026-06-09 10:43" in body
    assert "⚠️ 处置" in body
    assert "立即叫停其余 15 台" in body
    assert "sha256" not in body  # 镜像信息是可读构建时间，不是 sha256


def test_format_message_canary_failed_worker_role_and_omits_blank_fails():
    payload = {"role": "worker", "built": "2026-06-09 10:43"}
    title, body = shoutrrr._format_message("watchtower.canary.failed", payload, "[p]")
    assert title == "[p] 🔴 自动更新自检失败"
    assert "节点角色: worker（本台）" in body
    assert "失败项:" not in body  # 缺 fails 字段整行省略
    assert "⚠️ 处置" not in body  # 缺 runbook 不渲染处置块


def test_format_message_canary_updated_card():
    payload = {"role": "canary", "built": "2026-06-09 10:43"}
    title, body = shoutrrr._format_message("watchtower.canary.updated", payload, "[sb-xray:dc99-3]")
    assert title == "[sb-xray:dc99-3] ✅ 已自动更新"
    assert "镜像构建: 2026-06-09 10:43" in body
    assert "四项自检全部通过" in body


def test_format_message_canary_missing_built_shows_unknown():
    _title, body = shoutrrr._format_message("watchtower.canary.updated", {"role": "canary"}, "[p]")
    assert "镜像构建: 未知" in body


def test_format_message_canary_updated_falls_back_to_new_digest():
    # 旧版脚本只发 old/new（无 built）；formatter 须回退到 new digest，绝不显示「未知」。
    payload = {"role": "worker", "old": "x@sha256:aaa", "new": "currycan/sb-xray@sha256:bbb"}
    _title, body = shoutrrr._format_message("watchtower.canary.updated", payload, "[p]")
    assert "镜像构建: currycan/sb-xray@sha256:bbb" in body
    assert "未知" not in body


def test_format_message_canary_failed_falls_back_to_image_digest():
    # 失败事件脚本发 image（无 built）；formatter 回退到 image，不显示「未知」。
    payload = {"role": "worker", "fails": "回国链路端到端", "image": "currycan/sb-xray@sha256:ccc"}
    _title, body = shoutrrr._format_message("watchtower.canary.failed", payload, "[p]")
    assert "镜像构建: currycan/sb-xray@sha256:ccc" in body
    assert "未知" not in body
