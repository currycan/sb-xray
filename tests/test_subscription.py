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
    "NODE_SUFFIX": " ✈ isp",
    "FLAG_PREFIX": "🇯🇵 ",
    "NODE_NAME": "jp",
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
    (tmp_path / "v2rayn-compat").write_text("stale", encoding="utf-8")
    sub.write_subscriptions(output_dir=tmp_path)
    v2rayn = tmp_path / "v2rayn"
    common = tmp_path / "common"
    assert v2rayn.is_file()
    assert common.is_file()
    assert not (tmp_path / "v2rayn-compat").exists()
    decoded = base64.b64decode(v2rayn.read_text(encoding="utf-8")).decode("utf-8", errors="replace")
    assert "hysteria2://" in decoded or "tuic://" in decoded


def test_common_track_has_no_mlkem(tmp_path: Path, env: None) -> None:
    sub.write_subscriptions(output_dir=tmp_path)
    common = base64.b64decode((tmp_path / "common").read_text(encoding="utf-8")).decode("utf-8")
    if "encryption=" in common:
        assert "mlkem768" not in common


def test_urlquote_noop_for_plain_string() -> None:
    assert sub.urlquote("plain") == "plain"


def test_urlquote_escapes_special_chars() -> None:
    assert sub.urlquote("/foo bar") == "%2Ffoo%20bar"


# ------------------------------------------------------------------
# Regression: each link format from show-config.sh:generate_links
# ------------------------------------------------------------------


def test_hysteria2_has_obfs_salamander(env: None) -> None:
    url = sub.build_hysteria2_link()
    assert "obfs=salamander" in url
    assert "obfs-password=abcdef12-3456-4789-0abc-def123456789" in url
    frag = url.split("#", 1)[1]
    assert urllib.parse.unquote(frag) == "🇯🇵 Hysteria2 ✈ jp ✈ isp"


def test_vmess_payload_has_ws_path_and_alpn(env: None) -> None:
    url = sub.build_vmess_link()
    payload = url[len("vmess://") :]
    data = json.loads(base64.b64decode(payload + "==").decode("utf-8"))
    assert data["net"] == "ws"
    assert data["path"] == "/abcd1234-vmessws"
    assert data["alpn"] == "http/1.1"
    assert data["fp"] == "chrome"
    assert data["scy"] == "auto"


def test_vless_vision_reality_link(env: None) -> None:
    url = sub.build_vless_vision_link()
    assert url.startswith("vless://")
    assert "flow=xtls-rprx-vision" in url
    assert "security=reality" in url
    assert "sni=www.apple.com" in url
    assert "type=tcp" in url
    frag = url.split("#", 1)[1]
    assert urllib.parse.unquote(frag) == "🇯🇵 XTLS-Reality ✈ jp ✈ isp"


def test_xhttp_reality_main_has_mlkem(env: None) -> None:
    url = sub.build_xhttp_reality_link(compat=False)
    assert "encryption=mlkem768x25519plus.native.0rtt.mlkem-client-pubkey-placeholder" in url
    assert "mode=auto" in url
    assert "path=%2Fabcd1234-xhttp" in url
    assert "-xhttp-compat" not in url


def test_xhttp_reality_compat_uses_packet_up(env: None) -> None:
    url = sub.build_xhttp_reality_link(compat=True)
    assert "encryption=none" in url
    assert "mode=packet-up" in url
    assert "path=%2Fabcd1234-xhttp-compat" in url
    assert "mlkem768" not in url


def test_up_cdn_down_reality_main_carries_extra_json(env: None) -> None:
    url = sub.build_up_cdn_down_reality_link(compat=False)
    assert "extra=%7B%22downloadSettings%22%3A%7B" in url
    assert "%22network%22%3A%22xhttp%22" in url
    assert "%22security%22%3A%22reality%22" in url
    assert "%22mode%22%3A%22auto%22" in url
    assert "cdn.example.com" in url  # uplink host


def test_up_cdn_down_reality_compat_mode_packet_up(env: None) -> None:
    url = sub.build_up_cdn_down_reality_link(compat=True)
    assert "encryption=none" in url
    assert "%22mode%22%3A%22packet-up%22" in url
    assert "xhttp-compat" in url


def test_up_reality_down_cdn_main(env: None) -> None:
    url = sub.build_up_reality_down_cdn_link(compat=False)
    assert "security=reality" in url
    assert "%22security%22%3A%22tls%22" in url  # extra=downloadSettings→TLS
    assert "%22alpn%22%3A%5B%22h2%22%5D" in url


def test_mix_main_includes_reality_keys(env: None) -> None:
    url = sub.build_mix_link(compat=False)
    assert "pbk=reality-pub-placeholder" in url
    assert "sid=s1" in url
    assert "alpn=h2" in url


def test_mix_compat_omits_reality_keys(env: None) -> None:
    url = sub.build_mix_link(compat=True)
    assert "encryption=none" in url
    # pure CDN inbound → no reality pbk/sid
    assert "pbk=" not in url
    assert "sid=" not in url


def test_xhttp_h3_includes_extra_obfs(env: None) -> None:
    url = sub.build_xhttp_h3_link()
    assert "alpn=h3" in url
    assert "port" in urllib.parse.urlparse(url).netloc or ":6443" in url
    assert "%22noSSEHeader%22%3Atrue" in url
    assert "%22xPaddingQueryParam%22%3A%22cf_ray_id%22" in url
    assert "%22UplinkDataPlacement%22%3A%22auto%22" in url
    assert "path=%2Fabcd1234-xhttp-h3" in url


def test_v2rayn_subscription_includes_all_ten_lines(env: None) -> None:
    sub_text = sub.build_v2rayn_subscription()
    lines = sub_text.split("\n")
    assert len(lines) == 10
    # part1: hy2, tuic, anytls, vmess, vless-vision
    assert lines[0].startswith("hysteria2://")
    assert lines[1].startswith("tuic://")
    assert lines[2].startswith("anytls://")
    assert lines[3].startswith("vmess://")
    assert lines[4].startswith("vless://") and "flow=xtls-rprx-vision" in lines[4]
    # part2: xhttp-h3, xhttp-reality, up_cdn, up_reality, mix
    assert "Xhttp-H3%2BBBR" in lines[5]
    assert lines[6].startswith("vless://") and "Xhttp%2BReality%E7%9B%B4%E8%BF%9E" in lines[6]


def test_common_subscription_has_eight_lines(env: None) -> None:
    sub_text = sub.build_common_subscription()
    lines = sub_text.split("\n")
    # part1_common (5: hy2, tuic, anytls, vmess, vless-vision) + part2_common (3) = 8
    assert len(lines) == 8
    assert lines[0].startswith("hysteria2://")
    assert lines[1].startswith("tuic://")
    assert lines[2].startswith("anytls://")
    assert lines[3].startswith("vmess://")
    assert lines[4].startswith("vless://") and "flow=xtls-rprx-vision" in lines[4]
    assert "Xhttp%2BReality%E7%9B%B4%E8%BF%9E" in lines[5]
    assert "%E4%B8%8A%E8%A1%8CXhttp%2BTLS%2BCDN%E4%B8%8B%E8%A1%8CXhttp%2BReality" in lines[6]
    assert "Xhttp%2BTLS%2BCDN%E4%B8%8A%E4%B8%8B%E8%A1%8C%E4%B8%8D%E5%88%86%E7%A6%BB" in lines[7]
    assert not any("Xhttp%2BReality%E7%9B%B4%E8%BF%9E" in ln and "%E4%B8%8B%E8%A1%8CXhttp%2BTLS" in ln for ln in lines)
    # main-only H3 link must NOT be in common track
    assert not any("Xhttp-H3%2BBBR" in ln for ln in lines)
    # common XHTTP variants must all use mode=packet-up or encryption=none
    for ln in lines[5:]:
        assert "encryption=none" in ln


def test_vmess_ps_is_raw_not_percent_encoded(monkeypatch: pytest.MonkeyPatch) -> None:
    """vmess ps フィールドは生のノード名(パーセントエンコードなし)でなければならない。

    Regression guard for the Task-1 fix: vmess://base64(json) では ps が
    JSON 値として格納され、クライアントは URL-decode せずそのまま表示する。
    emoji/CJK が入った名前がリテラルで届くことを確認する。
    """
    for k, v in _FAKE_ENV.items():
        monkeypatch.setenv(k, v)
    # Use an emoji + CJK name to detect percent-encoding regression
    monkeypatch.setenv("FLAG_PREFIX", "🇯🇵 ")
    monkeypatch.setenv("NODE_NAME", "日本")
    monkeypatch.setenv("NODE_SUFFIX", "")
    url = sub.build_vmess_link()
    payload = url[len("vmess://"):]
    data = json.loads(base64.b64decode(payload + "==").decode("utf-8"))
    # ps must be the raw assembled string, not percent-encoded
    assert data["ps"] == "🇯🇵 Vmess ✈ 日本"
    # Confirm it does NOT contain any percent-encoded sequences
    assert "%" not in data["ps"]


def test_uri_fragment_still_url_encoded_after_vmess_ps_fix(monkeypatch: pytest.MonkeyPatch) -> None:
    """J1: URI fragment builders (hysteria2/vless/etc.) must still URL-encode.

    Ensures the vmess-ps-raw fix did NOT accidentally un-encode URI fragments.
    emoji + CJK in the fragment must be percent-encoded; unquote() restores them.
    """
    for k, v in _FAKE_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("FLAG_PREFIX", "🇯🇵 ")
    monkeypatch.setenv("NODE_NAME", "日本")
    monkeypatch.setenv("NODE_SUFFIX", "")
    url = sub.build_hysteria2_link()
    frag = url.split("#", 1)[1]
    # Raw emoji/CJK must be percent-encoded in the fragment
    assert "🇯🇵" not in frag
    assert "日本" not in frag
    assert "%" in frag
    # But unquoting restores the original
    assert urllib.parse.unquote(frag) == "🇯🇵 Hysteria2 ✈ 日本"


def test_remark_special_chars_are_url_encoded(monkeypatch: pytest.MonkeyPatch) -> None:
    """J1: 运维可控 NODE_NAME 含 #/&/换行 必须 URL-encode,否则破坏 fragment 解析或注入。"""
    for k, v in _FAKE_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("FLAG_PREFIX", "")
    monkeypatch.setenv("NODE_SUFFIX", "")
    monkeypatch.setenv("NODE_NAME", "ev#il&x=1\nz")
    url = sub.build_hysteria2_link()
    # fragment 必须不含裸 # / & / 换行(除分隔 query 与 fragment 的首个 #)
    frag = url.split("#", 1)[1]
    assert "#" not in frag
    assert "\n" not in frag
    assert "%23" in frag  # 被编码的 '#'
    # 解码后还原原始备注内容
    assert urllib.parse.unquote(frag) == "Hysteria2 ✈ ev#il&x=1\nz"


def test_remark_preserves_flag_emoji_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    """常规 emoji 旗标 + 中文备注仍能解码还原(无回归)。"""
    for k, v in _FAKE_ENV.items():
        monkeypatch.setenv(k, v)
    url = sub.build_tuic_link()
    frag = url.split("#", 1)[1]
    assert urllib.parse.unquote(frag) == "🇯🇵 TUIC ✈ jp ✈ isp"


# ------------------------------------------------------------------
# J1: port env validation — fail-loud (non-numeric / empty / OOB)
# ------------------------------------------------------------------


def test_nonnumeric_port_fails_loud_with_env_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """J1: LISTENING_PORT 非数字必须 fail-loud 并指名 env,而非生成无效 vmess。"""
    for k, v in _FAKE_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("LISTENING_PORT", "443; rm -rf")
    with pytest.raises(RuntimeError, match="LISTENING_PORT"):
        sub.build_vmess_link()


def test_empty_port_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in _FAKE_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("PORT_HYSTERIA2", "")
    with pytest.raises(RuntimeError, match="PORT_HYSTERIA2"):
        sub.build_hysteria2_link()


def test_valid_port_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in _FAKE_ENV.items():
        monkeypatch.setenv(k, v)
    assert ":8443" in sub.build_hysteria2_link()  # 数字端口正常
