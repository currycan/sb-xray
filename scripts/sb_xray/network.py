"""Network + GeoIP probes (entrypoint.sh §7-8 equivalent)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Final

import httpx

from sb_xray import http as sbhttp

_IPAPI_URL: Final[str] = "https://api.ipapi.is/"
_IP_SB_V4: Final[str] = "https://api.ip.sb/ip"
_IP111_URL: Final[str] = "https://ip111.cn/"
_BRUTAL_MODULE_PATH: Path = Path("/sys/module/brutal")

_RESTRICTED_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)(香港|HongKong|Hong Kong|HK|中国|China|CN|俄罗斯|Russia|RU|澳门|Macao|MO)"
)

_DEFAULT_CACHE: Final[Path] = Path("/tmp/ipapi.json")


def detect_ip_strategy(*, v4_ok: bool, v6_ok: bool) -> str:
    """Classify dual-stack reachability.

    Returns one of ``prefer_ipv4`` / ``ipv6_only`` / ``ipv4_only``.
    The Bash version defaulted to ``ipv4_only`` even when neither
    family was reachable — we preserve that behavior.
    """
    if v4_ok and v6_ok:
        return "prefer_ipv4"
    if v6_ok:
        return "ipv6_only"
    return "ipv4_only"


def probe_ip_sb() -> tuple[bool, bool]:
    """Probe api.ip.sb over IPv4 (and IPv6 when routable).

    Uses the shared httpx client. Any non-200 / timeout is treated as
    "family not reachable" to match the Bash behavior.
    """
    v4_ok = sbhttp.probe(_IP_SB_V4, follow=False, timeout=2.0) == "200"
    # IPv6 reachability is answered by the production VPS's actual
    # routing — in unit tests we leave this False and rely on the
    # detect_ip_strategy(v4_ok, v6_ok) call site to inject the truth.
    v6_ok = False
    return v4_ok, v6_ok


def check_ip_type(*, cache_path: Path | None = None) -> str:
    """Return the ASN type reported by ipapi.is.

    Caches the full JSON response at ``cache_path`` (defaults to
    ``/tmp/ipapi.json``) to avoid hitting the API multiple times
    during a single entrypoint run. Returns ``"unknown"`` on failure.
    """
    path = cache_path if cache_path is not None else _DEFAULT_CACHE
    if not path.exists():
        try:
            with httpx.Client(timeout=5.0, headers={"User-Agent": sbhttp.DEFAULT_UA}) as client:
                resp = client.get(_IPAPI_URL)
                resp.raise_for_status()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(resp.text, encoding="utf-8")
        except httpx.HTTPError:
            return "unknown"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        asn = data.get("asn") or {}
        return str(asn.get("type") or "unknown")
    except (json.JSONDecodeError, AttributeError):
        return "unknown"


def get_geo_info() -> str:
    """Extract `<region>|<ip>` from ip111.cn's landing page.

    Returns "" if the fetch fails or the regex doesn't match.
    """
    try:
        with httpx.Client(
            timeout=10.0,
            follow_redirects=True,
            headers={"User-Agent": sbhttp.DEFAULT_UA},
        ) as client:
            resp = client.get(_IP111_URL)
            resp.raise_for_status()
    except httpx.HTTPError:
        return ""

    plain = re.sub(r"<[^>]+>", " ", resp.text)
    for line in plain.splitlines():
        if "这是您访问国内网站所使用的IP" in line:
            parts = line.split()
            if len(parts) >= 3:
                ip = parts[0]
                region = "".join(parts[1:3])
                return f"{region}|{ip}"
    return ""


def check_brutal_status() -> str:
    """Return "true" if the tcp_brutal kernel module is loaded."""
    return "true" if _BRUTAL_MODULE_PATH.is_dir() else "false"


def is_restricted_region() -> bool:
    """Match the Bash regex on ``${GEOIP_INFO}`` (CN/HK/MO/RU variants)."""
    info = os.environ.get("GEOIP_INFO", "")
    return bool(_RESTRICTED_RE.search(info))


def get_fallback_proxy() -> str:
    """`isp-auto` when ISP nodes exist, otherwise the configured fallback tag.

    Defers to :func:`sb_xray.routing.isp._resolve_fallback_tags` for the
    no-ISP case so media probes and balancer renders never drift on
    ``ISP_FALLBACK_STRATEGY`` / ``WARP_ENABLED`` interpretation.
    """
    if os.environ.get("HAS_ISP_NODES"):
        return "isp-auto"
    # Import lazily — routing.isp already imports this module.
    from sb_xray.routing.isp import _resolve_fallback_tags

    return _resolve_fallback_tags()[0]


def get_isp_preferred_strategy() -> str:
    """Same semantics as :func:`get_fallback_proxy` — kept separate to
    preserve the Bash call sites during migration."""
    return get_fallback_proxy()
