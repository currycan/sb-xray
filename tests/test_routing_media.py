"""Tests for sb_xray.routing.media (risk-class media routing).

Services split into two classes with different decision rules:
- account-sensitive (chatgpt/social/tiktok/gemini/claude): no probe;
  home-broadband → direct, else → fallback.
- streaming-unlock (netflix/disney/youtube): GET body classify; REAL → direct.

The restricted-region guard is the top-level safety net (above the residential
short-circuit), so a home-broadband node in a censored region still falls back.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
import respx
from sb_xray.routing import media
from sb_xray.routing.service_spec import SPECS_BY_ENV, ContentSignature

_ALL_CHECKS: list[Callable[[], str]] = [
    media.check_netflix,
    media.check_disney,
    media.check_youtube,
    media.check_social_media,
    media.check_tiktok,
    media.check_chatgpt,
    media.check_claude,
    media.check_gemini,
]

_ACCOUNT_SENSITIVE: list[Callable[[], str]] = [
    media.check_social_media,
    media.check_tiktok,
    media.check_chatgpt,
    media.check_claude,
    media.check_gemini,
]


@pytest.fixture(autouse=True)
def _clear_routing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("IP_TYPE", "GEOIP_INFO", "GEOIP_CC", "HAS_ISP_NODES", "GEMINI_DIRECT"):
        monkeypatch.delenv(var, raising=False)


# ---- L2 residential short-circuit ------------------------------------------


@pytest.mark.parametrize("fn", _ALL_CHECKS)
def test_residential_unrestricted_returns_direct(
    fn: Callable[[], str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("IP_TYPE", "isp")
    assert fn() == "direct"


# ---- L1 restricted region = top-level safety net (beats residential) -------


@pytest.mark.parametrize("fn", _ALL_CHECKS)
def test_restricted_region_overrides_residential(
    fn: Callable[[], str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Home broadband IP that nonetheless sits in a censored region must NOT
    # go direct — the restricted guard short-circuits to fallback first.
    monkeypatch.setenv("IP_TYPE", "isp")
    monkeypatch.setenv("GEOIP_INFO", "中国 CN|192.0.2.5")
    monkeypatch.setenv("HAS_ISP_NODES", "1")
    assert fn() == "isp-auto"


# ---- A-class: account-sensitive, no probe ----------------------------------


@pytest.mark.parametrize("fn", _ACCOUNT_SENSITIVE)
def test_account_sensitive_non_residential_fallback(
    fn: Callable[[], str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("IP_TYPE", "hosting")
    monkeypatch.setenv("HAS_ISP_NODES", "1")
    assert fn() == "isp-auto"


@respx.mock
@pytest.mark.parametrize("fn", _ACCOUNT_SENSITIVE)
def test_account_sensitive_never_probes(
    fn: Callable[[], str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # No routes registered: any HTTP call would raise. A-class must not probe.
    monkeypatch.setenv("IP_TYPE", "hosting")
    monkeypatch.setenv("HAS_ISP_NODES", "1")
    assert fn() == "isp-auto"


def test_social_and_tiktok_are_identical(monkeypatch: pytest.MonkeyPatch) -> None:
    for ip_type in ("isp", "hosting"):
        monkeypatch.setenv("IP_TYPE", ip_type)
        monkeypatch.setenv("HAS_ISP_NODES", "1")
        assert media.check_social_media() == media.check_tiktok()


# ---- B-class: streaming unlock via GET body classify -----------------------


@respx.mock
def test_streaming_real_body_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IP_TYPE", "hosting")
    spec = SPECS_BY_ENV["YOUTUBE_OUT"]
    respx.get(spec.probe_url).mock(
        return_value=httpx.Response(200, text="<script>ytcfg.set({});</script>")
    )
    assert media.check_youtube() == "direct"


@respx.mock
def test_streaming_blocked_body_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IP_TYPE", "hosting")
    monkeypatch.setenv("HAS_ISP_NODES", "1")
    spec = SPECS_BY_ENV["YOUTUBE_OUT"]
    # 200 but a captcha interstitial that even contains a real marker — BLOCKED
    # must win over REAL.
    respx.get(spec.probe_url).mock(
        return_value=httpx.Response(200, text="Just a moment... ytcfg.set")
    )
    assert media.check_youtube() == "isp-auto"


@respx.mock
def test_streaming_unknown_body_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IP_TYPE", "hosting")
    monkeypatch.setenv("HAS_ISP_NODES", "1")
    spec = SPECS_BY_ENV["NETFLIX_OUT"]
    respx.get(spec.probe_url).mock(return_value=httpx.Response(200, text="<html>nothing</html>"))
    assert media.check_netflix() == "isp-auto"


@respx.mock
def test_streaming_unreachable_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IP_TYPE", "hosting")
    monkeypatch.setenv("HAS_ISP_NODES", "1")
    spec = SPECS_BY_ENV["DISNEY_OUT"]
    respx.get(spec.probe_url).mock(side_effect=httpx.ConnectError("down"))
    assert media.check_disney() == "isp-auto"


@respx.mock
def test_streaming_4xx_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IP_TYPE", "hosting")
    monkeypatch.setenv("HAS_ISP_NODES", "1")
    spec = SPECS_BY_ENV["NETFLIX_OUT"]
    respx.get(spec.probe_url).mock(
        return_value=httpx.Response(403, text="playerModel")  # real marker but blocked status
    )
    assert media.check_netflix() == "isp-auto"


# ---- _classify unit coverage (incl. blocked_url_patterns) ------------------


def test_classify_blocked_url_pattern() -> None:
    sig = ContentSignature(
        real_substrings=("ok",),
        blocked_url_patterns=(r"/sorry/",),
    )
    result = sbhttp_result(status=200, body="ok", final_url="https://www.google.com/sorry/index")
    assert media._classify(result, sig) == media._BLOCKED


def test_classify_blocked_over_real() -> None:
    sig = ContentSignature(real_substrings=("brand",), blocked_substrings=("blocked",))
    result = sbhttp_result(status=200, body="brand ... blocked", final_url="https://x/")
    assert media._classify(result, sig) == media._BLOCKED


def test_classify_network_failure_unreachable() -> None:
    sig = ContentSignature(real_substrings=("brand",))
    result = sbhttp_result(status=-1, body="", final_url="")
    assert media._classify(result, sig) == media._UNREACHABLE


def sbhttp_result(*, status: int, body: str, final_url: str) -> media.sbhttp.FetchResult:
    return media.sbhttp.FetchResult(status=status, body=body, final_url=final_url)


# ---- gemini override (L0) --------------------------------------------------


def test_gemini_override_true_beats_restricted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_DIRECT", "true")
    monkeypatch.setenv("GEOIP_INFO", "中国 CN|192.0.2.5")
    assert media.check_gemini() == "direct"


def test_gemini_override_false_beats_residential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_DIRECT", "false")
    monkeypatch.setenv("IP_TYPE", "isp")
    monkeypatch.setenv("HAS_ISP_NODES", "1")
    assert media.check_gemini() == "isp-auto"


# ---- check_all contract ----------------------------------------------------


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


def test_streaming_specs_have_real_signature() -> None:
    # Every B-class spec must carry at least one real substring, else it can
    # never reach a REAL verdict and would be permanently fallback.
    for env_var in ("NETFLIX_OUT", "DISNEY_OUT", "YOUTUBE_OUT"):
        sig = SPECS_BY_ENV[env_var].signature
        assert sig is not None and sig.real_substrings
