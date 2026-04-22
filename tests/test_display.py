"""Tests for sb_xray.display (show-config.sh §show_info_links equiv)."""

from __future__ import annotations

import subprocess
from typing import Any

import pytest
from sb_xray import display


@pytest.mark.parametrize(
    "info,expected",
    [
        ("Tokyo Japan|1.1.1.1", "🇯🇵"),
        ("日本东京|2.2.2.2", "🇯🇵"),
        ("香港 HK|3.3.3.3", "🇭🇰"),
        ("Hong Kong|3.3.3.3", "🇭🇰"),
        ("New York United States|4.4.4.4", "🇺🇸"),
        ("美国洛杉矶|4.4.4.4", "🇺🇸"),
        ("Seoul Korea|5.5.5.5", "🇰🇷"),
        ("Taipei Taiwan|6.6.6.6", "🇹🇼"),
        ("Singapore|7.7.7.7", "🇸🇬"),
        ("Berlin Germany|8.8.8.8", "🇩🇪"),
        ("random nowhere|9.9.9.9", ""),
    ],
)
def test_get_flag_emoji(info: str, expected: str) -> None:
    assert display.get_flag_emoji(info) == expected


def test_tls_ping_diagnose_calls_xray(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_cmd: list[list[str]] = []

    class FakeCompleted:
        returncode = 0
        stdout = "fake cert fp"
        stderr = ""

    def fake_run(cmd: list[str], **kwargs: Any) -> FakeCompleted:
        captured_cmd.append(cmd)
        return FakeCompleted()

    monkeypatch.setattr(subprocess, "run", fake_run)
    with caplog.at_level("INFO", logger="sb_xray.display"):
        display.tls_ping_diagnose("example.com:443")
    assert captured_cmd == [["xray", "tls", "ping", "example.com:443"]]
    messages = [r.getMessage() for r in caplog.records]
    assert any("example.com:443" in m for m in messages)


def test_tls_ping_no_xray_binary(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(cmd: list[str], **kwargs: Any) -> None:
        raise FileNotFoundError("xray not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with caplog.at_level("WARNING", logger="sb_xray.display"):
        display.tls_ping_diagnose("example.com:443")
    messages = [r.getMessage() for r in caplog.records]
    assert any("xray" in m for m in messages)


def test_show_qrcode_invokes_qrencode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[list[str]] = []

    class FakeCompleted:
        returncode = 0
        stdout = b""

    def fake_run(cmd: list[str], **kwargs: Any) -> FakeCompleted:
        captured.append(cmd)
        return FakeCompleted()

    monkeypatch.setattr(subprocess, "run", fake_run)
    display.show_qrcode("vless://content", name="test")
    assert any(c[0] == "qrencode" for c in captured)


def test_show_info_links_prints_banner(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CDNDOMAIN", "cdn.example.com")
    monkeypatch.setenv("DOMAIN", "vpn.example.com")
    monkeypatch.delenv("DEBUG", raising=False)
    monkeypatch.delenv("SUBSCRIBE_TOKEN", raising=False)
    display.show_info_links()
    out = capsys.readouterr().out
    assert "cdn.example.com" in out
    assert "📋 Index" in out
    assert "🚀 V2rayN 订阅" in out
    assert "🔓 V2rayN-Compat 订阅" in out
    assert "https://cdn.example.com/sb-xray/v2rayn" in out
    assert "https://cdn.example.com/sb-xray/v2rayn-compat" in out


def test_show_info_links_token_block_only_when_token_set(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CDNDOMAIN", "cdn.example.com")
    monkeypatch.setenv("DOMAIN", "vpn.example.com")
    monkeypatch.setenv("SUBSCRIBE_TOKEN", "secret-token")
    monkeypatch.setenv("PUBLIC_USER", "admin")
    monkeypatch.setenv("PUBLIC_PASSWORD", "s3cret")
    display.show_info_links()
    out = capsys.readouterr().out
    assert "?token=secret-token" in out
    assert "Basic Auth: admin / s3cret" in out


def test_show_info_links_writes_ansi_stripped_archive(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CDNDOMAIN", "cdn.example.com")
    monkeypatch.setenv("DOMAIN", "vpn.example.com")
    archive = tmp_path / "subscribe" / "show-config"
    display.show_info_links(archive_path=archive)
    assert archive.is_file()
    content = archive.read_text(encoding="utf-8")
    assert "\x1b[" not in content
    assert "📋 Index" in content
    assert "cdn.example.com" in content
