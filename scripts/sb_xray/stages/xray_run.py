"""xray launcher: clean stale UDS sockets then ``exec`` xray.

Used by supervisord (``templates/supervisord/daemon.ini``) so every restart —
including supervisord's own ``autorestart=true`` after a crash — starts from
a clean ``/dev/shm``. Without this, a previously crashed xray leaves
``/dev/shm/uds*.sock`` files behind, the new process fails to ``bind`` with
``EADDRINUSE``, and supervisord enters an autorestart loop while the xray
inbounds (XHTTP / Reality / VMess+WS) never come up.
"""

from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

SHM_DIR = Path("/dev/shm")
UDS_GLOB = "uds*.sock"
_DEFAULT_CONFDIR = "/sb-xray/xray/"


def cleanup_stale_uds(shm: Path = SHM_DIR) -> list[str]:
    """Remove any leftover ``uds*.sock`` files from ``shm``.

    Returns the names of files that were removed (useful for logging /
    tests). Missing directory or files vanishing mid-loop are not errors —
    the goal is "make sure these paths don't exist", not strict accounting.
    """
    removed: list[str] = []
    if not shm.is_dir():
        return removed
    for path in shm.glob(UDS_GLOB):
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
            removed.append(path.name)
    return removed


def exec_xray(confdir: str = _DEFAULT_CONFDIR) -> None:
    """``os.execvp`` into ``xray`` after cleaning stale UDS sockets.

    Replaces the current process image, so supervisord continues to manage
    the same PID and signal forwarding is preserved.
    """
    removed = cleanup_stale_uds()
    if removed:
        logger.info("xray-run 清理 stale UDS socket: %s", removed)
    os.execvp("xray", ["xray", "run", "-confdir", confdir])
