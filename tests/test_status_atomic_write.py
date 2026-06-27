"""STATUS_FILE atomic + flock-serialized write tests (race-fix Task 1)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from sb_xray import speed_test as st


@pytest.fixture
def status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    f = tmp_path / "status"
    monkeypatch.setenv("STATUS_FILE", str(f))
    return f


def test_write_status_line_upserts(status: Path) -> None:
    st._write_status_line("ISP_TAG", "proxy-us-isp")
    st._write_status_line("ISP_TAG", "proxy-la-isp")  # overwrite
    st._write_status_line("IS_8K_SMOOTH", "false")
    snap = st._read_status_snapshot()
    assert snap["ISP_TAG"] == "proxy-la-isp"
    assert snap["IS_8K_SMOOTH"] == "false"


def test_concurrent_writers_dont_lose_lines(status: Path) -> None:
    """Two threads each write 50 distinct keys — flock serializes, all survive."""

    def writer(prefix: str) -> None:
        for i in range(50):
            st._write_status_line(f"{prefix}_{i}", str(i))

    t1 = threading.Thread(target=writer, args=("A",))
    t2 = threading.Thread(target=writer, args=("B",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    snap = st._read_status_snapshot()
    for i in range(50):
        assert snap[f"A_{i}"] == str(i)
        assert snap[f"B_{i}"] == str(i)
    # no leftover tmp files
    assert not list(status.parent.glob(".status.*.tmp"))


def test_parse_status_line_grammar() -> None:
    assert st._parse_status_line("export ISP_TAG='proxy-us'") == ("ISP_TAG", "proxy-us")
    assert st._parse_status_line('export A="b c"') == ("A", "b c")
    assert st._parse_status_line("export N=5") == ("N", "5")
    assert st._parse_status_line("# comment") is None
    assert st._parse_status_line("") is None


def test_status_line_roundtrip_quoted_json(status: Path) -> None:
    payload = '{"proxy-us-isp": 12.5, "proxy-la-isp": 0.0}'
    st._write_status_line("_ISP_SPEEDS_JSON", payload)
    snap = st._read_status_snapshot()
    assert snap["_ISP_SPEEDS_JSON"] == payload
