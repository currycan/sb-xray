"""ISP routing + outbound JSON builders (entrypoint.sh §10 equivalent)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Final

from sb_xray.network import is_restricted_region

logger = logging.getLogger(__name__)

_SMOOTH_THRESHOLD_MBPS: Final[float] = 100.0

# Probe configuration. Cloudflare's 1 MiB `__down` endpoint is the new
# default: it is globally CDN-fronted, returns HTTP 200, and streams a
# small payload so `urltest`/`observatory` RTT measurements carry a
# bandwidth signal — throttled ISPs naturally rank lower instead of
# being invisible (as they were with the 0-byte `generate_204`).
_DEFAULT_PROBE_URL: Final[str] = "https://speed.cloudflare.com/__down?bytes=1048576"
_DEFAULT_PROBE_INTERVAL: Final[str] = "1m"
_DEFAULT_PROBE_TOLERANCE_MS: Final[int] = 300


@dataclass(frozen=True)
class ProbeConfig:
    url: str
    interval: str
    tolerance_ms: int


def _resolve_probe_config(
    *,
    url: str | None = None,
    interval: str | None = None,
    tolerance_ms: int | None = None,
) -> ProbeConfig:
    """Resolve probe settings from explicit kwargs → env → defaults.

    Explicit kwargs win (unit tests); otherwise we read ``ISP_PROBE_URL``,
    ``ISP_PROBE_INTERVAL`` and ``ISP_PROBE_TOLERANCE_MS``. Empty string
    env values are treated as "unset" so operators can't accidentally
    wipe the default by setting e.g. ``ISP_PROBE_URL=`` in docker-compose.
    """
    resolved_url = url or os.environ.get("ISP_PROBE_URL") or _DEFAULT_PROBE_URL
    resolved_interval = interval or os.environ.get("ISP_PROBE_INTERVAL") or _DEFAULT_PROBE_INTERVAL
    if tolerance_ms is None:
        raw = os.environ.get("ISP_PROBE_TOLERANCE_MS", "").strip()
        try:
            tolerance_ms = int(raw) if raw else _DEFAULT_PROBE_TOLERANCE_MS
        except ValueError:
            logger.warning(
                "invalid ISP_PROBE_TOLERANCE_MS=%r — falling back to %d",
                raw,
                _DEFAULT_PROBE_TOLERANCE_MS,
            )
            tolerance_ms = _DEFAULT_PROBE_TOLERANCE_MS
    return ProbeConfig(
        url=resolved_url,
        interval=resolved_interval,
        tolerance_ms=tolerance_ms,
    )


# ``geosite:*`` entries + env-var names that carry the override outbound.
# The last tuple (multi-domain) mirrors the original Bash JSON literal.
_SERVICE_SPEC: Final[tuple[tuple[tuple[str, ...], str, bool], ...]] = (
    (("geosite:openai",), "CHATGPT_OUT", True),  # has marktag
    (("geosite:netflix",), "NETFLIX_OUT", False),
    (("geosite:disney",), "DISNEY_OUT", False),
    (("geosite:anthropic",), "CLAUDE_OUT", False),
    (("geosite:google",), "GEMINI_OUT", False),
    (("geosite:google-gemini",), "GEMINI_OUT", False),
    (("geosite:youtube",), "YOUTUBE_OUT", False),
    (("geosite:category-social-media-!cn",), "SOCIAL_MEDIA_OUT", False),
    (("geosite:tiktok",), "TIKTOK_OUT", False),
    (("geosite:amazon", "geosite:paypal", "geosite:ebay"), "ISP_OUT", False),
)


@dataclass(frozen=True)
class RoutingContext:
    ip_type: str  # "isp" | "hosting" | "unknown"
    geoip_info: str  # "<region>|<ip>"
    default_isp: str  # "" | e.g. "CN2_ISP"
    direct_speed: float  # Mbps
    fastest_proxy_tag: str | None
    proxy_max_speed: float


@dataclass(frozen=True)
class IspDecision:
    isp_tag: str
    is_8k_smooth: bool


# ---- outbound JSON builders -------------------------------------------------


def process_single_isp(
    *,
    prefix: str,
    ip: str,
    port: int,
    user: str,
    password: str,
    tag: str,
) -> tuple[str, str]:
    """Return ``(xray_outbound_json, singbox_outbound_json)``.

    Both outputs are single-line JSON strings ready to be joined by the
    Jinja2 template. ``prefix`` is kept in the signature for future
    log/tag correlation but isn't embedded in the JSON itself (mirrors
    the Bash call contract).
    """
    del prefix  # currently unused but kept for call-site parity
    xray = json.dumps(
        {
            "tag": tag,
            "protocol": "socks",
            "settings": {
                "servers": [
                    {
                        "address": ip,
                        "port": port,
                        "users": [{"user": user, "pass": password}],
                    }
                ]
            },
        },
        ensure_ascii=False,
    )
    sb = json.dumps(
        {
            "type": "socks",
            "tag": tag,
            "server": ip,
            "server_port": port,
            "username": user,
            "password": password,
        },
        ensure_ascii=False,
    )
    return xray, sb


def _sort_tags_desc(speeds: dict[str, float]) -> list[str]:
    return [t for t, _ in sorted(speeds.items(), key=lambda kv: kv[1], reverse=True)]


def build_sb_urltest(
    speeds: dict[str, float],
    *,
    probe: ProbeConfig | None = None,
) -> str:
    """Sing-box urltest outbound JSON fragment (empty when no ISP nodes).

    The return value is spliced verbatim into ``templates/sing-box/sb.json``
    between ``${SB_CUSTOM_OUTBOUNDS}`` and the literal ``{"type":"block",
    ...}`` entry::

        "outbounds": [
            {"type":"direct","tag":"direct"},
            ${SB_CUSTOM_OUTBOUNDS}       ← ends with ",\\n"
            ${SB_ISP_URLTEST}            ← THIS fragment, must also end with ","
            {"type":"block","tag":"block"}
        ]

    So a non-empty return MUST carry a trailing comma to keep the
    outer array valid JSON (same contract as ``build_xray_balancer``
    fragments). Empty speeds → "" (bash parity; no placeholder, no
    trailing comma either).
    """
    if not speeds:
        return ""
    cfg = probe or _resolve_probe_config()
    outbounds = [*_sort_tags_desc(speeds), "direct"]
    payload = json.dumps(
        {
            "type": "urltest",
            "tag": "isp-auto",
            "outbounds": outbounds,
            "url": cfg.url,
            "interval": cfg.interval,
            "tolerance": cfg.tolerance_ms,
            "interrupt_exist_connections": True,
        },
        ensure_ascii=False,
    )
    return f"{payload},"


def build_xray_balancer(
    speeds: dict[str, float],
    *,
    probe: ProbeConfig | None = None,
) -> tuple[str, str]:
    """Xray observatory + balancer JSON fragments (each trailing comma)."""
    if not speeds:
        return "", ""
    cfg = probe or _resolve_probe_config()
    selector = _sort_tags_desc(speeds)
    observatory = json.dumps(
        {
            "observatory": {
                "subjectSelector": selector,
                "probeUrl": cfg.url,
                "probeInterval": cfg.interval,
                "enableConcurrency": True,
            }
        },
        ensure_ascii=False,
    )
    balancer = json.dumps(
        {
            "balancers": [
                {
                    "tag": "isp-auto",
                    "selector": selector,
                    "fallbackTag": "direct",
                    "strategy": {"type": "leastPing"},
                }
            ]
        },
        ensure_ascii=False,
    )
    # Strip the outer `{}` so the caller can splice into xr.json directly
    # ``.strip('{}')`` previously ate the *inner* closing brace too
    # (e.g. ``...true}}`` → ``...true``), producing invalid JSON once
    # the fragment was spliced back. Peel exactly one outer pair.
    observatory_inner = _unwrap_outer_braces(observatory)
    balancer_inner = _unwrap_outer_braces(balancer)
    return f"{observatory_inner},", f"{balancer_inner},"


def _unwrap_outer_braces(text: str) -> str:
    """Return ``text`` with one leading ``{`` + one trailing ``}`` peeled.

    Raises ``ValueError`` on malformed input — callers hand us
    ``json.dumps`` output so this should never fire in practice.
    """
    stripped = text.strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        raise ValueError(f"expected JSON object wrapper, got {stripped!r}")
    return stripped[1:-1].strip()


def build_xray_service_rules(*, outbounds: dict[str, str]) -> str:
    """Build Xray ``rules[]`` entries for streaming / AI services.

    ``outbounds`` is a flat map ``{ENV_VAR_NAME: outbound_or_balancer_tag}``.
    Missing entries default to ``direct``. Returned string terminates with
    a comma to match the Bash template expectation.
    """
    rules: list[str] = []
    for domains, env_name, has_marktag in _SERVICE_SPEC:
        out_val = outbounds.get(env_name) or "direct"
        rule: dict[str, object] = {
            "type": "field",
            "domain": list(domains),
        }
        if has_marktag:
            rule["marktag"] = "fix_openai"
        if out_val == "isp-auto":
            rule["balancerTag"] = "isp-auto"
        else:
            rule["outboundTag"] = out_val
        rules.append(json.dumps(rule, ensure_ascii=False))
    return ",".join(rules) + ","


# ---- apply_isp_routing_logic ------------------------------------------------


def _restricted_by_geoip(geoip_info: str) -> bool:
    """Run is_restricted_region with an isolated GEOIP_INFO value."""
    prev = os.environ.get("GEOIP_INFO")
    os.environ["GEOIP_INFO"] = geoip_info
    try:
        return is_restricted_region()
    finally:
        if prev is None:
            os.environ.pop("GEOIP_INFO", None)
        else:
            os.environ["GEOIP_INFO"] = prev


def _manual_isp_tag(default_isp: str) -> str:
    """Translate e.g. ``"AWS TOKYO_ISP"`` → ``"proxy-aws-tokyo"``."""
    cleaned = default_isp
    if cleaned.endswith("_ISP"):
        cleaned = cleaned[: -len("_ISP")]
    slug = cleaned.lower()
    for ch in (" ", "_"):
        slug = slug.replace(ch, "-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return f"proxy-{slug.strip('-')}"


def apply_isp_routing_logic(ctx: RoutingContext) -> IspDecision:
    """Produce ``ISP_TAG`` + ``IS_8K_SMOOTH`` from context.

    Priority:
      1. ``DEFAULT_ISP`` set → lock to ``proxy-<slug>`` unconditionally.
      2. Restricted region OR non-``isp`` IP → require proxy;
         fastest tag exists → use it, else fall back to direct.
      3. Residential ISP IP + unrestricted region → direct.
    """
    if ctx.default_isp:
        isp_tag = _manual_isp_tag(ctx.default_isp)
    elif _restricted_by_geoip(ctx.geoip_info) or ctx.ip_type != "isp":
        isp_tag = ctx.fastest_proxy_tag or "direct"
    else:
        isp_tag = "direct"

    ref_speed = ctx.proxy_max_speed if isp_tag != "direct" else ctx.direct_speed
    is_smooth = ref_speed > _SMOOTH_THRESHOLD_MBPS
    return IspDecision(isp_tag=isp_tag, is_8k_smooth=is_smooth)


# ---- Stage 4: build_client_and_server_configs -------------------------------


def _prefix_to_tag(prefix: str) -> str:
    slug = prefix.lower().replace("_", "-").replace(" ", "-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return f"proxy-{slug.strip('-')}"


def _discover_isp_nodes_with_tags() -> dict[str, tuple[str, str, str, str, str]]:
    """Return ``{tag: (prefix, ip, port, user, password)}`` from env."""
    nodes: dict[str, tuple[str, str, str, str, str]] = {}
    for key, value in os.environ.items():
        if not key.endswith("_ISP_IP") or not value:
            continue
        prefix = key[: -len("_IP")]
        port = os.environ.get(f"{prefix}_PORT", "").strip().strip("'\"")
        if not port:
            continue
        value = value.strip().strip("'\"")
        user = os.environ.get(f"{prefix}_USER", "")
        password = os.environ.get(f"{prefix}_SECRET", "")
        nodes[_prefix_to_tag(prefix)] = (prefix, value, port, user, password)
    return nodes


def build_client_and_server_configs(*, speeds: dict[str, float] | None = None) -> dict[str, str]:
    """Port of ``build_client_and_server_configs`` (entrypoint.sh:1264).

    Produces the six env vars the JSON templates consume:
      - ``CUSTOM_OUTBOUNDS`` / ``SB_CUSTOM_OUTBOUNDS``
      - ``SB_ISP_URLTEST`` (sing-box)
      - ``XRAY_OBSERVATORY_SECTION`` / ``XRAY_BALANCERS_SECTION`` (xray)
      - ``XRAY_SERVICE_RULES`` (dynamic media routing rules)

    Also sets ``ISP_IP`` / ``ISP_PORT`` / ``ISP_USER`` / ``ISP_SECRET`` to
    the currently fastest node's connection info (legacy consumers).
    """
    from sb_xray import speed_test as sbspeed

    if speeds is None:
        speeds = sbspeed.load_isp_speeds()

    for v in (
        "CUSTOM_OUTBOUNDS",
        "SB_CUSTOM_OUTBOUNDS",
        "SB_ISP_URLTEST",
        "XRAY_OBSERVATORY_SECTION",
        "XRAY_BALANCERS_SECTION",
        "XRAY_SERVICE_RULES",
    ):
        os.environ[v] = ""

    nodes_by_tag = _discover_isp_nodes_with_tags()
    has_isp_nodes = bool(os.environ.get("HAS_ISP_NODES"))
    fastest_tag = os.environ.get("FASTEST_PROXY_TAG", "")

    xray_parts: list[str] = []
    sb_parts: list[str] = []

    if has_isp_nodes and speeds:
        sorted_tags = [t for t, _ in sorted(speeds.items(), key=lambda kv: kv[1], reverse=True)]
        for tag in sorted_tags:
            node = nodes_by_tag.get(tag)
            if node is None:
                continue
            prefix, ip, port, user, password = node

            if tag == fastest_tag:
                os.environ["ISP_IP"] = ip
                os.environ["ISP_PORT"] = port
                os.environ["ISP_USER"] = user
                os.environ["ISP_SECRET"] = password

            xray_json, sb_json = process_single_isp(
                prefix=prefix,
                ip=ip,
                port=int(port),
                user=user,
                password=password,
                tag=tag,
            )
            xray_parts.append(xray_json + ",\n")
            sb_parts.append(sb_json + ",\n")
            logger.info("注入出站: %s (%.2f Mbps)", tag, speeds.get(tag, 0))

    custom_out = "".join(xray_parts)
    sb_custom_out = "".join(sb_parts)
    probe = _resolve_probe_config()
    urltest = build_sb_urltest(speeds, probe=probe) if has_isp_nodes else ""
    observatory, balancer = build_xray_balancer(speeds, probe=probe) if has_isp_nodes else ("", "")
    if has_isp_nodes and speeds:
        logger.info(
            "balancer configured: probe=%s interval=%s tolerance=%dms nodes=%d",
            probe.url,
            probe.interval,
            probe.tolerance_ms,
            len(speeds),
        )
    service_rules = build_xray_service_rules(
        outbounds={
            "CHATGPT_OUT": os.environ.get("CHATGPT_OUT", ""),
            "NETFLIX_OUT": os.environ.get("NETFLIX_OUT", ""),
            "DISNEY_OUT": os.environ.get("DISNEY_OUT", ""),
            "CLAUDE_OUT": os.environ.get("CLAUDE_OUT", ""),
            "GEMINI_OUT": os.environ.get("GEMINI_OUT", ""),
            "YOUTUBE_OUT": os.environ.get("YOUTUBE_OUT", ""),
            "SOCIAL_MEDIA_OUT": os.environ.get("SOCIAL_MEDIA_OUT", ""),
            "TIKTOK_OUT": os.environ.get("TIKTOK_OUT", ""),
            "ISP_OUT": os.environ.get("ISP_OUT", ""),
        }
    )

    result = {
        "CUSTOM_OUTBOUNDS": custom_out,
        "SB_CUSTOM_OUTBOUNDS": sb_custom_out,
        "SB_ISP_URLTEST": urltest,
        "XRAY_OBSERVATORY_SECTION": observatory,
        "XRAY_BALANCERS_SECTION": balancer,
        "XRAY_SERVICE_RULES": service_rules,
    }
    for k, v in result.items():
        os.environ[k] = v
    return result
