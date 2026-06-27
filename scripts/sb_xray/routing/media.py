"""Streaming / AI reachability probes (entrypoint.sh §11 equivalent).

Services are split by **account-risk class**, because "should this go direct
or through the residential ISP proxy?" has two different answers:

- **Account-sensitive** (chatgpt / social / tiktok / gemini / claude): the risk
  is *account bans*, which can't be probed. So we stay maximally conservative —
  home-broadband IP → ``direct``; anything else → fallback (``isp-auto``), no probe.
- **Streaming-unlock** (netflix / disney / youtube): the risk is *can this IP
  unlock the catalog?* A datacenter IP may well unlock, so it's worth probing.
  We GET the page and inspect the body (a HEAD 200 can hide a block/captcha page).

Unified decision order per service (highest priority first, short-circuit on hit):

    L1  is_restricted_region(GEOIP_INFO) → fallback        ← top-level safety net
    L2  IP_TYPE == "isp" (home broadband) → "direct"
    L3  non-residential, by class:
          account-sensitive → fallback (hardcoded, no probe)
          streaming-unlock  → GET probe; REAL → "direct", else → fallback

The restricted-region guard sits *above* the residential short-circuit on
purpose: a home-broadband node that happens to sit in a censored region must
not send these services out ``direct`` (censorship + account risk).
"""

from __future__ import annotations

import os

from sb_xray import http as sbhttp
from sb_xray.events import emit_event
from sb_xray.network import get_fallback_proxy, is_restricted_region
from sb_xray.routing.service_spec import SERVICE_SPECS, SPECS_BY_ENV, ContentSignature, ServiceSpec

# Streaming-unlock classify verdicts.
_REAL = "REAL"
_BLOCKED = "BLOCKED"
_UNREACHABLE = "UNREACHABLE"
_UNKNOWN = "UNKNOWN"


def _is_residential() -> bool:
    return os.environ.get("IP_TYPE", "unknown") == "isp"


def _classify(result: sbhttp.FetchResult, sig: ContentSignature) -> str:
    """Classify a fetched streaming page. BLOCKED wins over REAL because a
    block page can still contain brand keywords; anything unmatched is
    UNKNOWN (caller treats it as fail-safe → fallback)."""
    if result.status < 200 or result.status >= 400:
        return _UNREACHABLE
    body = result.body
    if any(s in body for s in sig.blocked_substrings):
        return _BLOCKED
    if any(p.search(result.final_url) for p in sig.compiled_url_patterns):
        return _BLOCKED
    if sig.real_substrings and any(s in body for s in sig.real_substrings):
        return _REAL
    # marker 可能落在 64 KiB 截断点之外 → 无法证明是 REAL,fail-safe 判 BLOCKED
    # 走住宅回退,而非 UNKNOWN（UNKNOWN 同样回退,但显式 BLOCKED 让事件总线可观测）
    if result.truncated:
        return _BLOCKED
    return _UNKNOWN


def classify_signature(spec: ServiceSpec) -> str:
    """Fetch ``spec.probe_url`` and return a raw verdict string.

    Reusable B-class kernel shared by ``_streaming_unlock`` (live routing) and
    the C3 self-check (signature-rot detection). A spec with no signature can
    never produce REAL, so it short-circuits to UNKNOWN without any HTTP — the
    same fail-safe stance ``_streaming_unlock`` already takes.
    """
    if spec.signature is None:
        return _UNKNOWN
    return _classify(sbhttp.fetch(spec.probe_url), spec.signature)


def _account_sensitive() -> str:
    """A-class decision: no probe. Restricted region or non-residential →
    fallback; only an unrestricted home-broadband IP earns ``direct``."""
    if is_restricted_region():
        return get_fallback_proxy()
    if _is_residential():
        return "direct"
    return get_fallback_proxy()


def _streaming_unlock(env_var: str) -> str:
    """B-class decision: restricted/residential short-circuits first, then a
    body-reading GET decides unlock. Only a REAL verdict earns ``direct``."""
    if is_restricted_region():
        return get_fallback_proxy()
    if _is_residential():
        return "direct"
    spec = SPECS_BY_ENV[env_var]
    verdict = classify_signature(spec)
    return "direct" if verdict == _REAL else get_fallback_proxy()


# ---- Streaming-unlock services (B-class: probed) ---------------------------


def check_netflix() -> str:
    return _streaming_unlock("NETFLIX_OUT")


def check_disney() -> str:
    return _streaming_unlock("DISNEY_OUT")


def check_youtube() -> str:
    return _streaming_unlock("YOUTUBE_OUT")


# ---- Account-sensitive services (A-class: hardcoded, no probe) -------------


def check_social_media() -> str:
    return _account_sensitive()


def check_tiktok() -> str:
    return _account_sensitive()


def check_chatgpt() -> str:
    return _account_sensitive()


def check_claude() -> str:
    return _account_sensitive()


def check_gemini() -> str:
    return _account_sensitive()


def run_signature_self_check() -> int:
    """Fetch each B-class probe URL and flag rotted content signatures.

    Pure observation: for every spec carrying a ``ContentSignature``, GET the
    real page and reclassify. A *reachable* page (status 200-399) that yields
    UNKNOWN means our markers no longer match the live markup — the signature
    has rotted and B-class routing has silently degraded to fail-safe fallback
    with no alert. We emit ``routing.signature.rot`` so operators see it.

    Routing is untouched — this never changes a verdict, it only reports. An
    unreachable probe (status < 200) is NOT a rot (the IP just can't reach it),
    so it raises no event. Returns the rot count (0 = all signatures healthy).
    """
    rot = 0
    for spec in SERVICE_SPECS:
        if spec.signature is None:
            continue
        result = sbhttp.fetch(spec.probe_url)
        if result.status < 200 or result.status >= 400:
            continue
        verdict = _classify(result, spec.signature)
        if verdict == _UNKNOWN:
            rot += 1
            emit_event(
                "routing.signature.rot",
                {
                    "service": spec.slug,
                    "probe_url": spec.probe_url,
                    "verdict": verdict,
                    "status": result.status,
                },
            )
    return rot


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
