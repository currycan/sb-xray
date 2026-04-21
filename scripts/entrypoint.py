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
from pathlib import Path

# Allow ``from sb_xray import …`` when invoked as ``python3 scripts/entrypoint.py``.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sb_xray import logging as sblog
from sb_xray import network as sbnet
from sb_xray.env import EnvManager

_DEFAULT_ENV_FILE = Path(os.environ.get("ENV_FILE", "/.env/sb-xray"))
_LEGACY_ENTRYPOINT = Path(__file__).resolve().parent / "entrypoint.sh"

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
        description="sb-xray container entrypoint (Python rewrite, Phase 1)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Bootstrap env + log summary; do NOT invoke the legacy shell.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=_DEFAULT_ENV_FILE,
        help=f"Persisted env file (default: {_DEFAULT_ENV_FILE}).",
    )
    parser.add_argument(
        "--skip-stage",
        action="append",
        default=[],
        metavar="STAGE",
        help="(Phase 2+) Stage names to skip. Currently advisory-only.",
    )
    parser.add_argument(
        "--python-stage",
        action="append",
        default=[],
        choices=["probe"],
        metavar="STAGE",
        help=(
            "Opt-in: run the named stage in Python before delegating to "
            "the legacy shell. Phase 2 supports: probe "
            "(GeoIP + IP type + brutal-module detection)."
        ),
    )
    return parser.parse_args(argv)


def bootstrap(env_file: Path) -> EnvManager:
    """Load persisted env file into os.environ (shell-env already wins)."""
    mgr = EnvManager(env_file)
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
    """Phase 2 Python replacement for ``analyze_base_env`` (entrypoint.sh §15).

    Populates (and persists) the handful of environment variables that
    downstream Bash stages rely on, using the Python network helpers:
      - ``GEOIP_INFO``      — ``<region>|<ip>`` string from ip111.cn
      - ``IP_TYPE``         — ``isp``/``hosting``/``unknown`` via ipapi.is
      - ``BRUTAL_STATUS``   — ``true`` / ``false`` based on /sys/module
    """
    sblog.log("INFO", "[probe] GeoIP / IP-type / brutal-module detection")
    geo = sbnet.get_geo_info()
    if geo:
        mgr.ensure_var("GEOIP_INFO", default=geo)
    ip_type = sbnet.check_ip_type()
    mgr.ensure_var("IP_TYPE", default=ip_type)
    brutal = sbnet.check_brutal_status()
    mgr.ensure_var("BRUTAL_STATUS", default=brutal)
    sblog.log(
        "INFO",
        f"[probe] GEOIP_INFO={geo or 'N/A'} IP_TYPE={ip_type} BRUTAL={brutal}",
    )


def run_legacy(skip_stage: list[str]) -> int:
    """Delegate un-migrated stages to the existing Bash entrypoint."""
    if not _LEGACY_ENTRYPOINT.exists():
        sblog.log("ERROR", f"legacy entrypoint missing: {_LEGACY_ENTRYPOINT}")
        return 127
    env = os.environ.copy()
    if skip_stage:
        env["SB_XRAY_SKIP_STAGES"] = ",".join(skip_stage)
    sblog.log("INFO", "delegating to legacy entrypoint.sh")
    result = subprocess.run(
        ["/usr/bin/env", "bash", str(_LEGACY_ENTRYPOINT)],
        env=env,
        check=False,
    )
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    sblog.log(
        "INFO",
        f"sb-xray entrypoint.py starting (env_file={args.env_file})",
    )
    mgr = bootstrap(args.env_file)
    if "probe" in args.python_stage:
        probe_base_env(mgr)
    sblog.log_summary_box(*_SUMMARY_KEYS)
    if args.dry_run:
        sblog.log("INFO", "dry-run complete, skipping legacy shell")
        return 0
    return run_legacy(args.skip_stage)


if __name__ == "__main__":
    raise SystemExit(main())
