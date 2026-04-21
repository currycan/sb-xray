"""Tests for sb_xray.subscription (show-config.sh generate_links equiv)."""

from __future__ import annotations

import base64
import json
import urllib.parse
from pathlib import Path

import pytest
from sb_xray import subscription as sub

_FAKE_ENV = {
    "DOMAIN": "vpn.example.com",
    "CDNDOMAIN": "cdn.example.com",
    "SB_UUID": "abcdef12-3456-4789-0abc-def123456789",
    "XRAY_UUID": "11111111-2222-4333-8444-555555555555",
    "XRAY_URL_PATH": "abcd1234",
    "XRAY_MLKEM768_CLIENT": "mlkem-client-pubkey-placeholder",
    "XRAY_REALITY_PUBLIC_KEY": "reality-pub-placeholder",
    "XRAY_REALITY_SHORTID": "s1",
    "DEST_HOST": "www.apple.com",
    "LISTENING_PORT": "443",
    "PORT_HYSTERIA2": "8443",
    "PORT_TUIC": "4443",
    "PORT_ANYTLS": "5443",
    "PORT_XHTTP_H3": "6443",
    "NODE_SUFFIX": "",
    "FLAG_PREFIX": "🇯🇵",
    "REGION_NAME": "Tokyo",
}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in _FAKE_ENV.items():
        monkeypatch.setenv(k, v)


def test_hysteria2_link(env: None) -> None:
    url = sub.build_hysteria2_link()
    parsed = urllib.parse.urlparse(url)
    assert parsed.scheme == "hysteria2"
    assert "vpn.example.com" in parsed.netloc
    assert ":8443" in parsed.netloc
    q = urllib.parse.parse_qs(parsed.query)
    assert q["sni"] == ["vpn.example.com"]


def test_tuic_link(env: None) -> None:
    url = sub.build_tuic_link()
    assert url.startswith("tuic://")
    parsed = urllib.parse.urlparse(url)
    assert ":4443" in parsed.netloc
    q = urllib.parse.parse_qs(parsed.query)
    assert q.get("congestion_control") == ["bbr"]


def test_anytls_link(env: None) -> None:
    url = sub.build_anytls_link()
    assert url.startswith("anytls://")
    assert ":5443" in url


def test_vmess_link_is_base64(env: None) -> None:
    url = sub.build_vmess_link()
    assert url.startswith("vmess://")
    payload = url[len("vmess://") :]
    decoded = base64.b64decode(payload + "==", validate=False)
    data = json.loads(decoded)
    assert data["add"] == "cdn.example.com"


def test_xhttp_h3_link(env: None) -> None:
    url = sub.build_xhttp_h3_link()
    assert url.startswith("vless://")
    assert ":6443" in url
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert q["security"] == ["tls"]
    assert q["type"] == ["xhttp"]
    assert q["alpn"] == ["h3"]


def test_write_subscriptions_produces_two_files(tmp_path: Path, env: None) -> None:
    sub.write_subscriptions(output_dir=tmp_path)
    v2rayn = tmp_path / "v2rayn"
    v2rayn_compat = tmp_path / "v2rayn-compat"
    assert v2rayn.is_file()
    assert v2rayn_compat.is_file()
    decoded = base64.b64decode(v2rayn.read_text(encoding="utf-8")).decode("utf-8", errors="replace")
    assert "hysteria2://" in decoded or "tuic://" in decoded


def test_compat_track_has_no_mlkem(tmp_path: Path, env: None) -> None:
    sub.write_subscriptions(output_dir=tmp_path)
    compat = base64.b64decode((tmp_path / "v2rayn-compat").read_text(encoding="utf-8")).decode(
        "utf-8"
    )
    if "encryption=" in compat:
        assert "mlkem768" not in compat


def test_urlquote_noop_for_plain_string() -> None:
    assert sub.urlquote("plain") == "plain"


def test_urlquote_escapes_special_chars() -> None:
    assert sub.urlquote("/foo bar") == "%2Ffoo%20bar"
