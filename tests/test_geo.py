"""Tests for scripts/sb_xray/geo.py (GeoIP/GeoSite downloader)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx
import pytest
import respx
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


@respx.mock
def test_download_one_retries_once_then_succeeds(tmp_path: Path) -> None:
    url = "https://example.test/geoip.dat"
    route = respx.get(url).mock(
        side_effect=[
            httpx.ConnectError("flap"),                       # 首次抖动
            httpx.Response(200, content=b"GEOIP-DATA-BYTES"),  # 重试成功
        ]
    )
    ok = geo._download_one("geoip.dat", url, tmp_path, timeout=5.0)
    assert ok is True
    assert route.call_count == 2
    assert (tmp_path / "geoip.dat").read_bytes() == b"GEOIP-DATA-BYTES"


@respx.mock
def test_download_one_returns_false_after_all_retries_fail(tmp_path: Path) -> None:
    url = "https://example.test/geosite.dat"
    route = respx.get(url).mock(side_effect=httpx.ConnectError("down"))
    ok = geo._download_one("geosite.dat", url, tmp_path, timeout=5.0)
    assert ok is False
    assert route.call_count == 2
    assert not (tmp_path / "geosite.dat").exists()  # 失败不留半文件


def test_manifest_geosite_source_is_metacubex() -> None:
    """geosite.dat 必须取自 MetaCubeX —— 其 geosite:cn 不含被 @cn 标记的海外 CDN
    (dl.google.com / *.gvt1.com 等)。Loyalsoldier 的 cn 会把这些海外服务混进
    国内直连清单,导致回国规则把 Google Play 等误送回国。防误回退到 Loyalsoldier。
    """
    url = geo._MANIFEST["geosite.dat"]
    assert "MetaCubeX/meta-rules-dat" in url
    assert "Loyalsoldier" not in url
    # geoip 仍用 Loyalsoldier(geoip 无 @cn 污染问题)
    assert "Loyalsoldier" in geo._MANIFEST["geoip.dat"]


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
