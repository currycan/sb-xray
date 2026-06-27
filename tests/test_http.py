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


@respx.mock
def test_fetch_returns_status_body_and_final_url() -> None:
    respx.get("https://example.test/page").mock(
        return_value=httpx.Response(200, text="<html>hello</html>")
    )
    result = sbhttp.fetch("https://example.test/page")
    assert result.status == 200
    assert "hello" in result.body
    assert result.final_url == "https://example.test/page"


@respx.mock
def test_fetch_follows_redirect_to_final_url() -> None:
    respx.get("https://example.test/go").mock(
        return_value=httpx.Response(302, headers={"location": "https://example.test/dest"})
    )
    respx.get("https://example.test/dest").mock(return_value=httpx.Response(200, text="ok"))
    result = sbhttp.fetch("https://example.test/go")
    assert result.status == 200
    assert result.final_url == "https://example.test/dest"


@respx.mock
def test_fetch_returns_minus_one_on_network_error() -> None:
    respx.get("https://example.test/down").mock(side_effect=httpx.ConnectError("refused"))
    result = sbhttp.fetch("https://example.test/down")
    assert result.status == -1
    assert result.body == ""
    assert result.final_url == ""


@respx.mock
def test_fetch_truncates_large_body() -> None:
    huge = "x" * (200 * 1024)
    respx.get("https://example.test/big").mock(return_value=httpx.Response(200, text=huge))
    result = sbhttp.fetch("https://example.test/big")
    assert len(result.body.encode("utf-8")) <= sbhttp._MAX_BODY_BYTES


def test_user_agent_is_mozilla() -> None:
    assert "Mozilla" in sbhttp.DEFAULT_UA


def test_default_timeout_is_3_seconds() -> None:
    assert sbhttp.PROBE_TIMEOUT == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_probe_async(respx_mock: respx.MockRouter) -> None:
    respx_mock.head("https://async.test/ok").mock(return_value=httpx.Response(204))
    assert await sbhttp.probe_async("https://async.test/ok") == "204"


@respx.mock
def test_fetch_sets_truncated_when_body_exceeds_cap() -> None:
    big = "A" * (70 * 1024)  # > 64 KiB cap
    respx.get("https://example.test/big").mock(return_value=httpx.Response(200, text=big))
    res = sbhttp.fetch("https://example.test/big")
    assert res.truncated is True
    assert len(res.body) == sbhttp._MAX_BODY_BYTES


@respx.mock
def test_fetch_not_truncated_for_small_body() -> None:
    respx.get("https://example.test/small").mock(return_value=httpx.Response(200, text="hi"))
    res = sbhttp.fetch("https://example.test/small")
    assert res.truncated is False


@respx.mock
def test_fetch_error_result_not_truncated() -> None:
    respx.get("https://example.test/err").mock(side_effect=httpx.ConnectError("x"))
    res = sbhttp.fetch("https://example.test/err")
    assert res == sbhttp.FetchResult(status=-1, body="", final_url="", truncated=False)
