"""Tests for scripts/sb_xray/shoutrrr.py (event-bus HTTP receiver)."""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from http.server import ThreadingHTTPServer

from sb_xray import shoutrrr


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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
        assert cmd[cmd.index("--title") + 1] == "[p] ban_ads"
        body = cmd[cmd.index("--message") + 1]
        assert "email: x" in body
        assert "source: 1.2.3.4" in body

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
