"""GeoIP / GeoSite data refresh (entrypoint.sh:main_init step 10 equivalent).

We keep ``geo_update.sh`` as the single source of truth for the download
manifest because it wraps ``supervisorctl`` reload semantics that would
complicate a pure-Python port. The Python wrapper just invokes the shell
script and surfaces exit codes through the standard logger.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from sb_xray import logging as sblog

_DEFAULT_SCRIPT = Path("/scripts/geo_update.sh")


def update_geo_data(*, script: Path = _DEFAULT_SCRIPT) -> int:
    """Run ``geo_update.sh`` and return its exit code.

    Non-zero exits are logged as WARN (matching the Bash call which does
    not ``set -e`` around this step) but not raised — a stale geoip
    database is preferable to aborting a cold start.
    """
    if not script.is_file():
        sblog.log("WARN", f"[geoip] geo 脚本缺失，跳过: {script}")
        return 0

    sblog.log("INFO", "[geoip] 更新 GeoIP/GeoSite 数据库")
    rc = subprocess.run(["/usr/bin/env", "bash", str(script)], check=False).returncode
    if rc != 0:
        sblog.log("WARN", f"[geoip] geo_update.sh 退出码 {rc}")
    return rc
