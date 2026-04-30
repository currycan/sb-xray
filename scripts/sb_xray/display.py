"""Terminal-facing rendering (show-config.sh §2-3 equivalent).

Covers the presentational pieces of the legacy shell: flag lookup,
TLS ping diagnostic panel, QR code rendering via ``qrencode``, and
the main ``show_info_links`` banner. ``sys.stdout`` is the target
(parity with the Bash ``echo`` statements), not stderr.

``show_info_links`` emits ANSI-colored output AND — when invoked via
``run_show_pipeline`` — writes a stripped copy to the
``${WORKDIR}/subscribe/show-config`` archive, mirroring the Bash
``main | tee >(sed 's/\\x1b\\[[0-9;]*m//g')`` flow.
"""

from __future__ import annotations

import io
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

# ---- ANSI palette (mirrors show-config.sh header constants) ---------------

RED: Final = "\033[31m"
GREEN: Final = "\033[32m"
YELLOW: Final = "\033[33m"
BLUE: Final = "\033[34m"
MAGENTA: Final = "\033[35m"
CYAN: Final = "\033[36m"
PURPLE: Final = "\033[0;35m"
BRIGHT_RED: Final = "\033[91m"
BRIGHT_GREEN: Final = "\033[92m"
BRIGHT_YELLOW: Final = "\033[93m"
BRIGHT_BLUE: Final = "\033[94m"
BRIGHT_MAGENTA: Final = "\033[95m"
BRIGHT_CYAN: Final = "\033[96m"
BOLD: Final = "\033[1m"
DIM: Final = "\033[2m"
RESET: Final = "\033[0m"

_TPL_COLORS: Final = (BRIGHT_YELLOW, BRIGHT_MAGENTA, BRIGHT_GREEN, BRIGHT_BLUE, BRIGHT_RED)
_ANSI_RE: Final = re.compile(r"\x1b\[[0-9;]*m")

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
    (("土耳其", "Turkey"), "🇹🇷"),
    (("阿根廷", "Argentina"), "🇦🇷"),
    (("巴西", "Brazil"), "🇧🇷"),
    (("南非", "South Africa"), "🇿🇦"),
    (("澳门", "Macao", "Macau"), "🇲🇴"),
    (("瑞士", "Switzerland"), "🇨🇭"),
    (("瑞典", "Sweden"), "🇸🇪"),
    (("意大利", "Italy"), "🇮🇹"),
    (("爱尔兰", "Ireland"), "🇮🇪"),
    (("土库曼斯坦",), "🇹🇲"),
    (("中国", "China"), "🇨🇳"),
)

_CLIENT_TEMPLATE_DIR: Final = Path("/templates/client_template")


def get_flag_emoji(info: str) -> str:
    """Return a flag emoji derived from an IP-info / region string.

    Returns "" when no match (matches the Bash default-case behavior).
    """
    for markers, flag in _FLAG_MATRIX:
        if any(marker in info for marker in markers):
            return flag
    return ""


def tls_ping_diagnose(target: str) -> None:
    """Invoke ``xray tls ping <target>`` and log its output.

    The output used to go to stdout (banner style). Now it goes to the
    logger — since the banner itself is a one-shot report and tls-ping
    diagnostics are verbose troubleshooting noise that should follow
    the same verbosity controls as the rest of the pipeline.
    """
    logger.info("tls-ping target=%s", target)
    try:
        result = subprocess.run(
            ["xray", "tls", "ping", target],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        logger.warning("tls-ping 跳过: xray CLI 不可用")
        return
    if result.stdout:
        # Preserve multi-line xray output as a single log record so the
        # cert fingerprint block stays readable.
        logger.info("tls-ping %s output:\n%s", target, result.stdout.rstrip())
    if result.returncode != 0:
        logger.warning("tls-ping %s 失败（目标不可达或未部署证书）", target)


def show_qrcode(content: str, *, name: str) -> None:
    """Emit a UTF-8 terminal QR code for ``content``.

    Matches show-config.sh:111 params byte-for-byte: ``-s 8 -m 4 -l H -v 10
    -d 300 -k 2`` + ``-f 0 -b 255`` for the utf8 foreground/background.
    """
    # QR codes are a visual artifact for human consumption — keep them
    # on stdout with the surrounding subscription banner, not in the
    # structured log stream.
    sys.stdout.write(f"{GREEN}== {name} QR Code =={RESET}\n")
    qr_opts = ["-s", "8", "-m", "4", "-l", "H", "-v", "10", "-d", "300", "-k", "2"]
    try:
        result = subprocess.run(
            ["qrencode", "-o", "-", "-t", "utf8", *qr_opts, "-f", "0", "-b", "255", content],
            check=False,
            capture_output=True,
        )
        if result.returncode == 0 and result.stdout:
            sys.stdout.buffer.write(result.stdout)
            sys.stdout.buffer.flush()
    except FileNotFoundError:
        logger.warning("qr 跳过: qrencode CLI 不可用")


# ---- banner ---------------------------------------------------------------


def _print_colored(color: str, text: str, *, out: io.TextIOBase | None = None) -> None:
    """``echo -e "${color}${text}${RESET}\\n"`` equivalent (trailing blank line)."""
    stream = out or sys.stdout
    stream.write(f"{color}{text}{RESET}\n\n")


def render_info_links(out: io.TextIOBase) -> None:
    """Write the full banner to ``out`` (TextIO). Used by both stdout and archive.

    Assumes ``node_meta.derive_and_export`` has already populated
    NODE_NAME/FLAG_PREFIX/NODE_SUFFIX etc.
    """
    cdn = os.environ.get("CDNDOMAIN", "")
    domain = os.environ.get("DOMAIN", "")
    token = os.environ.get("SUBSCRIBE_TOKEN", "")
    token_param = f"?token={token}" if token else ""
    base = f"https://{cdn}/sb-xray"
    sep = "━" * 66

    out.write("\n")
    out.write(f"{BOLD}{GREEN}{sep}{RESET}\n")
    out.write(f"{BOLD}{GREEN}  Sing-box / Xray 多协议多传输客户端配置文件汇总{RESET}\n")
    out.write(f"{BOLD}{GREEN}{sep}{RESET}\n")
    out.write("\n")

    if os.environ.get("DEBUG") == "1":
        # tls_ping_diagnose always targets stdout; when mirroring into the
        # archive we let the stripped tee capture it too.
        tls_ping_diagnose(f"{cdn}:443")
        if domain and domain != cdn:
            tls_ping_diagnose(f"{domain}:443")

    _print_colored(RED, f"📋 Index（订阅索引页）\n{base}/show-config{token_param}", out=out)
    _print_colored(
        CYAN,
        f"🚀 V2rayN 订阅  {DIM}[Xray-core 26.3.27+ · ML-KEM-768 + adv obfs + "
        f"fragment + H3 优先]{RESET}{CYAN}\n{base}/v2rayn{token_param}",
        out=out,
    )
    _print_colored(
        BRIGHT_CYAN,
        f"🔓 Common 订阅  {DIM}[mihomo/OpenClash/Karing + 低版 Xray-core · "
        f"无 VLESS 加密 · 6 协议]{RESET}{BRIGHT_CYAN}\n{base}/common{token_param}",
        out=out,
    )

    tpl_idx = 0
    if _CLIENT_TEMPLATE_DIR.is_dir():
        for path in sorted(_CLIENT_TEMPLATE_DIR.glob("*.yaml")):
            name = path.stem
            color = _TPL_COLORS[tpl_idx % len(_TPL_COLORS)]
            _print_colored(
                color,
                f"📄 {name} 订阅\n{base}/{path.name}{token_param}",
                out=out,
            )
            tpl_idx += 1
        surge = _CLIENT_TEMPLATE_DIR / "surge.conf"
        if surge.is_file():
            _print_colored(
                PURPLE,
                f"🧭 Surge 订阅\n{base}/surge.conf{token_param}",
                out=out,
            )

    if token_param:
        user = os.environ.get("PUBLIC_USER", "未设置")
        pwd = os.environ.get("PUBLIC_PASSWORD", "未设置")
        out.write(f"  💡 {YELLOW}已附加安全认证 Token，可直接导入客户端使用{RESET}\n")
        out.write(f"  🔒 {YELLOW}Basic Auth: {user} / {pwd}{RESET}\n")
        out.write("\n")

    _render_sub_store_links(out, domain=domain, cdn=cdn)

    out.write(f"{BOLD}{GREEN}{sep}{RESET}\n")


def _render_sub_store_links(out: io.TextIOBase, *, domain: str, cdn: str) -> None:
    """Emit Sub-Store web UI + hidden backend API path (empty when disabled)."""
    if os.environ.get("ENABLE_SUBSTORE", "true").lower() == "false":
        return

    host = domain or cdn
    webbase = os.environ.get("SUB_STORE_WEBBASEPATH", "sub-store").strip("/")
    backend = os.environ.get("SUB_STORE_FRONTEND_BACKEND_PATH", "")
    if not host or not backend:
        return

    web_url = f"https://{host}/{webbase}/"
    backend_url = f"https://{host}{backend}/"

    _print_colored(
        YELLOW,
        f"🗂  Sub-Store 面板  {DIM}[Web UI]{RESET}{YELLOW}\n{web_url}",
        out=out,
    )
    _print_colored(
        BRIGHT_CYAN,
        f"🔑 Sub-Store 后端 API  {DIM}[首次进入面板 → 设置 → 后端地址 粘贴]"
        f"{RESET}{BRIGHT_CYAN}\n{backend_url}",
        out=out,
    )


def show_info_links(*, archive_path: Path | None = None) -> None:
    """Print banner to stdout; when ``archive_path`` given, also write a
    color-stripped copy (show-config.sh `tee >(sed ...)` equivalent).
    """
    buf = io.StringIO()
    render_info_links(buf)
    text = buf.getvalue()
    sys.stdout.write(text)
    sys.stdout.flush()
    if archive_path is not None:
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        archive_path.write_text(_ANSI_RE.sub("", text), encoding="utf-8")
