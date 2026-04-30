"""Client-subscription URL builders (show-config.sh §generate_links port).

Byte-for-byte port of the legacy ``scripts/show-config.sh:generate_links``.
Every link format, parameter order, URL-encoding, base64 padding and file
layout must match the Bash version so deployed clients don't need to
re-subscribe.

Two subscription tracks are produced:

  * ``v2rayn``        — full feature set (ML-KEM-768 + XHTTP obfs + H3)
  * ``common``        — ``encryption=none`` + ``mode=packet-up`` common
                        client track without TUIC or split Reality→CDN
                        downlink.

``os.environ`` is the single source of truth — callers are expected to
have bootstrapped ``EnvManager`` first.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

# ---- helpers ---------------------------------------------------------------


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _remark(protocol: str) -> str:
    """`${FLAG_PREFIX}<proto> ✈ ${NODE_NAME}${NODE_SUFFIX}` — no URL-encode.

    show-config.sh leaves the ``#fragment`` remark un-encoded (the Bash
    scripts simply append ``#${FLAG_PREFIX}...``); we preserve that so
    the base64 payload is byte-identical.
    """
    return f"{_env('FLAG_PREFIX')}{protocol} ✈ {_env('NODE_NAME')}{_env('NODE_SUFFIX')}"


def urlquote(value: str) -> str:
    """``urllib.parse.quote`` preserving ``-._~`` (Bash/JS default)."""
    import urllib.parse

    return urllib.parse.quote(value, safe="-._~")


# ---- primitive links -------------------------------------------------------


def build_hysteria2_link() -> str:
    domain = _env("DOMAIN")
    port = _env("PORT_HYSTERIA2")
    pwd = _env("SB_UUID")
    return (
        f"hysteria2://{pwd}@{domain}:{port}/"
        f"?sni={domain}"
        f"&obfs=salamander&obfs-password={pwd}"
        f"&alpn=h3"
        f"#{_remark('Hysteria2')}"
    )


def build_tuic_link() -> str:
    domain = _env("DOMAIN")
    port = _env("PORT_TUIC")
    uuid = _env("SB_UUID")
    return (
        f"tuic://{uuid}:{uuid}@{domain}:{port}"
        f"?sni={domain}"
        f"&alpn=h3"
        f"&congestion_control=bbr"
        f"#{_remark('TUIC')}"
    )


def build_anytls_link() -> str:
    domain = _env("DOMAIN")
    port = _env("PORT_ANYTLS")
    uuid = _env("SB_UUID")
    return f"anytls://{uuid}@{domain}:{port}?security=tls&type=tcp#{_remark('AnyTLS')}"


def build_vmess_link() -> str:
    """WebSocket VMess — JSON payload → base64 (no padding stripping).

    Bash uses ``base64 -w0`` which keeps ``=`` padding; we emit the
    same to guarantee byte-level parity.
    """
    payload = {
        "v": "2",
        "ps": _remark("Vmess"),
        "add": _env("CDNDOMAIN"),
        "port": _env("LISTENING_PORT"),
        "id": _env("XRAY_UUID"),
        "aid": "0",
        "scy": "auto",
        "net": "ws",
        "type": "none",
        "host": _env("CDNDOMAIN"),
        "path": f"/{_env('XRAY_URL_PATH')}-vmessws",
        "tls": "tls",
        "sni": _env("CDNDOMAIN"),
        "alpn": "http/1.1",
        "fp": "chrome",
    }
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encoded = base64.b64encode(blob).decode("ascii")
    return f"vmess://{encoded}"


def build_vless_vision_link() -> str:
    uuid = _env("XRAY_UUID")
    domain = _env("DOMAIN")
    port = _env("LISTENING_PORT")
    dest = _env("DEST_HOST")
    pbk = _env("XRAY_REALITY_PUBLIC_KEY")
    sid = _env("XRAY_REALITY_SHORTID")
    return (
        f"vless://{uuid}@{domain}:{port}"
        f"?encryption=none&flow=xtls-rprx-vision"
        f"&security=reality&sni={dest}&fp=chrome"
        f"&pbk={pbk}&sid={sid}&spx=%2F"
        f"&type=tcp&headerType=none"
        f"#{_remark('XTLS-Reality')}"
    )


# ---- XHTTP family (reality + compat) ---------------------------------------


def _xhttp_reality_base(*, compat: bool) -> str:
    """Shared Reality+XHTTP query string used by the reality-direct link.

    * main  → ``encryption=mlkem768x25519plus.native.0rtt.<key>`` + ``mode=auto``
    * compat → ``encryption=none`` + ``mode=packet-up`` (path also carries
      ``-compat`` so nginx/xray's ``decryption:none`` inbound matches).
    """
    mlkem = _env("XRAY_MLKEM768_CLIENT")
    url_path = _env("XRAY_URL_PATH")
    dest = _env("DEST_HOST")
    pbk = _env("XRAY_REALITY_PUBLIC_KEY")
    sid = _env("XRAY_REALITY_SHORTID")
    if compat:
        enc = "encryption=none"
        path = f"%2F{url_path}-xhttp-compat"
        mode = "packet-up"
    else:
        enc = f"encryption=mlkem768x25519plus.native.0rtt.{mlkem}"
        path = f"%2F{url_path}-xhttp"
        mode = "auto"
    return (
        f"{enc}"
        f"&security=reality&sni={dest}&fp=chrome"
        f"&pbk={pbk}&sid={sid}"
        f"&type=xhttp&path={path}&mode={mode}"
    )


def build_xhttp_reality_link(*, compat: bool = False) -> str:
    uuid = _env("XRAY_UUID")
    domain = _env("DOMAIN")
    port = _env("LISTENING_PORT")
    return (
        f"vless://{uuid}@{domain}:{port}"
        f"?{_xhttp_reality_base(compat=compat)}"
        f"#{_remark('Xhttp+Reality直连')}"
    )


def _xhttp_down_reality_extra(*, compat: bool) -> str:
    """URL-encoded JSON for the `extra=...` param on up-CDN / down-Reality.

    Mirrors the two hand-encoded blocks in show-config.sh verbatim.
    """
    url_path = _env("XRAY_URL_PATH")
    dest = _env("DEST_HOST")
    pbk = _env("XRAY_REALITY_PUBLIC_KEY")
    sid = _env("XRAY_REALITY_SHORTID")
    domain = _env("DOMAIN")
    port = _env("LISTENING_PORT")
    path_tail = "xhttp-compat" if compat else "xhttp"
    mode = "packet-up" if compat else "auto"
    return (
        "%7B%22downloadSettings%22%3A%7B"
        f"%22address%22%3A%22{domain}%22"
        f"%2C%22port%22%3A{port}"
        "%2C%22network%22%3A%22xhttp%22"
        "%2C%22security%22%3A%22reality%22"
        "%2C%22realitySettings%22%3A%7B"
        "%22show%22%3Afalse"
        f"%2C%22serverName%22%3A%22{dest}%22"
        "%2C%22fingerprint%22%3A%22chrome%22"
        f"%2C%22publicKey%22%3A%22{pbk}%22"
        f"%2C%22shortId%22%3A%22{sid}%22"
        "%2C%22spiderX%22%3A%22%2F%22%7D"
        "%2C%22xhttpSettings%22%3A%7B"
        "%22host%22%3A%22%22"
        f"%2C%22path%22%3A%22%2F{url_path}-{path_tail}%22"
        f"%2C%22mode%22%3A%22{mode}%22%7D%7D%7D"
    )


def _xhttp_down_tls_extra(*, compat: bool) -> str:
    url_path = _env("XRAY_URL_PATH")
    cdn = _env("CDNDOMAIN")
    domain = _env("DOMAIN")
    port = _env("LISTENING_PORT")
    path_tail = "xhttp-compat" if compat else "xhttp"
    mode = "packet-up" if compat else "auto"
    return (
        "%7B%22downloadSettings%22%3A%7B"
        f"%22address%22%3A%22{domain}%22"
        f"%2C%22port%22%3A{port}"
        "%2C%22network%22%3A%22xhttp%22"
        "%2C%22security%22%3A%22tls%22"
        "%2C%22tlsSettings%22%3A%7B"
        f"%22serverName%22%3A%22{cdn}%22"
        "%2C%22alpn%22%3A%5B%22h2%22%5D"
        "%2C%22fingerprint%22%3A%22chrome%22%7D"
        "%2C%22xhttpSettings%22%3A%7B"
        f"%22host%22%3A%22{cdn}%22"
        f"%2C%22path%22%3A%22%2F{url_path}-{path_tail}%22"
        f"%2C%22mode%22%3A%22{mode}%22%7D%7D%7D"
    )


def build_up_cdn_down_reality_link(*, compat: bool = False) -> str:
    uuid = _env("XRAY_UUID")
    cdn = _env("CDNDOMAIN")
    port = _env("LISTENING_PORT")
    url_path = _env("XRAY_URL_PATH")
    mlkem = _env("XRAY_MLKEM768_CLIENT")
    path_tail = "xhttp-compat" if compat else "xhttp"
    mode = "packet-up" if compat else "auto"
    enc = "encryption=none" if compat else f"encryption=mlkem768x25519plus.native.0rtt.{mlkem}"
    return (
        f"vless://{uuid}@{cdn}:{port}"
        f"?{enc}&security=tls&sni={cdn}&alpn=h2&fp=chrome"
        f"&type=xhttp&host={cdn}&path=%2F{url_path}-{path_tail}&mode={mode}"
        f"&extra={_xhttp_down_reality_extra(compat=compat)}"
        f"#{_remark('上行Xhttp+TLS+CDN下行Xhttp+Reality')}"
    )


def build_up_reality_down_cdn_link(*, compat: bool = False) -> str:
    uuid = _env("XRAY_UUID")
    domain = _env("DOMAIN")
    port = _env("LISTENING_PORT")
    url_path = _env("XRAY_URL_PATH")
    mlkem = _env("XRAY_MLKEM768_CLIENT")
    dest = _env("DEST_HOST")
    pbk = _env("XRAY_REALITY_PUBLIC_KEY")
    sid = _env("XRAY_REALITY_SHORTID")
    path_tail = "xhttp-compat" if compat else "xhttp"
    mode = "packet-up" if compat else "auto"
    enc = "encryption=none" if compat else f"encryption=mlkem768x25519plus.native.0rtt.{mlkem}"
    return (
        f"vless://{uuid}@{domain}:{port}"
        f"?{enc}&security=reality&sni={dest}&fp=chrome"
        f"&pbk={pbk}&sid={sid}"
        f"&type=xhttp&path=%2F{url_path}-{path_tail}&mode={mode}"
        f"&extra={_xhttp_down_tls_extra(compat=compat)}"
        f"#{_remark('上行Xhttp+Reality下行Xhttp+TLS+CDN')}"
    )


def build_mix_link(*, compat: bool = False) -> str:
    uuid = _env("XRAY_UUID")
    cdn = _env("CDNDOMAIN")
    port = _env("LISTENING_PORT")
    url_path = _env("XRAY_URL_PATH")
    mlkem = _env("XRAY_MLKEM768_CLIENT")
    pbk = _env("XRAY_REALITY_PUBLIC_KEY")
    sid = _env("XRAY_REALITY_SHORTID")
    path_tail = "xhttp-compat" if compat else "xhttp"
    mode = "packet-up" if compat else "auto"
    if compat:
        enc_block = "encryption=none"
    else:
        enc_block = f"encryption=mlkem768x25519plus.native.0rtt.{mlkem}&pbk={pbk}&sid={sid}"
    # Bash places pbk/sid between alpn/fp and type= for the main track but
    # omits them for compat (pure CDN inbound has no reality keys).
    if compat:
        return (
            f"vless://{uuid}@{cdn}:{port}"
            f"?{enc_block}&security=tls&sni={cdn}&alpn=h2&fp=chrome"
            f"&type=xhttp&host={cdn}&path=%2F{url_path}-{path_tail}&mode={mode}"
            f"#{_remark('Xhttp+TLS+CDN上下行不分离')}"
        )
    return (
        f"vless://{uuid}@{cdn}:{port}"
        f"?encryption=mlkem768x25519plus.native.0rtt.{mlkem}"
        f"&security=tls&sni={cdn}&alpn=h2&fp=chrome"
        f"&pbk={pbk}&sid={sid}"
        f"&type=xhttp&host={cdn}&path=%2F{url_path}-{path_tail}&mode={mode}"
        f"#{_remark('Xhttp+TLS+CDN上下行不分离')}"
    )


def build_xhttp_h3_link() -> str:
    """XHTTP/3 + BBR — main track only (Bash has no compat variant)."""
    uuid = _env("XRAY_UUID")
    domain = _env("DOMAIN")
    port = _env("PORT_XHTTP_H3")
    url_path = _env("XRAY_URL_PATH")
    mlkem = _env("XRAY_MLKEM768_CLIENT")
    extra = (
        "%7B%22noSSEHeader%22%3Atrue"
        "%2C%22scMaxEachPostBytes%22%3A1000000"
        "%2C%22scMaxBufferedPosts%22%3A30"
        "%2C%22xPaddingBytes%22%3A%22100-1000%22"
        "%2C%22xPaddingQueryParam%22%3A%22cf_ray_id%22"
        "%2C%22xPaddingPlacement%22%3A%22cookie%22"
        "%2C%22UplinkDataPlacement%22%3A%22auto%22%7D"
    )
    return (
        f"vless://{uuid}@{domain}:{port}"
        f"?encryption=mlkem768x25519plus.native.0rtt.{mlkem}"
        f"&security=tls&sni={domain}&alpn=h3&fp=chrome"
        f"&type=xhttp&path=%2F{url_path}-xhttp-h3&mode=auto"
        f"&extra={extra}"
        f"#{_remark('Xhttp-H3+BBR')}"
    )


# ---- aggregate + write ------------------------------------------------------

# show-config.sh concatenation order:
#   part1 = hysteria2 / tuic / anytls / vmess / vless-vision
#   part2 = xhttp-h3 / xhttp-reality / up_cdn / up_reality / mix
#   part1_common = hysteria2 / anytls / vmess / vless-vision
#   part2_common = xhttp-reality_compat / up_cdn_compat / mix_compat
# v2rayn        = part1 + part2
# common        = part1_common + part2_common


def _part1_links() -> list[str]:
    return [
        build_hysteria2_link(),
        build_tuic_link(),
        build_anytls_link(),
        build_vmess_link(),
        build_vless_vision_link(),
    ]


def _part1_common_links() -> list[str]:
    return [
        build_hysteria2_link(),
        build_anytls_link(),
        build_vmess_link(),
        build_vless_vision_link(),
    ]


def _part2_main_links() -> list[str]:
    return [
        build_xhttp_h3_link(),
        build_xhttp_reality_link(compat=False),
        build_up_cdn_down_reality_link(compat=False),
        build_up_reality_down_cdn_link(compat=False),
        build_mix_link(compat=False),
    ]


def _part2_common_links() -> list[str]:
    return [
        build_xhttp_reality_link(compat=True),
        build_up_cdn_down_reality_link(compat=True),
        build_mix_link(compat=True),
    ]


def build_v2rayn_subscription() -> str:
    return "\n".join(_part1_links() + _part2_main_links())


def build_common_subscription() -> str:
    return "\n".join(_part1_common_links() + _part2_common_links())


def write_subscriptions(*, output_dir: Path) -> None:
    """Write base64-encoded ``v2rayn`` + ``common`` into ``output_dir``.

    Matches ``base64 -w0`` behaviour (no line wrapping, keep ``=`` padding).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "v2rayn-compat").unlink(missing_ok=True)
    for name, content in (
        ("v2rayn", build_v2rayn_subscription()),
        ("common", build_common_subscription()),
    ):
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        (output_dir / name).write_text(encoded, encoding="utf-8")
