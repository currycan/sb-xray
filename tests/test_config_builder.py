"""Tests for sb_xray.config_builder (entrypoint.sh:createConfig port)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from sb_xray import config_builder as cb


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Fresh workdir + minimal env; isolate from real /templates."""
    for key in (
        "ENABLE_XICMP",
        "ENABLE_XDNS",
        "ENABLE_REVERSE",
        "REVERSE_DOMAINS",
        "XRAY_REVERSE_UUID",
        "REVERSE_CN_EXIT",
        "ENABLE_SOCKS5_PROXY",
        "CN_EXIT_SOCKS5_HOST",
        "CN_EXIT_SOCKS5_PORT",
        "CN_EXIT_MODE",
        "CN_EXIT_PROBE_URL",
        "CN_EXIT_PROBE_INTERVAL",
        "RANDOM_NUM",
        "ENABLE_SUBSTORE",
        "ENABLE_XUI",
        # "ENABLE_SUI",  # s-ui removed
        "ENABLE_SHOUTRRR",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("WORKDIR", str(tmp_path / "workdir"))
    monkeypatch.setenv("DOMAIN", "vpn.example.com")
    monkeypatch.setenv("CDNDOMAIN", "cdn.example.com")
    return tmp_path


def test_envsubst_preserves_unset_references(env: Path) -> None:
    """Regression: nginx.conf references nginx runtime vars like
    ``$http_x_forwarded_for`` / ``$client_ip`` that are NOT shell env
    vars — GNU envsubst leaves them untouched. Replacing with empty
    string collapses ``map $src $dst {...}`` into ``map   {...}``
    which nginx rejects with 'invalid number of arguments in map
    directive' (the exact failure observed on production)."""
    # Unset → keep literal form
    assert cb._envsubst("$FOO-$BAR") == "$FOO-$BAR"
    assert cb._envsubst("${FOO}:${BAZ}") == "${FOO}:${BAZ}"
    # Known → substitute
    os.environ["FOO"] = "hi"
    assert cb._envsubst("$FOO-$BAR") == "hi-$BAR"
    assert cb._envsubst("${FOO}:${BAZ}") == "hi:${BAZ}"


def test_envsubst_preserves_nginx_runtime_vars(env: Path) -> None:
    """Concrete regression mirroring templates/nginx/nginx.conf L19:
    ``map $http_x_forwarded_for $client_ip { "" $remote_addr; }``.
    None of those ``$`` refs are shell vars — all must survive
    unchanged."""
    tpl = 'map $http_x_forwarded_for $client_ip { "" $remote_addr; }'
    assert cb._envsubst(tpl) == tpl


def test_render_flat_writes_expanded_text(env: Path, tmp_path: Path) -> None:
    src = tmp_path / "source.conf"
    src.write_text("listen $DOMAIN;\n", encoding="utf-8")
    dest = tmp_path / "out" / "nginx.conf"
    cb._render_flat(src, dest)
    assert dest.read_text(encoding="utf-8") == "listen vpn.example.com;\n"


def test_render_json_fails_on_invalid_output(env: Path, tmp_path: Path) -> None:
    src = tmp_path / "bad.json"
    src.write_text('{"key": $UNSET}\n', encoding="utf-8")
    dest = tmp_path / "out.json"
    with pytest.raises(RuntimeError, match="invalid JSON"):
        cb._render_json(src, dest)


def test_render_json_validates_and_reformats(env: Path, tmp_path: Path) -> None:
    src = tmp_path / "tpl.json"
    src.write_text('{"domain":"$DOMAIN","port":443}\n', encoding="utf-8")
    dest = tmp_path / "out.json"
    cb._render_json(src, dest)
    data = json.loads(dest.read_text(encoding="utf-8"))
    assert data == {"domain": "vpn.example.com", "port": 443}


def test_cleanup_orphan_json_removes_unsourced(env: Path, tmp_path: Path) -> None:
    workdir = tmp_path / "xray"
    workdir.mkdir()
    (workdir / "01_still_here.json").write_text("{}", encoding="utf-8")
    (workdir / "99_orphan.json").write_text("{}", encoding="utf-8")

    template_dir = tmp_path / "tpl-xray"
    template_dir.mkdir()
    (template_dir / "01_still_here.json").write_text("{}", encoding="utf-8")

    cb._cleanup_orphan_json(workdir, template_dir)
    assert (workdir / "01_still_here.json").is_file()
    assert not (workdir / "99_orphan.json").is_file()


def test_feature_flag_disabled_when_env_not_true(env: Path) -> None:
    os.environ["ENABLE_XICMP"] = "false"
    assert cb._is_feature_disabled("05_xicmp_emergency_inbounds.json") is True
    os.environ["ENABLE_XICMP"] = "true"
    assert cb._is_feature_disabled("05_xicmp_emergency_inbounds.json") is False


def test_feature_flag_unrelated_template_returns_false(env: Path) -> None:
    assert cb._is_feature_disabled("01_reality_inbounds.json") is False


def test_parse_reverse_domains_strips_and_filters_empty(env: Path) -> None:
    assert cb._parse_reverse_domains("") == []
    assert cb._parse_reverse_domains("a.com, b.com,,c.com ") == ["a.com", "b.com", "c.com"]


def test_inject_reverse_client_appends_to_clients_array(env: Path, tmp_path: Path) -> None:
    reality = tmp_path / "01_reality_inbounds.json"
    reality.write_text(
        json.dumps({"inbounds": [{"settings": {"clients": [{"id": "orig", "level": 0}]}}]}),
        encoding="utf-8",
    )
    cb._inject_reverse_client(reality, "new-uuid-123")
    data = json.loads(reality.read_text(encoding="utf-8"))
    clients = data["inbounds"][0]["settings"]["clients"]
    assert len(clients) == 2
    assert clients[1]["id"] == "new-uuid-123"
    assert clients[1]["reverse"] == {"tag": "r-tunnel"}
    assert clients[1]["flow"] == "xtls-rprx-vision"


def test_inject_reverse_route_prepends_rule(env: Path, tmp_path: Path) -> None:
    xr = tmp_path / "xr.json"
    xr.write_text(
        json.dumps({"routing": {"rules": [{"ruleTag": "existing", "type": "field"}]}}),
        encoding="utf-8",
    )
    cb._inject_reverse_route(xr, ["reverse1.example", "reverse2.example"])
    data = json.loads(xr.read_text(encoding="utf-8"))
    rules = data["routing"]["rules"]
    assert rules[0]["ruleTag"] == "reverse-bridge"
    assert rules[0]["domain"] == ["reverse1.example", "reverse2.example"]
    assert rules[0]["outboundTag"] == "r-tunnel"
    assert rules[1]["ruleTag"] == "existing"


def test_apply_reverse_proxy_noop_when_disabled(env: Path, tmp_path: Path) -> None:
    os.environ["ENABLE_REVERSE"] = "false"
    workdir = tmp_path / "workdir"
    (workdir / "xray").mkdir(parents=True)
    reality = workdir / "xray" / "01_reality_inbounds.json"
    reality.write_text(
        json.dumps({"inbounds": [{"settings": {"clients": []}}]}),
        encoding="utf-8",
    )
    cb._apply_reverse_proxy(workdir)
    data = json.loads(reality.read_text(encoding="utf-8"))
    assert data["inbounds"][0]["settings"]["clients"] == []


def test_apply_reverse_proxy_injects_when_enabled(env: Path, tmp_path: Path) -> None:
    os.environ["ENABLE_REVERSE"] = "true"
    os.environ["XRAY_REVERSE_UUID"] = "rev-uuid-789"
    os.environ["REVERSE_DOMAINS"] = "a.com,b.com"
    workdir = tmp_path / "workdir"
    (workdir / "xray").mkdir(parents=True)
    reality = workdir / "xray" / "01_reality_inbounds.json"
    reality.write_text(
        json.dumps({"inbounds": [{"settings": {"clients": []}}]}),
        encoding="utf-8",
    )
    xr = workdir / "xray" / "xr.json"
    xr.write_text(json.dumps({"routing": {"rules": []}}), encoding="utf-8")

    cb._apply_reverse_proxy(workdir)

    reality_data = json.loads(reality.read_text(encoding="utf-8"))
    assert len(reality_data["inbounds"][0]["settings"]["clients"]) == 1
    assert reality_data["inbounds"][0]["settings"]["clients"][0]["id"] == "rev-uuid-789"

    xr_data = json.loads(xr.read_text(encoding="utf-8"))
    assert xr_data["routing"]["rules"][0]["domain"] == ["a.com", "b.com"]


def test_apply_reverse_proxy_skip_route_without_domains(env: Path, tmp_path: Path) -> None:
    os.environ["ENABLE_REVERSE"] = "true"
    os.environ["XRAY_REVERSE_UUID"] = "u"
    os.environ["REVERSE_DOMAINS"] = ""
    workdir = tmp_path / "workdir"
    (workdir / "xray").mkdir(parents=True)
    xr = workdir / "xray" / "xr.json"
    xr.write_text(json.dumps({"routing": {"rules": [{"ruleTag": "keep"}]}}), encoding="utf-8")

    cb._apply_reverse_proxy(workdir)

    xr_data = json.loads(xr.read_text(encoding="utf-8"))
    assert xr_data["routing"]["rules"] == [{"ruleTag": "keep"}]


def test_create_config_full_flow(
    env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sandbox the module-level paths to a temp template tree."""
    templates = tmp_path / "templates"
    (templates / "supervisord").mkdir(parents=True)
    (templates / "supervisord" / "supervisord.conf").write_text(
        "domain=$DOMAIN\n", encoding="utf-8"
    )
    (templates / "supervisord" / "daemon.ini").write_text("d=$DOMAIN\n", encoding="utf-8")
    (templates / "nginx").mkdir()
    for name in ("nginx.conf", "http.conf", "tcp.conf", "network_internal.conf"):
        (templates / "nginx" / name).write_text(f"stub-{name}\n", encoding="utf-8")
    (templates / "dufs").mkdir()
    (templates / "dufs" / "conf.yml").write_text("port: 5000\n", encoding="utf-8")
    (templates / "providers").mkdir()
    (templates / "providers" / "providers.yaml").write_text(
        "proxy-providers:\n# placeholder\n", encoding="utf-8"
    )
    (templates / "xray").mkdir()
    (templates / "xray" / "01_reality_inbounds.json").write_text(
        '{"inbounds":[{"settings":{"clients":[]}}]}', encoding="utf-8"
    )
    (templates / "xray" / "xr.json").write_text('{"routing":{"rules":[]}}', encoding="utf-8")
    (templates / "sing-box").mkdir()
    (templates / "sing-box" / "sb.json").write_text('{"tag":"$DOMAIN"}', encoding="utf-8")

    workdir = tmp_path / "workdir"
    (workdir / "dufs").mkdir(parents=True)

    monkeypatch.setattr(cb, "_TEMPLATES", templates)
    etc_root = tmp_path / "etc"
    (etc_root / "supervisor.d").mkdir(parents=True)
    (etc_root / "nginx" / "conf.d").mkdir(parents=True)
    (etc_root / "nginx" / "stream.d").mkdir(parents=True)
    monkeypatch.setattr(
        cb,
        "_FLAT_RENDERS",
        (
            ("supervisord/supervisord.conf", str(etc_root / "supervisord.conf")),
            ("supervisord/daemon.ini", str(etc_root / "supervisor.d" / "daemon.ini")),
            ("nginx/nginx.conf", str(etc_root / "nginx" / "nginx.conf")),
            ("nginx/http.conf", str(etc_root / "nginx" / "conf.d" / "http.conf")),
            ("nginx/tcp.conf", str(etc_root / "nginx" / "stream.d" / "tcp.conf")),
            ("dufs/conf.yml", "${WORKDIR}/dufs/conf.yml"),
            ("providers/providers.yaml", "${WORKDIR}/providers"),
        ),
    )
    monkeypatch.setattr(
        cb,
        "_FLAT_COPIES",
        (("nginx/network_internal.conf", str(etc_root / "nginx" / "network_internal.conf")),),
    )

    cb.create_config(workdir=workdir)

    assert (etc_root / "supervisord.conf").read_text(encoding="utf-8") == "domain=vpn.example.com\n"
    assert (
        (etc_root / "nginx" / "network_internal.conf")
        .read_text(encoding="utf-8")
        .startswith("stub-")
    )
    assert (workdir / "xray" / "01_reality_inbounds.json").is_file()
    assert (workdir / "sing-box" / "sb.json").is_file()
    sb_data = json.loads((workdir / "sing-box" / "sb.json").read_text(encoding="utf-8"))
    assert sb_data == {"tag": "vpn.example.com"}
    assert os.environ["RANDOM_NUM"].isdigit()


# ---------------------------------------------------------------------------
# Small-memory VPS trim switches (nodes ≤ 512 MB RAM OOM mitigation)
# ---------------------------------------------------------------------------


_DAEMON_INI_FIXTURE = """\
[program:x-ui]
command=x-ui
priority=5

[program:sub-store]
command=node /sub-store/sub-store.bundle.js
priority=15
environment=SUB_STORE_DOCKER=%(ENV_SUB_STORE_DOCKER)s

[program:http-meta]
command=node /sub-store/http-meta.bundle.js
priority=15

[program:shoutrrr-forwarder]
command=python3 /scripts/entrypoint.py shoutrrr-forward
priority=18

[program:xray]
command=xray run -confdir ${WORKDIR}/xray/
priority=20

[program:nginx]
command=/usr/sbin/nginx -g "daemon off;"
priority=25
"""


def test_flag_is_disabled_only_on_explicit_false(env: Path) -> None:
    os.environ["ENABLE_SUBSTORE"] = "false"
    assert cb._flag_is_disabled("ENABLE_SUBSTORE") is True
    os.environ["ENABLE_SUBSTORE"] = "FALSE"
    assert cb._flag_is_disabled("ENABLE_SUBSTORE") is True
    os.environ["ENABLE_SUBSTORE"] = "true"
    assert cb._flag_is_disabled("ENABLE_SUBSTORE") is False
    os.environ["ENABLE_SUBSTORE"] = ""
    assert cb._flag_is_disabled("ENABLE_SUBSTORE") is False


def test_filter_supervisord_keeps_all_when_flags_unset(env: Path, tmp_path: Path) -> None:
    dest = tmp_path / "daemon.ini"
    dest.write_text(_DAEMON_INI_FIXTURE, encoding="utf-8")
    cb._filter_supervisord_programs(dest)
    assert dest.read_text(encoding="utf-8") == _DAEMON_INI_FIXTURE


def test_filter_supervisord_drops_substore_pair(env: Path, tmp_path: Path) -> None:
    os.environ["ENABLE_SUBSTORE"] = "false"
    dest = tmp_path / "daemon.ini"
    dest.write_text(_DAEMON_INI_FIXTURE, encoding="utf-8")
    cb._filter_supervisord_programs(dest)
    text = dest.read_text(encoding="utf-8")
    assert "[program:sub-store]" not in text
    assert "[program:http-meta]" not in text
    # assert "[program:s-ui]" in text  # s-ui removed
    assert "[program:xray]" in text
    assert "[program:nginx]" in text


# s-ui removed — test updated
def test_filter_supervisord_drops_shoutrrr(env: Path, tmp_path: Path) -> None:
    os.environ["ENABLE_SHOUTRRR"] = "false"
    dest = tmp_path / "daemon.ini"
    dest.write_text(_DAEMON_INI_FIXTURE, encoding="utf-8")
    cb._filter_supervisord_programs(dest)
    text = dest.read_text(encoding="utf-8")
    # assert "[program:s-ui]" not in text  # s-ui removed from fixture
    assert "[program:shoutrrr-forwarder]" not in text
    assert "[program:sub-store]" in text
    assert "[program:x-ui]" in text  # ENABLE_XUI unset → kept
    assert "[program:xray]" in text


def test_filter_supervisord_drops_xui_when_flag_false(env: Path, tmp_path: Path) -> None:
    os.environ["ENABLE_XUI"] = "false"
    dest = tmp_path / "daemon.ini"
    dest.write_text(_DAEMON_INI_FIXTURE, encoding="utf-8")
    cb._filter_supervisord_programs(dest)
    text = dest.read_text(encoding="utf-8")
    assert "[program:x-ui]" not in text
    # assert "[program:s-ui]" in text  # s-ui removed
    assert "[program:xray]" in text


# s-ui removed — test updated
def test_filter_supervisord_preserves_supervisor_interpolation(env: Path, tmp_path: Path) -> None:
    """``%(ENV_*)s`` must survive filtering verbatim (regex, not configparser)."""
    dest = tmp_path / "daemon.ini"
    dest.write_text(_DAEMON_INI_FIXTURE, encoding="utf-8")
    cb._filter_supervisord_programs(dest)
    assert "%(ENV_SUB_STORE_DOCKER)s" in dest.read_text(encoding="utf-8")


# s-ui removed — test updated
def test_trim_runtime_configs_filters_existing_daemon_ini(env: Path, tmp_path: Path) -> None:
    os.environ["ENABLE_SUBSTORE"] = "false"
    daemon = tmp_path / "daemon.ini"
    daemon.write_text(_DAEMON_INI_FIXTURE, encoding="utf-8")

    cb.trim_runtime_configs(daemon_ini=daemon)

    text = daemon.read_text(encoding="utf-8")
    assert "[program:sub-store]" not in text
    assert "[program:http-meta]" not in text
    # assert "[program:s-ui]" not in text  # s-ui removed from fixture
    assert "[program:xray]" in text


def test_trim_runtime_configs_silent_when_daemon_missing(env: Path, tmp_path: Path) -> None:
    """No daemon.ini present → must not raise."""
    cb.trim_runtime_configs(daemon_ini=tmp_path / "missing.ini")


# ---------------------------------------------------------------------------
# nginx access-log 档位 (NGINX_ACCESS_LOG) + logrotate
# ---------------------------------------------------------------------------


def test_access_log_minimal_default(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NGINX_ACCESS_LOG", raising=False)
    monkeypatch.setenv("LOGDIR", "/var/log")
    cb._apply_access_log_env()
    assert (
        os.environ["NGINX_HTTP_ACCESS_LOG"]
        == "access_log /var/log/nginx/http-access.log http_json if=$loggable;"
    )
    assert (
        os.environ["NGINX_TCP_ACCESS_LOG"]
        == "access_log /var/log/nginx/tcp-access.log tcp_json if=$loggable;"
    )


def test_access_log_full(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NGINX_ACCESS_LOG", "full")
    monkeypatch.setenv("LOGDIR", "/var/log")
    cb._apply_access_log_env()
    assert (
        os.environ["NGINX_HTTP_ACCESS_LOG"]
        == "access_log /var/log/nginx/http-access.log http_json;"
    )
    assert "if=" not in os.environ["NGINX_TCP_ACCESS_LOG"]


def test_access_log_off(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NGINX_ACCESS_LOG", "off")
    cb._apply_access_log_env()
    assert os.environ["NGINX_HTTP_ACCESS_LOG"] == "access_log off;"
    assert os.environ["NGINX_TCP_ACCESS_LOG"] == "access_log off;"


def test_access_log_invalid_falls_back_to_minimal(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NGINX_ACCESS_LOG", "verbose")
    monkeypatch.setenv("LOGDIR", "/var/log")
    cb._apply_access_log_env()
    assert "if=$loggable" in os.environ["NGINX_HTTP_ACCESS_LOG"]


def test_access_log_normalises_logdir_trailing_slash(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NGINX_ACCESS_LOG", "full")
    monkeypatch.setenv("LOGDIR", "/var/log/")
    cb._apply_access_log_env()
    assert "/var/log/nginx/http-access.log" in os.environ["NGINX_HTTP_ACCESS_LOG"]
    assert "//nginx" not in os.environ["NGINX_HTTP_ACCESS_LOG"]


def test_access_log_placeholder_survives_render(
    env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rendered access_log line keeps the runtime ``$loggable`` var.

    The single-pass envsubst must not rescan the substituted text, otherwise
    ``$loggable`` would collapse to a literal and break the nginx ``if=``.
    """
    monkeypatch.setenv("NGINX_ACCESS_LOG", "minimal")
    monkeypatch.setenv("LOGDIR", "/var/log")
    cb._apply_access_log_env()
    src = tmp_path / "n.conf"
    src.write_text("    ${NGINX_HTTP_ACCESS_LOG}\n", encoding="utf-8")
    dest = tmp_path / "out.conf"
    cb._render_flat(src, dest)
    out = dest.read_text(encoding="utf-8")
    assert "access_log /var/log/nginx/http-access.log http_json if=$loggable;" in out
    assert "$loggable" in out


def test_run_logrotate_skips_when_conf_missing(env: Path, tmp_path: Path) -> None:
    assert cb.run_logrotate(conf=tmp_path / "nope.conf", state=tmp_path / "s") == 0


def test_run_logrotate_invokes_binary(
    env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conf = tmp_path / "sb-xray.conf"
    conf.write_text("/var/log/*.log {}\n", encoding="utf-8")
    state = tmp_path / "lib" / "sb-xray.status"
    captured: dict[str, list[str]] = {}

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(cb.shutil, "which", lambda _: "/usr/sbin/logrotate")
    monkeypatch.setattr(cb.subprocess, "run", fake_run)

    assert cb.run_logrotate(conf=conf, state=state) == 0
    assert captured["argv"][0] == "/usr/sbin/logrotate"
    assert "-s" in captured["argv"]
    assert str(state) in captured["argv"]
    assert str(conf) in captured["argv"]
    assert state.parent.is_dir()  # parent dir created before invocation


def test_run_logrotate_propagates_nonzero(
    env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conf = tmp_path / "sb-xray.conf"
    conf.write_text("/var/log/*.log {}\n", encoding="utf-8")

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="boom")

    monkeypatch.setattr(cb.subprocess, "run", fake_run)
    assert cb.run_logrotate(conf=conf, state=tmp_path / "s") == 1


# ---------------------------------------------------------------------------
# CN exit (REVERSE_CN_EXIT)
# ---------------------------------------------------------------------------


def test_apply_cn_exit_noop_when_disabled(env: Path, tmp_path: Path) -> None:
    os.environ["REVERSE_CN_EXIT"] = "false"
    xr = tmp_path / "xr.json"
    xr.write_text(
        json.dumps({"routing": {"rules": [{"ruleTag": "cn-ip", "outboundTag": "block"}]}}),
        encoding="utf-8",
    )
    cb._apply_cn_exit(xr)
    data = json.loads(xr.read_text(encoding="utf-8"))
    assert data["routing"]["rules"][0]["outboundTag"] == "block"


def test_apply_cn_exit_redirects_cn_ip_to_r_tunnel(env: Path, tmp_path: Path) -> None:
    os.environ["REVERSE_CN_EXIT"] = "true"
    xr = tmp_path / "xr.json"
    xr.write_text(
        json.dumps(
            {
                "routing": {
                    "rules": [
                        {"ruleTag": "bt", "outboundTag": "block"},
                        {"ruleTag": "cn-ip", "ip": ["geoip:cn"], "outboundTag": "block"},
                        {"ruleTag": "ad-domain", "outboundTag": "block"},
                        {"ruleTag": "private-ip", "ip": ["geoip:private"], "outboundTag": "block"},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    cb._apply_cn_exit(xr)
    data = json.loads(xr.read_text(encoding="utf-8"))
    rules = data["routing"]["rules"]
    tags = [r["ruleTag"] for r in rules]
    # cn 规则下移到 private-ip 之前，前置健康检查豁免 + 海外直出护栏
    assert tags == [
        "bt",
        "ad-domain",
        "cn-exit-probe-bypass",
        "cn-exit-overseas",
        "cn-geosite",
        "cn-ip",
        "private-ip",
    ]
    probe = rules[2]
    assert probe["domain"] == ["full:www.gstatic.com"]
    assert probe["outboundTag"] == "direct"
    overseas = rules[3]
    assert overseas["domain"] == ["geosite:geolocation-!cn"]
    assert overseas["outboundTag"] == "direct"
    assert rules[4]["domain"] == ["geosite:cn"]
    assert rules[4]["outboundTag"] == "r-tunnel"
    assert rules[5]["ip"] == ["geoip:cn"]
    assert rules[5]["outboundTag"] == "r-tunnel"


def test_apply_cn_exit_noop_when_cn_ip_rule_absent(env: Path, tmp_path: Path) -> None:
    os.environ["REVERSE_CN_EXIT"] = "true"
    xr = tmp_path / "xr.json"
    original = {"routing": {"rules": [{"ruleTag": "other", "outboundTag": "block"}]}}
    xr.write_text(json.dumps(original), encoding="utf-8")
    cb._apply_cn_exit(xr)
    data = json.loads(xr.read_text(encoding="utf-8"))
    assert len(data["routing"]["rules"]) == 1


# ---------------------------------------------------------------------------
# CN exit - SOCKS5 mode (CN_EXIT_SOCKS5_HOST)
# ---------------------------------------------------------------------------

_SOCKS5_XR_BASE = {
    "outbounds": [{"tag": "direct"}, {"tag": "block"}],
    "routing": {
        "rules": [
            {"ruleTag": "bt", "outboundTag": "block"},
            {
                "ruleTag": "cn-ip",
                "ip": ["geoip:cn"],
                "marktag": "ban_geoip_cn",
                "outboundTag": "block",
                "webhook": {"url": "http://127.0.0.1:18085/ban_geoip_cn"},
            },
            {"ruleTag": "ad-domain", "outboundTag": "block"},
            {"ruleTag": "private-ip", "ip": ["geoip:private"], "outboundTag": "block"},
        ]
    },
}


def test_apply_cn_exit_socks5_injects_outbound(env: Path, tmp_path: Path) -> None:
    os.environ["CN_EXIT_SOCKS5_HOST"] = "100.99.99.1"
    os.environ["CN_EXIT_SOCKS5_PORT"] = "7891"
    xr = tmp_path / "xr.json"
    xr.write_text(json.dumps(_SOCKS5_XR_BASE), encoding="utf-8")
    cb._apply_cn_exit(xr)
    data = json.loads(xr.read_text(encoding="utf-8"))
    outbounds = data["outbounds"]
    socks_obs = [o for o in outbounds if o.get("tag") == "cn-exit"]
    assert len(socks_obs) == 1
    assert socks_obs[0]["protocol"] == "socks"
    assert socks_obs[0]["settings"]["servers"][0]["address"] == "100.99.99.1"
    assert socks_obs[0]["settings"]["servers"][0]["port"] == 7891


def test_apply_cn_exit_socks5_rewires_rules(env: Path, tmp_path: Path) -> None:
    os.environ["CN_EXIT_SOCKS5_HOST"] = "100.99.99.1"
    xr = tmp_path / "xr.json"
    xr.write_text(json.dumps(_SOCKS5_XR_BASE), encoding="utf-8")
    cb._apply_cn_exit(xr)
    data = json.loads(xr.read_text(encoding="utf-8"))
    rules = data["routing"]["rules"]
    tags = [r["ruleTag"] for r in rules]
    # cn 规则下移到 private-ip 之前，让服务直连例外规则优先匹配
    assert tags == [
        "bt",
        "ad-domain",
        "cn-exit-probe-bypass",
        "cn-exit-overseas",
        "cn-geosite",
        "cn-ip",
        "private-ip",
    ]
    assert rules[2]["domain"] == ["full:www.gstatic.com"]
    assert rules[2]["outboundTag"] == "direct"
    assert rules[3]["domain"] == ["geosite:geolocation-!cn"]
    assert rules[3]["outboundTag"] == "direct"
    assert rules[4]["domain"] == ["geosite:cn"]
    assert rules[4]["outboundTag"] == "cn-exit"
    assert rules[5]["ip"] == ["geoip:cn"]
    assert rules[5]["outboundTag"] == "cn-exit"


def test_apply_cn_exit_overseas_guard_precedes_cn_geosite(env: Path, tmp_path: Path) -> None:
    """geosite:geolocation-!cn → direct 护栏必须排在 cn-geosite 回国规则之前。

    Loyalsoldier geosite:cn 收录了 dl.google.com / *.gvt1.com 等 Google Play
    CDN 子域（国内可直连清单）。若 cn-geosite 抢先匹配，这些海外服务会被送回
    国内出口，导致 Google Play 等地区敏感应用从国内 IP 访问而失效。护栏让所有
    明确属于海外的域名先走 direct（海外 VPS 直出）。
    """
    os.environ["CN_EXIT_SOCKS5_HOST"] = "100.99.99.1"
    xr = tmp_path / "xr.json"
    xr.write_text(json.dumps(_SOCKS5_XR_BASE), encoding="utf-8")
    cb._apply_cn_exit(xr)
    rules = json.loads(xr.read_text(encoding="utf-8"))["routing"]["rules"]
    tags = [r["ruleTag"] for r in rules]
    overseas_idx = tags.index("cn-exit-overseas")
    cn_geosite_idx = tags.index("cn-geosite")
    assert overseas_idx < cn_geosite_idx
    overseas = rules[overseas_idx]
    assert overseas["domain"] == ["geosite:geolocation-!cn"]
    assert overseas["outboundTag"] == "direct"


def test_apply_cn_exit_socks5_strips_ban_marktag_and_webhook(env: Path, tmp_path: Path) -> None:
    """cn-ip 由封禁改为回国出站后，不得再携带 ban marktag/webhook（否则误报封禁事件）。"""
    os.environ["CN_EXIT_SOCKS5_HOST"] = "100.99.99.1"
    xr = tmp_path / "xr.json"
    xr.write_text(json.dumps(_SOCKS5_XR_BASE), encoding="utf-8")
    cb._apply_cn_exit(xr)
    data = json.loads(xr.read_text(encoding="utf-8"))
    cn_ip_rule = next(r for r in data["routing"]["rules"] if r["ruleTag"] == "cn-ip")
    assert "marktag" not in cn_ip_rule
    assert "webhook" not in cn_ip_rule


def test_apply_cn_exit_socks5_appends_when_private_ip_absent(env: Path, tmp_path: Path) -> None:
    """没有 private-ip 锚点时，cn 规则追加到规则表末尾。"""
    os.environ["CN_EXIT_SOCKS5_HOST"] = "100.99.99.1"
    xr = tmp_path / "xr.json"
    base = {
        "outbounds": [{"tag": "direct"}],
        "routing": {
            "rules": [
                {"ruleTag": "bt", "outboundTag": "block"},
                {"ruleTag": "cn-ip", "ip": ["geoip:cn"], "outboundTag": "block"},
            ]
        },
    }
    xr.write_text(json.dumps(base), encoding="utf-8")
    cb._apply_cn_exit(xr)
    data = json.loads(xr.read_text(encoding="utf-8"))
    tags = [r["ruleTag"] for r in data["routing"]["rules"]]
    assert tags == ["bt", "cn-exit-probe-bypass", "cn-exit-overseas", "cn-geosite", "cn-ip"]


def test_apply_cn_exit_socks5_takes_priority_over_rtunnel(env: Path, tmp_path: Path) -> None:
    os.environ["CN_EXIT_SOCKS5_HOST"] = "100.99.99.1"
    os.environ["REVERSE_CN_EXIT"] = "true"
    xr = tmp_path / "xr.json"
    xr.write_text(json.dumps(_SOCKS5_XR_BASE), encoding="utf-8")
    cb._apply_cn_exit(xr)
    data = json.loads(xr.read_text(encoding="utf-8"))
    rules = data["routing"]["rules"]
    cn_ip_rule = next(r for r in rules if r["ruleTag"] == "cn-ip")
    assert cn_ip_rule["outboundTag"] == "cn-exit"


def test_apply_cn_exit_socks5_disabled_by_switch(env: Path, tmp_path: Path) -> None:
    """ENABLE_SOCKS5_PROXY=false 时即使 HOST 有值也不注入 SOCKS5 出站。"""
    os.environ["ENABLE_SOCKS5_PROXY"] = "false"
    os.environ["CN_EXIT_SOCKS5_HOST"] = "100.99.99.1"
    xr = tmp_path / "xr.json"
    xr.write_text(json.dumps(_SOCKS5_XR_BASE), encoding="utf-8")
    cb._apply_cn_exit(xr)
    data = json.loads(xr.read_text(encoding="utf-8"))
    assert not [o for o in data["outbounds"] if o.get("tag") == "cn-exit"]
    cn_ip_rule = next(r for r in data["routing"]["rules"] if r["ruleTag"] == "cn-ip")
    assert cn_ip_rule["outboundTag"] == "block"


def test_apply_cn_exit_switch_off_falls_back_to_rtunnel(env: Path, tmp_path: Path) -> None:
    """开关关闭且 REVERSE_CN_EXIT=true 时回退 r-tunnel 模式。"""
    os.environ["ENABLE_SOCKS5_PROXY"] = "false"
    os.environ["CN_EXIT_SOCKS5_HOST"] = "100.99.99.1"
    os.environ["REVERSE_CN_EXIT"] = "true"
    xr = tmp_path / "xr.json"
    xr.write_text(json.dumps(_SOCKS5_XR_BASE), encoding="utf-8")
    cb._apply_cn_exit(xr)
    data = json.loads(xr.read_text(encoding="utf-8"))
    assert not [o for o in data["outbounds"] if o.get("tag") == "cn-exit"]
    cn_ip_rule = next(r for r in data["routing"]["rules"] if r["ruleTag"] == "cn-ip")
    assert cn_ip_rule["outboundTag"] == "r-tunnel"


def test_apply_cn_exit_socks5_default_enabled_when_unset(env: Path, tmp_path: Path) -> None:
    """ENABLE_SOCKS5_PROXY 未设置时默认 true（向后兼容）。"""
    os.environ["CN_EXIT_SOCKS5_HOST"] = "100.99.99.1"
    xr = tmp_path / "xr.json"
    xr.write_text(json.dumps(_SOCKS5_XR_BASE), encoding="utf-8")
    cb._apply_cn_exit(xr)
    data = json.loads(xr.read_text(encoding="utf-8"))
    assert [o for o in data["outbounds"] if o.get("tag") == "cn-exit"]


# ---------------------------------------------------------------------------
# CN exit - 显式开关 CN_EXIT_MODE (socks5 | reverse | balance | off)
# ---------------------------------------------------------------------------


def test_cn_exit_mode_explicit_socks5(env: Path, tmp_path: Path) -> None:
    os.environ["CN_EXIT_MODE"] = "socks5"
    os.environ["CN_EXIT_SOCKS5_HOST"] = "100.99.99.1"
    xr = tmp_path / "xr.json"
    xr.write_text(json.dumps(_SOCKS5_XR_BASE), encoding="utf-8")
    cb._apply_cn_exit(xr)
    data = json.loads(xr.read_text(encoding="utf-8"))
    assert [o for o in data["outbounds"] if o.get("tag") == "cn-exit"]
    cn_ip = next(r for r in data["routing"]["rules"] if r["ruleTag"] == "cn-ip")
    assert cn_ip["outboundTag"] == "cn-exit"


def test_cn_exit_mode_explicit_reverse_overrides_socks5(env: Path, tmp_path: Path) -> None:
    """显式 reverse 覆盖既有 socks5 隐式优先级（即使 HOST 有值且 SOCKS5 开启）。"""
    os.environ["CN_EXIT_MODE"] = "reverse"
    os.environ["ENABLE_SOCKS5_PROXY"] = "true"
    os.environ["CN_EXIT_SOCKS5_HOST"] = "100.99.99.1"
    xr = tmp_path / "xr.json"
    xr.write_text(json.dumps(_SOCKS5_XR_BASE), encoding="utf-8")
    cb._apply_cn_exit(xr)
    data = json.loads(xr.read_text(encoding="utf-8"))
    assert not [o for o in data["outbounds"] if o.get("tag") == "cn-exit"]
    cn_ip = next(r for r in data["routing"]["rules"] if r["ruleTag"] == "cn-ip")
    assert cn_ip["outboundTag"] == "r-tunnel"


def test_cn_exit_mode_explicit_off_overrides_host(env: Path, tmp_path: Path) -> None:
    """显式 off：即使 HOST 有值，CN 流量也保持 block（不回国）。"""
    os.environ["CN_EXIT_MODE"] = "off"
    os.environ["CN_EXIT_SOCKS5_HOST"] = "100.99.99.1"
    xr = tmp_path / "xr.json"
    xr.write_text(json.dumps(_SOCKS5_XR_BASE), encoding="utf-8")
    cb._apply_cn_exit(xr)
    data = json.loads(xr.read_text(encoding="utf-8"))
    assert not [o for o in data["outbounds"] if o.get("tag") == "cn-exit"]
    cn_ip = next(r for r in data["routing"]["rules"] if r["ruleTag"] == "cn-ip")
    assert cn_ip["outboundTag"] == "block"


def test_cn_exit_mode_unrecognised_falls_back_to_derivation(env: Path, tmp_path: Path) -> None:
    """无法识别的值按既有变量派生（此处 HOST 有值 → socks5）。"""
    os.environ["CN_EXIT_MODE"] = "bogus"
    os.environ["CN_EXIT_SOCKS5_HOST"] = "100.99.99.1"
    xr = tmp_path / "xr.json"
    xr.write_text(json.dumps(_SOCKS5_XR_BASE), encoding="utf-8")
    cb._apply_cn_exit(xr)
    data = json.loads(xr.read_text(encoding="utf-8"))
    cn_ip = next(r for r in data["routing"]["rules"] if r["ruleTag"] == "cn-ip")
    assert cn_ip["outboundTag"] == "cn-exit"


# ---------------------------------------------------------------------------
# CN exit - balance 模式（socks5 + r-tunnel 主备故障转移）
# ---------------------------------------------------------------------------


def _balance_xr_base() -> dict:
    return {
        "outbounds": [{"tag": "direct"}, {"tag": "block"}],
        "routing": {
            "rules": [
                {"ruleTag": "bt", "outboundTag": "block"},
                {
                    "ruleTag": "cn-ip",
                    "ip": ["geoip:cn"],
                    "marktag": "ban_geoip_cn",
                    "outboundTag": "block",
                    "webhook": {"url": "http://127.0.0.1:18085/ban_geoip_cn"},
                },
                {"ruleTag": "ad-domain", "outboundTag": "block"},
                {"ruleTag": "private-ip", "ip": ["geoip:private"], "outboundTag": "block"},
            ]
        },
    }


def test_cn_exit_mode_balance_rewires_with_balancer_tag(env: Path, tmp_path: Path) -> None:
    os.environ["CN_EXIT_MODE"] = "balance"
    os.environ["CN_EXIT_SOCKS5_HOST"] = "100.99.99.1"
    xr = tmp_path / "xr.json"
    xr.write_text(json.dumps(_balance_xr_base()), encoding="utf-8")
    cb._apply_cn_exit(xr)
    data = json.loads(xr.read_text(encoding="utf-8"))
    rules = data["routing"]["rules"]
    tags = [r["ruleTag"] for r in rules]
    assert tags == [
        "bt",
        "ad-domain",
        "cn-exit-probe-bypass",
        "cn-exit-overseas",
        "cn-geosite",
        "cn-ip",
        "private-ip",
    ]
    # probe 豁免与海外护栏仍走 outboundTag direct（不进 balancer）
    assert rules[2]["outboundTag"] == "direct"
    assert rules[3]["domain"] == ["geosite:geolocation-!cn"]
    assert rules[3]["outboundTag"] == "direct"
    # cn 流量改用 balancerTag，而非 outboundTag
    assert rules[4]["balancerTag"] == "cn-exit-balance"
    assert "outboundTag" not in rules[4]
    assert rules[5]["balancerTag"] == "cn-exit-balance"
    assert "outboundTag" not in rules[5]
    # ban marktag/webhook 必须剥离
    assert "marktag" not in rules[5]
    assert "webhook" not in rules[5]


def test_cn_exit_mode_balance_injects_socks_outbound(env: Path, tmp_path: Path) -> None:
    os.environ["CN_EXIT_MODE"] = "balance"
    os.environ["CN_EXIT_SOCKS5_HOST"] = "100.99.99.1"
    os.environ["CN_EXIT_SOCKS5_PORT"] = "7891"
    xr = tmp_path / "xr.json"
    xr.write_text(json.dumps(_balance_xr_base()), encoding="utf-8")
    cb._apply_cn_exit(xr)
    data = json.loads(xr.read_text(encoding="utf-8"))
    cn_exit = [o for o in data["outbounds"] if o.get("tag") == "cn-exit"]
    assert len(cn_exit) == 1
    assert cn_exit[0]["protocol"] == "socks"
    assert cn_exit[0]["settings"]["servers"][0]["address"] == "100.99.99.1"
    assert cn_exit[0]["settings"]["servers"][0]["port"] == 7891


def test_cn_exit_mode_balance_appends_balancer_and_observatory(env: Path, tmp_path: Path) -> None:
    os.environ["CN_EXIT_MODE"] = "balance"
    os.environ["CN_EXIT_SOCKS5_HOST"] = "100.99.99.1"
    xr = tmp_path / "xr.json"
    xr.write_text(json.dumps(_balance_xr_base()), encoding="utf-8")
    cb._apply_cn_exit(xr)
    data = json.loads(xr.read_text(encoding="utf-8"))
    balancers = data["routing"]["balancers"]
    bal = next(b for b in balancers if b["tag"] == "cn-exit-balance")
    assert bal["selector"] == ["cn-exit", "r-tunnel"]
    assert bal["fallbackTag"] == "direct"
    assert bal["strategy"]["type"] == "leastPing"
    obs = data["observatory"]
    assert "cn-exit" in obs["subjectSelector"]
    assert "r-tunnel" in obs["subjectSelector"]
    assert obs["probeUrl"] == cb._DEFAULT_CN_EXIT_PROBE_URL
    assert obs["probeInterval"] == "30s"
    assert obs["enableConcurrency"] is True


def test_cn_exit_mode_balance_honours_probe_env(env: Path, tmp_path: Path) -> None:
    os.environ["CN_EXIT_MODE"] = "balance"
    os.environ["CN_EXIT_SOCKS5_HOST"] = "100.99.99.1"
    os.environ["CN_EXIT_PROBE_URL"] = "http://probe.example/generate_204"
    os.environ["CN_EXIT_PROBE_INTERVAL"] = "15s"
    xr = tmp_path / "xr.json"
    xr.write_text(json.dumps(_balance_xr_base()), encoding="utf-8")
    cb._apply_cn_exit(xr)
    data = json.loads(xr.read_text(encoding="utf-8"))
    obs = data["observatory"]
    assert obs["probeUrl"] == "http://probe.example/generate_204"
    assert obs["probeInterval"] == "15s"


def test_cn_exit_mode_balance_merges_existing_observatory(env: Path, tmp_path: Path) -> None:
    """已有 ISP observatory 时，合并 cn-exit/r-tunnel 而不丢弃既有 subject。"""
    os.environ["CN_EXIT_MODE"] = "balance"
    os.environ["CN_EXIT_SOCKS5_HOST"] = "100.99.99.1"
    base = _balance_xr_base()
    base["observatory"] = {
        "subjectSelector": ["proxy-hk"],
        "probeUrl": "https://existing.example/probe",
        "probeInterval": "1m",
        "enableConcurrency": True,
    }
    base["routing"]["balancers"] = [
        {"tag": "isp-auto", "selector": ["proxy-hk"], "strategy": {"type": "leastPing"}}
    ]
    xr = tmp_path / "xr.json"
    xr.write_text(json.dumps(base), encoding="utf-8")
    cb._apply_cn_exit(xr)
    data = json.loads(xr.read_text(encoding="utf-8"))
    obs = data["observatory"]
    assert obs["subjectSelector"] == ["proxy-hk", "cn-exit", "r-tunnel"]
    # 既有 ISP observatory 探测配置不被覆盖
    assert obs["probeUrl"] == "https://existing.example/probe"
    # 既有 balancer 保留，cn-exit-balance 追加
    bal_tags = [b["tag"] for b in data["routing"]["balancers"]]
    assert bal_tags == ["isp-auto", "cn-exit-balance"]


def test_cn_exit_mode_balance_noop_without_host(env: Path, tmp_path: Path) -> None:
    """balance 但缺 CN_EXIT_SOCKS5_HOST：不改写、不注入（避免半成品配置）。"""
    os.environ["CN_EXIT_MODE"] = "balance"
    xr = tmp_path / "xr.json"
    xr.write_text(json.dumps(_balance_xr_base()), encoding="utf-8")
    cb._apply_cn_exit(xr)
    data = json.loads(xr.read_text(encoding="utf-8"))
    assert "balancers" not in data["routing"]
    cn_ip = next(r for r in data["routing"]["rules"] if r["ruleTag"] == "cn-ip")
    assert cn_ip["outboundTag"] == "block"


def test_resolve_dufs_permissions_defaults_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A4: 未设 DUFS_ALLOW_* 时必须 fail-closed(全 false),
    避免渲染出字面量 ${DUFS_ALLOW_UPLOAD} 这种非法 yaml,也避免默认放开。"""
    for k in ("DUFS_ALLOW_ALL", "DUFS_ALLOW_UPLOAD", "DUFS_ALLOW_DELETE",
              "DUFS_ALLOW_SEARCH", "DUFS_ALLOW_SYMLINK", "DUFS_ALLOW_ARCHIVE",
              "DUFS_ENABLE_CORS", "DUFS_RENDER_INDEX", "DUFS_RENDER_TRY_INDEX",
              "DUFS_RENDER_SPA", "DUFS_COMPRESS", "DUFS_LOG_FORMAT"):
        monkeypatch.delenv(k, raising=False)
    cb._resolve_dufs_permissions()
    assert os.environ["DUFS_ALLOW_ALL"] == "false"
    assert os.environ["DUFS_ALLOW_UPLOAD"] == "false"
    assert os.environ["DUFS_ALLOW_DELETE"] == "false"
    assert os.environ["DUFS_ALLOW_SYMLINK"] == "false"
    assert os.environ["DUFS_ALLOW_ARCHIVE"] == "false"
    # 只读浏览必需项保持安全的可用默认
    assert os.environ["DUFS_ALLOW_SEARCH"] == "true"
    assert os.environ["DUFS_COMPRESS"] == "low"


def test_resolve_dufs_permissions_respects_explicit_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """显式设置必须原样保留(运维可按需放开);未设键必须仍 fail-closed(防进程级 env 渗漏)。"""
    # 明确清除未显式设置的权限键,防止进程级 env 渗漏干扰断言
    for k in ("DUFS_ALLOW_ALL", "DUFS_ALLOW_SYMLINK", "DUFS_ALLOW_ARCHIVE", "DUFS_ENABLE_CORS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DUFS_ALLOW_UPLOAD", "true")
    monkeypatch.setenv("DUFS_ALLOW_DELETE", "true")
    cb._resolve_dufs_permissions()
    # 显式设置的键保留原值
    assert os.environ["DUFS_ALLOW_UPLOAD"] == "true"
    assert os.environ["DUFS_ALLOW_DELETE"] == "true"
    # 未显式设置的写权限必须 fail-closed,不受其他测试 env 渗漏影响
    assert os.environ["DUFS_ALLOW_ALL"] == "false"
    assert os.environ["DUFS_ALLOW_SYMLINK"] == "false"
    assert os.environ["DUFS_ALLOW_ARCHIVE"] == "false"
    assert os.environ["DUFS_ENABLE_CORS"] == "false"


def test_dufs_conf_renders_fail_closed_defaults(
    env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """conf.yml 模板经 _resolve_dufs_permissions + _envsubst 渲染后,
    写权限全 false,且无残留字面量 ${...} 占位符。"""
    for k in ("DUFS_ALLOW_ALL", "DUFS_ALLOW_UPLOAD", "DUFS_ALLOW_DELETE",
              "DUFS_ALLOW_SEARCH", "DUFS_ALLOW_SYMLINK", "DUFS_ALLOW_ARCHIVE",
              "DUFS_ENABLE_CORS", "DUFS_RENDER_INDEX", "DUFS_RENDER_TRY_INDEX",
              "DUFS_RENDER_SPA", "DUFS_COMPRESS", "DUFS_LOG_FORMAT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LOGDIR", str(tmp_path / "log"))
    cb._resolve_dufs_permissions()
    src = Path("templates/dufs/conf.yml")
    rendered = cb._envsubst(src.read_text(encoding="utf-8"))
    assert "allow-upload: false" in rendered
    assert "allow-delete: false" in rendered
    assert "allow-symlink: false" in rendered
    assert "allow-all: false" in rendered
    # 无残留未解析占位符
    assert "${DUFS_ALLOW" not in rendered
    assert "${DUFS_RENDER" not in rendered


def test_dufs_conf_logformat_tokens_survive_literally(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A4 I2: DUFS_LOG_FORMAT 中的 dufs 原生 token($remote_addr 等)经 _envsubst 渲染后
    必须原样保留——不得被二次展开或消除。"""
    for k in ("DUFS_ALLOW_ALL", "DUFS_ALLOW_UPLOAD", "DUFS_ALLOW_DELETE",
              "DUFS_ALLOW_SEARCH", "DUFS_ALLOW_SYMLINK", "DUFS_ALLOW_ARCHIVE",
              "DUFS_ENABLE_CORS", "DUFS_RENDER_INDEX", "DUFS_RENDER_TRY_INDEX",
              "DUFS_RENDER_SPA", "DUFS_COMPRESS", "DUFS_LOG_FORMAT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LOGDIR", str(tmp_path / "log"))
    cb._resolve_dufs_permissions()
    # 使用绝对路径锚定模板,避免 cwd 依赖导致脆性
    template_path = Path(__file__).parent.parent / "templates" / "dufs" / "conf.yml"
    rendered = cb._envsubst(template_path.read_text(encoding="utf-8"))
    # dufs 原生 token 必须字面量存活,不被 _envsubst 展开或消除
    assert "$remote_addr" in rendered, "dufs token $remote_addr was stripped or re-expanded"
    assert "$request" in rendered, "dufs token $request was stripped or re-expanded"
    assert "$status" in rendered, "dufs token $status was stripped or re-expanded"
    assert "$http_user_agent" in rendered, "dufs token $http_user_agent was stripped or re-expanded"
    # 确认 log-format 行本身存在
    assert "log-format:" in rendered


def test_resolve_supervisor_credentials_derives_distinct_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F4: supervisord 控制密码必须与 PUBLIC_PASSWORD 不同值,
    且未显式设置时从 PUBLIC_PASSWORD 确定性派生(§2 镜像内默认,
    watchtower 旧 env 集重建也能稳定生成)。"""
    monkeypatch.delenv("SUPERVISOR_USER", raising=False)
    monkeypatch.delenv("SUPERVISOR_PASSWORD", raising=False)
    monkeypatch.setenv("PUBLIC_PASSWORD", "shared-secret-123")
    cb._resolve_supervisor_credentials()
    assert os.environ["SUPERVISOR_USER"] == "sb-xray"
    assert os.environ["SUPERVISOR_PASSWORD"] != "shared-secret-123"
    assert os.environ["SUPERVISOR_PASSWORD"]  # 非空
    # 确定性:再解析一次同输入 → 同输出
    derived = os.environ["SUPERVISOR_PASSWORD"]
    monkeypatch.delenv("SUPERVISOR_PASSWORD", raising=False)
    cb._resolve_supervisor_credentials()
    assert os.environ["SUPERVISOR_PASSWORD"] == derived


def test_resolve_supervisor_credentials_respects_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """显式 SUPERVISOR_PASSWORD 原样保留。"""
    monkeypatch.setenv("PUBLIC_PASSWORD", "pw")
    monkeypatch.setenv("SUPERVISOR_PASSWORD", "explicit-sup-pw")
    monkeypatch.setenv("SUPERVISOR_USER", "ctl")
    cb._resolve_supervisor_credentials()
    assert os.environ["SUPERVISOR_PASSWORD"] == "explicit-sup-pw"
    assert os.environ["SUPERVISOR_USER"] == "ctl"


def test_supervisord_conf_renders_separated_creds(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F4: supervisord.conf 渲染后,unix_http_server 与 supervisorctl 两段
    都用 SUPERVISOR_PASSWORD,且 != PUBLIC_PASSWORD。"""
    monkeypatch.delenv("SUPERVISOR_USER", raising=False)
    monkeypatch.delenv("SUPERVISOR_PASSWORD", raising=False)
    monkeypatch.setenv("PUBLIC_PASSWORD", "public-pw-xyz")
    monkeypatch.setenv("PUBLIC_USER", "admin")
    cb._resolve_supervisor_credentials()
    src = Path(__file__).parent.parent / "templates/supervisord/supervisord.conf"
    rendered = cb._envsubst(src.read_text(encoding="utf-8"))
    sup_pw = os.environ["SUPERVISOR_PASSWORD"]
    assert rendered.count(f"password={sup_pw}") == 2  # 两段都替换
    assert "password=public-pw-xyz" not in rendered    # public 不再出现在控制段
    assert "${PUBLIC_PASSWORD}" not in rendered          # 无残留占位符


def test_resolve_supervisor_credentials_empty_public_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F4 §2 watchtower 空 seed 路径：PUBLIC_PASSWORD 未设时派生密码必须非空且与
    空字符串不同，保证旧 env 集重建镜像不产出空白 supervisord 控制密码。"""
    monkeypatch.delenv("PUBLIC_PASSWORD", raising=False)
    monkeypatch.delenv("SUPERVISOR_PASSWORD", raising=False)
    monkeypatch.delenv("SUPERVISOR_USER", raising=False)
    cb._resolve_supervisor_credentials()
    sup_pw = os.environ["SUPERVISOR_PASSWORD"]
    assert sup_pw  # 非空
    assert sup_pw != ""  # 明确不等于空字符串
    # 空 PUBLIC_PASSWORD seed 派生值不等于空字符串本身（独立盐确保区分）
    assert sup_pw != os.environ.get("PUBLIC_PASSWORD", "")


def test_resolve_subscribe_token_map_malicious_token_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G4 injection guard: token 含 nginx 语法破坏字符时 fail-closed,注入不写入 map。"""
    monkeypatch.setenv("SUBSCRIBE_TOKEN", 'abc" "on"; map $x $y { default')
    cb._resolve_subscribe_token_map()
    token_map = os.environ["NGINX_SUBSCRIBE_TOKEN_MAP"]
    assert token_map == ""
    assert '"off"' not in token_map


def test_resolve_subscribe_token_map_whitespace_only_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G4: 纯空白 token strip 后为空 → fail-closed,不产出 off 映射。"""
    monkeypatch.setenv("SUBSCRIBE_TOKEN", "   ")
    cb._resolve_subscribe_token_map()
    token_map = os.environ["NGINX_SUBSCRIBE_TOKEN_MAP"]
    assert token_map == ""
    assert '"off"' not in token_map


def test_resolve_subscribe_token_map_fail_closed_when_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G4: SUBSCRIBE_TOKEN 为空时,token-map 绝不能生成 '\"\" off' 映射,
    否则无 ?token= 的请求会命中空串 key 而绕过 Basic Auth。"""
    monkeypatch.delenv("SUBSCRIBE_TOKEN", raising=False)
    cb._resolve_subscribe_token_map()
    token_map = os.environ["NGINX_SUBSCRIBE_TOKEN_MAP"]
    assert '"off"' not in token_map
    assert '"" "off"' not in token_map


def test_resolve_subscribe_token_map_active_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非空 token → 生成该 token 的 off 映射,允许 token 持有者免 Basic Auth。"""
    monkeypatch.setenv("SUBSCRIBE_TOKEN", "s3cr3t-tok")
    cb._resolve_subscribe_token_map()
    token_map = os.environ["NGINX_SUBSCRIBE_TOKEN_MAP"]
    assert '"s3cr3t-tok" "off";' in token_map


def test_nginx_conf_token_map_fail_closed_render(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """空 SUBSCRIBE_TOKEN 渲染 nginx.conf 后,$auth_type map 体内
    不得出现任何 'off' 映射(只剩 default Restricted)。"""
    monkeypatch.delenv("SUBSCRIBE_TOKEN", raising=False)
    cb._resolve_subscribe_token_map()
    src = Path("templates/nginx/nginx.conf")
    rendered = cb._envsubst(src.read_text(encoding="utf-8"))
    start = rendered.index("map $arg_token $auth_type {")
    block = rendered[start:rendered.index("}", start)]
    assert '"off"' not in block
    assert 'default "Restricted";' in block
    assert "${NGINX_SUBSCRIBE_TOKEN_MAP}" not in rendered  # 占位符已展开(空串)


def test_http_conf_includes_internal_acl_in_admin_locations() -> None:
    """A1: /supervisor/、DUFS、XUI 三个管理面 location 必须 include
    network_internal.conf,否则内网 ACL 形同虚设(对公网开放)。"""
    http_conf = Path("templates/nginx/http.conf").read_text(encoding="utf-8")
    inc = "include /etc/nginx/network_internal.conf;"
    # 必须恰好出现在三个管理面 location 内(共 3 次)
    assert http_conf.count(inc) == 3, f"期望 3 处 internal-acl include,实得 {http_conf.count(inc)}"
    # 每个管理面 location 块内都要有 include —— 用 location 锚点切片验证
    for anchor in ("location /supervisor/ {", "location ${DUFS_PATH_PREFIX}/ {",
                   "location /${XUI_WEBBASEPATH}/ {"):
        start = http_conf.index(anchor)
        block = http_conf[start:start + 600]
        assert inc in block, f"{anchor} 缺少 internal-acl include"


def test_providers_template_has_no_hardcoded_account() -> None:
    """E1: providers.yaml must parameterize the gist owner, not hardcode it."""
    src = Path("templates/providers/providers.yaml")
    raw = src.read_text(encoding="utf-8")
    assert "currycan" not in raw
    assert "${GIST_OWNER}" in raw
    # owner segment sits directly before the gist code segment
    assert "gist.githubusercontent.com/${GIST_OWNER}/${GIST_CODE}/raw/" in raw


def test_providers_gist_owner_substituted_when_set(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GIST_OWNER in env is expanded; unset GIST_CODE stays literal (client fills)."""
    import sb_xray.config_builder as cb

    monkeypatch.setenv("GIST_OWNER", "acme")
    monkeypatch.delenv("GIST_CODE", raising=False)
    rendered = cb._envsubst(
        'url: "https://gh-proxy.com/gist.githubusercontent.com/${GIST_OWNER}/${GIST_CODE}/raw/AllOne-Common"'
    )
    assert "gist.githubusercontent.com/acme/${GIST_CODE}/raw/AllOne-Common" in rendered


# ---------------------------------------------------------------------------
# J2: _render_json 失败时报出触发的 env 键名
# ---------------------------------------------------------------------------


def test_render_json_error_names_breaking_env(
    env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """J2: 含换行的 env 注入 JSON 字符串字面量导致 json.loads 失败时,错误必须指名是哪个 env。

    _JSON_BREAKING 检测的危险字符包括换行; 注入后 json.loads 抛
    ``Invalid control character``,此时 RuntimeError 消息必须含 DEST_HOST。
    (原始 brief 值 www.apple.com\",\"injected\":\"x 恰好产出合法 JSON —— 换行
    才是此类测试的可靠触发点。)
    """
    monkeypatch.setenv("DEST_HOST", "host.example.com\n")
    src = tmp_path / "t.json"
    src.write_text('{"sni": "${DEST_HOST}"}', encoding="utf-8")
    dest = tmp_path / "out.json"
    with pytest.raises(RuntimeError, match="DEST_HOST"):
        cb._render_json(src, dest)


def test_render_json_error_names_env_with_backslash(
    env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """J2: 末尾裸反斜杠转义了闭合引号,导致字符串未终止 (Unterminated string)。"""
    monkeypatch.setenv("DOMAIN", "a\\")
    src = tmp_path / "t.json"
    src.write_text('{"d": "${DOMAIN}"}', encoding="utf-8")
    dest = tmp_path / "out.json"
    with pytest.raises(RuntimeError, match="DOMAIN"):
        cb._render_json(src, dest)


def test_suspect_envs_ignores_clean_values(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """干净 env(无 JSON-危险字符)不被列入嫌疑。"""
    monkeypatch.setenv("DEST_HOST", "www.apple.com")
    assert cb._suspect_json_breaking_envs('{"sni": "${DEST_HOST}"}') == []
