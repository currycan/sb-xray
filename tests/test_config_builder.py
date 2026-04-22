"""Tests for sb_xray.config_builder (entrypoint.sh:createConfig port)."""

from __future__ import annotations

import json
import os
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
        "RANDOM_NUM",
        "ENABLE_SUBSTORE",
        "ENABLE_XUI",
        "ENABLE_SUI",
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
[program:s-ui]
command=sui
priority=5

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
    assert "[program:s-ui]" in text  # unaffected
    assert "[program:xray]" in text
    assert "[program:nginx]" in text


def test_filter_supervisord_drops_multiple_flags(env: Path, tmp_path: Path) -> None:
    os.environ["ENABLE_SUI"] = "false"
    os.environ["ENABLE_SHOUTRRR"] = "false"
    dest = tmp_path / "daemon.ini"
    dest.write_text(_DAEMON_INI_FIXTURE, encoding="utf-8")
    cb._filter_supervisord_programs(dest)
    text = dest.read_text(encoding="utf-8")
    assert "[program:s-ui]" not in text
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
    assert "[program:s-ui]" in text  # 不同开关互不影响
    assert "[program:xray]" in text


def test_filter_supervisord_preserves_supervisor_interpolation(env: Path, tmp_path: Path) -> None:
    """``%(ENV_*)s`` must survive filtering verbatim (regex, not configparser)."""
    os.environ["ENABLE_SUI"] = "false"
    dest = tmp_path / "daemon.ini"
    dest.write_text(_DAEMON_INI_FIXTURE, encoding="utf-8")
    cb._filter_supervisord_programs(dest)
    assert "%(ENV_SUB_STORE_DOCKER)s" in dest.read_text(encoding="utf-8")


def test_trim_runtime_configs_filters_existing_daemon_ini(env: Path, tmp_path: Path) -> None:
    os.environ["ENABLE_SUBSTORE"] = "false"
    os.environ["ENABLE_SUI"] = "false"
    daemon = tmp_path / "daemon.ini"
    daemon.write_text(_DAEMON_INI_FIXTURE, encoding="utf-8")

    cb.trim_runtime_configs(daemon_ini=daemon)

    text = daemon.read_text(encoding="utf-8")
    assert "[program:sub-store]" not in text
    assert "[program:http-meta]" not in text
    assert "[program:s-ui]" not in text
    assert "[program:xray]" in text


def test_trim_runtime_configs_silent_when_daemon_missing(env: Path, tmp_path: Path) -> None:
    """No daemon.ini present → must not raise."""
    cb.trim_runtime_configs(daemon_ini=tmp_path / "missing.ini")
