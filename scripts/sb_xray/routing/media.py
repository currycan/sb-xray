"""Streaming / AI reachability probes (entrypoint.sh §11 equivalent)."""

from __future__ import annotations

import os
import re

from sb_xray import http as sbhttp
from sb_xray.network import get_fallback_proxy, is_restricted_region

_OK_RE = re.compile(r"^[23]")


def _is_residential() -> bool:
    return os.environ.get("IP_TYPE", "unknown") == "isp"


def _probe_direct_or_fallback(url: str, *, follow: bool = True) -> str:
    """HEAD ``url``; 2xx/3xx → ``direct``; otherwise ``get_fallback_proxy``."""
    code = sbhttp.probe(url, follow=follow)
    if _OK_RE.match(code):
        return "direct"
    return get_fallback_proxy()


# ---- Netflix / Disney / YouTube (simple HEAD probe) ------------------------


def check_netflix() -> str:
    if _is_residential():
        return "direct"
    return _probe_direct_or_fallback("https://www.netflix.com/title/81249783", follow=True)


def check_disney() -> str:
    if _is_residential():
        return "direct"
    return _probe_direct_or_fallback("https://www.disneyplus.com/", follow=True)


def check_youtube() -> str:
    if _is_residential():
        return "direct"
    return _probe_direct_or_fallback("https://www.youtube.com/", follow=True)


# ---- Social / TikTok (no HTTP, pure env) -----------------------------------


def check_social_media() -> str:
    if is_restricted_region():
        return get_fallback_proxy()
    if not _is_residential():
        return get_fallback_proxy()
    return "direct"


def check_tiktok() -> str:
    if is_restricted_region():
        return get_fallback_proxy()
    if not _is_residential():
        return get_fallback_proxy()
    return "direct"


# ---- ChatGPT / Claude / Gemini (restricted-first) --------------------------


def check_chatgpt() -> str:
    if is_restricted_region():
        return get_fallback_proxy()
    if _is_residential():
        return "direct"
    return _probe_direct_or_fallback("https://chatgpt.com", follow=False)


def check_claude() -> str:
    if is_restricted_region():
        return get_fallback_proxy()
    if _is_residential():
        return "direct"
    final = sbhttp.trace_url("https://claude.ai/login")
    if not final or re.search(r"claude\.ai/(login|chats)", final):
        return "direct"
    return get_fallback_proxy()


def check_gemini() -> str:
    override = os.environ.get("GEMINI_DIRECT", "")
    if override == "true":
        return "direct"
    if override == "false":
        return get_fallback_proxy()
    if is_restricted_region():
        return get_fallback_proxy()
    if _is_residential():
        return "direct"
    return _probe_direct_or_fallback("https://gemini.google.com/app", follow=True)


# ---- aggregate --------------------------------------------------------------


def check_all() -> dict[str, str]:
    """Run all 8 probes and map them to ENV-style keys that downstream
    stages (routing/isp, subscription) already consume."""
    return {
        "NETFLIX_OUT": check_netflix(),
        "DISNEY_OUT": check_disney(),
        "YOUTUBE_OUT": check_youtube(),
        "SOCIAL_MEDIA_OUT": check_social_media(),
        "TIKTOK_OUT": check_tiktok(),
        "CHATGPT_OUT": check_chatgpt(),
        "CLAUDE_OUT": check_claude(),
        "GEMINI_OUT": check_gemini(),
    }
