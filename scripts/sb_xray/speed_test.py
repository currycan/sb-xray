"""Download speed measurement (entrypoint.sh §9 equivalent).

Replaces curl's ``-w '%{speed_download}'`` with an httpx client that
downloads the full body, timed with ``time.perf_counter``. A node
context helper is provided so callers can record multiple probes and
surface the fastest one — preserving the Bash ``proxy_max_speed`` /
``FASTEST_PROXY_TAG`` semantics.
"""

from __future__ import annotations

import json as _json
import logging
import os
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import httpx

from sb_xray import http as sbhttp

logger = logging.getLogger(__name__)

# v2 sampler defaults (see docs/01-architecture-and-traffic.md §speed-test)
_DEFAULT_WARMUP_SEC: Final[float] = 1.5
_DEFAULT_WINDOW_SEC: Final[float] = 8.0
_DEFAULT_MAX_BYTES: Final[int] = 256 * 1024 * 1024
_DEFAULT_CHUNK_BYTES: Final[int] = 64 * 1024
_DEFAULT_SAMPLE_TIMEOUT_SEC: Final[float] = 20.0


@dataclass(frozen=True)
class SampleResult:
    """Outcome of one streamed bandwidth sample.

    ``status`` is one of:
      - ``ok`` — measurement succeeded above the valid-rate threshold
      - ``connect_fail`` — stream open / HTTP status failed
      - ``timeout`` — httpx raised a timeout mid-transfer
      - ``low_speed`` — measured rate below ``_MIN_VALID_BPS``
      - ``zero_body`` — stream opened but delivered no bytes
      - ``proxy_dep_missing`` — upstream reports socksio unavailable
    """

    mbps: float
    status: str
    bytes_read: int
    window_sec: float
    proxy_overhead_ms: float = 0.0


# Thresholds from the Bash ``show_report`` ladder (Mbps)
_THRESH_8K_HDR: Final[float] = 100.0
_THRESH_8K: Final[float] = 60.0
_THRESH_4K: Final[float] = 25.0
_THRESH_1080P: Final[float] = 10.0

_MIN_VALID_BPS: Final[float] = 1024.0  # < 1 KiB/s → connection failed


def _httpx_client(
    *, timeout: float, proxy: str | None = None, proxy_auth: str | None = None
) -> httpx.Client | None:
    """Factory isolated so tests can monkeypatch it with a fake client.

    Returns ``None`` when a SOCKS proxy is requested but the optional
    ``socksio`` transport dependency is missing — ``measure()`` then
    gracefully reports 0 Mbps instead of crashing the whole boot
    pipeline (bash parity: a failed proxy test just yielded 0).
    """
    if proxy and proxy_auth and "@" not in proxy:
        scheme, _, rest = proxy.partition("://")
        proxy = f"{scheme}://{proxy_auth}@{rest}"
    try:
        return httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": sbhttp.DEFAULT_UA},
            proxy=proxy,
        )
    except ImportError as exc:
        logger.warning(
            "httpx 代理依赖缺失 (%s); 跳过该节点（视为 0 Mbps）。"
            " 生产镜像请确认 socksio 已 pip install。",
            exc,
        )
        return None


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


def _stream_measure(
    client: httpx.Client,
    url: str,
    *,
    warmup_sec: float = _DEFAULT_WARMUP_SEC,
    window_sec: float = _DEFAULT_WINDOW_SEC,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    chunk_bytes: int = _DEFAULT_CHUNK_BYTES,
    clock: Callable[[], float] = time.monotonic,
) -> SampleResult:
    """Streamed bandwidth sample with warmup / time-window / byte-cap.

    Key differences from :func:`_sample_once`:
      * Opens a streaming response (``client.stream``) instead of reading
        the full body — never materializes >chunk_bytes at once.
      * Discards the first ``warmup_sec`` of received bytes so that TCP
        slow-start does not pollute the measured throughput.
      * Starts the meter clock at **first-byte arrival after warmup**,
        excluding DNS / TLS / SOCKS5 handshake and TTFB from the
        denominator.
      * Halts at ``window_sec`` elapsed *or* ``max_bytes`` transferred,
        whichever comes first.

    Returns a :class:`SampleResult` with a structured ``status`` so
    callers can distinguish "node down" from "node slow" from
    "measurement truncated" instead of all collapsing to ``0.0``.
    """
    try:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            iterator = resp.iter_bytes(chunk_bytes)

            t_first: float | None = None
            t_warm_end: float | None = None
            t_meter_start: float | None = None
            warmup_bytes = 0
            metered_bytes = 0
            elapsed = 0.0

            for chunk in iterator:
                now = clock()
                clen = len(chunk)

                if t_first is None:
                    t_first = now
                    t_warm_end = t_first + warmup_sec
                    warmup_bytes += clen
                    continue

                if t_meter_start is None:
                    assert t_warm_end is not None
                    if now < t_warm_end:
                        warmup_bytes += clen
                        continue
                    # Warmup is over — this chunk is the first metered one.
                    t_meter_start = now
                    metered_bytes = clen
                    continue

                # Metering phase.
                metered_bytes += clen
                elapsed = now - t_meter_start
                if elapsed >= window_sec or metered_bytes >= max_bytes:
                    break
    except httpx.ConnectError:
        return SampleResult(mbps=0.0, status="connect_fail", bytes_read=0, window_sec=0.0)
    except httpx.TimeoutException:
        return SampleResult(mbps=0.0, status="timeout", bytes_read=0, window_sec=0.0)
    except httpx.HTTPError:
        return SampleResult(mbps=0.0, status="connect_fail", bytes_read=0, window_sec=0.0)

    if t_first is None:
        return SampleResult(mbps=0.0, status="zero_body", bytes_read=0, window_sec=0.0)

    if t_meter_start is None:
        # Never reached the metering window — use warmup data as fallback.
        total_elapsed = max(clock() - t_first, 1e-9)
        bps = warmup_bytes / total_elapsed
        mbps = bps * 8 / 1024 / 1024
        status = "low_speed" if bps < _MIN_VALID_BPS else "ok"
        return SampleResult(
            mbps=mbps,
            status=status,
            bytes_read=warmup_bytes,
            window_sec=total_elapsed,
        )

    if elapsed <= 0.0:
        elapsed = max(clock() - t_meter_start, 1e-9)

    bps = metered_bytes / elapsed if elapsed > 0 else 0.0
    mbps = bps * 8 / 1024 / 1024
    status = "low_speed" if bps < _MIN_VALID_BPS else "ok"
    return SampleResult(
        mbps=mbps,
        status=status,
        bytes_read=metered_bytes,
        window_sec=elapsed,
    )


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


def _legacy_sampler_enabled() -> bool:
    """Kill switch: ``ISP_SPEED_LEGACY=true`` routes ``measure()`` through
    the v1 single-GET sampler. Any other value (or unset) runs the v2
    streaming sampler introduced by feat(isp-speed-test-v2).
    """
    return os.environ.get("ISP_SPEED_LEGACY", "false").strip().lower() == "true"


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("invalid %s=%r — falling back to %s", name, raw, default)
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("invalid %s=%r — falling back to %s", name, raw, default)
        return default


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

    Two sampler implementations exist:

    * **v2 (default)** — streamed measurement with warmup discard, time
      window, and structured failure classification. Gives accurate
      results on cross-border SOCKS5 paths where v1 systematically
      underestimates due to TCP slow-start + small probe file.
    * **v1 (opt-in via ``ISP_SPEED_LEGACY=true``)** — original single
      GET + ``resp.content`` + ``time.perf_counter`` wall-clock. Kept as
      a kill switch for one release cycle.

    Both paths share the same post-processing:
      - samples < 1 KiB/s are discarded as failed connections;
      - with ≥3 valid samples, drop min + max before averaging (truncated mean);
      - compute population stddev across ALL valid samples to surface
        the raw variability;
      - log a ``[稳定]/[轻微波动]/[波动较大]`` label based on CV.

    ``name`` is surfaced in the summary log line when supplied.
    """
    label_name = name or "节点"
    legacy = _legacy_sampler_enabled()
    sampler_tag = "v1" if legacy else "v2"

    # v2 needs a longer transport timeout than v1's 5s because a single
    # sample covers warmup + measurement window + slack. Respect the
    # caller's explicit override when it differs from the v1 default.
    effective_timeout = timeout
    if not legacy and timeout == 5.0:
        effective_timeout = _env_float("ISP_SPEED_TIMEOUT_SEC", _DEFAULT_SAMPLE_TIMEOUT_SEC)

    logger.info(
        "开始: %s%s | 测速源: %s | 采样: %d次 | sampler=%s",
        label_name,
        f" | 代理: {proxy}" if proxy else "",
        url,
        samples,
        sampler_tag,
    )

    client = _httpx_client(timeout=effective_timeout, proxy=proxy, proxy_auth=proxy_auth)
    if client is None:
        # Missing proxy transport dep (e.g. socksio for socks5h://). Log
        # above; return 0 so the caller treats the node as unreachable
        # instead of propagating the ImportError.
        return 0.0
    with client:
        valid: list[float] = []
        for idx in range(1, samples + 1):
            if legacy:
                bps = _sample_once(client, url)
            else:
                result = _stream_measure(
                    client,
                    url,
                    warmup_sec=_env_float("ISP_SPEED_WARMUP_SEC", _DEFAULT_WARMUP_SEC),
                    window_sec=_env_float("ISP_SPEED_WINDOW_SEC", _DEFAULT_WINDOW_SEC),
                    max_bytes=_env_int("ISP_SPEED_MAX_BYTES", _DEFAULT_MAX_BYTES),
                    chunk_bytes=_env_int("ISP_SPEED_CHUNK_BYTES", _DEFAULT_CHUNK_BYTES),
                )
                # Convert back to bytes/sec for the downstream
                # _truncated_mean_with_stability input. The round-trip
                # preserves result.mbps byte-for-byte since that
                # function's internal conversion is the exact inverse
                # (bps * 8 / 1024 / 1024).
                bps = result.mbps * 1024 * 1024 / 8 if result.status == "ok" else 0.0
            kbps = bps / 1024
            mbps_raw = bps * 8 / 1024 / 1024
            logger.info(
                "%s | 第 %d/%d 轮: %.0f KB/s → %.2f Mbps",
                label_name,
                idx,
                samples,
                kbps,
                mbps_raw,
            )
            if bps > _MIN_VALID_BPS:
                valid.append(bps)

    if not valid:
        logger.warning("%s: 全部 %d 次采样失败，返回 0", label_name, samples)
        return 0.0

    trimmed_mean, stddev, label = _truncated_mean_with_stability(valid)
    logger.info(
        "%s: %d/%d 有效样本，截断均值 %.2f Mbps，标准差 %.2f Mbps %s",
        label_name,
        len(valid),
        samples,
        trimmed_mean,
        stddev,
        label,
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
        logger.warning("status: cannot create %s: %s", path.parent, exc)
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
        logger.warning("status: cannot write %s: %s", path, exc)


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


_STALE_ENV_KEYS: Final[tuple[str, ...]] = (
    "ISP_TAG",
    "TOP_ISP_TAG",
    "proxy_max_speed",
    "FASTEST_PROXY_TAG",
    "IS_8K_SMOOTH",
    "DIRECT_SPEED",
    "HAS_ISP_NODES",
    "_ISP_SPEEDS_JSON",
)


def _resolve_sample_count(samples: int | None) -> int:
    """Pick the sample count from CLI arg, env, or default."""
    if samples is not None:
        return samples
    return int(os.environ.get("SPEED_SAMPLES", _SPEED_SAMPLES_DEFAULT))


def _try_cache_hit(cached_tag: str) -> bool:
    """Handle the ``ISP_TAG`` cache. Returns True iff fully handled.

    Validates the cached tag against the current ``*_ISP_IP`` env —
    when the operator drops an ISP from SECRET_FILE without clearing
    STATUS_FILE, xray would otherwise start with
    ``outbound tag proxy-X not found``. A stale cache falls through
    and the caller runs a fresh measurement.
    """
    nodes = _discover_isp_nodes()
    available_tags = {_isp_tag_for(prefix) for prefix, *_ in nodes}
    if cached_tag == "direct" or cached_tag in available_tags:
        logger.info("命中缓存 ISP_TAG=%s，跳过测速", cached_tag)
        if nodes:
            os.environ["HAS_ISP_NODES"] = "true"
            speeds = {tag: 0.0 for tag in available_tags}
            if cached_tag in speeds:
                speeds[cached_tag] = _KEEP_ON_CACHE_HIT_MBPS
            os.environ["_ISP_SPEEDS_JSON"] = _json_speeds(speeds)
        return True
    logger.warning(
        "缓存 ISP_TAG=%s 在当前 *_ISP_IP 环境里已不存在 (现有 %s)，清缓存后重新测速",
        cached_tag,
        sorted(available_tags) or "无",
    )
    return False


def _reset_caches_for_fresh_run() -> None:
    """Wipe STATUS_FILE ``*_OUT`` + pop the in-process env keys."""
    _purge_service_caches()
    for key in _STALE_ENV_KEYS:
        os.environ.pop(key, None)


def _log_routing_inputs() -> None:
    region = os.environ.get("GEOIP_INFO", "").split("|", 1)[0] or "未知"
    logger.info(
        "IP_TYPE=%s | 地区=%s | DEFAULT_ISP=%s",
        os.environ.get("IP_TYPE", "未知"),
        region,
        os.environ.get("DEFAULT_ISP", "未设置"),
    )


def _measure_direct_baseline(url: str, sample_count: int) -> float:
    """Direct speed measurement, not used for routing but for 8K 判定."""
    direct_mbps = measure(url, samples=sample_count)
    os.environ["DIRECT_SPEED"] = f"{direct_mbps:.2f}"
    show_report(direct_mbps, name="Direct")
    logger.info(
        "直连基准: %.2f Mbps（不参与选路；无代理时用于 IS_8K_SMOOTH 判定）",
        direct_mbps,
    )
    return direct_mbps


def _measure_isp_nodes(url: str, sample_count: int) -> IspSpeedContext:
    """Iterate every configured ISP node and return the aggregated context."""
    nodes = _discover_isp_nodes()
    ctx = IspSpeedContext()
    if not nodes:
        logger.warning("未发现 ISP 节点（无 *_ISP_IP 环境变量），将回退直连")
        return ctx

    os.environ["HAS_ISP_NODES"] = "true"
    logger.info("发现 ISP 节点 %d 个，逐节点采样 %d 次", len(nodes), sample_count)
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
            logger.info("%s: %.2f Mbps → 新最优", tag, mbps)
        else:
            logger.info(
                "%s: %.2f Mbps (最优仍: %s %.2f Mbps)",
                tag,
                mbps,
                ctx.fastest_tag or "未定",
                ctx.fastest_speed,
            )
    return ctx


def _persist_routing_decision(direct_mbps: float, ctx: IspSpeedContext) -> None:
    """Feed the routing logic and export the decision to env + STATUS_FILE."""
    from sb_xray.routing.isp import RoutingContext, apply_isp_routing_logic

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
    logger.info(
        "ISP_TAG=%s IS_8K_SMOOTH=%s",
        decision.isp_tag,
        os.environ["IS_8K_SMOOTH"],
    )

    # Phase 2 observability: structured event so ops can track every
    # speed-test outcome (boot-time and cron-triggered alike).
    from sb_xray.events import emit_event

    emit_event(
        "isp.speed_test.result",
        {
            "direct_mbps": round(direct_mbps, 2),
            "fastest_tag": ctx.fastest_tag or "",
            "fastest_mbps": round(ctx.fastest_speed, 2),
            "speeds": {t: round(v, 2) for t, v in ctx.speeds.items()},
            "isp_tag": decision.isp_tag,
            "is_8k_smooth": decision.is_8k_smooth,
        },
    )


def run_isp_speed_tests(
    *,
    samples: int | None = None,
    url: str = _SPEED_TEST_URL,
    force: bool = False,
) -> None:
    """Port of ``run_speed_tests_if_needed`` (entrypoint.sh:1153).

    Orchestrator — composed of 5 single-purpose helpers:

      1. :func:`_try_cache_hit` — honor a valid ``ISP_TAG`` cache.
      2. :func:`_reset_caches_for_fresh_run` — wipe stale state.
      3. :func:`_measure_direct_baseline` — direct speed for 8K 判定.
      4. :func:`_measure_isp_nodes` — per-``*_ISP_IP`` SOCKS5h probes.
      5. :func:`_persist_routing_decision` — route + write STATUS_FILE.

    ``force=True`` (Phase 3) bypasses the ``ISP_TAG`` cache hit path —
    the periodic retest cron needs a real measurement every time.
    """
    sample_count = _resolve_sample_count(samples)

    if not force:
        # Phase 5: optional TTL cache. Use last retest timestamp + cached
        # speeds on cold boot; kick off a daemon thread to refresh in the
        # background so the boot stays fast.
        if _try_speed_cache_hit():
            return
        cached_tag = os.environ.get("ISP_TAG", "").strip()
        if cached_tag and _try_cache_hit(cached_tag):
            return

    _reset_caches_for_fresh_run()
    _log_routing_inputs()
    direct_mbps = _measure_direct_baseline(url, sample_count)
    ctx = _measure_isp_nodes(url, sample_count)
    _persist_routing_decision(direct_mbps, ctx)


def _try_speed_cache_hit() -> bool:
    """Phase 5 cold-boot cache.

    Returns True if the STATUS_FILE has a recent ``_ISP_SPEEDS_JSON`` +
    ``ISP_LAST_RETEST_TS`` pair within ``ISP_SPEED_CACHE_TTL_MIN`` (default
    60 minutes, ``0`` disables). When hit, the cached speeds are loaded
    into env and an async daemon thread refreshes in the background.

    Defensive: any parse / filesystem error turns into a cache miss (log
    at DEBUG, run the full speed test normally).
    """
    raw_ttl = os.environ.get("ISP_SPEED_CACHE_TTL_MIN", "60").strip()
    try:
        ttl_min = float(raw_ttl) if raw_ttl else 60.0
    except ValueError:
        ttl_min = 60.0
    if ttl_min <= 0:
        return False

    path = _status_file()
    if not path.is_file():
        return False

    status: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^export (\w+)=['\"]?(.*?)['\"]?$", line.strip())
            if m:
                status[m.group(1)] = m.group(2)
    except OSError:
        return False

    ts_raw = status.get("ISP_LAST_RETEST_TS", "")
    speeds_raw = status.get("_ISP_SPEEDS_JSON", "")
    isp_tag = status.get("ISP_TAG", "")
    is_8k = status.get("IS_8K_SMOOTH", "")
    if not (ts_raw and speeds_raw and isp_tag):
        return False
    try:
        ts = int(ts_raw)
    except ValueError:
        return False

    import time

    age_min = (time.time() - ts) / 60.0
    if age_min > ttl_min:
        logger.info(
            "speed cache stale (age=%.1fmin > ttl=%.1fmin) — running live test",
            age_min,
            ttl_min,
        )
        return False

    # Validate parsed speeds before accepting.
    try:
        _json.loads(speeds_raw)
    except _json.JSONDecodeError:
        return False

    os.environ["_ISP_SPEEDS_JSON"] = speeds_raw
    os.environ["ISP_TAG"] = isp_tag
    if is_8k:
        os.environ["IS_8K_SMOOTH"] = is_8k
    os.environ["HAS_ISP_NODES"] = "true" if isp_tag not in ("", "direct", "block") else ""
    logger.info(
        "speed cache hit (age=%.1fmin ttl=%.1fmin) — deferring live test to background",
        age_min,
        ttl_min,
    )

    from sb_xray.events import emit_event

    emit_event(
        "isp.speed_test.cache_hit",
        {"age_min": round(age_min, 2), "ttl_min": ttl_min, "isp_tag": isp_tag},
    )

    if os.environ.get("ISP_SPEED_CACHE_ASYNC", "true").strip().lower() != "false":
        _spawn_async_refresh()
    return True


def _spawn_async_refresh() -> None:
    """Background daemon refreshing speeds after a cache hit."""
    import threading

    def _runner() -> None:
        try:
            run_isp_speed_tests(force=True)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("async speed refresh failed: %s", exc)
            from sb_xray.events import emit_event

            emit_event("isp.speed_test.error", {"error": repr(exc), "stage": "async_refresh"})

    t = threading.Thread(target=_runner, name="isp-speed-refresh", daemon=True)
    t.start()


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
