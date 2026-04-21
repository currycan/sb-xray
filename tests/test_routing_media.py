"""Tests for sb_xray.routing.media (entrypoint.sh §11 equivalent)."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
import respx
from sb_xray.routing import media


@pytest.fixture(autouse=True)
def _clear_routing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("IP_TYPE", "GEOIP_INFO", "HAS_ISP_NODES", "GEMINI_DIRECT"):
        monkeypatch.delenv(var, raising=False)


# ---- short-circuit for residential IP (isp) -------------------------------


@pytest.mark.parametrize(
    "fn",
    [
        media.check_netflix,
        media.check_disney,
        media.check_youtube,
        media.check_chatgpt,
        media.check_claude,
        media.check_gemini,
    ],
)
def test_residential_ip_returns_direct(
    fn: Callable[[], str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("IP_TYPE", "isp")
    assert fn() == "direct"


# ---- Netflix / Disney / YouTube --------------------------------------------


@respx.mock
def test_netflix_direct_on_2xx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IP_TYPE", "hosting")
    respx.head("https://www.netflix.com/title/81249783").mock(return_value=httpx.Response(200))
    assert media.check_netflix() == "direct"


@respx.mock
def test_netflix_fallback_on_4xx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IP_TYPE", "hosting")
    monkeypatch.setenv("HAS_ISP_NODES", "1")
    respx.head("https://www.netflix.com/title/81249783").mock(return_value=httpx.Response(403))
    assert media.check_netflix() == "isp-auto"


@respx.mock
def test_disney_direct_on_3xx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IP_TYPE", "hosting")
    # 302 without location = httpx stops following, code stays 3xx
    respx.head("https://www.disneyplus.com/").mock(return_value=httpx.Response(302))
    assert media.check_disney() == "direct"


@respx.mock
def test_youtube_fallback_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IP_TYPE", "hosting")
    respx.head("https://www.youtube.com/").mock(side_effect=httpx.ConnectError("down"))
    # No HAS_ISP_NODES → direct fallback
    assert media.check_youtube() == "direct"


# ---- Social / TikTok: env-only checks --------------------------------------


def test_social_restricted_region_uses_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEOIP_INFO", "香港 HK|192.0.2.1")
    monkeypatch.setenv("HAS_ISP_NODES", "1")
    assert media.check_social_media() == "isp-auto"


def test_social_hosting_ip_uses_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IP_TYPE", "hosting")
    monkeypatch.setenv("HAS_ISP_NODES", "1")
    assert media.check_social_media() == "isp-auto"


def test_social_residential_unrestricted_direct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IP_TYPE", "isp")
    monkeypatch.setenv("GEOIP_INFO", "Tokyo JP|203.0.113.1")
    assert media.check_social_media() == "direct"


def test_tiktok_restricted_uses_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEOIP_INFO", "中国 CN|192.0.2.5")
    monkeypatch.setenv("HAS_ISP_NODES", "1")
    assert media.check_tiktok() == "isp-auto"


# ---- ChatGPT ---------------------------------------------------------------


def test_chatgpt_restricted_immediate_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEOIP_INFO", "中国 CN|192.0.2.5")
    monkeypatch.setenv("IP_TYPE", "hosting")
    monkeypatch.setenv("HAS_ISP_NODES", "1")
    assert media.check_chatgpt() == "isp-auto"


@respx.mock
def test_chatgpt_direct_on_2xx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IP_TYPE", "hosting")
    respx.head("https://chatgpt.com").mock(return_value=httpx.Response(200))
    assert media.check_chatgpt() == "direct"


# ---- Claude ----------------------------------------------------------------


def test_claude_restricted_region(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEOIP_INFO", "中国 CN|192.0.2.5")
    monkeypatch.setenv("IP_TYPE", "hosting")
    monkeypatch.setenv("HAS_ISP_NODES", "1")
    assert media.check_claude() == "isp-auto"


def test_claude_direct_when_redirect_to_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IP_TYPE", "hosting")
    monkeypatch.setattr(media.sbhttp, "trace_url", lambda _url: "https://claude.ai/login")
    assert media.check_claude() == "direct"


def test_claude_fallback_when_unexpected_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IP_TYPE", "hosting")
    monkeypatch.setenv("HAS_ISP_NODES", "1")
    monkeypatch.setattr(media.sbhttp, "trace_url", lambda _url: "https://restricted.example/")
    assert media.check_claude() == "isp-auto"


# ---- Gemini override -------------------------------------------------------


def test_gemini_direct_override_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_DIRECT", "true")
    monkeypatch.setenv("GEOIP_INFO", "中国 CN|192.0.2.5")
    assert media.check_gemini() == "direct"


def test_gemini_direct_override_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_DIRECT", "false")
    monkeypatch.setenv("HAS_ISP_NODES", "1")
    monkeypatch.setenv("IP_TYPE", "isp")
    assert media.check_gemini() == "isp-auto"


@respx.mock
def test_gemini_probe_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IP_TYPE", "hosting")
    respx.head("https://gemini.google.com/app").mock(return_value=httpx.Response(200))
    assert media.check_gemini() == "direct"


# ---- check_all -------------------------------------------------------------


def test_check_all_returns_8_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IP_TYPE", "isp")
    result = media.check_all()
    assert set(result.keys()) == {
        "NETFLIX_OUT",
        "DISNEY_OUT",
        "YOUTUBE_OUT",
        "SOCIAL_MEDIA_OUT",
        "TIKTOK_OUT",
        "CHATGPT_OUT",
        "CLAUDE_OUT",
        "GEMINI_OUT",
    }
    assert all(v == "direct" for v in result.values())
