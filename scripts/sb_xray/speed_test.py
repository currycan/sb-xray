"""Download speed measurement (entrypoint.sh §9 equivalent).

Replaces curl's ``-w '%{speed_download}'`` with an httpx client that
downloads the full body, timed with ``time.perf_counter``. A node
context helper is provided so callers can record multiple probes and
surface the fastest one — preserving the Bash ``proxy_max_speed`` /
``FASTEST_PROXY_TAG`` semantics.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from typing import Final

import httpx

from sb_xray import http as sbhttp

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


def measure(
    url: str,
    *,
    samples: int = 3,
    proxy: str | None = None,
    proxy_auth: str | None = None,
    timeout: float = 5.0,
) -> float:
    """Sample ``url`` ``samples`` times; return mean throughput in Mbps.

    Samples below 1 KiB/s are discarded. If every sample fails, returns
    ``0.0`` (matching the Bash contract).
    """
    with _httpx_client(timeout=timeout, proxy=proxy, proxy_auth=proxy_auth) as client:
        valid: list[float] = []
        for _ in range(samples):
            bps = _sample_once(client, url)
            if bps > _MIN_VALID_BPS:
                valid.append(bps)
    if not valid:
        return 0.0
    avg_bps = sum(valid) / len(valid)
    return avg_bps * 8 / 1_000_000


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

    ``tolerance`` prevents micro-oscillations from replacing the current
    leader: a new candidate must beat the current best by more than
    ``tolerance``× (default 1.15, matching the Bash ``_test_isp_node``).
    """

    tolerance: float = 1.15
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
