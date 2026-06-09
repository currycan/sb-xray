"""Download speed measurement (entrypoint.sh §9 equivalent).

Replaces curl's ``-w '%{speed_download}'`` with an httpx client that
downloads the full body, timed with ``time.perf_counter``. A node
context helper is provided so callers can record multiple probes and
surface the fastest one — preserving the Bash ``proxy_max_speed`` /
``FASTEST_PROXY_TAG`` semantics.
"""

from __future__ import annotations

import contextlib
import fcntl
import json as _json
import logging
import os
import re
import sys
import tempfile
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
_DEFAULT_SAMPLE_RETRIES: Final[int] = 1


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


# Sample statuses that are worth one immediate retry: a transient connection
# blip or a mid-transfer timeout often clears on a second attempt, whereas
# low_speed / zero_body reflect a real (if poor) measurement.
_TRANSIENT_STATUSES: Final[frozenset[str]] = frozenset({"connect_fail", "timeout"})


def _stream_measure_with_retry(
    client: httpx.Client,
    url: str,
    *,
    retries: int,
    **kwargs: object,
) -> SampleResult:
    """Run :func:`_stream_measure`, retrying once (by default) on a transient
    failure so a single connection blip on a healthy proxy does not get the
    whole node written off as down.
    """
    result = _stream_measure(client, url, **kwargs)  # type: ignore[arg-type]
    attempt = 0
    while result.status in _TRANSIENT_STATUSES and attempt < retries:
        attempt += 1
        logger.info("采样瞬时失败(%s)，重试第 %d 次", result.status, attempt)
        result = _stream_measure(client, url, **kwargs)  # type: ignore[arg-type]
    return result


def _truncated_mean_with_stability(
    samples_bps: list[float],
) -> tuple[float, float, str]:
    """Robust central tendency + stddev + stability label.

    For ``n>=3`` the central value is the **median** — it rejects a single
    slow/fast outlier sample outright, which matters on the jittery
    cross-border SOCKS5 paths these proxies live on. (At ``n==3`` the median
    equals the old truncated mean, so existing n=3 behaviour is preserved;
    at ``n>=5`` the median is strictly more outlier-resistant.) For ``n<3``
    it falls back to the plain mean.

    Returns ``(central_mbps, stddev_mbps, label)`` where ``label`` is one of
    ``[稳定]`` (CV<0.2), ``[轻微波动]`` (CV<0.5), ``[波动较大]``. Empty input
    yields ``(0.0, 0.0, "[稳定]")`` to stay side-effect free.
    """
    n = len(samples_bps)
    if n == 0:
        return 0.0, 0.0, "[稳定]"

    def _to_mbps(bps: float) -> float:
        return bps * 8 / 1024 / 1024

    all_mbps = sorted(_to_mbps(v) for v in samples_bps)
    if n >= 3:
        mid = n // 2
        central = all_mbps[mid] if n % 2 else (all_mbps[mid - 1] + all_mbps[mid]) / 2
    else:
        central = sum(all_mbps) / n

    full_mean = sum(all_mbps) / n
    variance = sum((v - full_mean) ** 2 for v in all_mbps) / n
    stddev = variance**0.5

    cv = stddev / central if central > 0 else 0.0
    if cv < 0.2:
        label = "[稳定]"
    elif cv < 0.5:
        label = "[轻微波动]"
    else:
        label = "[波动较大]"
    return round(central, 2), round(stddev, 2), label


def _resolve_tag_probe_url(tag: str, fallback: str) -> str:
    """Look up ``tag`` in ``ISP_SPEED_URL_MAP`` JSON; return ``fallback`` if
    unset, missing, or malformed.

    Example ``ISP_SPEED_URL_MAP``::

        {"proxy-kr-isp": "https://kr-speed.example/100mb",
         "proxy-us-isp": "https://us-speed.example/100mb"}

    Rationale: a single Cloudflare URL is a poor benchmark across
    geographies because Cloudflare's routing, per-region PoP load, and
    any ISP-side CF rate-limiting inject noise that dominates the v2
    bandwidth signal. Operators can pin a geo-appropriate target per
    tag — if any tag is not listed, the fallback URL is used so
    unconfigured deployments keep working.
    """
    raw = os.environ.get("ISP_SPEED_URL_MAP", "").strip()
    if not raw:
        return fallback
    try:
        mapping = _json.loads(raw)
    except _json.JSONDecodeError:
        logger.warning("invalid ISP_SPEED_URL_MAP JSON (ignored): %r", raw[:80])
        return fallback
    if not isinstance(mapping, dict):
        return fallback
    candidate = mapping.get(tag)
    if isinstance(candidate, str) and candidate.strip():
        return candidate.strip()
    return fallback


def _adaptive_warmup_sec(*, base: float, rtt_sec: float) -> float:
    """Extend ``base`` to ``max(base, 10*rtt_sec)`` capped at 5 seconds.

    TCP slow-start doubles cwnd every RTT; 5 doublings (≈ 10 RTTs) clears
    the exponential ramp on most OSes. For RTT=200ms (cross-border
    typical) this pulls warmup from 1.5s default to 2.0s. For pathological
    RTT the cap prevents burning the entire sample budget on warmup.
    """
    rtt_sec = max(0.0, rtt_sec)
    target = max(base, 10.0 * rtt_sec)
    return min(5.0, target)


def _probe_rtt(client: httpx.Client, url: str) -> float | None:
    """Estimate one-way RTT via a HEAD request. Returns seconds or None.

    Defensive: any transport error falls through as ``None`` so callers
    can skip adaptive warmup and proceed with the ``base`` value.
    """
    try:
        t0 = time.monotonic()
        resp = client.head(url)
        elapsed = time.monotonic() - t0
        resp.close() if hasattr(resp, "close") else None
        return max(0.0, elapsed)
    except httpx.HTTPError:
        return None


def _aggregate_diag(statuses: list[str], samples: list[SampleResult]) -> dict[str, object]:
    """Summarize a batch of v2 SampleResults into a single per-tag diag record.

    Schema (stable, consumed by _ISP_SPEEDS_DIAG_JSON + event payload):
      - ``status``: overall classification — ``ok`` (all ok),
        the shared failure code if all samples share one, or
        ``mixed`` if samples disagree.
      - ``ok``: count of status=="ok" samples.
      - ``total``: total samples attempted.
      - ``statuses``: per-sample list for deep troubleshooting.
      - ``bytes``: sum of bytes_read across samples.
      - ``window_sec``: sum of window_sec across samples.
    """
    ok_count = sum(1 for s in statuses if s == "ok")
    unique = set(statuses)
    if not statuses:
        status = "zero_body"
    elif unique == {"ok"}:
        status = "ok"
    elif len(unique) == 1:
        status = next(iter(unique))
    else:
        status = "mixed"
    return {
        "status": status,
        "ok": ok_count,
        "total": len(statuses),
        "statuses": list(statuses),
        "bytes": sum(s.bytes_read for s in samples),
        "window_sec": round(sum(s.window_sec for s in samples), 2),
    }


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
                result = _stream_measure_with_retry(
                    client,
                    url,
                    retries=_env_int("ISP_SPEED_SAMPLE_RETRIES", _DEFAULT_SAMPLE_RETRIES),
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


def measure_detailed(
    url: str,
    *,
    samples: int = 3,
    proxy: str | None = None,
    proxy_auth: str | None = None,
    timeout: float = 5.0,
    name: str | None = None,
) -> tuple[float, dict[str, object]]:
    """Variant of :func:`measure` that also returns a diag dict.

    Runs the v2 streaming sampler (ignores ``ISP_SPEED_LEGACY`` because
    diag is a v2-only artefact), aggregates per-sample SampleResults
    via :func:`_aggregate_diag`, and returns ``(mbps, diag)``.

    For the v1 legacy path, callers should call :func:`measure` directly
    — diag is not meaningful when the v1 sampler has no structured
    failure classification.
    """
    label_name = name or "节点"
    effective_timeout = _env_float("ISP_SPEED_TIMEOUT_SEC", _DEFAULT_SAMPLE_TIMEOUT_SEC)

    logger.info(
        "开始(diag): %s%s | 测速源: %s | 采样: %d次",
        label_name,
        f" | 代理: {proxy}" if proxy else "",
        url,
        samples,
    )

    client = _httpx_client(timeout=effective_timeout, proxy=proxy, proxy_auth=proxy_auth)
    if client is None:
        return 0.0, {
            "status": "proxy_dep_missing",
            "ok": 0,
            "total": samples,
            "statuses": ["proxy_dep_missing"] * samples,
            "bytes": 0,
            "window_sec": 0.0,
        }

    base_warmup = _env_float("ISP_SPEED_WARMUP_SEC", _DEFAULT_WARMUP_SEC)
    effective_warmup = base_warmup
    rtt_adaptive = os.environ.get("ISP_SPEED_RTT_ADAPTIVE", "false").strip().lower() == "true"

    with client:
        if rtt_adaptive:
            rtt = _probe_rtt(client, url)
            if rtt is not None:
                effective_warmup = _adaptive_warmup_sec(base=base_warmup, rtt_sec=rtt)
                logger.info(
                    "%s | RTT=%.3fs → warmup %.2fs (base=%.2f)",
                    label_name,
                    rtt,
                    effective_warmup,
                    base_warmup,
                )

        results: list[SampleResult] = []
        for _ in range(samples):
            result = _stream_measure_with_retry(
                client,
                url,
                retries=_env_int("ISP_SPEED_SAMPLE_RETRIES", _DEFAULT_SAMPLE_RETRIES),
                warmup_sec=effective_warmup,
                window_sec=_env_float("ISP_SPEED_WINDOW_SEC", _DEFAULT_WINDOW_SEC),
                max_bytes=_env_int("ISP_SPEED_MAX_BYTES", _DEFAULT_MAX_BYTES),
                chunk_bytes=_env_int("ISP_SPEED_CHUNK_BYTES", _DEFAULT_CHUNK_BYTES),
            )
            results.append(result)

    valid_bps = [r.mbps * 1024 * 1024 / 8 for r in results if r.status == "ok"]
    if valid_bps:
        mbps, _stddev, _label = _truncated_mean_with_stability(valid_bps)
    else:
        mbps = 0.0

    diag = _aggregate_diag([r.status for r in results], results)
    return mbps, diag


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

    ``diag`` holds optional v2 sampler per-tag diagnostics
    (``status`` / ``ok`` / ``total`` / ``bytes`` / ``window_sec``). It is
    exposed via ``_ISP_SPEEDS_DIAG_JSON`` in the STATUS_FILE *separately*
    from ``speeds``; the primary ``_ISP_SPEEDS_JSON`` stays
    ``{tag: float}`` so ``stages/isp_retest.py`` keeps parsing unchanged.
    """

    tolerance: float = 1.0
    speeds: dict[str, float] = field(default_factory=dict)
    fastest_tag: str | None = None
    fastest_speed: float = 0.0
    diag: dict[str, dict[str, object]] = field(default_factory=dict)

    def record(
        self,
        tag: str,
        mbps: float,
        *,
        diag: dict[str, object] | None = None,
    ) -> None:
        self.speeds[tag] = mbps
        if diag is not None:
            self.diag[tag] = diag
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
# 3 samples give a median that rejects a single slow/fast outlier on the
# jittery cross-border SOCKS5 paths these proxies live on. Was 2 (no median
# possible); bumped together with the v2 sampler hardening.
_SPEED_SAMPLES_DEFAULT: Final[int] = 3
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


# Strictly-positive Mbps marks a tag as "usable"; 0.0 means every sample
# failed (connect_fail / timeout / low_speed), i.e. a dead line that must not
# steer routing or linger in the balancer selector.
_USABLE_MIN_MBPS: Final[float] = 0.0
_DEFAULT_LEADER_HYSTERESIS: Final[float] = 1.15


def _usable_speed_tags(speeds: dict[str, float]) -> set[str]:
    """Tags whose measured speed clears the usable floor (strictly > 0)."""
    return {t for t, v in speeds.items() if v > _USABLE_MIN_MBPS}


def _read_status_snapshot() -> dict[str, str]:
    """Parse ``export KEY='VALUE'`` lines from STATUS_FILE into a flat dict.

    Returns ``{}`` on any read/parse failure so callers degrade to "no prior
    state" (which is the safe, notify-on-first-run default). Shares the line
    grammar with :func:`_try_speed_cache_hit`.
    """
    path = _status_file()
    if not path.is_file():
        return {}
    snapshot: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^export (\w+)=['\"]?(.*?)['\"]?$", line.strip())
            if m:
                snapshot[m.group(1)] = m.group(2)
    except OSError:
        return {}
    return snapshot


def _atomic_write_text(path: Path, text: str) -> None:
    """Write via tmp file + ``os.replace`` so readers never see a half file.

    ``os.replace`` is an atomic rename within the same directory on POSIX, so a
    concurrent reader (or an ``exec``-killed async daemon) sees either the old
    complete file or the new complete file — never a truncated one.
    """
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".status.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)  # POSIX rename — atomic within same dir
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _write_status_line(key: str, value: str) -> None:
    """Upsert ``export KEY='VALUE'`` in ``STATUS_FILE``, atomic + flock-serialized.

    The read-modify-write runs under an exclusive ``flock`` so concurrent writers
    (main thread + async refresh daemon) never clobber each other's lines, and
    the actual file swap is atomic (:func:`_atomic_write_text`). Silently warns +
    returns on ``OSError`` so a read-only status dir never aborts boot (the bash
    equivalent used ``|| true`` on every sed).
    """
    path = _status_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("status: cannot create %s: %s", path.parent, exc)
        return
    lock = path.parent / ".status.lock"
    pattern = re.compile(rf"^export {re.escape(key)}=.*\n?", re.MULTILINE)
    try:
        with open(lock, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            existing = path.read_text(encoding="utf-8") if path.is_file() else ""
            cleaned = pattern.sub("", existing).rstrip("\n")
            if cleaned:
                cleaned += "\n"
            cleaned += f"export {key}='{value}'\n"
            _atomic_write_text(path, cleaned)
    except OSError as exc:
        logger.warning("status: cannot write %s: %s", path, exc)


def _purge_service_caches() -> None:
    """entrypoint.sh:1183 — drop stale ``*_OUT`` caches from STATUS_FILE.

    Called when ``ISP_TAG`` is being recomputed so downstream media probes
    re-run against the fresh routing decision.
    """
    path = _status_file()
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
    if path.is_file():
        lock = path.parent / ".status.lock"
        try:
            with open(lock, "w") as lf:
                fcntl.flock(lf, fcntl.LOCK_EX)
                text = path.read_text(encoding="utf-8")
                for key in removed_keys:
                    text = re.sub(
                        rf"^export {re.escape(key)}=.*\n?", "", text, flags=re.MULTILINE
                    )
                _atomic_write_text(path, text)
        except OSError as exc:
            logger.warning("status: purge failed %s: %s", path, exc)
    for key in removed_keys:
        os.environ.pop(key, None)


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
    """Pick the sample count from CLI arg, env, or default.

    ``ISP_SPEED_SAMPLES`` is the canonical knob (the rest of the v2 sampler
    env is ``ISP_SPEED_*``); ``SPEED_SAMPLES`` is kept as a legacy alias so
    existing deployments keep working. Previously only ``SPEED_SAMPLES`` was
    read, so a deployment setting ``ISP_SPEED_SAMPLES`` was silently ignored.
    """
    if samples is not None:
        return samples
    if os.environ.get("ISP_SPEED_SAMPLES", "").strip():
        return _env_int("ISP_SPEED_SAMPLES", _SPEED_SAMPLES_DEFAULT)
    return _env_int("SPEED_SAMPLES", _SPEED_SAMPLES_DEFAULT)


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


def measure_isp_speeds(url: str, sample_count: int) -> SpeedOutcome:
    """Pure measurement: network IO + decision compute. NO env/STATUS writes.

    Reads the *previous* speeds from STATUS_FILE (read-only) to drive leader
    hysteresis and the notify edge-trigger, then returns an immutable
    :class:`SpeedOutcome`. Side effects are the caller's job
    (:func:`apply_outcome_to_env` / :func:`persist_outcome_to_status` /
    :func:`_emit_outcome_event`), which keeps this safe to run from the async
    refresh thread.
    """
    from sb_xray.routing.isp import RoutingContext, apply_isp_routing_logic

    # Direct baseline — not used for routing, only the 8K verdict when no proxy.
    direct_mbps = measure(url, samples=sample_count)
    show_report(direct_mbps, name="Direct")
    logger.info(
        "直连基准: %.2f Mbps（不参与选路；无代理时用于 IS_8K_SMOOTH 判定）",
        direct_mbps,
    )

    ctx = _measure_isp_nodes(url, sample_count)
    has_isp = bool(ctx.speeds)

    # Snapshot prior persisted state (from STATUS_FILE, not env, so it works in
    # the fresh cron-retest process) for hysteresis + notify edge-trigger.
    prev = _read_status_snapshot()
    prev_speeds: dict[str, float] = {}
    prev_raw = prev.get("_ISP_SPEEDS_JSON", "").strip()
    if prev_raw:
        try:
            prev_speeds = {str(k): float(v) for k, v in _json.loads(prev_raw).items()}
        except (ValueError, TypeError):
            prev_speeds = {}

    leader_tag, leader_speed = _leader_with_hysteresis(ctx, prev_speeds)
    decision = apply_isp_routing_logic(
        RoutingContext(
            ip_type=os.environ.get("IP_TYPE", "unknown"),
            geoip_info=os.environ.get("GEOIP_INFO", ""),
            default_isp=os.environ.get("DEFAULT_ISP", ""),
            direct_speed=direct_mbps,
            fastest_proxy_tag=leader_tag,
            proxy_max_speed=leader_speed,
        )
    )
    notify = _should_notify(
        prev=prev,
        new_speeds=ctx.speeds,
        new_isp_tag=decision.isp_tag,
        new_fastest_mbps=leader_speed,
    )
    logger.info(
        "ISP_TAG=%s IS_8K_SMOOTH=%s",
        decision.isp_tag,
        "true" if decision.is_8k_smooth else "false",
    )
    return SpeedOutcome(
        speeds=dict(ctx.speeds),
        diag=dict(ctx.diag) if ctx.diag else None,
        direct_mbps=direct_mbps,
        fastest_tag=leader_tag,
        fastest_speed=leader_speed,
        isp_tag=decision.isp_tag,
        is_8k_smooth=decision.is_8k_smooth,
        has_isp_nodes=has_isp,
        notify=notify,
    )


def _measure_isp_nodes(url: str, sample_count: int) -> IspSpeedContext:
    """Iterate every configured ISP node and return the aggregated context."""
    nodes = _discover_isp_nodes()
    ctx = IspSpeedContext()
    if not nodes:
        logger.warning("未发现 ISP 节点（无 *_ISP_IP 环境变量），将回退直连")
        return ctx

    # HAS_ISP_NODES is no longer written here — that env mutation would leak from
    # the async refresh thread. The signal is carried by SpeedOutcome.has_isp_nodes
    # (= bool(ctx.speeds)) and applied to env only in the main process via
    # apply_outcome_to_env.
    legacy = _legacy_sampler_enabled()
    logger.info(
        "发现 ISP 节点 %d 个，逐节点采样 %d 次 | sampler=%s",
        len(nodes),
        sample_count,
        "v1" if legacy else "v2",
    )
    for prefix, ip, port, user, password in nodes:
        tag = _isp_tag_for(prefix)
        proxy_auth = f"{user}:{password}" if user and password else None
        # Per-tag URL override (Phase D) — falls back to global ``url``
        # when ISP_SPEED_URL_MAP is unset or doesn't list this tag.
        tag_url = _resolve_tag_probe_url(tag, url)
        if legacy:
            mbps = measure(
                tag_url,
                samples=sample_count,
                proxy=_proxy_url(ip, port),
                proxy_auth=proxy_auth,
                name=prefix,
            )
            diag: dict[str, object] | None = None
        else:
            mbps, diag = measure_detailed(
                tag_url,
                samples=sample_count,
                proxy=_proxy_url(ip, port),
                proxy_auth=proxy_auth,
                name=prefix,
            )
        show_report(mbps, name=prefix)
        ctx.record(tag, mbps, diag=diag)
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


def _leader_with_hysteresis(
    ctx: IspSpeedContext, prev_speeds: dict[str, float]
) -> tuple[str | None, float]:
    """Apply cross-run hysteresis to the fastest-tag pick.

    The within-run ``IspSpeedContext`` already names a winner, but on jittery
    proxies the raw argmax flips between retests on sub-percent noise. To keep
    the *reported* leader stable, we retain the **previous** run's leader
    unless this run's winner beats it by ``ISP_LEADER_HYSTERESIS`` (default
    1.15 = 15%). The previous leader must still be usable this run to be kept.

    Returns ``(tag, speed)`` — ``ctx``'s own winner when there is no eligible
    incumbent. Note this only stabilises the headline/verdict; live routing is
    handled by xray ``leastPing`` regardless of this pick.
    """
    if not ctx.fastest_tag or not prev_speeds:
        return ctx.fastest_tag, ctx.fastest_speed
    prev_leader = max(prev_speeds.items(), key=lambda kv: kv[1])[0]
    if prev_leader == ctx.fastest_tag:
        return ctx.fastest_tag, ctx.fastest_speed
    incumbent_speed = ctx.speeds.get(prev_leader, 0.0)
    if incumbent_speed <= _USABLE_MIN_MBPS:
        return ctx.fastest_tag, ctx.fastest_speed
    margin = _env_float("ISP_LEADER_HYSTERESIS", _DEFAULT_LEADER_HYSTERESIS)
    if ctx.fastest_speed <= incumbent_speed * margin:
        logger.info(
            "leader 滞回: 保留上轮 %s (%.2f Mbps)，挑战者 %s (%.2f) 未超 %.0f%% 余量",
            prev_leader,
            incumbent_speed,
            ctx.fastest_tag,
            ctx.fastest_speed,
            (margin - 1) * 100,
        )
        return prev_leader, incumbent_speed
    return ctx.fastest_tag, ctx.fastest_speed


def _should_notify(
    *,
    prev: dict[str, str],
    new_speeds: dict[str, float],
    new_isp_tag: str,
    new_fastest_mbps: float,
) -> bool:
    """Edge-trigger: only push a Telegram alert on a *notable* change.

    Notable = first-ever result, usable-membership change (a line went up or
    down), selected-tag change, or a rating-tier flip (e.g. 4K→1080P). Pure
    bandwidth jitter that leaves all of these unchanged stays silent, killing
    the every-retest spam.
    """
    prev_raw = prev.get("_ISP_SPEEDS_JSON", "").strip()
    if not prev_raw:
        return True
    try:
        prev_speeds = {str(k): float(v) for k, v in _json.loads(prev_raw).items()}
    except (ValueError, TypeError):
        return True
    if _usable_speed_tags(prev_speeds) != _usable_speed_tags(new_speeds):
        return True
    if prev.get("ISP_TAG", "") != new_isp_tag:
        return True
    prev_fastest = max(prev_speeds.values(), default=0.0)
    return rate(prev_fastest) != rate(new_fastest_mbps)


@dataclass(frozen=True)
class SpeedOutcome:
    """Immutable result of one speed-test run — no side effects to produce.

    Decouples *measurement* (network IO + decision compute) from *side effects*
    (env writes, STATUS_FILE writes, event emission). The async refresh daemon
    may compute an outcome but must never apply it to ``os.environ`` — only the
    main process owns the env that cold-boot config generation consumes.
    """

    speeds: dict[str, float]
    diag: dict[str, dict[str, object]] | None
    direct_mbps: float
    fastest_tag: str | None
    fastest_speed: float
    isp_tag: str
    is_8k_smooth: bool
    has_isp_nodes: bool
    notify: bool


def _diag_enabled() -> bool:
    return os.environ.get("ISP_SPEED_DIAG_ENABLED", "true").strip().lower() != "false"


def apply_outcome_to_env(o: SpeedOutcome) -> None:
    """Write the outcome into ``os.environ``. MAIN-PROCESS ONLY (never async).

    Mirrors the env exports the old ``_persist_routing_decision`` performed
    inline, plus ``DIRECT_SPEED`` (formerly set by ``_measure_direct_baseline``)
    so the split keeps full parity.
    """
    os.environ["DIRECT_SPEED"] = f"{o.direct_mbps:.2f}"
    os.environ["_ISP_SPEEDS_JSON"] = _json_speeds(o.speeds)
    if _diag_enabled() and o.diag:
        os.environ["_ISP_SPEEDS_DIAG_JSON"] = _json.dumps(o.diag)
    if o.fastest_tag:
        os.environ["FASTEST_PROXY_TAG"] = o.fastest_tag
        os.environ["proxy_max_speed"] = f"{o.fastest_speed:.2f}"  # noqa: SIM112
    os.environ["ISP_TAG"] = o.isp_tag
    os.environ["IS_8K_SMOOTH"] = "true" if o.is_8k_smooth else "false"
    os.environ["HAS_ISP_NODES"] = "true" if o.has_isp_nodes else ""


def persist_outcome_to_status(o: SpeedOutcome) -> None:
    """Persist the outcome to STATUS_FILE (atomic). Safe from any thread.

    Each ``_write_status_line`` call is individually atomic + flock-serialized;
    there is no cross-key transaction (an ``exec``-kill between keys leaves a
    syntactically-valid mix of old/new lines, never a corrupt file).
    """
    _write_status_line("_ISP_SPEEDS_JSON", _json_speeds(o.speeds))
    if _diag_enabled() and o.diag:
        _write_status_line("_ISP_SPEEDS_DIAG_JSON", _json.dumps(o.diag))
    _write_status_line("IS_8K_SMOOTH", "true" if o.is_8k_smooth else "false")
    _write_status_line("ISP_TAG", o.isp_tag)


def _emit_outcome_event(o: SpeedOutcome) -> None:
    """Emit the ``isp.speed_test.result`` observability event for an outcome."""
    from sb_xray.events import emit_event

    payload: dict[str, object] = {
        "direct_mbps": round(o.direct_mbps, 2),
        "fastest_tag": o.fastest_tag or "",
        "fastest_mbps": round(o.fastest_speed, 2),
        "speeds": {t: round(v, 2) for t, v in o.speeds.items()},
        "isp_tag": o.isp_tag,
        "is_8k_smooth": o.is_8k_smooth,
        "notify": o.notify,
    }
    if o.diag:
        payload["diag"] = o.diag
    emit_event("isp.speed_test.result", payload)


def run_isp_speed_tests(
    *,
    samples: int | None = None,
    url: str = _SPEED_TEST_URL,
    force: bool = False,
) -> None:
    """Port of ``run_speed_tests_if_needed`` (entrypoint.sh:1153).

    Main-process orchestrator:

      1. :func:`_try_speed_cache_hit` / :func:`_try_cache_hit` — honor a valid
         cache (unless ``force``).
      2. :func:`_reset_caches_for_fresh_run` — wipe stale env + STATUS ``*_OUT``.
      3. :func:`measure_isp_speeds` — pure measurement → :class:`SpeedOutcome`.
      4. :func:`apply_outcome_to_env` — write env (MAIN PROCESS ONLY).
      5. :func:`persist_outcome_to_status` — atomic STATUS_FILE write.
      6. :func:`_emit_outcome_event` — observability event.

    ``force=True`` (Phase 3) bypasses the cache hit path — the periodic retest
    cron needs a real measurement every time. The async cache-hit refresh uses
    :func:`_async_refresh_once` instead (persist only, never mutate env).
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

    _reset_caches_for_fresh_run()  # main-process cache purge
    _log_routing_inputs()
    outcome = measure_isp_speeds(url, sample_count)
    apply_outcome_to_env(outcome)  # main process: write env
    persist_outcome_to_status(outcome)  # atomic STATUS_FILE
    _emit_outcome_event(outcome)


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


def _async_refresh_once(url: str, sample_count: int) -> None:
    """Background refresh body — measure + atomic persist ONLY. Never env.

    The old runner called ``run_isp_speed_tests(force=True)``, which both purged
    env (``_reset_caches_for_fresh_run``) and wrote env (``apply_outcome_to_env``)
    — racing the main thread that was still consuming ``HAS_ISP_NODES`` during
    cold-boot config generation. This body touches neither: it only computes an
    outcome and atomically persists it to STATUS_FILE (safe under ``exec``-kill).
    """
    outcome = measure_isp_speeds(url, sample_count)
    persist_outcome_to_status(outcome)
    _emit_outcome_event(outcome)


def _spawn_async_refresh() -> None:
    """Background daemon refreshing speeds after a cache hit (persist only)."""
    import threading

    sample_count = _resolve_sample_count(None)
    url = _SPEED_TEST_URL

    def _runner() -> None:
        try:
            _async_refresh_once(url, sample_count)
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
