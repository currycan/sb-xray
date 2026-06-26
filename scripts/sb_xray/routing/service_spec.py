"""Central registry of streaming / AI service specs (Phase 4).

Single source of truth shared by:
- ``sb_xray.routing.media``    — probe each service for unlock status
- ``sb_xray.routing.isp``      — build per-service sing-box urltest balancers
- ``sb_xray.config_builder``   — snapshot/override ``*_OUT`` during sb.json render

Each ServiceSpec declares:
- ``env_var``: ``*_OUT`` env name (NETFLIX_OUT, CHATGPT_OUT, …)
- ``sb_tag``:  the per-service sing-box balancer tag (``isp-auto-<slug>``)
- ``probe_url``: HTTPS endpoint for sing-box urltest probing
- ``slug``: lowercase identifier used in tag derivation / logging

The single-balancer (legacy) tag is ``isp-auto``. When
``ISP_PER_SERVICE_SB=true``, the renderer overrides the service's
``*_OUT`` env so sb.json splices the per-service tag instead.

Xray keeps the legacy single-observatory/single-balancer model because
``observatory`` is a global singleton (one probe URL for the entire
xray instance); multi-balancer-per-service is structurally impossible
without a second xray process.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final


@dataclass(frozen=True)
class ContentSignature:
    """Body / final-URL fingerprints that classify a fetched streaming page.

    Used only by ``media._streaming_unlock`` (B-class services). A match in
    ``blocked_substrings`` / ``blocked_url_patterns`` wins over ``real_substrings``
    (a block page can still contain brand words). When nothing matches, the
    verdict is UNKNOWN and the caller routes through the residential fallback
    (fail-safe), so a stale/incomplete signature degrades to "slower but safe".

    Each literal carries an evidence + observation-date comment at its call
    site so signatures can be re-verified when a site changes its markup.
    """

    real_substrings: tuple[str, ...] = ()
    blocked_substrings: tuple[str, ...] = ()
    blocked_url_patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class ServiceSpec:
    env_var: str
    slug: str
    probe_url: str
    # Set only for B-class streaming services (netflix/disney/youtube) that
    # are probed for unlock. None for account-sensitive services, which are
    # never probed (see media.py risk-class split).
    signature: ContentSignature | None = field(default=None)

    @property
    def sb_tag(self) -> str:
        return f"isp-auto-{self.slug}"


# Order mirrors sb.json rule sections + routing/isp._SERVICE_SPEC tuples
# so any service listed here is covered by both probe logic and routing
# rule generation. The social/tiktok/isp-lumped entries point at the
# shared "isp-auto" via an empty slug — handled explicitly in the
# override logic (no separate balancer, uses the legacy tag).
# NOTE on signatures below: values are knowledge-based starting points
# (observed 2026-06-10) and MUST be re-verified against real datacenter-IP
# response bodies on a VPS before relying on `direct` routing (plan §8.5).
# The conservative UNKNOWN→fallback default makes a wrong signature fail safe
# (routes through the residential proxy), never fail open.
SERVICE_SPECS: Final[tuple[ServiceSpec, ...]] = (
    ServiceSpec(
        env_var="NETFLIX_OUT",
        slug="netflix",
        probe_url="https://www.netflix.com/title/81249783",
        signature=ContentSignature(
            # VERIFIED 2026-06-10 on dc99-3 + cstonecloud (both datacenter IPs):
            # a real, playable title page carries `netflix.reactContext` (SPA
            # bootstrap) + `playerModel` (title playback metadata). NOT usable as
            # real markers: `Pardon`/`Sorry` — they appear on the real page too.
            real_substrings=("netflix.reactContext", "playerModel"),
            # Netflix's documented proxy/geo block errors (checked before REAL).
            blocked_substrings=("M7111", "NSEZ-403", "unblocker or proxy"),
        ),
    ),
    ServiceSpec(
        env_var="DISNEY_OUT",
        slug="disney",
        probe_url="https://www.disneyplus.com/",
        signature=ContentSignature(
            real_substrings=("disneyplus", "__NEXT_DATA__"),
            blocked_substrings=("not available in your region", "Disney+ is not available"),
        ),
    ),
    ServiceSpec(
        env_var="YOUTUBE_OUT",
        slug="youtube",
        probe_url="https://www.youtube.com/",
        signature=ContentSignature(
            # `ytcfg.set` / `ytInitialData` mark the real app shell; absent on
            # Google "sorry" / captcha interstitials.
            real_substrings=("ytcfg.set", "ytInitialData"),
            blocked_substrings=("Just a moment", "unusual traffic"),
            blocked_url_patterns=(r"/sorry/", r"consent\.youtube\.com"),
        ),
    ),
    ServiceSpec(
        env_var="CHATGPT_OUT",
        slug="openai",
        probe_url="https://chatgpt.com",
    ),
    ServiceSpec(
        env_var="CLAUDE_OUT",
        slug="claude",
        probe_url="https://claude.ai/login",
    ),
    ServiceSpec(
        env_var="GEMINI_OUT",
        slug="gemini",
        probe_url="https://gemini.google.com/app",
    ),
)

# env_var → spec lookup for media decision (unique env_vars).
SPECS_BY_ENV: Final[dict[str, ServiceSpec]] = {s.env_var: s for s in SERVICE_SPECS}


def service_env_vars() -> frozenset[str]:
    """``*_OUT`` env vars declared in the central registry.

    The single source of truth for the C4 superset invariant: any env var here
    MUST also appear in ``routing.isp._SERVICE_SPEC`` so the xray rule tuples
    cover every probed service (drift = a service with no xray routing rule).
    """
    return frozenset(s.env_var for s in SERVICE_SPECS)


def per_service_enabled() -> bool:
    """Read ``ISP_PER_SERVICE_SB`` as a boolean; default off for safety."""
    import os

    return os.environ.get("ISP_PER_SERVICE_SB", "").strip().lower() == "true"
