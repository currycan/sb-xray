"""Tests for scripts/sb_xray/geo.py (GeoIP/GeoSite downloader)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx
import pytest
from sb_xray import geo


class _FakeStream:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> _FakeStream:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_bytes(self, chunk_size: int = 65536):
        yield self._payload


class _FakeClient:
    def __init__(self, *, payloads: dict[str, bytes] | None = None, error: bool = False):
        self._payloads = payloads or {}
        self._error = error
        self.get_calls: list[str] = []

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def stream(self, method: str, url: str):
        self.get_calls.append(url)
        if self._error:
            raise httpx.ConnectError("boom")
        payload = self._payloads.get(url, b"stub-data")
        return _FakeStream(payload)


@pytest.fixture
def manifest() -> dict[str, str]:
    return {
        "geoip.dat": "https://example.test/geoip.dat",
        "geosite.dat": "https://example.test/geosite.dat",
    }


def test_refresh_first_boot_downloads_all(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manifest: dict[str, str],
) -> None:
    target = tmp_path / "geo"
    links = tmp_path / "bin"

    def fake_client(**_: object) -> _FakeClient:
        return _FakeClient(payloads={url: b"x" * 1024 for url in manifest.values()})

    monkeypatch.setattr(geo.httpx, "Client", fake_client)

    failed = geo.refresh(
        on_startup=True,
        target_dir=target,
        link_dir=links,
        manifest=manifest,
    )
    assert failed == 0
    for name in manifest:
        assert (target / name).is_file()
        link = links / name
        assert link.is_symlink()
        assert link.resolve() == (target / name).resolve()


def test_refresh_skips_when_all_fresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manifest: dict[str, str],
) -> None:
    target = tmp_path / "geo"
    links = tmp_path / "bin"
    target.mkdir()
    for name in manifest:
        (target / name).write_bytes(b"cached")

    called = {"n": 0}

    def fake_client(**_: object) -> _FakeClient:
        called["n"] += 1
        return _FakeClient(payloads={})

    monkeypatch.setattr(geo.httpx, "Client", fake_client)

    failed = geo.refresh(
        on_startup=True,
        target_dir=target,
        link_dir=links,
        manifest=manifest,
    )
    assert failed == 0
    assert called["n"] == 0
    assert (links / "geoip.dat").is_symlink()


def test_refresh_atomic_on_download_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manifest: dict[str, str],
) -> None:
    target = tmp_path / "geo"
    target.mkdir()
    stale = b"existing-content-do-not-clobber"
    for name in manifest:
        (target / name).write_bytes(stale)
        # 让 mtime 落到 10 天前以绕过 <7 天新鲜度分支
        old = time.time() - 10 * 24 * 3600
        os.utime(target / name, (old, old))

    def fake_client(**_: object) -> _FakeClient:
        return _FakeClient(error=True)

    monkeypatch.setattr(geo.httpx, "Client", fake_client)

    failed = geo.refresh(
        on_startup=True,
        target_dir=target,
        link_dir=tmp_path / "bin",
        manifest=manifest,
    )
    assert failed == len(manifest)
    for name in manifest:
        assert (target / name).read_bytes() == stale
        assert not (target / f".{name}.tmp").exists()


def test_refresh_forces_download_when_not_on_startup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manifest: dict[str, str],
) -> None:
    target = tmp_path / "geo"
    target.mkdir()
    for name in manifest:
        (target / name).write_bytes(b"stale")

    hits: list[str] = []

    def fake_client(**_: object) -> _FakeClient:
        client = _FakeClient(payloads={url: b"fresh" for url in manifest.values()})
        # Shared sink for tracking stream() invocations across Client contexts.
        client.get_calls = hits
        return client

    monkeypatch.setattr(geo.httpx, "Client", fake_client)
    monkeypatch.setattr(
        geo,
        "_restart_xray_if_running",
        lambda **_: None,
    )

    failed = geo.refresh(
        on_startup=False,
        target_dir=target,
        link_dir=tmp_path / "bin",
        manifest=manifest,
    )
    assert failed == 0
    assert len(hits) == len(manifest)
    for name in manifest:
        assert (target / name).read_bytes() == b"fresh"


def test_restart_xray_skipped_without_socket(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    class _Runner:
        @staticmethod
        def run(cmd: list[str], **_: object) -> None:
            calls.append(cmd)

    geo._restart_xray_if_running(
        socket_path=tmp_path / "nonexistent.sock",
        runner=_Runner,
    )
    assert calls == []


def test_refresh_maintains_multiple_link_dirs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manifest: dict[str, str],
) -> None:
    """xray 读 ``/usr/local/bin/bin``,sing-box 读 ``/usr/local/bin``,两处必须都有符号链接。"""
    target = tmp_path / "geo"
    link_a = tmp_path / "usr_local_bin" / "bin"
    link_b = tmp_path / "usr_local_bin"

    def fake_client(**_: object) -> _FakeClient:
        return _FakeClient(payloads={url: b"ok" for url in manifest.values()})

    monkeypatch.setattr(geo.httpx, "Client", fake_client)

    failed = geo.refresh(
        on_startup=True,
        target_dir=target,
        link_dirs=(link_a, link_b),
        manifest=manifest,
    )
    assert failed == 0
    for name in manifest:
        for link_dir in (link_a, link_b):
            link = link_dir / name
            assert link.is_symlink(), f"{link} 应为符号链接"
            assert link.resolve() == (target / name).resolve()
