#!/usr/bin/env python3
"""sb-xray container entrypoint (Python rewrite).

This is the thin shell introduced in Phase 1. It:
  1. Bootstraps ``EnvManager`` and loads anything already persisted in
     ``${ENV_FILE}`` (default /.env/sb-xray).
  2. Logs a summary of the most-important ENV variables via
     :func:`sb_xray.logging.log_summary_box`.
  3. Delegates to ``scripts/entrypoint.sh`` for every stage that hasn't
     been migrated yet, inheriting the current ``os.environ``.

As subsequent phases migrate stages into the ``sb_xray`` package, the
``subprocess`` fallback below will shrink until it is removed in
Phase 5 (which also flips ``ENTRYPOINT`` in the Dockerfile).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

# Allow ``from sb_xray import …`` when invoked as ``python3 scripts/entrypoint.py``.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sb_xray import display as sbdisplay
from sb_xray import logging as sblog
from sb_xray import network as sbnet
from sb_xray import node_meta as sbnode
from sb_xray import subscription as sbsub
from sb_xray.env import EnvManager

_DEFAULT_ENV_FILE = Path(os.environ.get("ENV_FILE", "/.env/sb-xray"))
_LEGACY_ENTRYPOINT = Path(__file__).resolve().parent / "entrypoint.sh"
_CLIENT_TEMPLATE_DIR = Path("/templates/client_template")
_SOURCES_DIR = Path("/sources")

_SUMMARY_KEYS = (
    "DOMAIN",
    "CDNDOMAIN",
    "GEOIP_INFO",
    "IP_TYPE",
    "ISP_TAG",
    "ENABLE_REVERSE",
    "WORKDIR",
    "ENV_FILE",
)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="entrypoint.py",
        description="sb-xray container entrypoint (Python rewrite)",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=_DEFAULT_ENV_FILE,
        help=f"Persisted env file (default: {_DEFAULT_ENV_FILE}).",
    )

    sub = parser.add_subparsers(dest="command")

    # ``run`` — the default (container ENTRYPOINT) behavior
    run_p = sub.add_parser("run", help="Boot container (default).")
    run_p.add_argument("--dry-run", action="store_true")
    run_p.add_argument("--skip-stage", action="append", default=[], metavar="STAGE")
    run_p.add_argument(
        "--python-stage",
        action="append",
        default=[],
        choices=["probe", "cert", "providers", "config", "media"],
        metavar="STAGE",
    )

    # ``show`` — replaces show-config.sh
    sub.add_parser(
        "show",
        help="Print subscription-link banner + optional TLS diagnostics.",
    )

    # ``trim`` — post-process daemon.ini with ENABLE_* program switches;
    # safe to call after either Bash `createConfig` or Python `create_config`.
    sub.add_parser(
        "trim",
        help="Apply ENABLE_* trim switches to the already-rendered daemon.ini.",
    )

    # ``parse_known_args`` (vs ``parse_args``) captures everything the
    # Docker CMD appends (e.g. ``supervisord``) as ``extras``; we
    # forward those to the legacy Bash entrypoint as its ``$@`` so it
    # can still do ``[ "${1#-}" = 'supervisord' ]`` + ``exec "$@"``.
    args, extras = parser.parse_known_args(argv)
    args.extras = extras

    # Backward-compat: no subcommand → default to run
    if args.command is None:
        args.command = "run"
        args.dry_run = getattr(args, "dry_run", False)
        args.skip_stage = getattr(args, "skip_stage", [])
        args.python_stage = getattr(args, "python_stage", [])
    return args


def bootstrap(env_file: Path) -> EnvManager:
    """Load persisted env file into os.environ (shell-env already wins).

    Also publishes the resolved ``ENV_FILE`` path into ``os.environ`` so
    ``log_summary_box`` can show it (Bash ``source ${ENV_FILE}`` gets this
    for free via shell semantics).
    """
    mgr = EnvManager(env_file)
    os.environ.setdefault("ENV_FILE", str(env_file))
    text = env_file.read_text(encoding="utf-8")
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("export "):
            continue
        _, _, assign = line.partition("export ")
        key, _, value_raw = assign.partition("=")
        value = value_raw.strip().strip("'")
        os.environ.setdefault(key, value)
    return mgr


def probe_base_env(mgr: EnvManager) -> None:
    """Full port of ``analyze_base_env`` (entrypoint.sh:1118-1148).

    Ensures every base-env variable the downstream stages rely on. Each
    call uses ``EnvManager.ensure_var`` so the three-tier precedence holds:
    shell env wins → ``${ENV_FILE}`` value wins → generator fallback.

    Variables in declaration order (matches Bash):
      - Random ports  : ``XUI_LOCAL_PORT`` / ``DUFS_PORT``
      - Random creds  : ``PASSWORD`` (16 chars) / ``XRAY_UUID`` /
                        ``XRAY_REVERSE_UUID`` / ``SB_UUID``
      - Reality IDs   : ``XRAY_REALITY_SHORTID`` (16h) / ``_2`` (8h) / ``_3`` (12h)
      - URL paths     : ``XRAY_URL_PATH`` (32) / ``SUBSCRIBE_TOKEN`` (32) /
                        ``SUB_STORE_FRONTEND_BACKEND_PATH`` (leading ``/``)
      - Network probe : ``STRATEGY`` / ``GEOIP_INFO`` / ``IP_TYPE`` /
                        ``IS_BRUTAL``
    """
    from sb_xray import random_gen as sbrand

    sblog.log("INFO", "[阶段 1] 初始化基础环境变量...")

    def _strategy() -> str:
        v4, v6 = sbnet.probe_ip_sb()
        return sbnet.detect_ip_strategy(v4_ok=v4, v6_ok=v6)

    # Variables paired with their generator. Ordering mirrors bash.
    specs: tuple[tuple[str, Callable[[], str]], ...] = (
        ("XUI_LOCAL_PORT", lambda: sbrand.generate("port")),
        ("DUFS_PORT", lambda: sbrand.generate("port")),
        ("PASSWORD", lambda: sbrand.generate("password", 16)),
        ("XRAY_UUID", lambda: sbrand.generate("uuid")),
        ("XRAY_REVERSE_UUID", lambda: sbrand.generate("uuid")),
        ("SB_UUID", lambda: sbrand.generate("uuid")),
        ("XRAY_REALITY_SHORTID", lambda: sbrand.generate("hex", 8)),
        ("XRAY_REALITY_SHORTID_2", lambda: sbrand.generate("hex", 4)),
        ("XRAY_REALITY_SHORTID_3", lambda: sbrand.generate("hex", 6)),
        ("XRAY_URL_PATH", lambda: sbrand.generate("path", 32)),
        ("SUBSCRIBE_TOKEN", lambda: sbrand.generate("path", 32)),
        ("STRATEGY", _strategy),
        ("GEOIP_INFO", sbnet.get_geo_info),
        ("IS_BRUTAL", sbnet.check_brutal_status),
        ("SUB_STORE_FRONTEND_BACKEND_PATH", lambda: "/" + sbrand.generate("path", 32)),
        ("IP_TYPE", sbnet.check_ip_type),
    )
    for key, gen in specs:
        mgr.ensure_var(key, generator=gen)

    sblog.log(
        "INFO",
        f"[阶段 1] 完成 port_hy2={os.environ.get('PORT_HYSTERIA2', '?')} "
        f"port_tuic={os.environ.get('PORT_TUIC', '?')} "
        f"port_anytls={os.environ.get('PORT_ANYTLS', '?')}",
    )


def run_legacy(skip_stage: list[str], extras: list[str] | None = None) -> int:
    """Delegate un-migrated stages to the existing Bash entrypoint.

    ``extras`` are forwarded as trailing ``$@`` so the Bash script can
    still run its final ``exec "$@"`` to launch supervisord (the legacy
    entrypoint aborts with "$1: unbound variable" when ``set -u`` is
    active and no args are passed).
    """
    if not _LEGACY_ENTRYPOINT.exists():
        sblog.log("ERROR", f"legacy entrypoint missing: {_LEGACY_ENTRYPOINT}")
        return 127
    env = os.environ.copy()
    if skip_stage:
        env["SB_XRAY_SKIP_STAGES"] = ",".join(skip_stage)
    forwarded = extras if extras else ["supervisord"]
    sblog.log(
        "INFO",
        f"delegating to legacy entrypoint.sh (argv={forwarded})",
    )
    result = subprocess.run(
        ["/usr/bin/env", "bash", str(_LEGACY_ENTRYPOINT), *forwarded],
        env=env,
        check=False,
    )
    return result.returncode


def _envsubst_render(src: Path, dst: Path) -> None:
    """Bash-``envsubst`` compatible ``${VAR}``/``$VAR`` expansion.

    ``string.Template.safe_substitute`` leaves unknown placeholders intact,
    matching ``envsubst``'s behaviour of emitting the literal ``${FOO}``
    when ``FOO`` is unset. We pre-normalize ``os.environ`` to ``str`` values
    to keep ``safe_substitute`` happy under strict typing.
    """
    from string import Template

    raw = src.read_text(encoding="utf-8")
    rendered = Template(raw).safe_substitute(os.environ)
    dst.write_text(rendered, encoding="utf-8")


def run_show_pipeline(env_file: Path) -> int:
    """End-to-end ``show`` subcommand pipeline (show-config.sh `main`).

    Order mirrors the Bash ``main`` function exactly:
      1. Bootstrap env + derive node metadata
      2. ``mkdir -p ${WORKDIR}/subscribe``
      3. ``generate_links`` → write base64 subscription files
      4. envsubst-render every client template into ``${WORKDIR}/subscribe``
      5. ``cp -a /sources/* ${WORKDIR}/subscribe`` (best-effort)
      6. ``show_info_links`` → banner + ANSI-stripped ``show-config`` archive
    """
    import shutil

    bootstrap(env_file)
    # show-config.sh:14-16 also sources STATUS_FILE (ISP_TAG/IS_8K_SMOOTH) +
    # SECRET_FILE (远端密钥), without which node_meta can't produce the
    # ✈ super / ✈ good tags. Best-effort: ignore if files absent.
    for extra in (
        Path(os.environ.get("STATUS_FILE", "/.env/status")),
        Path(os.environ.get("SECRET_FILE", "/.env/secret")),
    ):
        if extra.is_file():
            bootstrap(extra)
    sbnode.derive_and_export()

    workdir = Path(os.environ.get("WORKDIR", "/tmp/sb-xray"))
    subscribe_dir = workdir / "subscribe"
    subscribe_dir.mkdir(parents=True, exist_ok=True)

    sbsub.write_subscriptions(output_dir=subscribe_dir)

    if _CLIENT_TEMPLATE_DIR.is_dir():
        for tpl in sorted(_CLIENT_TEMPLATE_DIR.iterdir()):
            if tpl.suffix == ".yaml" or tpl.name == "surge.conf":
                _envsubst_render(tpl, subscribe_dir / tpl.name)

    if _SOURCES_DIR.is_dir():
        for item in _SOURCES_DIR.iterdir():
            target = subscribe_dir / item.name
            try:
                if item.is_dir():
                    shutil.copytree(item, target, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, target)
            except OSError as exc:  # best-effort: Bash uses `|| true`
                sblog.log("WARN", f"[show] copy {item} failed: {exc}")

    sbdisplay.show_info_links(archive_path=subscribe_dir / "show-config")
    return 0


def issue_bundle_certificate() -> None:
    """entrypoint.sh:1381 `issueCertificate sb_xray_bundle ...` 的 Python 接线。

    只在 DOMAIN + CDNDOMAIN 都齐全时才调 ``cert.ensure_certificate``；缺任何
    一个就静默跳过,继续走 Bash 主流程的其它步骤(bash issueCertificate 自己
    会再 skip 掉 7d 有效期内的证书)。
    """
    from sb_xray import cert as sbcert

    domain = os.environ.get("DOMAIN", "")
    cdn = os.environ.get("CDNDOMAIN", "")
    if not domain or not cdn:
        sblog.log("WARN", "[cert] DOMAIN/CDNDOMAIN 未就绪,跳过 Python 侧证书签发")
        return
    params = f"{domain}:ali|{cdn}:cf"
    sblog.log("INFO", f"[cert] ensure_certificate(sb_xray_bundle, {params})")
    try:
        status = sbcert.ensure_certificate(name="sb_xray_bundle", params=params)
        sblog.log("INFO", f"[cert] status={status.value}")
    except Exception as exc:  # pragma: no cover - 运行时异常不阻塞主流程
        sblog.log("ERROR", f"[cert] 失败,回落到 Bash issueCertificate: {exc}")


def run_media_probes() -> dict[str, str]:
    """entrypoint.sh ``analyze_ai_routing_env`` 媒体探测部分的 Python 接线。

    跑 8 个 Netflix/Disney/YouTube/Social/TikTok/ChatGPT/Claude/Gemini 探针,
    结果写入 ``os.environ`` (下游 build_xray_service_rules 消费);不持久化
    到 ENV_FILE——bash entrypoint.sh 会把它们写入 STATUS_FILE。
    """
    from sb_xray.routing import media as sbmedia

    sblog.log("INFO", "[media] 运行 8 个可达性探针")
    results = sbmedia.check_all()
    for key, value in results.items():
        os.environ[key] = value
    sblog.log(
        "INFO",
        "[media] " + " ".join(f"{k}={v}" for k, v in results.items()),
    )
    return results


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.command == "show":
        return run_show_pipeline(args.env_file)

    if args.command == "trim":
        from sb_xray import config_builder as sbcfg

        sbcfg.trim_runtime_configs()
        return 0

    sblog.log(
        "INFO",
        f"sb-xray entrypoint.py starting (env_file={args.env_file})",
    )
    mgr = bootstrap(args.env_file)
    # Mirror entrypoint.sh:main_init 1351-1352 — source STATUS_FILE (ISP_TAG,
    # IS_8K_SMOOTH, media probe results) + SECRET_FILE (remote secrets) so
    # log_summary_box and downstream stages see the same state that Bash does.
    for extra in (
        Path(os.environ.get("STATUS_FILE", "/.env/status")),
        Path(os.environ.get("SECRET_FILE", "/.env/secret")),
    ):
        if extra.is_file():
            bootstrap(extra)
    if "probe" in args.python_stage:
        probe_base_env(mgr)
    if "cert" in args.python_stage:
        issue_bundle_certificate()
    if "media" in args.python_stage:
        run_media_probes()
    if "providers" in args.python_stage:
        from sb_xray.routing import providers as sbprov

        sbprov.generate_and_export()
    if "config" in args.python_stage:
        from sb_xray import config_builder as sbcfg

        sbcfg.create_config()
    sblog.log_summary_box(*_SUMMARY_KEYS)
    if args.dry_run:
        sblog.log("INFO", "dry-run complete, skipping legacy shell")
        return 0
    return run_legacy(args.skip_stage, args.extras)


if __name__ == "__main__":
    raise SystemExit(main())
