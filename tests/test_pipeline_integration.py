"""集成测试:对真实 templates/ 树跑 config_builder.create_config,
断言渲染出的 xr.json / sb.json 是合法 JSON 且无未解析 ${...}。

这覆盖 D3:既有套件对启动流水线全 stub,无端到端 stage 联动覆盖。
此测试真实跑 config + routing 渲染(cert/network/supervisord 不参与)。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from sb_xray import config_builder as cb

# repo 根:tests/ 的上一级。templates/ 与 scripts/ 同级。
_REPO_ROOT = Path(__file__).resolve().parent.parent
_REAL_TEMPLATES = _REPO_ROOT / "templates"

# create_config 渲染前由 boot stages(probe/speed/media/keys/outbounds)写入的
# env。集成测试用占位符替身代表"上游 stage 已产出",聚焦"渲染层不留 dangling"。
_BOOT_ENV: dict[str, str] = {
    "WORKDIR": "",  # 测试用 tmp workdir 覆盖,此处仅防 ${WORKDIR} 残留
    "DOMAIN": "vpn.example.com",
    "DEST_HOST": "www.example.com",
    "LOGDIR": "/var/log",
    "LOG_LEVEL": "warning",
    "SSL_PATH": "/ssl",
    "SB_UUID": "00000000-0000-0000-0000-000000000000",
    "XRAY_UUID": "00000000-0000-0000-0000-000000000001",
    "XRAY_URL_PATH": "/ray",
    "XRAY_REALITY_PRIVATE_KEY": "stub-priv-key",
    "XRAY_REALITY_SHORTID": "01",
    "XRAY_REALITY_SHORTID_2": "0123",
    "XRAY_REALITY_SHORTID_3": "012345",
    "XRAY_MLKEM768_SEED": "stub-seed",
    "PORT_TUIC": "443",
    "PORT_ANYTLS": "8443",
    "PORT_HYSTERIA2": "2053",
    "PORT_XHTTP_H3": "2083",
    "PORT_XICMP_ID": "1",
    "PORT_XDNS": "5353",
    "XDNS_DOMAIN": "dns.example.com",
    "STRATEGY": "leastPing",
    "SB_ISP_URLTEST": "",
    # ${*_OUT} 服务出站:渲染期须已解析为合法 outbound tag(boot media stage 产出)。
    "ISP_OUT": "direct",
    "NETFLIX_OUT": "direct",
    "DISNEY_OUT": "direct",
    "YOUTUBE_OUT": "direct",
    "CHATGPT_OUT": "direct",
    "CLAUDE_OUT": "direct",
    "GEMINI_OUT": "direct",
    "TIKTOK_OUT": "direct",
    "SOCIAL_MEDIA_OUT": "direct",
    # section 类占位符:展开为空仍须 JSON 合法(逗号位置由模板兜)。
    "XRAY_OBSERVATORY_SECTION": "",
    "XRAY_BALANCERS_SECTION": "",
    "XRAY_SERVICE_RULES": "",
    "CUSTOM_OUTBOUNDS": "",
    "SB_CUSTOM_OUTBOUNDS": "",
}

_DANGLING_RE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}")


@pytest.fixture
def _rendered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for key, value in _BOOT_ENV.items():
        monkeypatch.setenv(key, value)
    workdir = tmp_path / "workdir"
    (workdir / "dufs").mkdir(parents=True)
    monkeypatch.setenv("WORKDIR", str(workdir))

    # 真实 xray/sing-box 模板;flat renders 改写到 tmp 防止触碰 /etc。
    monkeypatch.setattr(cb, "_TEMPLATES", _REAL_TEMPLATES)
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
            ("logrotate/sb-xray.conf", str(etc_root / "logrotate-sb-xray")),
        ),
    )
    monkeypatch.setattr(
        cb,
        "_FLAT_COPIES",
        (("nginx/network_internal.conf", str(etc_root / "nginx" / "network_internal.conf")),),
    )
    cb.create_config(workdir=workdir)
    return workdir


def test_xr_json_valid_and_no_dangling_placeholders(_rendered: Path) -> None:
    xr = _rendered / "xray" / "xr.json"
    assert xr.is_file()
    raw = xr.read_text(encoding="utf-8")
    json.loads(raw)  # 合法 JSON(否则 raise)
    assert not _DANGLING_RE.search(raw), f"xr.json 残留未解析占位符: {_DANGLING_RE.findall(raw)}"


def test_sb_json_valid_and_no_dangling_placeholders(_rendered: Path) -> None:
    sb = _rendered / "sing-box" / "sb.json"
    assert sb.is_file()
    raw = sb.read_text(encoding="utf-8")
    json.loads(raw)
    assert not _DANGLING_RE.search(raw), f"sb.json 残留未解析占位符: {_DANGLING_RE.findall(raw)}"
