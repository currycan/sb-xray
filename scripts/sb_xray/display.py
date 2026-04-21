"""Terminal-facing rendering (show-config.sh §2-3 equivalent).

Covers the presentational pieces of the legacy shell: flag lookup,
TLS ping diagnostic panel, QR code rendering via ``qrencode``, and
the main ``show_info_links`` banner. ``sys.stdout`` is the target
(parity with the Bash ``echo`` statements), not stderr.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Final

_FLAG_MATRIX: Final[tuple[tuple[tuple[str, ...], str], ...]] = (
    (("香港", "Hong Kong", "HongKong", "HK"), "🇭🇰"),
    (("台湾", "Taiwan"), "🇹🇼"),
    (("日本", "Japan", "东京", "大阪"), "🇯🇵"),
    (("新加坡", "Singapore"), "🇸🇬"),
    (("美国", "United States", "USA", "洛杉矶", "纽约"), "🇺🇸"),
    (("韩国", "Korea"), "🇰🇷"),
    (("英国", "United Kingdom", "UK", "Britain"), "🇬🇧"),
    (("德国", "Germany"), "🇩🇪"),
    (("法国", "France"), "🇫🇷"),
    (("加拿大", "Canada"), "🇨🇦"),
    (("澳大利亚", "Australia"), "🇦🇺"),
    (("俄罗斯", "Russia"), "🇷🇺"),
    (("印度", "India"), "🇮🇳"),
    (("荷兰", "Netherlands"), "🇳🇱"),
    (("菲律宾", "Philippines"), "🇵🇭"),
    (("马来西亚", "Malaysia"), "🇲🇾"),
    (("泰国", "Thailand"), "🇹🇭"),
    (("越南", "Vietnam"), "🇻🇳"),
    (("印尼", "印度尼西亚", "Indonesia"), "🇮🇩"),
)


def get_flag_emoji(info: str) -> str:
    """Return a flag emoji derived from an IP-info / region string.

    Returns "" when no match (matches the Bash default-case behavior).
    """
    for markers, flag in _FLAG_MATRIX:
        if any(marker in info for marker in markers):
            return flag
    return ""


def tls_ping_diagnose(target: str) -> None:
    """Invoke ``xray tls ping <target>`` and echo its output."""
    print(f"[tls-ping] {target}")
    try:
        result = subprocess.run(
            ["xray", "tls", "ping", target],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.stdout:
            print(result.stdout)
        if result.returncode != 0:
            print("[tls-ping] failed (target unreachable or no cert installed)")
    except FileNotFoundError:
        print("[tls-ping] xray CLI unavailable in PATH")


def show_qrcode(content: str, *, name: str) -> None:
    """Emit a UTF-8 terminal QR code for ``content``."""
    print(f"== {name} QR Code ==")
    qr_opts = ["-s", "8", "-m", "4", "-l", "H"]
    try:
        result = subprocess.run(
            ["qrencode", "-o", "-", "-t", "utf8", *qr_opts, content],
            check=False,
            capture_output=True,
        )
        if result.returncode == 0 and result.stdout:
            sys.stdout.buffer.write(result.stdout)
            sys.stdout.buffer.flush()
    except FileNotFoundError:
        print("[qr] qrencode CLI unavailable in PATH")


def show_info_links() -> None:
    """Print the subscription-link banner (show-config.sh §show_info_links)."""
    cdn = os.environ.get("CDNDOMAIN", "")
    domain = os.environ.get("DOMAIN", "")
    token = os.environ.get("SUBSCRIBE_TOKEN", "")
    token_param = f"?token={token}" if token else ""
    base = f"https://{cdn}/sb-xray"
    sep = "━" * 66

    print()
    print(sep)
    print("  Sing-box / Xray 多协议多传输客户端配置文件汇总")
    print(sep)
    print()

    if os.environ.get("DEBUG") == "1":
        tls_ping_diagnose(f"{cdn}:443")
        if domain and domain != cdn:
            tls_ping_diagnose(f"{domain}:443")

    print(f"v2rayn        : {base}/v2rayn{token_param}")
    print(f"v2rayn-compat : {base}/v2rayn-compat{token_param}")
    print()
    print(sep)
