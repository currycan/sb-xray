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
    bootstrap(args.env_file)
    sblog.log_summary_box(*_SUMMARY_KEYS)
    if args.dry_run:
        sblog.log("INFO", "dry-run complete, skipping legacy shell")
        return 0
    return run_legacy(args.skip_stage)


if __name__ == "__main__":
    raise SystemExit(main())
