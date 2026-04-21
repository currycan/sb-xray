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
    # 10 MiB payload / 1 second elapsed ≈ 10 MiB/s → ~83.89 Mbps
    content = b"x" * (10 * 1024 * 1024)
    monkeypatch.setattr(st, "_httpx_client", lambda **_: _FakeClient(content))
    times = iter([0.0, 1.0])
    monkeypatch.setattr(st.time, "perf_counter", lambda: next(times))
    mbps = st.measure("https://speed.test/dl", samples=1)
    assert pytest.approx(mbps, rel=0.02) == 10 * 1024 * 1024 * 8 / 1_000_000


def test_measure_returns_zero_when_all_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
