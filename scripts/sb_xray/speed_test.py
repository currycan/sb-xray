"""Download speed measurement (entrypoint.sh §9 equivalent).

Replaces curl's ``-w '%{speed_download}'`` with an httpx client that
downloads the full body, timed with ``time.perf_counter``. A node
context helper is provided so callers can record multiple probes and
surface the fastest one — preserving the Bash ``proxy_max_speed`` /
``FASTEST_PROXY_TAG`` semantics.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import httpx

from sb_xray import http as sbhttp
from sb_xray import logging as sblog

# Thresholds from the Bash ``show_report`` ladder (Mbps)
_THRESH_8K_HDR: Final[float] = 100.0
_THRESH_8K: Final[float] = 60.0
_THRESH_4K: Final[float] = 25.0
_THRESH_1080P: Final[float] = 10.0

_MIN_VALID_BPS: Final[float] = 1024.0  # < 1 KiB/s → connection failed


def _httpx_client(
    *, timeout: float, proxy: str | None = None, proxy_auth: str | None = None
) -> httpx.Client:
    """Factory isolated so tests can monkeypatch it with a fake client."""
    if proxy and proxy_auth and "@" not in proxy:
        scheme, _, rest = proxy.partition("://")
        proxy = f"{scheme}://{proxy_auth}@{rest}"
    return httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": sbhttp.DEFAULT_UA},
        proxy=proxy,
    )


def _sample_once(client: httpx.Client, url: str) -> float:
    """Single GET; returns bytes/sec (0.0 on error)."""
    try:
        start = time.perf_counter()
        resp = client.get(url)
        resp.raise_for_status()
        elapsed = time.perf_counter() - start
        body_len = len(resp.content)
        if elapsed <= 0:
            return 0.0
        return body_len / elapsed
    except httpx.HTTPError:
        return 0.0


def _truncated_mean_with_stability(
    samples_bps: list[float],
) -> tuple[float, float, str]:
    """Port of entrypoint.sh §9 truncated-mean + stddev + stability label.

    Returns ``(trimmed_mean_mbps, stddev_mbps, label)`` where ``label`` is
    one of ``[稳定]`` (CV<0.2), ``[轻微波动]`` (CV<0.5), ``[波动较大]``.
    Empty input yields ``(0.0, 0.0, "[稳定]")`` to stay side-effect free.
    """
    n = len(samples_bps)
    if n == 0:
        return 0.0, 0.0, "[稳定]"

    ordered = sorted(samples_bps)
    # Bash: n>=3 drops min+max; otherwise keeps everything.
    trimmed = ordered[1:-1] if n >= 3 else ordered

    def _to_mbps(bps: float) -> float:
        return bps * 8 / 1024 / 1024

    trimmed_mean_mbps = sum(_to_mbps(v) for v in trimmed) / len(trimmed)
    all_mbps = [_to_mbps(v) for v in samples_bps]
    full_mean = sum(all_mbps) / n
    variance = sum((v - full_mean) ** 2 for v in all_mbps) / n
    stddev = variance**0.5

    cv = stddev / trimmed_mean_mbps if trimmed_mean_mbps > 0 else 0.0
    if cv < 0.2:
        label = "[稳定]"
    elif cv < 0.5:
        label = "[轻微波动]"
    else:
        label = "[波动较大]"
    return round(trimmed_mean_mbps, 2), round(stddev, 2), label


def measure(
    url: str,
    *,
    samples: int = 3,
    proxy: str | None = None,
    proxy_auth: str | None = None,
    timeout: float = 5.0,
    name: str | None = None,
) -> float:
    """Sample ``url`` ``samples`` times; return truncated-mean throughput in Mbps.

    Semantics match entrypoint.sh::speed_test:
      - samples < 1 KiB/s are discarded as failed connections;
      - with ≥3 valid samples, drop min + max before averaging (truncated mean);
      - compute population stddev across ALL valid samples to surface the
        raw variability (not the trimmed one);
      - log a ``[稳定]/[轻微波动]/[波动较大]`` label based on CV vs. thresholds
        (<0.2 / <0.5 / else).

    ``name`` is surfaced in the summary log line when supplied.
    """
    label_name = name or "节点"
    sblog.log(
        "INFO",
        f"[测速] 开始: {label_name}"
        + (f" | 代理: {proxy}" if proxy else "")
        + f" | 测速源: {url} | 采样: {samples}次",
    )

    with _httpx_client(timeout=timeout, proxy=proxy, proxy_auth=proxy_auth) as client:
        valid: list[float] = []
        for idx in range(1, samples + 1):
            bps = _sample_once(client, url)
            kbps = bps / 1024
            mbps_raw = bps * 8 / 1024 / 1024
            sblog.log(
                "INFO",
                f"[测速] {label_name} | 第 {idx}/{samples} 轮: "
                f"{kbps:.0f} KB/s → {mbps_raw:.2f} Mbps",
            )
            if bps > _MIN_VALID_BPS:
                valid.append(bps)

    if not valid:
        sblog.log(
            "WARN",
            f"[测速] {label_name}: 全部 {samples} 次采样失败，返回 0",
        )
        return 0.0

    trimmed_mean, stddev, label = _truncated_mean_with_stability(valid)
    sblog.log(
        "INFO",
        f"[测速] {label_name}: {len(valid)}/{samples} 有效样本，"
        f"截断均值 {trimmed_mean:.2f} Mbps，标准差 {stddev:.2f} Mbps {label}",
    )
    return trimmed_mean


def rate(mbps: float) -> str:
    """Translate Mbps into the Bash show_report categories."""
    if mbps > _THRESH_8K_HDR:
        return "8K-HDR"
    if mbps > _THRESH_8K:
        return "8K"
    if mbps > _THRESH_4K:
        return "4K"
    if mbps > _THRESH_1080P:
        return "1080P"
    return "slow"


_RATING_LABEL: Final[dict[str, str]] = {
    "8K-HDR": "极速，流畅播放 8K (HDR/60fps)",
    "8K": "流畅播放 8K",
    "4K": "流畅 4K，8K 可能卡顿",
    "1080P": "满足 1080P/4K",
    "slow": "网络较慢",
}


def show_report(mbps: float, *, name: str = "直连") -> None:
    """Pretty-print a speed report block to stderr (Bash parity)."""
    label = _RATING_LABEL.get(rate(mbps), "—")
    border = "=" * 40
    sys.stderr.write(
        f"{border}\n 8K 测速报告 — {name}\n{border}\n"
        f" 速度: {mbps:.2f} Mbps\n"
        f" 评级: {label}\n"
        f"{border}\n"
    )
    sys.stderr.flush()


@dataclass
class IspSpeedContext:
    """Tracks per-tag speeds and surfaces the fastest one.

    ``tolerance`` is a multiplicative margin a new candidate must clear
    before replacing the current leader. The Bash implementation
    (``_test_isp_node``) uses a plain ``awk '>'`` comparison — i.e. any
    strictly larger value wins — so the default is ``1.0`` for parity.
    Raise it only when you want to dampen oscillations on near-ties.
    """

    tolerance: float = 1.0
    speeds: dict[str, float] = field(default_factory=dict)
    fastest_tag: str | None = None
    fastest_speed: float = 0.0

    def record(self, tag: str, mbps: float) -> None:
        self.speeds[tag] = mbps
        if self.fastest_tag is None:
            self.fastest_tag = tag
            self.fastest_speed = mbps
            return
        if mbps > self.fastest_speed * self.tolerance:
            self.fastest_tag = tag
            self.fastest_speed = mbps


# ============================================================================
# Stage 2 — entrypoint.sh ``run_speed_tests_if_needed`` orchestration
# ============================================================================

_SPEED_TEST_URL: Final[str] = "https://speed.cloudflare.com/__down?bytes=25000000"
_SPEED_SAMPLES_DEFAULT: Final[int] = 2
_KEEP_ON_CACHE_HIT_MBPS: Final[float] = 999.0


def _isp_tag_for(prefix: str) -> str:
    """Bash ``tr '[:upper:]_ ' '[:lower:]-'`` + ``proxy-`` prefix."""
    slug = prefix.lower().replace("_", "-").replace(" ", "-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return f"proxy-{slug.strip('-')}"


def _discover_isp_nodes() -> list[tuple[str, str, str, str, str]]:
    """Return ``[(prefix, ip, port, user, password), ...]`` from env vars.

    Mirrors ``env | grep "_ISP_IP=" | cut -d= -f1`` + sibling lookups
    (``${prefix}_PORT`` / ``${prefix}_USER`` / ``${prefix}_SECRET``).
    Entries missing IP or PORT are skipped.
    """
    nodes: list[tuple[str, str, str, str, str]] = []
    for key, value in os.environ.items():
        if not key.endswith("_ISP_IP") or not value:
            continue
        prefix = key[: -len("_IP")]
        port = os.environ.get(f"{prefix}_PORT", "").strip().strip("'\"")
        if not port:
            continue
        user = os.environ.get(f"{prefix}_USER", "").strip().strip("'\"")
        password = os.environ.get(f"{prefix}_SECRET", "").strip().strip("'\"")
        nodes.append((prefix, value.strip().strip("'\""), port, user, password))
    return nodes


def _status_file() -> Path:
    return Path(os.environ.get("STATUS_FILE", "/.env/status"))


def _write_status_line(key: str, value: str) -> None:
    """Upsert ``export KEY='VALUE'`` in ``STATUS_FILE`` (mirrors sed -i+echo).

    Silently warns + returns on ``OSError`` so a read-only status dir
    never aborts boot (bash equivalent uses ``|| true`` on every sed).
    """
    path = _status_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        sblog.log("WARN", f"[status] cannot create {path.parent}: {exc}")
        return
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    import re as _re

    pattern = _re.compile(rf"^export {_re.escape(key)}=.*\n?", _re.MULTILINE)
    cleaned = pattern.sub("", existing).rstrip("\n")
    if cleaned:
        cleaned += "\n"
    cleaned += f"export {key}='{value}'\n"
    try:
        path.write_text(cleaned, encoding="utf-8")
    except OSError as exc:
        sblog.log("WARN", f"[status] cannot write {path}: {exc}")


def _purge_service_caches() -> None:
    """entrypoint.sh:1183 — drop stale ``*_OUT`` caches from STATUS_FILE.

    Called when ``ISP_TAG`` is being recomputed so downstream media probes
    re-run against the fresh routing decision.
    """
    path = _status_file()
    if not path.is_file():
        return
    import re as _re

    removed_keys = (
        "ISP_OUT",
        "CHATGPT_OUT",
        "NETFLIX_OUT",
        "DISNEY_OUT",
        "YOUTUBE_OUT",
        "GEMINI_OUT",
        "CLAUDE_OUT",
        "SOCIAL_MEDIA_OUT",
        "TIKTOK_OUT",
    )
    text = path.read_text(encoding="utf-8")
    for key in removed_keys:
        text = _re.sub(rf"^export {_re.escape(key)}=.*\n?", "", text, flags=_re.MULTILINE)
        os.environ.pop(key, None)
    path.write_text(text, encoding="utf-8")


def _proxy_url(ip: str, port: str) -> str:
    return f"socks5h://{ip}:{port}"


def run_isp_speed_tests(
    *,
    samples: int | None = None,
    url: str = _SPEED_TEST_URL,
) -> None:
    """Port of ``run_speed_tests_if_needed`` (entrypoint.sh:1153).

    Steps (bash parity):
      1. If ``ISP_TAG`` already cached → rebuild ``HAS_ISP_NODES`` /
         ``ISP_SPEEDS``-like state and bail early.
      2. Otherwise wipe stale ``*_OUT`` caches, measure the direct
         baseline (for ``DIRECT_SPEED`` / ``IS_8K_SMOOTH`` fallback), then
         iterate every ``*_ISP_IP`` env var, speed-testing through a
         SOCKS5h proxy.
      3. Hand the aggregated context to
         :func:`sb_xray.routing.isp.apply_isp_routing_logic` and persist
         ``ISP_TAG`` / ``IS_8K_SMOOTH`` into ``STATUS_FILE``.
    """
    from sb_xray.routing.isp import RoutingContext, apply_isp_routing_logic

    sample_count = (
        samples
        if samples is not None
        else int(os.environ.get("SPEED_SAMPLES", _SPEED_SAMPLES_DEFAULT))
    )

    cached_tag = os.environ.get("ISP_TAG", "").strip()
    if cached_tag:
        sblog.log("INFO", f"[选路] 命中缓存 ISP_TAG={cached_tag}，跳过测速")
        nodes = _discover_isp_nodes()
        if nodes:
            os.environ["HAS_ISP_NODES"] = "true"
            speeds = {_isp_tag_for(prefix): 0.0 for prefix, *_ in nodes}
            if cached_tag in speeds:
                speeds[cached_tag] = _KEEP_ON_CACHE_HIT_MBPS
            os.environ["_ISP_SPEEDS_JSON"] = _json_speeds(speeds)
        return

    _purge_service_caches()
    for v in (
        "ISP_TAG",
        "TOP_ISP_TAG",
        "proxy_max_speed",
        "FASTEST_PROXY_TAG",
        "IS_8K_SMOOTH",
        "DIRECT_SPEED",
        "HAS_ISP_NODES",
        "_ISP_SPEEDS_JSON",
    ):
        os.environ.pop(v, None)

    region = os.environ.get("GEOIP_INFO", "").split("|", 1)[0] or "未知"
    sblog.log(
        "INFO",
        f"[选路] IP_TYPE={os.environ.get('IP_TYPE', '未知')} | "
        f"地区={region} | DEFAULT_ISP={os.environ.get('DEFAULT_ISP', '未设置')}",
    )

    direct_mbps = measure(url, samples=sample_count)
    os.environ["DIRECT_SPEED"] = f"{direct_mbps:.2f}"
    show_report(direct_mbps, name="Direct")
    sblog.log(
        "INFO",
        f"[测速] 直连基准: {direct_mbps:.2f} Mbps（不参与选路；无代理时用于 IS_8K_SMOOTH 判定）",
    )

    nodes = _discover_isp_nodes()
    ctx = IspSpeedContext()
    if not nodes:
        sblog.log(
            "WARN",
            "[测速] 未发现 ISP 节点（无 *_ISP_IP 环境变量），将回退直连",
        )
    else:
        os.environ["HAS_ISP_NODES"] = "true"
        sblog.log(
            "INFO",
            f"[测速] 发现 ISP 节点 {len(nodes)} 个，逐节点采样 {sample_count} 次",
        )
        for prefix, ip, port, user, password in nodes:
            tag = _isp_tag_for(prefix)
            proxy_auth = f"{user}:{password}" if user and password else None
            mbps = measure(
                url,
                samples=sample_count,
                proxy=_proxy_url(ip, port),
                proxy_auth=proxy_auth,
            )
            show_report(mbps, name=prefix)
            ctx.record(tag, mbps)
            if ctx.fastest_tag == tag and mbps > 0:
                sblog.log("INFO", f"[测速] {tag}: {mbps:.2f} Mbps → 新最优")
            else:
                sblog.log(
                    "INFO",
                    f"[测速] {tag}: {mbps:.2f} Mbps (最优仍: "
                    f"{ctx.fastest_tag or '未定'} {ctx.fastest_speed:.2f} Mbps)",
                )

    os.environ["_ISP_SPEEDS_JSON"] = _json_speeds(ctx.speeds)
    if ctx.fastest_tag:
        os.environ["FASTEST_PROXY_TAG"] = ctx.fastest_tag
        # Bash export name is lowercase (_test_isp_node); mirror it verbatim.
        os.environ["proxy_max_speed"] = f"{ctx.fastest_speed:.2f}"  # noqa: SIM112

    decision = apply_isp_routing_logic(
        RoutingContext(
            ip_type=os.environ.get("IP_TYPE", "unknown"),
            geoip_info=os.environ.get("GEOIP_INFO", ""),
            default_isp=os.environ.get("DEFAULT_ISP", ""),
            direct_speed=direct_mbps,
            fastest_proxy_tag=ctx.fastest_tag,
            proxy_max_speed=ctx.fastest_speed,
        )
    )
    os.environ["ISP_TAG"] = decision.isp_tag
    os.environ["IS_8K_SMOOTH"] = "true" if decision.is_8k_smooth else "false"

    _write_status_line("IS_8K_SMOOTH", os.environ["IS_8K_SMOOTH"])
    _write_status_line("ISP_TAG", decision.isp_tag)
    sblog.log(
        "INFO",
        f"[选路] ISP_TAG={decision.isp_tag} IS_8K_SMOOTH={os.environ['IS_8K_SMOOTH']}",
    )


def _json_speeds(speeds: dict[str, float]) -> str:
    """Encode per-tag speeds for downstream consumers (routing.isp)."""
    import json as _json

    return _json.dumps({t: round(v, 2) for t, v in speeds.items()})


def load_isp_speeds() -> dict[str, float]:
    """Inverse of ``_json_speeds`` — returns an empty dict when unset."""
    import json as _json

    raw = os.environ.get("_ISP_SPEEDS_JSON", "")
    if not raw:
        return {}
    try:
        data = _json.loads(raw)
    except _json.JSONDecodeError:
        return {}
    return {str(k): float(v) for k, v in data.items()}
