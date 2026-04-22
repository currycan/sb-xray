#!/usr/bin/env python3
"""sb-xray container entrypoint (100% Python orchestration).

Replaces the legacy ``entrypoint.sh`` end-to-end. Pipeline (all stages
are idempotent and safe to re-run across container restarts):

  1. ``_init_dirs`` + load persisted state (ENV_FILE / STATUS_FILE / SECRET_FILE).
  2. ``decrypt_remote_secrets`` (entrypoint.sh §14).
  3. ``probe_base_env`` — UUID/port/geo probes (entrypoint.sh §15 step 1).
  4. ``run_isp_speed_tests`` — ISP 选路 (step 2).
  5. ``run_media_probes`` + ensure Reality / MLKEM key pairs (step 3).
  6. ``build_client_and_server_configs`` — outbound JSON (step 4).
  7. ``issue_bundle_certificate`` — acme.sh (step 8).
  8. ``ensure_dhparam`` — openssl (step 9).
  9. ``update_geo_data`` — ``sb_xray.geo.refresh`` (step 10; persisted under ``/geo``).
 10. ``create_config`` + ``generate_and_export`` — templates (step 11).
 11. ``trim_runtime_configs`` — ENABLE_* switches (step 11b).
 12. ``init_panels`` — X-UI / S-UI (step 12).
 13. ``setup_basic_auth`` — nginx htpasswd (step 13).
 14. ``install_crontab`` — geo daily (step 14).
 15. Banner + ``exec_supervisord`` (tail).
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
_DEFAULT_STATUS_FILE = Path("/.env/status")
_DEFAULT_SECRET_FILE = Path("/.env/secret")


def _status_file() -> Path:
    return Path(os.environ.get("STATUS_FILE", str(_DEFAULT_STATUS_FILE)))


def _secret_file() -> Path:
    return Path(os.environ.get("SECRET_FILE", str(_DEFAULT_SECRET_FILE)))


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

# Every stage identifier that ``--skip-stage`` understands.
_STAGE_IDS: tuple[str, ...] = (
    "secrets",
    "probe",
    "speed",
    "media",
    "keys",
    "outbounds",
    "cert",
    "dhparam",
    "geoip",
    "config",
    "providers",
    "trim",
    "panels",
    "nginx_auth",
    "cron",
    "show",
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

    run_p = sub.add_parser("run", help="Boot container (default).")
    run_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the pipeline but don't exec supervisord at the end.",
    )
    run_p.add_argument(
        "--skip-stage",
        action="append",
        default=[],
        choices=list(_STAGE_IDS),
        metavar="STAGE",
        help="Skip a stage by id (repeatable).",
    )

    sub.add_parser(
        "show",
        help="Print subscription-link banner + optional TLS diagnostics.",
    )

    sub.add_parser(
        "trim",
        help="Apply ENABLE_* trim switches to the already-rendered daemon.ini.",
    )

    sub.add_parser(
        "geo-update",
        help="Download GeoIP/GeoSite rule-sets (cron entry; forces refresh + xray reload).",
    )

    sub.add_parser(
        "shoutrrr-forward",
        help="Run the shoutrrr event-bus HTTP receiver (long-running; supervisord-managed).",
    )

    args, extras = parser.parse_known_args(argv)
    args.extras = extras

    if args.command is None:
        args.command = "run"
        args.dry_run = getattr(args, "dry_run", False)
        args.skip_stage = getattr(args, "skip_stage", [])
    return args


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_dirs(env_file: Path) -> None:
    """entrypoint.sh:_init_dirs equivalent.

    Creates every directory the stages below expect. Silently tolerates
    ``OSError`` / ``PermissionError`` on the log + panel trees so the
    unit tests (no `/var/log`, no `/opt`) never abort boot.
    """
    status_file = Path(os.environ.get("STATUS_FILE", str(env_file.parent / "status")))

    env_file.parent.mkdir(parents=True, exist_ok=True)
    status_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.touch(exist_ok=True)
    status_file.touch(exist_ok=True)

    env_text = env_file.read_text(encoding="utf-8")
    cleaned_lines = [
        line
        for line in env_text.splitlines()
        if not line.startswith(("export ISP_TAG=", "export IS_8K_SMOOTH="))
    ]
    cleaned = "\n".join(cleaned_lines)
    if cleaned != env_text.rstrip("\n"):
        env_file.write_text(cleaned + ("\n" if cleaned else ""), encoding="utf-8")

    def _safe_mkdir(path: Path) -> None:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            sblog.log("DEBUG", f"[init] mkdir {path} skipped: {exc}")

    log_dir = Path(os.environ.get("LOGDIR", "/var/log"))
    for child in ("supervisor", "xray", "sing-box", "dufs", "nginx", "x-ui", "s-ui"):
        _safe_mkdir(log_dir / child)

    _safe_mkdir(Path(os.environ.get("SUI_DB_FOLDER", "/opt/s-ui")))
    _safe_mkdir(Path(os.environ.get("SUB_STORE_DATA_BASE_PATH", "/opt/substore")))


def _load_env_file(path: Path) -> int:
    """``source "${path}"`` by delegating to bash; inject new vars into env.

    entrypoint.sh used ``source "${FILE}"`` for ENV_FILE / STATUS_FILE /
    SECRET_FILE. ``source`` is a full bash feature — it handles quoted
    and bareword assignments, ``export`` prefix, nested quotes, inline
    comments, multi-line here-docs, CRLF line endings, BOM, command
    substitutions like ``KEY=$(date +%s)``, and every other construct
    that shows up in the wild.

    We previously hand-rolled a regex parser and kept finding new edge
    cases that broke ACME credential loading. This rewrite outsources
    the parsing to bash itself by invoking::

        bash -c 'set -a; [ -f "$1" ] && . "$1"; env -0' _ "${path}"

    and diffs the resulting env against the current process env. Any
    key **not** already in ``os.environ`` is injected via
    ``setdefault`` (shell-set vars still win). Values are NUL-delimited
    so embedded newlines are preserved correctly.

    Returns the number of NEW variables actually injected (surfaced to
    operators in the step-2 log). Missing file → ``0`` (no-op),
    matching bash ``source`` of a missing file followed by ``|| true``.
    """
    if not path.is_file():
        return 0

    try:
        result = subprocess.run(
            [
                "/usr/bin/env",
                "bash",
                "-c",
                'set -a; [ -f "$1" ] && . "$1"; env -0',
                "_",
                str(path),
            ],
            check=True,
            capture_output=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        sblog.log("WARN", f"[env-load] bash source {path} failed: {exc}")
        return 0

    injected = 0
    for record in result.stdout.split(b"\x00"):
        if not record:
            continue
        key_b, sep, value_b = record.partition(b"=")
        if not sep:
            continue
        try:
            key = key_b.decode("utf-8")
            value = value_b.decode("utf-8")
        except UnicodeDecodeError:
            continue
        # Drop bash-internal vars we never want to bleed in.
        if key in _BASH_INTERNAL_VARS or key.startswith("BASH_"):
            continue
        # Parent-shell priority: honour the value ONLY when it's non-empty.
        # Dockerfile declares ``ENV ACMESH_REGISTER_EMAIL=""`` (empty
        # placeholder) so that operators can override via compose, but the
        # real cert credentials live in SECRET_FILE and must win over the
        # empty placeholder. Bash ``set -a; source SECRET_FILE`` overwrites
        # unconditionally; the equivalent here is to treat an empty string
        # in ``os.environ`` the same as "not set". A non-empty parent
        # value still wins (docker-compose explicit override).
        if os.environ.get(key):
            continue
        os.environ[key] = value
        injected += 1
    return injected


# Variables bash sets inside the subshell but which we must NOT leak out
# (they'd shadow the parent process's values or pollute os.environ).
_BASH_INTERNAL_VARS = frozenset(
    {
        "_",
        "PWD",
        "OLDPWD",
        "SHLVL",
        "IFS",
        "PS1",
        "PS2",
        "PS4",
        "UID",
        "EUID",
        "PPID",
        "RANDOM",
        "SECONDS",
        "LINENO",
        "HOSTNAME",
        "HOSTTYPE",
        "MACHTYPE",
        "OSTYPE",
        "SHELL",
    }
)


def bootstrap(env_file: Path) -> EnvManager:
    """Create ``EnvManager`` and seed ``os.environ`` from ``env_file``."""
    mgr = EnvManager(env_file)
    os.environ.setdefault("ENV_FILE", str(env_file))
    os.environ.setdefault("STATUS_FILE", str(_status_file()))
    os.environ.setdefault("SECRET_FILE", str(_secret_file()))
    _load_env_file(env_file)
    return mgr


def probe_base_env(mgr: EnvManager) -> None:
    """Full port of ``analyze_base_env`` (entrypoint.sh:1118-1148)."""
    from sb_xray import random_gen as sbrand

    sblog.log("DEBUG", "[env] 按三层优先级填充 16 个基础变量")

    def _strategy() -> str:
        v4, v6 = sbnet.probe_ip_sb()
        return sbnet.detect_ip_strategy(v4_ok=v4, v6_ok=v6)

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
        f"[env] hy2={os.environ.get('PORT_HYSTERIA2', '?')} "
        f"tuic={os.environ.get('PORT_TUIC', '?')} "
        f"anytls={os.environ.get('PORT_ANYTLS', '?')}",
    )


def _envsubst_render(src: Path, dst: Path) -> None:
    """Bash-``envsubst`` compatible ``${VAR}`` / ``$VAR`` expansion."""
    from string import Template

    raw = src.read_text(encoding="utf-8")
    rendered = Template(raw).safe_substitute(os.environ)
    dst.write_text(rendered, encoding="utf-8")


class CertStageError(RuntimeError):
    """Fatal error from the cert stage — abort the entire pipeline.

    Raised to abort startup **before** supervisord launches: nginx /
    xray / sing-box all reference ``${SSL_PATH}/sb_xray_bundle.crt``
    in their templates, so a missing cert triggers a permanent
    ``nginx: [emerg] cannot load certificate`` / ``xray: open ...:
    no such file`` restart loop. Bash ``set -eou pipefail`` had
    similar fail-fast semantics at the ``acme.sh`` subcommand level;
    Python makes it explicit so later stages never render configs
    pointing at files that don't exist.
    """


def issue_bundle_certificate() -> None:
    """entrypoint.sh:1381 ``issueCertificate sb_xray_bundle …`` wrapper.

    **Fail-fast**: any unrecoverable problem (missing DOMAIN, acme.sh
    exception, post-install cert files absent) raises
    :class:`CertStageError`. ``run_pipeline`` lets the exception
    propagate and Python exits non-zero; docker-compose restart policy
    then respawns the container, giving the operator time to fix the
    underlying issue (expired ACME credentials, wrong DNS, etc.).
    """
    from sb_xray import cert as sbcert

    domain = os.environ.get("DOMAIN", "")
    cdn = os.environ.get("CDNDOMAIN", "")
    if not domain or not cdn:
        raise CertStageError("DOMAIN/CDNDOMAIN 未设置，无法签发证书")
    params = f"{domain}:ali|{cdn}:cf"
    sblog.log("INFO", f"  [cert] ensure_certificate(sb_xray_bundle, {params})")
    try:
        status = sbcert.ensure_certificate(name="sb_xray_bundle", params=params)
    except Exception as exc:
        raise CertStageError(f"ensure_certificate 抛异常: {exc}") from exc
    sblog.log("INFO", f"  [cert] status={status.value}")

    # Post-install verification: acme.sh --install-cert runs with
    # check=False so a silent failure (wrong DNS cred, stale zone, etc.)
    # returns INSTALLED without actually writing the files. Explicit
    # on-disk check guarantees downstream template stages can't render
    # configs pointing at non-existent cert paths.
    ssl_path = Path(os.environ.get("SSL_PATH", "/pki"))
    expected = [
        ssl_path / "sb_xray_bundle.crt",
        ssl_path / "sb_xray_bundle.key",
        ssl_path / "sb_xray_bundle-ca.crt",
    ]
    missing = [p for p in expected if not p.is_file()]
    if missing:
        raise CertStageError(
            "证书安装完成但预期文件缺失 — 检查 DNS 凭据与 acme.sh 日志: "
            + ", ".join(str(p) for p in missing)
        )


def run_media_probes() -> dict[str, str]:
    """entrypoint.sh ``analyze_ai_routing_env`` media portion."""
    from sb_xray.routing import media as sbmedia

    sblog.log(
        "DEBUG",
        "[media] 运行 8 个可达性探针 (Netflix/Disney/YouTube/Social/TikTok/ChatGPT/Claude/Gemini)",
    )
    results = sbmedia.check_all()
    for key, value in results.items():
        os.environ[key] = value
    sblog.log(
        "INFO",
        "[media] " + " ".join(f"{k.replace('_OUT', '').lower()}={v}" for k, v in results.items()),
    )
    return results


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def run_show_pipeline(env_file: Path) -> int:
    """End-to-end ``show`` subcommand pipeline (show-config.sh ``main``)."""
    import shutil

    bootstrap(env_file)
    _load_env_file(_status_file())
    _load_env_file(_secret_file())
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
            except OSError as exc:
                sblog.log("WARN", f"[show] copy {item} failed: {exc}")

    sbdisplay.show_info_links(archive_path=subscribe_dir / "show-config")
    return 0


def run_pipeline(
    *,
    env_file: Path,
    skip_stage: list[str],
    dry_run: bool,
    extras: list[str] | None,
) -> int:
    """Full boot pipeline. Exec's supervisord on success unless ``dry_run``."""
    from sb_xray import config_builder as sbcfg
    from sb_xray import secrets as sbsecrets
    from sb_xray.routing import isp as sbisp
    from sb_xray.routing import providers as sbprov
    from sb_xray.speed_test import run_isp_speed_tests
    from sb_xray.stages import (
        cron as sbcron,
    )
    from sb_xray.stages import (
        dhparam as sbdh,
    )
    from sb_xray.stages import (
        geoip as sbgeo,
    )
    from sb_xray.stages import (
        keys as sbkeys,
    )
    from sb_xray.stages import (
        nginx_auth as sbauth,
    )
    from sb_xray.stages import (
        panels as sbpanels,
    )
    from sb_xray.stages import (
        supervisord as sbsup,
    )

    skips = set(skip_stage)

    def _run(name: str, fn: Callable[[], None]) -> None:
        if name in skips:
            sblog.log("INFO", f"  [skip] stage '{name}' skipped via --skip-stage")
            return
        fn()

    def _step(_n: int, label: str) -> None:
        # Emit a descriptive phase heading. The counter is kept in the
        # signature only for source-reading convenience — it never
        # appears in the log since arbitrary indices ("3/15") mean
        # nothing to operators.
        sblog.log("INFO", f"▸ {label}")

    sblog.log("INFO", "▶ SB-Xray 启动初始化 (15 阶段 Python pipeline)")

    _step(1, "初始化目录与文件")
    _init_dirs(env_file)

    _step(2, "解密远端密钥库 + 加载密钥")

    def _secrets() -> None:
        """Bash parity (entrypoint.sh:main_init step 2)::

            decryptSecretsEnv
            source "${SECRET_FILE}"

        The ``source`` was *inside* step 2 in bash — sourcing always
        happened after decrypt, regardless of whether the file was newly
        decrypted or pre-existing. Without this, ACMESH_* / ALI_* / CF_*
        never reach ``os.environ`` when the file is already on disk
        (status=skipped) and ``cert.ensure_certificate`` later fails
        with 'required environment variables missing' — the exact bug
        reported after the first VPS re-deploy.
        """
        try:
            status = sbsecrets.decrypt_remote_secrets(secret_file=_secret_file())
            sblog.log("INFO", f"  [secrets] status={status.value}")
        except RuntimeError as exc:
            sblog.log("WARN", f"  [secrets] 解密失败，继续启动: {exc}")
        # ``source "${SECRET_FILE}"`` equivalent — idempotent; no-op if
        # the file is missing (decrypt failed or user hasn't set it up).
        loaded = _load_env_file(_secret_file())
        if loaded:
            sblog.log(
                "INFO",
                f"  [secrets] 已加载 {loaded} 个变量到环境 (来自 {_secret_file()})",
            )

    _run("secrets", _secrets)

    _step(3, "加载持久化状态 (ENV/STATUS)")
    mgr = bootstrap(env_file)
    _load_env_file(_status_file())
    # Safety net: if ``--skip-stage secrets`` was used, the step-2
    # source above never ran. Re-try here so the subsequent cert step
    # still has a chance (setdefault — won't clobber existing shell env).
    _load_env_file(_secret_file())

    _step(4, "基础环境变量初始化")
    _run("probe", lambda: probe_base_env(mgr))

    _step(5, "ISP 测速与选路")
    _run("speed", lambda: run_isp_speed_tests())

    _step(6, "流媒体/AI 可达性检测")
    _run("media", lambda: _cache_media_probes(mgr))

    _step(7, "生成加密密钥对")
    _run("keys", lambda: sbkeys.ensure_all_keys(mgr))

    _step(8, "生成客户端/服务端配置片段")
    _run("outbounds", lambda: sbisp.build_client_and_server_configs())

    _step(9, "TLS 证书申请/续签")
    _run("cert", issue_bundle_certificate)

    _step(10, "生成 DH 参数")
    _run("dhparam", lambda: sbdh.ensure_dhparam())

    _step(11, "更新 GeoIP/GeoSite 数据库")
    _run("geoip", lambda: sbgeo.update_geo_data())

    _step(12, "渲染配置模板 + Proxy Providers")
    _run("config", lambda: sbcfg.create_config())
    _run("providers", lambda: sbprov.generate_and_export())
    _run("trim", lambda: sbcfg.trim_runtime_configs())

    _step(13, "初始化 X-UI / S-UI 管理面板")
    _run("panels", lambda: sbpanels.init_panels())

    _step(14, "配置 Nginx Basic Auth + Cron")
    _run("nginx_auth", lambda: sbauth.setup_basic_auth())
    _run("cron", lambda: sbcron.install_crontab())

    _step(15, "打印订阅链接 banner")
    _run("show", lambda: _banner_best_effort(env_file))

    sblog.log_summary_box(*_SUMMARY_KEYS)

    sblog.log("INFO", "✅ 初始化完成，移交 Supervisord 接管")

    if dry_run:
        sblog.log("INFO", "dry-run complete, skipping supervisord exec")
        return 0
    sbsup.exec_supervisord(extras)
    return 0  # unreachable


def _cache_media_probes(mgr: EnvManager) -> None:
    """Persist media probe results to STATUS_FILE (bash parity)."""
    from sb_xray.speed_test import _write_status_line  # reuse upsert helper

    results = run_media_probes()
    for key, value in results.items():
        _write_status_line(key, value)
    # ``ISP_OUT`` mirrors ``get_isp_preferred_strategy`` in bash
    isp_out = "isp-auto" if os.environ.get("HAS_ISP_NODES") else "direct"
    os.environ["ISP_OUT"] = isp_out
    _write_status_line("ISP_OUT", isp_out)

    sblog.log_summary_box(
        "IP_TYPE",
        "ISP_TAG",
        "IS_8K_SMOOTH",
        "ISP_OUT",
        "CHATGPT_OUT",
        "NETFLIX_OUT",
        "DISNEY_OUT",
        "YOUTUBE_OUT",
        "GEMINI_OUT",
        "CLAUDE_OUT",
        "TIKTOK_OUT",
        "SOCIAL_MEDIA_OUT",
    )
    # mgr kept in signature for future extension; unused today
    del mgr


def _banner_best_effort(env_file: Path) -> None:
    """Run the ``show`` pipeline but never fail the boot if it raises."""
    try:
        run_show_pipeline(env_file)
    except Exception as exc:  # pragma: no cover — cosmetic banner only
        sblog.log("WARN", f"[banner] show pipeline 失败,跳过: {exc}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.command == "show":
        return run_show_pipeline(args.env_file)

    if args.command == "trim":
        from sb_xray import config_builder as sbcfg

        sbcfg.trim_runtime_configs()
        return 0

    if args.command == "geo-update":
        from sb_xray import geo

        return geo.refresh(on_startup=False)

    if args.command == "shoutrrr-forward":
        from sb_xray import shoutrrr

        return shoutrrr.run()

    sblog.log(
        "INFO",
        f"sb-xray entrypoint.py starting (env_file={args.env_file})",
    )
    return run_pipeline(
        env_file=args.env_file,
        skip_stage=args.skip_stage,
        dry_run=args.dry_run,
        extras=args.extras,
    )


if __name__ == "__main__":
    raise SystemExit(main())
