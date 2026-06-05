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
import logging
import os
import random
import re
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# Matches ``${VAR}`` or ``$VAR`` (POSIX identifier: ``[A-Za-z_][A-Za-z0-9_]*``).
_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")

# Per-program ENABLE_* switches for daemon.ini. Opt-out semantics: a program is
# kept unless its flag is explicitly set to "false" (case-insensitive). Used by
# low-memory deployments (≤ 512 MB RAM) to trim ~220–320 MB of resident RSS.
_SUPERVISOR_PROGRAM_FLAGS: dict[str, str] = {
    "x-ui": "ENABLE_XUI",
    # "s-ui": "ENABLE_SUI",  # s-ui project removed
    "sub-store": "ENABLE_SUBSTORE",
    "http-meta": "ENABLE_SUBSTORE",
    "shoutrrr-forwarder": "ENABLE_SHOUTRRR",
}

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
    """GNU ``envsubst`` equivalent: expand ``$VAR`` / ``${VAR}``.

    Matches the actual behaviour of ``envsubst`` (coreutils / gettext):
    references whose name is **in the environment** get substituted;
    references to names that are **not set** keep their literal ``$VAR``
    / ``${VAR}`` form. (Earlier revisions returned empty-string for
    unknown names — but that breaks every nginx ``$http_*`` / ``$arg_*``
    runtime variable in templates/nginx/*.conf, collapsing
    ``map $http_x_forwarded_for $client_ip {...}`` into
    ``map   {...}`` which nginx refuses with
    ``invalid number of arguments in "map" directive``.)
    """

    def repl(m: re.Match[str]) -> str:
        name = m.group(1) or m.group(2)
        if name in os.environ:
            return os.environ[name]
        return m.group(0)

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


def _flag_is_disabled(flag: str) -> bool:
    """True only when ``os.environ[flag]`` is explicitly ``"false"`` (case-insensitive)."""
    return os.environ.get(flag, "").strip().lower() == "false"


def _filter_supervisord_programs(dest: Path) -> None:
    """Drop ``[program:*]`` sections whose ``ENABLE_*`` flag is ``"false"``.

    Regex-split rather than configparser so we don't disturb supervisor's
    own ``%(ENV_*)s`` interpolation syntax inside the file.
    """
    disabled = {
        program for program, flag in _SUPERVISOR_PROGRAM_FLAGS.items() if _flag_is_disabled(flag)
    }
    if not disabled:
        return

    text = dest.read_text(encoding="utf-8")
    blocks = re.split(r"(?m)^(?=\[)", text)
    kept: list[str] = []
    for block in blocks:
        header = re.match(r"\[program:([^\]]+)\]", block)
        if header and header.group(1) in disabled:
            logger.info("精简: 丢弃 supervisord 段: %s", header.group(1))
            continue
        kept.append(block)
    dest.write_text("".join(kept), encoding="utf-8")


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
            logger.info("清理孤儿 JSON: %s", f)
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
            logger.info("M4: 跳过禁用模板: %s", tpl.name)
            continue
        _render_json(tpl, dest)


def _snapshot_service_outs() -> dict[str, str | None]:
    """Phase 4: capture ``*_OUT`` env values before sb.json render."""
    from sb_xray.routing.service_spec import SERVICE_SPECS

    return {spec.env_var: os.environ.get(spec.env_var) for spec in SERVICE_SPECS}


def _override_service_outs_for_sb() -> None:
    """Phase 4: swap ``*_OUT=isp-auto`` → ``isp-auto-<slug>`` for sb.json.

    Only rewrites entries whose current value is exactly ``"isp-auto"``;
    direct / proxy-<tag> / user-override values pass through unchanged
    because the per-service balancer only helps operators who already
    opted into the auto balancer for that service.
    """
    from sb_xray.routing.service_spec import SERVICE_SPECS

    for spec in SERVICE_SPECS:
        if os.environ.get(spec.env_var, "").strip() == "isp-auto":
            os.environ[spec.env_var] = spec.sb_tag


def _restore_service_outs(snapshot: dict[str, str | None]) -> None:
    for key, value in snapshot.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _render_sing_box_templates(workdir: Path) -> None:
    template_dir = _TEMPLATES / "sing-box"
    dest_dir = workdir / "sing-box"
    dest_dir.mkdir(parents=True, exist_ok=True)

    from sb_xray.routing.service_spec import per_service_enabled

    enabled = per_service_enabled() and bool(os.environ.get("HAS_ISP_NODES"))
    snapshot: dict[str, str | None] | None = None
    if enabled:
        snapshot = _snapshot_service_outs()
        _override_service_outs_for_sb()
    try:
        for tpl in sorted(template_dir.glob("*.json")):
            _render_json(tpl, dest_dir / tpl.name)
    finally:
        if snapshot is not None:
            _restore_service_outs(snapshot)


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


# 回国出口健康探测默认 URL：中国大陆可达、返回 204，且不被 cn-exit-probe-bypass
# 影响（observatory 直连出站探测，不经路由规则）。可用 CN_EXIT_PROBE_URL 覆盖。
_DEFAULT_CN_EXIT_PROBE_URL = "http://connect.rom.miui.com/generate_204"

_CN_EXIT_MODES = frozenset({"socks5", "reverse", "balance", "off"})


def _resolve_cn_exit_mode() -> str:
    """解析回国出口模式。

    显式 ``CN_EXIT_MODE``（socks5|reverse|balance|off）优先级最高；未设置或
    无法识别时，按既有变量派生（向后兼容）：``ENABLE_SOCKS5_PROXY`` 开启且
    ``CN_EXIT_SOCKS5_HOST`` 有值 → socks5；否则 ``REVERSE_CN_EXIT=true`` →
    reverse；否则 off。
    """
    explicit = os.environ.get("CN_EXIT_MODE", "").strip().lower()
    if explicit in _CN_EXIT_MODES:
        return explicit
    if explicit:
        logger.warning("CN-exit: CN_EXIT_MODE=%r 无法识别,按既有变量派生", explicit)

    socks5_enabled = os.environ.get("ENABLE_SOCKS5_PROXY", "true") == "true"
    socks5_host = os.environ.get("CN_EXIT_SOCKS5_HOST", "").strip()
    if socks5_enabled and socks5_host:
        derived = "socks5"
    elif os.environ.get("REVERSE_CN_EXIT", "false") == "true":
        derived = "reverse"
    else:
        derived = "off"
    if derived != "off":
        logger.info("CN-exit: 由既有变量派生 mode=%s(建议显式设置 CN_EXIT_MODE)", derived)
    return derived


def _apply_cn_exit(xr_file: Path) -> None:
    """调度器：按 CN_EXIT_MODE 选择回国出口模式。"""
    mode = _resolve_cn_exit_mode()
    if mode == "socks5":
        _apply_cn_exit_socks5(xr_file)
    elif mode == "reverse":
        _apply_cn_exit_rtunnel(xr_file)
    elif mode == "balance":
        _apply_cn_exit_balance(xr_file)
    # off → 不改写，CN 流量保持 base 模板的 block


def _rewire_cn_rules(data: dict, route_tag: str, *, use_balancer: bool = False) -> bool:
    """把 cn-ip 封禁规则改造为回国出站规则，返回是否找到 cn-ip。

    ``use_balancer=True`` 时 cn 流量规则写 ``balancerTag``（balance 模式），
    否则写 ``outboundTag``。probe 豁免规则始终走 ``outboundTag: direct``。

    三个要点（缺一会导致客户端节点全挂或误报）：

    1. 下移到 ``private-ip`` 锚点之前 —— 原 cn-ip 位置在所有服务直连
       例外规则（geosite:google → direct 等）之前，``geosite:cn`` 会抢先
       吞掉这些域名。
    2. 豁免 ``www.gstatic.com`` —— mihomo/OpenClash 默认健康检查 URL，
       Loyalsoldier geosite:cn 收录了 ``full:www.gstatic.com``；若被吸进
       回国隧道，隧道一断所有节点健康检查全部失败。
    3. 剥离 ban ``marktag``/``webhook`` —— cn-ip 已不再是封禁规则，保留
       会对正常回国流量持续误报 ban_geoip_cn 事件。
    """
    rules = data["routing"]["rules"]
    cn_ip = next((r for r in rules if r.get("ruleTag") == "cn-ip"), None)
    if cn_ip is None:
        return False
    route_key = "balancerTag" if use_balancer else "outboundTag"
    cn_geosite = {"type": "field", "ruleTag": "cn-geosite", "domain": ["geosite:cn"]}
    cn_geosite[route_key] = route_tag
    cn_ip_rule = {"type": "field", "ruleTag": "cn-ip", "ip": cn_ip.get("ip", ["geoip:cn"])}
    cn_ip_rule[route_key] = route_tag
    remaining = [r for r in rules if r.get("ruleTag") != "cn-ip"]
    anchor = next(
        (i for i, r in enumerate(remaining) if r.get("ruleTag") == "private-ip"),
        len(remaining),
    )
    remaining[anchor:anchor] = [
        {
            "type": "field",
            "ruleTag": "cn-exit-probe-bypass",
            "domain": ["full:www.gstatic.com"],
            "outboundTag": "direct",
        },
        cn_geosite,
        cn_ip_rule,
    ]
    data["routing"]["rules"] = remaining
    return True


def _apply_cn_exit_rtunnel(xr_file: Path) -> None:
    data = json.loads(xr_file.read_text(encoding="utf-8"))
    if _rewire_cn_rules(data, "r-tunnel"):
        logger.info("CN-exit: cn-ip/geosite:cn 规则改为 r-tunnel")
    xr_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _cn_exit_socks5_outbound() -> dict | None:
    """构造 cn-exit socks outbound；缺少 host 时返回 None。"""
    host = os.environ.get("CN_EXIT_SOCKS5_HOST", "").strip()
    if not host:
        return None
    port = int(os.environ.get("CN_EXIT_SOCKS5_PORT", "7891"))
    return {
        "tag": "cn-exit",
        "protocol": "socks",
        "settings": {"servers": [{"address": host, "port": port}]},
    }


def _apply_cn_exit_socks5(xr_file: Path) -> None:
    outbound = _cn_exit_socks5_outbound()
    if outbound is None:
        logger.warning("CN-exit(socks5): CN_EXIT_SOCKS5_HOST 未设置,跳过")
        return
    data = json.loads(xr_file.read_text(encoding="utf-8"))
    if not _rewire_cn_rules(data, "cn-exit"):
        return
    data["outbounds"].append(outbound)
    server = outbound["settings"]["servers"][0]
    logger.info("CN-exit(socks5): %s:%d", server["address"], server["port"])
    xr_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _merge_observatory(data: dict, selector: list[str]) -> None:
    """把 selector 合并进全局 observatory（xray 仅支持单个 observatory）。"""
    obs = data.get("observatory")
    if obs is None:
        data["observatory"] = {
            "subjectSelector": list(selector),
            "probeUrl": os.environ.get("CN_EXIT_PROBE_URL", _DEFAULT_CN_EXIT_PROBE_URL),
            "probeInterval": os.environ.get("CN_EXIT_PROBE_INTERVAL", "30s"),
            "enableConcurrency": True,
        }
        return
    existing = obs.setdefault("subjectSelector", [])
    for tag in selector:
        if tag not in existing:
            existing.append(tag)


def _apply_cn_exit_balance(xr_file: Path) -> None:
    """balance 模式：socks5 + r-tunnel 主备，leastPing + observatory 故障转移。"""
    outbound = _cn_exit_socks5_outbound()
    if outbound is None:
        logger.warning("CN-exit(balance): CN_EXIT_SOCKS5_HOST 未设置,跳过")
        return
    if os.environ.get("ENABLE_REVERSE", "false") != "true":
        logger.warning(
            "CN-exit(balance): ENABLE_REVERSE!=true,r-tunnel 不可用,balance 退化为仅 socks5"
        )
    data = json.loads(xr_file.read_text(encoding="utf-8"))
    if not _rewire_cn_rules(data, "cn-exit-balance", use_balancer=True):
        return
    data["outbounds"].append(outbound)
    selector = ["cn-exit", "r-tunnel"]
    _merge_observatory(data, selector)
    data["routing"].setdefault("balancers", []).append(
        {
            "tag": "cn-exit-balance",
            "selector": selector,
            "fallbackTag": "direct",
            "strategy": {"type": "leastPing"},
        }
    )
    logger.info("CN-exit(balance): selector=%s leastPing", selector)
    xr_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _apply_reverse_proxy(workdir: Path) -> None:
    if os.environ.get("ENABLE_REVERSE", "false") != "true":
        return
    uuid = os.environ.get("XRAY_REVERSE_UUID", "")
    if not uuid:
        logger.warning("Reverse: ENABLE_REVERSE=true 但 XRAY_REVERSE_UUID 未设置,跳过")
        return

    domains = _parse_reverse_domains(os.environ.get("REVERSE_DOMAINS", ""))
    logger.info(
        "Reverse: 注入 reverse client (UUID=...%s) + routing (domains=%s)",
        uuid[-8:],
        ",".join(domains) or "<none>",
    )

    reality = workdir / "xray" / "01_reality_inbounds.json"
    if reality.is_file():
        _inject_reverse_client(reality, uuid)
    xr = workdir / "xray" / "xr.json"
    if domains and xr.is_file():
        _inject_reverse_route(xr, domains)


def trim_runtime_configs(
    *,
    daemon_ini: Path = Path("/etc/supervisor.d/daemon.ini"),
) -> None:
    """Post-process already-rendered ``daemon.ini`` with ``ENABLE_*`` switches.

    Runs **after** whoever wrote the file (Bash ``createConfig`` or Python
    ``create_config``) — so ``ENABLE_*`` flags take effect regardless of
    which entrypoint rendered it. Silent no-op when the file is absent.
    """
    if daemon_ini.is_file():
        _filter_supervisord_programs(daemon_ini)


def create_config(*, workdir: Path | None = None) -> None:
    """Render every service-level config (entrypoint.sh ``createConfig``)."""
    if workdir is None:
        workdir = Path(os.environ.get("WORKDIR", "/tmp/sb-xray"))

    logger.info("渲染所有模板...")
    os.environ["RANDOM_NUM"] = str(random.randint(0, 9))

    for src, dest in _FLAT_RENDERS:
        dest_path = _expand_dest(dest)
        _render_flat(_TEMPLATES / src, dest_path)
        if src == "supervisord/daemon.ini":
            _filter_supervisord_programs(dest_path)
    for src, dest in _FLAT_COPIES:
        dest_path = _expand_dest(dest)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(_TEMPLATES / src, dest_path)

    _cleanup_orphan_json(workdir / "xray", _TEMPLATES / "xray")
    _cleanup_orphan_json(workdir / "sing-box", _TEMPLATES / "sing-box")

    _render_xray_templates(workdir)
    _render_sing_box_templates(workdir)

    _apply_reverse_proxy(workdir)
    xr = workdir / "xray" / "xr.json"
    if xr.is_file():
        _apply_cn_exit(xr)

    logger.info("所有模板渲染完成")
