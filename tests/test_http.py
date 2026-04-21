"""Tests for sb_xray.http (entrypoint.sh §3 equivalent)."""

from __future__ import annotations

import httpx
import pytest
import respx
from sb_xray import http as sbhttp


@respx.mock
def test_probe_returns_status_code_string() -> None:
    respx.head("https://example.test/ok").mock(return_value=httpx.Response(200))
    assert sbhttp.probe("https://example.test/ok") == "200"


@respx.mock
def test_probe_returns_timeout_on_connect_error() -> None:
    respx.head("https://example.test/down").mock(side_effect=httpx.ConnectError("refused"))
    assert sbhttp.probe("https://example.test/down") == "Timeout"


@respx.mock
def test_probe_returns_timeout_on_timeout_exc() -> None:
    respx.head("https://example.test/slow").mock(side_effect=httpx.ReadTimeout("slow"))
    assert sbhttp.probe("https://example.test/slow") == "Timeout"


@respx.mock
def test_probe_without_follow_returns_3xx() -> None:
    respx.head("https://example.test/redir").mock(
        return_value=httpx.Response(302, headers={"location": "https://example.test/final"})
    )
    assert sbhttp.probe("https://example.test/redir", follow=False) == "302"


@respx.mock
def test_probe_with_follow_returns_final_status() -> None:
    respx.head("https://example.test/redir").mock(
        return_value=httpx.Response(302, headers={"location": "https://example.test/final"})
    )
    respx.head("https://example.test/final").mock(return_value=httpx.Response(200))
    assert sbhttp.probe("https://example.test/redir", follow=True) == "200"


@respx.mock
def test_trace_url_returns_final_location_on_redirect() -> None:
    respx.head("https://example.test/go").mock(
        return_value=httpx.Response(302, headers={"location": "https://example.test/dest"})
    )
    respx.head("https://example.test/dest").mock(return_value=httpx.Response(200))
    assert sbhttp.trace_url("https://example.test/go") == "https://example.test/dest"


@respx.mock
def test_trace_url_returns_input_when_no_redirect() -> None:
    respx.head("https://example.test/stay").mock(return_value=httpx.Response(200))
    assert sbhttp.trace_url("https://example.test/stay") == "https://example.test/stay"


@respx.mock
def test_trace_url_returns_empty_on_failure() -> None:
    respx.head("https://example.test/err").mock(side_effect=httpx.ConnectError("nope"))
    assert sbhttp.trace_url("https://example.test/err") == ""


def test_user_agent_is_mozilla() -> None:
    assert "Mozilla" in sbhttp.DEFAULT_UA


def test_default_timeout_is_3_seconds() -> None:
    assert sbhttp.PROBE_TIMEOUT == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_probe_async(respx_mock: respx.MockRouter) -> None:
    respx_mock.head("https://async.test/ok").mock(return_value=httpx.Response(204))
    assert await sbhttp.probe_async("https://async.test/ok") == "204"
