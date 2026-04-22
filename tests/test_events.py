"""Tests for sb_xray.events.emit_event (Phase 2)."""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock

import httpx
import pytest
from sb_xray import events


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("SHOUTRRR_URLS", "SHOUTRRR_FORWARDER_PORT", "ISP_EVENTS_ENABLED"):
        monkeypatch.delenv(k, raising=False)


def test_stdout_always_even_without_shoutrrr(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.INFO, logger="sb_xray.events")
    post = MagicMock()
    monkeypatch.setattr(httpx, "post", post)

    events.emit_event("isp.speed_test.result", {"fastest": "proxy-cn2", "mbps": 100.5})

    assert any("event=isp.speed_test.result" in r.message for r in caplog.records)
    post.assert_not_called()


def test_http_fanout_when_shoutrrr_urls_set(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.INFO, logger="sb_xray.events")
    monkeypatch.setenv("SHOUTRRR_URLS", "telegram://token@chat")
    post = MagicMock()
    monkeypatch.setattr(httpx, "post", post)

    events.emit_event("isp.retest.noop", {"reason": "no_delta"})

    post.assert_called_once()
    args, kwargs = post.call_args
    url = args[0] if args else kwargs["url"]
    assert url == "http://127.0.0.1:18085/xray"
    body = json.loads(kwargs["content"])
    assert body == {"event": "isp.retest.noop", "reason": "no_delta"}


def test_http_failure_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHOUTRRR_URLS", "telegram://token@chat")

    def _boom(*_a: object, **_kw: object) -> None:
        raise httpx.ConnectError("forwarder down")

    monkeypatch.setattr(httpx, "post", _boom)
    # Must not raise — guarantee for callers in hot paths.
    events.emit_event("isp.whatever", {"k": "v"})


def test_non_serialisable_payload_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    post = MagicMock()
    monkeypatch.setattr(httpx, "post", post)

    class _X:
        def __repr__(self) -> str:
            return "<x>"

    # Circular reference defeats default=str — triggers ValueError path.
    a: dict[str, object] = {}
    a["self"] = a
    events.emit_event("isp.bad", a)
    post.assert_not_called()


def test_invalid_event_name_rejected(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.WARNING, logger="sb_xray.events")
    post = MagicMock()
    monkeypatch.setattr(httpx, "post", post)

    events.emit_event("ISP.BAD", {"k": "v"})
    events.emit_event("", {"k": "v"})
    events.emit_event("isp spaces bad", {"k": "v"})

    post.assert_not_called()
    assert sum("rejected invalid event name" in r.message for r in caplog.records) == 3


def test_disabled_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ISP_EVENTS_ENABLED", "false")
    monkeypatch.setenv("SHOUTRRR_URLS", "telegram://token@chat")
    post = MagicMock()
    monkeypatch.setattr(httpx, "post", post)

    events.emit_event("isp.ignored", {})
    post.assert_not_called()


def test_respects_shoutrrr_forwarder_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHOUTRRR_URLS", "telegram://x")
    monkeypatch.setenv("SHOUTRRR_FORWARDER_PORT", "19999")
    post = MagicMock()
    monkeypatch.setattr(httpx, "post", post)

    events.emit_event("isp.port.custom", {})

    url = post.call_args.args[0] if post.call_args.args else post.call_args.kwargs["url"]
    assert url == "http://127.0.0.1:19999/xray"
