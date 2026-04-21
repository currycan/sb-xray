"""Port of ``entrypoint.sh:createConfig`` (L919-1000).

Orchestrates every per-service config render done at container boot:
  1. Non-JSON templates (supervisord, nginx, dufs, providers) — simple
     envsubst-style ``${VAR}`` substitution via ``string.Template``.
  2. ``${WORKDIR}/{xray,sing-box}/*.json`` orphan cleanup.
  3. Xray/Sing-box JSON templates, with M4 feature-flag skipping of
     ``05_xicmp_emergency_inbounds.json`` / ``06_xdns_emergency_inbounds.json``
     when ``ENABLE_XICMP`` / ``ENABLE_XDNS`` aren't ``"true"``.
  4. VLESS Reverse-Proxy injection (M3): when ``ENABLE_REVERSE=true``,
     append a reverse client to Reality inbound + prepend a routing
     rule to ``xr.json`` matching ``REVERSE_DOMAINS``.

We keep ``string.Template.safe_substitute`` semantics (unknown vars
stay as empty strings — matches Bash ``envsubst``). Jinja2's
``StrictUndefined`` behaviour would be more defensive but would
regress shell compatibility.
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
from pathlib import Path

from sb_xray import logging as sblog

# Matches ``${VAR}`` or ``$VAR`` (POSIX identifier: ``[A-Za-z_][A-Za-z0-9_]*``).
_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")

_TEMPLATES = Path("/templates")

_FLAT_RENDERS: tuple[tuple[str, str], ...] = (
    ("supervisord/supervisord.conf", "/etc/supervisord.conf"),
    ("supervisord/daemon.ini", "/etc/supervisor.d/daemon.ini"),
    ("nginx/nginx.conf", "/etc/nginx/nginx.conf"),
    ("nginx/http.conf", "/etc/nginx/conf.d/http.conf"),
    ("nginx/tcp.conf", "/etc/nginx/stream.d/tcp.conf"),
    ("dufs/conf.yml", "${WORKDIR}/dufs/conf.yml"),
    ("providers/providers.yaml", "${WORKDIR}/providers"),
)

_FLAT_COPIES: tuple[tuple[str, str], ...] = (
    ("nginx/network_internal.conf", "/etc/nginx/network_internal.conf"),
)

_FEATURE_FLAG_TEMPLATES: dict[str, str] = {
    "05_xicmp_emergency_inbounds.json": "ENABLE_XICMP",
    "06_xdns_emergency_inbounds.json": "ENABLE_XDNS",
}


def _envsubst(raw: str) -> str:
    """Bash ``envsubst`` equivalent: expand ``$VAR`` / ``${VAR}``; leave
    unknown references as the empty string.

    ``string.Template.safe_substitute`` keeps unknown vars as ``$VAR``
    literal — that's NOT envsubst behaviour (which treats unset as empty).
    Hand-rolled regex substitution below matches ``envsubst`` exactly.
    """

    def repl(m: re.Match[str]) -> str:
        name = m.group(1) or m.group(2)
        return os.environ.get(name, "")

    return _VAR_RE.sub(repl, raw)


def _render_flat(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_envsubst(src.read_text(encoding="utf-8")), encoding="utf-8")


def _render_json(src: Path, dest: Path) -> None:
    """Render ``src`` then validate as JSON (entrypoint.sh ``_apply_tpl`` jq)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    rendered = _envsubst(src.read_text(encoding="utf-8"))
    try:
        data = json.loads(rendered)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON after render: {src} -> {exc}") from exc
    dest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _expand_dest(dest_tpl: str) -> Path:
    """``${WORKDIR}/foo`` → absolute Path (``WORKDIR`` read from env)."""
    return Path(_envsubst(dest_tpl))


def _cleanup_orphan_json(service_dir: Path, template_dir: Path) -> None:
    """Remove ``*.json`` files in ``service_dir`` that don't exist in
    ``template_dir`` (entrypoint.sh:933-945)."""
    service_dir.mkdir(parents=True, exist_ok=True)
    if not template_dir.is_dir():
        return
    for f in service_dir.glob("*.json"):
        if not (template_dir / f.name).is_file():
            sblog.log("INFO", f"[配置] 清理孤儿 JSON: {f}")
            f.unlink()


def _is_feature_disabled(template_name: str) -> bool:
    flag = _FEATURE_FLAG_TEMPLATES.get(template_name)
    if not flag:
        return False
    return os.environ.get(flag, "") != "true"


def _render_xray_templates(workdir: Path) -> None:
    template_dir = _TEMPLATES / "xray"
    dest_dir = workdir / "xray"
    dest_dir.mkdir(parents=True, exist_ok=True)
    for tpl in sorted(template_dir.glob("*.json")):
        dest = dest_dir / tpl.name
        if _is_feature_disabled(tpl.name):
            if dest.is_file():
                dest.unlink()
            sblog.log("INFO", f"[配置][M4] 跳过禁用模板: {tpl.name}")
            continue
        _render_json(tpl, dest)


def _render_sing_box_templates(workdir: Path) -> None:
    template_dir = _TEMPLATES / "sing-box"
    dest_dir = workdir / "sing-box"
    dest_dir.mkdir(parents=True, exist_ok=True)
    for tpl in sorted(template_dir.glob("*.json")):
        _render_json(tpl, dest_dir / tpl.name)


def _parse_reverse_domains(raw: str) -> list[str]:
    return [d for d in (chunk.strip() for chunk in raw.split(",")) if d]


def _inject_reverse_client(reality_file: Path, uuid: str) -> None:
    data = json.loads(reality_file.read_text(encoding="utf-8"))
    client = {
        "id": uuid,
        "level": 0,
        "email": "reverse@portal.bridge",
        "flow": "xtls-rprx-vision",
        "reverse": {"tag": "r-tunnel"},
    }
    data["inbounds"][0]["settings"]["clients"].append(client)
    reality_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _inject_reverse_route(xr_file: Path, domains: list[str]) -> None:
    data = json.loads(xr_file.read_text(encoding="utf-8"))
    rule = {
        "type": "field",
        "ruleTag": "reverse-bridge",
        "domain": domains,
        "outboundTag": "r-tunnel",
    }
    data["routing"]["rules"] = [rule, *data["routing"]["rules"]]
    xr_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _apply_reverse_proxy(workdir: Path) -> None:
    if os.environ.get("ENABLE_REVERSE", "false") != "true":
        return
    uuid = os.environ.get("XRAY_REVERSE_UUID", "")
    if not uuid:
        sblog.log("WARN", "[配置][Reverse] ENABLE_REVERSE=true 但 XRAY_REVERSE_UUID 未设置,跳过")
        return

    domains = _parse_reverse_domains(os.environ.get("REVERSE_DOMAINS", ""))
    sblog.log(
        "INFO",
        f"[配置][Reverse] 注入 reverse client (UUID=...{uuid[-8:]}) "
        f"+ routing (domains={','.join(domains) or '<none>'})",
    )

    reality = workdir / "xray" / "01_reality_inbounds.json"
    if reality.is_file():
        _inject_reverse_client(reality, uuid)
    xr = workdir / "xray" / "xr.json"
    if domains and xr.is_file():
        _inject_reverse_route(xr, domains)


def create_config(*, workdir: Path | None = None) -> None:
    """Render every service-level config (entrypoint.sh ``createConfig``)."""
    if workdir is None:
        workdir = Path(os.environ.get("WORKDIR", "/tmp/sb-xray"))

    sblog.log("INFO", "[配置] 渲染所有模板...")
    os.environ["RANDOM_NUM"] = str(random.randint(0, 9))

    for src, dest in _FLAT_RENDERS:
        _render_flat(_TEMPLATES / src, _expand_dest(dest))
    for src, dest in _FLAT_COPIES:
        dest_path = _expand_dest(dest)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(_TEMPLATES / src, dest_path)

    _cleanup_orphan_json(workdir / "xray", _TEMPLATES / "xray")
    _cleanup_orphan_json(workdir / "sing-box", _TEMPLATES / "sing-box")

    _render_xray_templates(workdir)
    _render_sing_box_templates(workdir)

    _apply_reverse_proxy(workdir)

    sblog.log("INFO", "[配置] 所有模板渲染完成")
