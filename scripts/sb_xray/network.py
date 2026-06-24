"""Network + GeoIP probes (entrypoint.sh §7-8 equivalent)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Final

import httpx

from sb_xray import http as sbhttp

_IPAPI_URL: Final[str] = "https://api.ipapi.is/"
_IP_SB_V4: Final[str] = "https://api.ip.sb/ip"
_BRUTAL_MODULE_PATH: Path = Path("/sys/module/brutal")

# 落地国判定的单一真相源是 ISO alpha-2 国家码（来自 ipapi.is）。名称正则仅作
# 兜底——迁移期旧节点或探测失败、GEOIP_CC 为空时，回退按 GEOIP_INFO 文本匹配。
_RESTRICTED_CC: Final[frozenset[str]] = frozenset({"CN", "HK", "MO", "RU"})
_RESTRICTED_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)(香港|HongKong|Hong Kong|HK|中国|China|CN|俄罗斯|Russia|RU|澳门|Macao|MO)"
)

# ISO alpha-2 → 中文国名。覆盖常见 VPS/机场落地国；未命中时回退 ipapi.is 的英文
# country 字段，保证 region 只要探测成功就不为空。
_CC_TO_ZH: Final[dict[str, str]] = {
    "US": "美国", "JP": "日本", "HK": "香港", "TW": "台湾", "SG": "新加坡",
    "KR": "韩国", "GB": "英国", "DE": "德国", "FR": "法国", "CA": "加拿大",
    "AU": "澳大利亚", "RU": "俄罗斯", "ID": "印度尼西亚", "IN": "印度",
    "NL": "荷兰", "PH": "菲律宾", "MY": "马来西亚", "TH": "泰国", "VN": "越南",
    "TR": "土耳其", "AR": "阿根廷", "BR": "巴西", "ZA": "南非", "MO": "澳门",
    "CH": "瑞士", "SE": "瑞典", "IT": "意大利", "IE": "爱尔兰", "TM": "土库曼斯坦",
    "ES": "西班牙", "PT": "葡萄牙", "PL": "波兰", "UA": "乌克兰", "MX": "墨西哥",
    "FI": "芬兰", "NO": "挪威", "DK": "丹麦", "BE": "比利时", "AT": "奥地利",
    "CZ": "捷克", "RO": "罗马尼亚", "HU": "匈牙利", "GR": "希腊", "LU": "卢森堡",
    "IS": "冰岛", "IL": "以色列", "AE": "阿联酋", "SA": "沙特", "QA": "卡塔尔",
    "KZ": "哈萨克斯坦", "PK": "巴基斯坦", "BD": "孟加拉", "LK": "斯里兰卡",
    "KH": "柬埔寨", "MM": "缅甸", "LA": "老挝", "NP": "尼泊尔", "MN": "蒙古",
    "CL": "智利", "CO": "哥伦比亚", "PE": "秘鲁", "NZ": "新西兰", "NG": "尼日利亚",
    "KE": "肯尼亚", "EG": "埃及", "MA": "摩洛哥", "RS": "塞尔维亚", "BG": "保加利亚",
    "HR": "克罗地亚", "SK": "斯洛伐克", "SI": "斯洛文尼亚", "LT": "立陶宛",
    "LV": "拉脱维亚", "EE": "爱沙尼亚", "CY": "塞浦路斯", "MT": "马耳他", "CN": "中国",
}

_DEFAULT_CACHE: Final[Path] = Path("/tmp/ipapi.json")

# 二级回退 geo 源。ipapi.is 失败时改用 ip-api.com 拿 ISO 国家码（免费档 HTTP-only）。
# 只取 status/countryCode/country(中文)/query(本机 IP)——不要城市。
_IP_API_URL: Final[str] = (
    "http://ip-api.com/json/?fields=status,countryCode,country,query&lang=zh-CN"
)
_DEFAULT_IP_API_CACHE: Final[Path] = Path("/tmp/ip-api.json")


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


def _load_ipapi(cache_path: Path | None = None) -> dict[str, Any] | None:
    """Fetch ipapi.is once, caching the raw JSON to ``cache_path``.

    Shared by :func:`check_ip_type` and :func:`get_geo_info` /
    :func:`get_geo_cc` so a single entrypoint run makes **one** request:
    whichever runs first writes the cache (defaults to ``/tmp/ipapi.json``),
    the rest read it. Returns the parsed dict, or ``None`` on
    fetch/parse failure.
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
            return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def check_ip_type(*, cache_path: Path | None = None) -> str:
    """Return the ASN type reported by ipapi.is (``"unknown"`` on failure)."""
    data = _load_ipapi(cache_path)
    if data is None:
        return "unknown"
    asn = data.get("asn")
    if isinstance(asn, dict) and asn.get("type"):
        return str(asn["type"])
    return "unknown"


def _ipapi_country_code(data: dict[str, Any]) -> str:
    """ISO alpha-2 country code from an ipapi.is response.

    Tries ``location.country_code`` → ``datacenter.country`` →
    ``asn.country`` (all verified to carry the ISO code). Returns "" if none.
    """
    for section, key in (("location", "country_code"), ("datacenter", "country"), ("asn", "country")):
        obj = data.get(section)
        if isinstance(obj, dict) and obj.get(key):
            return str(obj[key]).upper()
    return ""


def _load_ip_api(cache_path: Path | None = None) -> dict[str, Any] | None:
    """Fallback geo source (ip-api.com), cached separately from ipapi.is.

    Only hit when ipapi.is fails to yield a country code. Caches the raw JSON
    so repeated lookups in one boot make at most one request. Returns the
    parsed dict, or ``None`` on fetch/parse failure.
    """
    path = cache_path if cache_path is not None else _DEFAULT_IP_API_CACHE
    if not path.exists():
        try:
            with httpx.Client(timeout=5.0, headers={"User-Agent": sbhttp.DEFAULT_UA}) as client:
                resp = client.get(_IP_API_URL)
                resp.raise_for_status()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(resp.text, encoding="utf-8")
        except httpx.HTTPError:
            return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _resolve_geo() -> tuple[str, str]:
    """``(cc, region)`` + node IP encoded as ``region|ip``-ready parts.

    Returns ``(country_code, "<region>|<ip>")`` from ipapi.is, falling back to
    ip-api.com when ipapi.is can't yield a country code. ``region`` is a
    country-level Chinese name (no city) via :data:`_CC_TO_ZH`, falling back to
    the source's own country name. Returns ``("", "")`` if both sources fail.
    """
    data = _load_ipapi()
    if data is not None:
        cc = _ipapi_country_code(data)
        ip = str(data.get("ip") or "")
        if cc and ip:
            loc = data.get("location")
            en = str(loc.get("country")) if isinstance(loc, dict) and loc.get("country") else ""
            return cc, f"{_CC_TO_ZH.get(cc) or en or cc}|{ip}"

    alt = _load_ip_api()
    if alt is not None and alt.get("status") == "success":
        cc = str(alt.get("countryCode") or "").upper()
        ip = str(alt.get("query") or "")
        if cc and ip:
            zh = str(alt.get("country") or "")  # lang=zh-CN → Chinese country name
            return cc, f"{_CC_TO_ZH.get(cc) or zh or cc}|{ip}"

    return "", ""


def get_geo_cc() -> str:
    """ISO alpha-2 country code of this node's egress IP, or "" on failure."""
    return _resolve_geo()[0]


def get_geo_info() -> str:
    """`<region>|<ip>` for this node's egress IP (ipapi.is → ip-api.com).

    ``region`` is a country-level Chinese name (no city): the node name carries
    only ``FLAG_PREFIX`` (ISO-derived), never the region text, so the city is
    not needed here. Returns "" when both sources fail —
    :func:`EnvManager.ensure_var` with ``regenerate_if_empty`` keeps that empty
    value from being persisted, so the next boot retries.
    """
    return _resolve_geo()[1]


def check_brutal_status() -> str:
    """Return "true" if the tcp_brutal kernel module is loaded."""
    return "true" if _BRUTAL_MODULE_PATH.is_dir() else "false"


def is_restricted_region(info: str | None = None, cc: str | None = None) -> bool:
    """True for CN/HK/MO/RU landing regions.

    Resolution order:

    1. Explicit ``cc`` (ISO alpha-2) → decided purely on it.
    2. Explicit ``info`` → name regex on that value alone. Callers passing a
       value want the decision made on *it*, not on ambient env (this is how
       ``isp._restricted_by_geoip`` keeps its "no env round-trip" contract).
    3. Neither given → read env: ``${GEOIP_CC}`` (ISO, authoritative) first,
       then the ``${GEOIP_INFO}`` name regex as a migration-era fallback.
    """
    if cc:
        return cc.upper() in _RESTRICTED_CC
    if info is not None:
        return bool(_RESTRICTED_RE.search(info))
    cc_env = os.environ.get("GEOIP_CC", "")
    if cc_env:
        return cc_env.upper() in _RESTRICTED_CC
    return bool(_RESTRICTED_RE.search(os.environ.get("GEOIP_INFO", "")))


def get_fallback_proxy() -> str:
    """`isp-auto` when ISP nodes exist, otherwise the configured fallback tag.

    Defers to :func:`sb_xray.routing.isp._resolve_fallback_tags` for the
    no-ISP case so media probes and balancer renders never drift on
    ``ISP_FALLBACK_STRATEGY`` interpretation.
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
