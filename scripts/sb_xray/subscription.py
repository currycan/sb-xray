"""Client-subscription URL builders (show-config.sh §generate_links).

Each builder reads ``os.environ`` and emits a single protocol URL.
``write_subscriptions`` aggregates the set into two base64-encoded
files — ``v2rayn`` (full feature set) and ``v2rayn-compat``
(drops ``mlkem768`` so mihomo / sing-box clients still connect).
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import urllib.parse
from pathlib import Path


def urlquote(value: str) -> str:
    """``urllib.parse.quote`` preserving ``-._~`` (matches Bash default)."""
    return urllib.parse.quote(value, safe="-._~")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _remark(protocol: str) -> str:
    prefix = _env("FLAG_PREFIX")
    region = _env("REGION_NAME")
    suffix = _env("NODE_SUFFIX")
    return f"{prefix}{protocol} ✈ {region}{suffix}"


# ---- Hysteria2 --------------------------------------------------------------


def build_hysteria2_link() -> str:
    domain = _env("DOMAIN")
    port = _env("PORT_HYSTERIA2")
    password = _env("SB_UUID")
    query = urllib.parse.urlencode({"sni": domain, "alpn": "h3"})
    remark = urlquote(_remark("Hy2"))
    return f"hysteria2://{password}@{domain}:{port}?{query}#{remark}"


# ---- TUIC -------------------------------------------------------------------


def build_tuic_link() -> str:
    domain = _env("DOMAIN")
    port = _env("PORT_TUIC")
    uuid = _env("SB_UUID")
    password = _env("SB_UUID")
    query = urllib.parse.urlencode({"sni": domain, "alpn": "h3", "congestion_control": "bbr"})
    remark = urlquote(_remark("TUIC"))
    return f"tuic://{uuid}:{password}@{domain}:{port}?{query}#{remark}"


# ---- AnyTLS -----------------------------------------------------------------


def build_anytls_link() -> str:
    domain = _env("DOMAIN")
    port = _env("PORT_ANYTLS")
    uuid = _env("SB_UUID")
    query = urllib.parse.urlencode({"security": "tls", "type": "tcp"})
    remark = urlquote(_remark("AnyTLS"))
    return f"anytls://{uuid}@{domain}:{port}?{query}#{remark}"


# ---- VMess (JSON envelope base64 encoded) ----------------------------------


def build_vmess_link() -> str:
    payload = {
        "v": "2",
        "ps": _remark("VMess"),
        "add": _env("CDNDOMAIN"),
        "port": _env("LISTENING_PORT"),
        "id": _env("XRAY_UUID"),
        "aid": "0",
        "net": "ws",
        "type": "none",
        "host": _env("CDNDOMAIN"),
        "path": f"/{_env('XRAY_URL_PATH')}-vmess",
        "tls": "tls",
        "sni": _env("CDNDOMAIN"),
    }
    encoded = (
        base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        .decode("ascii")
        .rstrip("=")
    )
    return f"vmess://{encoded}"


# ---- XHTTP / XHTTP-H3 (VLESS with Xray custom fields) ----------------------


def build_xhttp_h3_link(*, compat: bool = False) -> str:
    domain = _env("DOMAIN")
    port = _env("PORT_XHTTP_H3")
    uuid = _env("XRAY_UUID")
    params: dict[str, str] = {
        "security": "tls",
        "sni": domain,
        "alpn": "h3",
        "fp": "chrome",
        "type": "xhttp",
        "path": f"/{_env('XRAY_URL_PATH')}-xhttp-h3",
        "mode": "auto",
    }
    if not compat:
        mlkem = _env("XRAY_MLKEM768_CLIENT")
        params["encryption"] = f"mlkem768x25519plus.native.0rtt.{mlkem}"
    else:
        params["encryption"] = "none"
    query = urllib.parse.urlencode(params)
    remark = urlquote(_remark("Xhttp-H3"))
    return f"vless://{uuid}@{domain}:{port}?{query}#{remark}"


# ---- write_subscriptions ----------------------------------------------------


def _collect_links(*, compat: bool) -> list[str]:
    links: list[str] = []
    for builder in (
        build_hysteria2_link,
        build_tuic_link,
        build_anytls_link,
        build_vmess_link,
    ):
        try:
            links.append(builder())
        except Exception:
            continue
    with contextlib.suppress(Exception):
        links.append(build_xhttp_h3_link(compat=compat))
    return links


def write_subscriptions(*, output_dir: Path) -> None:
    """Produce ``v2rayn`` + ``v2rayn-compat`` base64 subscription files."""
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, compat in (("v2rayn", False), ("v2rayn-compat", True)):
        joined = "\n".join(_collect_links(compat=compat))
        encoded = base64.b64encode(joined.encode("utf-8")).decode("ascii")
        (output_dir / name).write_text(encoded, encoding="utf-8")
