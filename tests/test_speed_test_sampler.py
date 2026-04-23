"""Tests for the v2 streaming sampler (``speed_test._stream_measure``).

The v2 sampler replaces ``_sample_once`` with a streamed, time-boxed
measurement that discards a warmup phase, starts its clock at first-byte
arrival, and classifies failures with a structured status code.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

import httpx
import pytest
from sb_xray import speed_test as st

# ---------------------------------------------------------------------------
# Fake streaming HTTP client (httpx.Client.stream() compatible surface)
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(
        self,
        chunks: Iterable[bytes | BaseException],
        *,
        status_code: int = 200,
    ) -> None:
        self._chunks = list(chunks)
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("GET", "https://x/"),
                response=httpx.Response(self.status_code),
            )

    def iter_bytes(self, chunk_size: int | None = None) -> Iterable[bytes]:
        for item in self._chunks:
            if isinstance(item, BaseException):
                raise item
            yield item


class _FakeStreamCM:
    def __init__(self, resp: _FakeResponse) -> None:
        self._resp = resp

    def __enter__(self) -> _FakeResponse:
        return self._resp

    def __exit__(self, *args: object) -> None:
        pass


class _FakeStreamClient:
    def __init__(
        self,
        chunks: list[bytes | BaseException] | None = None,
        *,
        stream_exception: BaseException | None = None,
        status_code: int = 200,
    ) -> None:
        self._chunks = chunks if chunks is not None else []
        self._stream_exception = stream_exception
        self._status_code = status_code

    def stream(self, method: str, url: str) -> _FakeStreamCM:
        if self._stream_exception is not None:
            raise self._stream_exception
        return _FakeStreamCM(_FakeResponse(self._chunks, status_code=self._status_code))


def _clock_from(values: list[float]) -> Callable[[], float]:
    """Deterministic clock — pops from ``values``; repeats last if exhausted."""
    it = iter(values)
    state = {"last": 0.0}

    def _now() -> float:
        try:
            state["last"] = next(it)
        except StopIteration:
            pass
        return state["last"]

    return _now


# ---------------------------------------------------------------------------
# SampleResult dataclass shape
# ---------------------------------------------------------------------------


def test_sample_result_is_frozen_dataclass() -> None:
    r = st.SampleResult(mbps=80.0, status="ok", bytes_read=10_000_000, window_sec=1.0)
    with pytest.raises(Exception):
        r.mbps = 42.0  # type: ignore[misc]


def test_sample_result_default_proxy_overhead_ms() -> None:
    r = st.SampleResult(mbps=0.0, status="ok", bytes_read=0, window_sec=0.0)
    assert r.proxy_overhead_ms == 0.0


# ---------------------------------------------------------------------------
# Core measurement semantics
# ---------------------------------------------------------------------------


def test_stream_measure_discards_warmup_bytes() -> None:
    """Bytes delivered before warmup_sec elapses must NOT count toward Mbps."""
    # Chunk 1 arrives at t=0.0 (first byte; starts warmup clock)
    # Chunks 2-3 arrive during warmup (discarded)
    # Chunks 4-6 arrive during metering window
    chunks = [b"w" * 1_000_000] * 6  # 1 MiB each
    clock = _clock_from(
        [
            0.0,  # chunk 1 → first byte, warmup starts
            0.5,  # chunk 2 → still in warmup
            1.0,  # chunk 3 → still in warmup
            2.0,  # chunk 4 → past warmup_sec=1.5, meter_start=2.0
            3.0,  # chunk 5 → elapsed 1.0s
            4.0,  # chunk 6 → elapsed 2.0s (window=2.0 → done)
        ]
    )
    client = _FakeStreamClient(chunks)
    result = st._stream_measure(
        client,  # type: ignore[arg-type]
        "https://x/",
        warmup_sec=1.5,
        window_sec=2.0,
        max_bytes=100 * 1024 * 1024,
        chunk_bytes=1_000_000,
        clock=clock,
    )
    # Only chunks 4-6 (3 MiB) counted over 2.0s → 12 Mibps
    assert result.status == "ok"
    assert result.bytes_read >= 3 * 1_000_000
    assert 11.0 < result.mbps < 13.0


def test_stream_measure_window_bounded() -> None:
    """Iteration stops once window_sec elapsed, even if chunks keep coming."""
    chunks = [b"x" * 500_000] * 100  # plenty of chunks
    clock = _clock_from(
        [
            0.0,  # chunk 1 → first byte
            0.1,  # chunk 2 → warmup over (warmup=0.05)
            0.2,  # chunk 3 → meter_start=0.2
            0.4,  # chunk 4 → elapsed 0.2
            0.6,  # chunk 5 → elapsed 0.4
            0.9,  # chunk 6 → elapsed 0.7
            1.3,  # chunk 7 → elapsed 1.1 > window 1.0 → break
        ]
    )
    client = _FakeStreamClient(chunks)
    result = st._stream_measure(
        client,  # type: ignore[arg-type]
        "https://x/",
        warmup_sec=0.05,
        window_sec=1.0,
        max_bytes=100 * 1024 * 1024,
        chunk_bytes=500_000,
        clock=clock,
    )
    assert result.status == "ok"
    # Window elapsed ≈ 1.1s, broke after ~4 chunks (chunks 4-7 metered)
    assert result.window_sec > 1.0
    assert result.window_sec < 1.3


def test_stream_measure_max_bytes_cap() -> None:
    """Stops at max_bytes before window_sec elapses."""
    chunks = [b"y" * 1_000_000] * 100
    clock = _clock_from(
        [
            0.0,  # chunk 1 → first byte
            0.01,  # chunk 2 → warmup over (warmup=0.001)
            0.02,  # chunk 3 → meter_start
            0.03,
            0.04,
            0.05,
            0.06,
            0.07,  # chunks 4-8: 5 MiB metered
        ]
    )
    client = _FakeStreamClient(chunks)
    result = st._stream_measure(
        client,  # type: ignore[arg-type]
        "https://x/",
        warmup_sec=0.001,
        window_sec=100.0,  # very long
        max_bytes=5 * 1_000_000,  # trips first
        chunk_bytes=1_000_000,
        clock=clock,
    )
    assert result.status == "ok"
    assert result.bytes_read >= 5_000_000
    # Window should be nowhere near 100s
    assert result.window_sec < 1.0


# ---------------------------------------------------------------------------
# Mbps calculation — units are Mib/s (bytes*8/1024/1024) for parity with v1
# ---------------------------------------------------------------------------


def test_stream_measure_mbps_uses_mebibit_units() -> None:
    """Match v1 unit: bytes * 8 / 1024 / 1024 (Mib/s, not Mbps).

    This keeps ``_ISP_SPEEDS_JSON`` values on the same scale as the legacy
    sampler so ``isp_retest._max_delta_pct`` doesn't see a schema-wide
    jump on the v1→v2 cutover.
    """
    # 10 MiB metered over exactly 1.0s → 80.00 Mib/s
    chunks = [b"z" * (10 * 1024 * 1024)]
    clock = _clock_from(
        [
            0.0,  # chunk 1 → first byte, warmup starts
            0.001,  # next chunk would be checked — but iterator is done
        ]
    )
    # Use warmup=0 + window positive; single-chunk path lands in degraded branch
    # To force the happy path we need ≥3 chunks. Split 10 MiB → 3 × ~3.33 MiB.
    chunk = b"z" * (10 * 1024 * 1024 // 3)
    client = _FakeStreamClient([chunk, chunk, chunk, chunk])  # 4 chunks
    clock = _clock_from(
        [
            0.0,  # chunk 1 → first byte
            0.001,  # chunk 2 → warmup over
            0.002,  # chunk 3 → meter_start
            1.002,  # chunk 4 → elapsed 1.0s
        ]
    )
    result = st._stream_measure(
        client,  # type: ignore[arg-type]
        "https://x/",
        warmup_sec=0.0005,
        window_sec=0.5,
        max_bytes=100 * 1024 * 1024,
        chunk_bytes=1,
        clock=clock,
    )
    # chunks 2,3,4 metered (chunk 2 seeds the meter; chunks 3,4 extend):
    # 3 * (10 MiB / 3) ≈ 10 MiB in ~1.001s → ~80 Mib/s
    chunk_sz = 10 * 1024 * 1024 // 3
    expected = (3 * chunk_sz * 8) / 1024 / 1024 / result.window_sec
    assert result.status == "ok"
    assert abs(result.mbps - expected) < 0.5


# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------


def test_stream_measure_connect_fail_on_connect_error() -> None:
    client = _FakeStreamClient(stream_exception=httpx.ConnectError("refused"))
    result = st._stream_measure(
        client,  # type: ignore[arg-type]
        "https://x/",
    )
    assert result.status == "connect_fail"
    assert result.mbps == 0.0
    assert result.bytes_read == 0


def test_stream_measure_timeout_on_timeout_exception() -> None:
    client = _FakeStreamClient(stream_exception=httpx.ReadTimeout("slow"))
    result = st._stream_measure(
        client,  # type: ignore[arg-type]
        "https://x/",
    )
    assert result.status == "timeout"
    assert result.mbps == 0.0


def test_stream_measure_http_status_error_is_connect_fail() -> None:
    """HTTP 5xx / 4xx surface as connect_fail (node broken, not slow)."""
    client = _FakeStreamClient([b"err"], status_code=503)
    result = st._stream_measure(
        client,  # type: ignore[arg-type]
        "https://x/",
    )
    assert result.status == "connect_fail"


def test_stream_measure_zero_body_when_iterator_empty() -> None:
    """Stream opened but delivered zero bytes → ``zero_body``."""
    client = _FakeStreamClient([])  # empty chunk list
    result = st._stream_measure(
        client,  # type: ignore[arg-type]
        "https://x/",
    )
    assert result.status == "zero_body"
    assert result.bytes_read == 0
    assert result.mbps == 0.0


def test_stream_measure_low_speed_classification() -> None:
    """Metered rate below _MIN_VALID_BPS → status=low_speed (not ok)."""
    # 512 bytes metered over 1 second = 512 bytes/sec < 1024 threshold
    chunk = b"s" * 512
    clock = _clock_from(
        [
            0.0,  # chunk 1 first byte
            0.001,  # chunk 2 warmup over
            0.002,  # chunk 3 meter_start
            2.002,  # chunk 4 elapsed 2s (window over)
        ]
    )
    client = _FakeStreamClient([chunk, chunk, chunk, chunk])
    result = st._stream_measure(
        client,  # type: ignore[arg-type]
        "https://x/",
        warmup_sec=0.0005,
        window_sec=1.0,
        max_bytes=100 * 1024 * 1024,
        chunk_bytes=1,
        clock=clock,
    )
    assert result.status == "low_speed"
    assert result.mbps > 0.0  # we did measure something, just below threshold


# ---------------------------------------------------------------------------
# Chunk pattern robustness
# ---------------------------------------------------------------------------


def test_stream_measure_tiny_chunks_accumulate() -> None:
    """Many small chunks must sum correctly through the metering window."""
    # 1000 chunks of 1000 bytes = 1 MB
    chunks = [b"t" * 1000] * 1000
    clock_values = [0.0]  # chunk 1 first byte
    # Chunks 2..1000 — warmup covers chunks 2-3, meter covers 4-1000
    clock_values.append(0.5)  # chunk 2, warmup in-progress
    clock_values.append(1.0)  # chunk 3, warmup over at >=0.9
    # Remaining chunks: linearly 1.0s..10.0s
    for i in range(4, 1001):
        clock_values.append(1.0 + (i - 3) * 0.01)
    clock = _clock_from(clock_values)
    client = _FakeStreamClient(list(chunks))
    result = st._stream_measure(
        client,  # type: ignore[arg-type]
        "https://x/",
        warmup_sec=0.9,
        window_sec=5.0,
        max_bytes=100 * 1024 * 1024,
        chunk_bytes=1000,
        clock=clock,
    )
    assert result.status == "ok"
    # ≥ some metered bytes arrived
    assert result.bytes_read > 0


# ---------------------------------------------------------------------------
# Proxy-dep missing is handled OUTSIDE _stream_measure (by _httpx_client
# returning None upstream). Test that wrapper integration is documented.
# ---------------------------------------------------------------------------


def test_proxy_dep_missing_is_upstream_responsibility() -> None:
    """_stream_measure does NOT handle socksio-missing; that's _httpx_client."""
    # Smoke: passing a None client must raise (caller must guard).
    with pytest.raises((AttributeError, TypeError)):
        st._stream_measure(
            None,  # type: ignore[arg-type]
            "https://x/",
        )
