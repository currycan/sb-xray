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

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class ServiceSpec:
    env_var: str
    slug: str
    probe_url: str

    @property
    def sb_tag(self) -> str:
        return f"isp-auto-{self.slug}"


# Order mirrors sb.json rule sections + routing/isp._SERVICE_SPEC tuples
# so any service listed here is covered by both probe logic and routing
# rule generation. The social/tiktok/isp-lumped entries point at the
# shared "isp-auto" via an empty slug — handled explicitly in the
# override logic (no separate balancer, uses the legacy tag).
SERVICE_SPECS: Final[tuple[ServiceSpec, ...]] = (
    ServiceSpec(
        env_var="NETFLIX_OUT",
        slug="netflix",
        probe_url="https://www.netflix.com/title/81249783",
    ),
    ServiceSpec(
        env_var="DISNEY_OUT",
        slug="disney",
        probe_url="https://www.disneyplus.com/",
    ),
    ServiceSpec(
        env_var="YOUTUBE_OUT",
        slug="youtube",
        probe_url="https://www.youtube.com/",
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


def per_service_enabled() -> bool:
    """Read ``ISP_PER_SERVICE_SB`` as a boolean; default off for safety."""
    import os

    return os.environ.get("ISP_PER_SERVICE_SB", "").strip().lower() == "true"
