"""Tests for sb_xray.speed_test (entrypoint.sh §9 equivalent)."""

from __future__ import annotations

import pytest
from sb_xray import speed_test as st

# ---- rating / show_report ---------------------------------------------------


@pytest.mark.parametrize(
    "mbps,rating",
    [
        (150.0, "8K-HDR"),
        (80.0, "8K"),
        (40.0, "4K"),
        (15.0, "1080P"),
        (5.0, "slow"),
        (0.0, "slow"),
    ],
)
def test_rate(mbps: float, rating: str) -> None:
    assert st.rate(mbps) == rating


def test_show_report_includes_name_and_mbps(
    capsys: pytest.CaptureFixture[str],
) -> None:
    st.show_report(42.5, name="节点A")
    err = capsys.readouterr().err
    assert "节点A" in err
    assert "42.50" in err


# ---- measure with mocked client --------------------------------------------


class _FakeResponse:
    def __init__(self, content: bytes) -> None:
        self._content = content
        self.status_code = 200

    def raise_for_status(self) -> None:
        pass

    @property
    def content(self) -> bytes:
        return self._content


class _FakeClient:
    def __init__(self, content: bytes) -> None:
        self._content = content

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def get(self, url: str, **kwargs: object) -> _FakeResponse:
        return _FakeResponse(self._content)


def test_measure_converts_bytes_sec_to_mbps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # v1 semantics: ``awk '{printf "%.2f", s * 8 / 1024 / 1024}'`` — mebibit/s.
    # 10 MiB payload / 1 second elapsed → exactly 80.00 Mibps.
    monkeypatch.setenv("ISP_SPEED_LEGACY", "true")
    content = b"x" * (10 * 1024 * 1024)
    monkeypatch.setattr(st, "_httpx_client", lambda **_: _FakeClient(content))
    times = iter([0.0, 1.0])
    monkeypatch.setattr(st.time, "perf_counter", lambda: next(times))
    mbps = st.measure("https://speed.test/dl", samples=1)
    assert pytest.approx(mbps, rel=0.01) == 10 * 1024 * 1024 * 8 / 1024 / 1024


def test_measure_returns_zero_when_proxy_import_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: when the SOCKS transport dep (socksio) is missing in
    the runtime image, httpx.Client(proxy='socks5h://...') raises
    ImportError mid-boot. Bash parity is 'speed failed → 0 Mbps, keep
    going', not 'crash the whole pipeline'."""

    def _boom(**kw: object) -> st.httpx.Client:
        raise ImportError("Using SOCKS proxy, but the 'socksio' package is not installed.")

    monkeypatch.setattr(st.httpx, "Client", _boom)
    mbps = st.measure(
        "https://speed.test/dl",
        samples=2,
        proxy="socks5h://proxy:1080",
        proxy_auth="u:p",
    )
    assert mbps == 0.0


def test_measure_returns_zero_when_all_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ISP_SPEED_LEGACY", "true")

    class _BrokenClient:
        def __enter__(self) -> _BrokenClient:
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def get(self, *a: object, **k: object) -> None:
            import httpx

            raise httpx.ConnectError("down")

    monkeypatch.setattr(st, "_httpx_client", lambda **_: _BrokenClient())
    assert st.measure("https://speed.test/dl", samples=2) == 0.0


def test_measure_averages_valid_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ISP_SPEED_LEGACY", "true")
    content = b"x" * (1 * 1024 * 1024)  # 1 MiB
    monkeypatch.setattr(st, "_httpx_client", lambda **_: _FakeClient(content))
    # Two samples: first 1.0s (~8 Mbps), second 0.5s (~16 Mbps)
    times = iter([0.0, 1.0, 0.0, 0.5])
    monkeypatch.setattr(st.time, "perf_counter", lambda: next(times))
    mbps = st.measure("https://speed.test/dl", samples=2)
    assert 7 < mbps < 17


# ---- IspSpeedContext --------------------------------------------------------


def test_isp_context_tracks_fastest() -> None:
    ctx = st.IspSpeedContext()
    ctx.record("n1", 5.0)
    ctx.record("n2", 20.0)
    assert ctx.fastest_tag == "n2"
    assert ctx.fastest_speed == 20.0
    assert ctx.speeds == {"n1": 5.0, "n2": 20.0}


def test_isp_context_tolerance() -> None:
    # Within 1.15x of current best → no replacement.
    ctx = st.IspSpeedContext(tolerance=1.15)
    ctx.record("first", 10.0)
    ctx.record("second", 11.0)  # 11/10 = 1.10 < 1.15
    assert ctx.fastest_tag == "first"
    ctx.record("third", 12.0)  # 12/10 = 1.20 > 1.15 → replaces
    assert ctx.fastest_tag == "third"


def test_isp_context_empty() -> None:
    ctx = st.IspSpeedContext()
    assert ctx.fastest_tag is None
    assert ctx.fastest_speed == 0.0
    assert ctx.speeds == {}


# ---- v2 sampler kill switch -------------------------------------------------


class _FakeStreamResp:
    def __init__(self, chunks: list[bytes], status_code: int = 200) -> None:
        self._chunks = list(chunks)
        self.status_code = status_code

    def raise_for_status(self) -> None:
        pass

    def iter_bytes(self, chunk_size: int | None = None):
        for c in self._chunks:
            yield c


class _FakeStreamCM:
    def __init__(self, resp: _FakeStreamResp) -> None:
        self._resp = resp

    def __enter__(self) -> _FakeStreamResp:
        return self._resp

    def __exit__(self, *a: object) -> None:
        pass


class _FakeV2Client:
    """Supports both v1 ``.get()`` and v2 ``.stream()`` call sites."""

    def __init__(self, content: bytes) -> None:
        self._content = content

    def __enter__(self) -> _FakeV2Client:
        return self

    def __exit__(self, *a: object) -> None:
        pass

    def get(self, url: str, **kw: object) -> _FakeResponse:
        return _FakeResponse(self._content)

    def stream(self, method: str, url: str) -> _FakeStreamCM:
        # Deliver the body in three chunks so the sampler sees a
        # warmup / meter_start / metered pattern.
        third = len(self._content) // 3
        return _FakeStreamCM(
            _FakeStreamResp(
                [
                    self._content[:third],
                    self._content[third : 2 * third],
                    self._content[2 * third :],
                ]
            )
        )


def test_measure_defaults_to_v2_sampler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default path runs the v2 stream sampler, not _sample_once."""
    monkeypatch.delenv("ISP_SPEED_LEGACY", raising=False)
    seen = {"v1": 0, "v2": 0}

    def _fake_stream_measure(*a: object, **kw: object) -> st.SampleResult:
        seen["v2"] += 1
        return st.SampleResult(mbps=50.0, status="ok", bytes_read=10_000_000, window_sec=1.0)

    def _fake_sample_once(*a: object, **kw: object) -> float:
        seen["v1"] += 1
        return 10 * 1024 * 1024  # 10 MiB/s as bytes

    monkeypatch.setattr(st, "_stream_measure", _fake_stream_measure)
    monkeypatch.setattr(st, "_sample_once", _fake_sample_once)
    monkeypatch.setattr(st, "_httpx_client", lambda **_: _FakeV2Client(b"x" * 1024))

    st.measure("https://speed.test/dl", samples=2)
    assert seen["v2"] == 2
    assert seen["v1"] == 0


def test_measure_honours_legacy_kill_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ISP_SPEED_LEGACY=true routes through the v1 _sample_once."""
    monkeypatch.setenv("ISP_SPEED_LEGACY", "true")
    seen = {"v1": 0, "v2": 0}

    def _fake_stream_measure(*a: object, **kw: object) -> st.SampleResult:
        seen["v2"] += 1
        return st.SampleResult(mbps=50.0, status="ok", bytes_read=10_000_000, window_sec=1.0)

    def _fake_sample_once(*a: object, **kw: object) -> float:
        seen["v1"] += 1
        return 2 * 1024 * 1024  # 2 MiB/s bytes

    monkeypatch.setattr(st, "_stream_measure", _fake_stream_measure)
    monkeypatch.setattr(st, "_sample_once", _fake_sample_once)
    monkeypatch.setattr(st, "_httpx_client", lambda **_: _FakeV2Client(b"x" * 1024))

    st.measure("https://speed.test/dl", samples=2)
    assert seen["v1"] == 2
    assert seen["v2"] == 0


def test_measure_v2_returns_trimmed_mean_of_mbps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v2 path averages SampleResult.mbps via _truncated_mean_with_stability."""
    monkeypatch.delenv("ISP_SPEED_LEGACY", raising=False)
    results = iter(
        [
            st.SampleResult(mbps=80.0, status="ok", bytes_read=10**7, window_sec=1.0),
            st.SampleResult(mbps=90.0, status="ok", bytes_read=10**7, window_sec=1.0),
            st.SampleResult(mbps=100.0, status="ok", bytes_read=10**7, window_sec=1.0),
        ]
    )
    monkeypatch.setattr(st, "_stream_measure", lambda *a, **kw: next(results))
    monkeypatch.setattr(st, "_httpx_client", lambda **_: _FakeV2Client(b"x" * 1024))

    mbps = st.measure("https://speed.test/dl", samples=3)
    # n=3 truncated mean drops min (80) + max (100) → returns 90
    assert pytest.approx(mbps, rel=0.01) == 90.0


def test_measure_v2_returns_zero_when_all_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-ok statuses (connect_fail/timeout/zero_body) yield 0 like v1."""
    monkeypatch.delenv("ISP_SPEED_LEGACY", raising=False)

    def _all_fail(*a: object, **kw: object) -> st.SampleResult:
        return st.SampleResult(mbps=0.0, status="connect_fail", bytes_read=0, window_sec=0.0)

    monkeypatch.setattr(st, "_stream_measure", _all_fail)
    monkeypatch.setattr(st, "_httpx_client", lambda **_: _FakeV2Client(b""))

    assert st.measure("https://speed.test/dl", samples=3) == 0.0
