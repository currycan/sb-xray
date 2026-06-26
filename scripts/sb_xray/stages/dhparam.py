"""DH parameter generation (entrypoint.sh:main_init step 9 equivalent)."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path("/etc/nginx/dhparam/dhparam.pem")
_DEFAULT_BITS = 4096
# 4096-bit DH on a constrained VPS can legitimately take >2min; 300s is a
# generous ceiling that still bounds a hung openssl so the boot stage can't
# wedge supervisord forever (G1). On timeout we re-raise — dhparam is a
# fail-fast crypto stage, the restart loop recovers.
_DHPARAM_TIMEOUT_SEC: Final[float] = 300.0


def ensure_dhparam(
    *,
    path: Path = _DEFAULT_PATH,
    bits: int = _DEFAULT_BITS,
) -> bool:
    """Generate ``dhparam.pem`` when missing. Return True iff generated.

    Bash equivalent::

        if [ ! -f "$dh_file" ]; then
            mkdir -p "$(dirname "$dh_file")"
            openssl dhparam -dsaparam -out "$dh_file" 4096
        fi
    """
    if path.is_file():
        logger.debug("DH 参数已存在，跳过: %s", path)
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("生成 DH 参数 (%d bit)，首轮耗时可能较长...", bits)
    rc = subprocess.run(
        ["openssl", "dhparam", "-dsaparam", "-out", str(path), str(bits)],
        check=False,
        timeout=_DHPARAM_TIMEOUT_SEC,
    ).returncode
    if rc != 0:
        raise RuntimeError(f"openssl dhparam exited with code {rc}")
    logger.info("DH 参数生成完成")
    return True
