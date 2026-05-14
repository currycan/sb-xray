"""X-UI / S-UI panel bootstrap (entrypoint.sh:main_init step 12 equivalent).

Both panels ship their own ``setting`` subcommand that writes a SQLite
config file in-place. We invoke them exactly as ``entrypoint.sh`` did and
add the same post-hooks (fail2ban start, sqlite3 subURI patch for S-UI).
"""

from __future__ import annotations

import logging
import os
import subprocess

logger = logging.getLogger(__name__)


def _flag_enabled(name: str) -> bool:
    """True unless ``os.environ[name]`` is literally ``"false"`` (case-insensitive)."""
    return os.environ.get(name, "true").strip().lower() != "false"


def _run(cmd: list[str], *, capture: bool = True) -> int:
    """Quiet ``subprocess.run`` that mirrors ``>/dev/null`` in bash."""
    stdout = subprocess.DEVNULL if capture else None
    stderr = subprocess.DEVNULL if capture else None
    return subprocess.run(cmd, check=False, stdout=stdout, stderr=stderr).returncode


def init_xui() -> bool:
    """Initialise X-UI. Returns True iff the CLI was invoked.

    Required env: ``PUBLIC_USER``, ``PUBLIC_PASSWORD``, ``XUI_LOCAL_PORT``,
    ``XUI_WEBBASEPATH``.
    """
    if not _flag_enabled("ENABLE_XUI"):
        return False

    user = os.environ.get("PUBLIC_USER", "")
    password = os.environ.get("PUBLIC_PASSWORD", "")
    port = os.environ.get("XUI_LOCAL_PORT", "")
    base_path = os.environ.get("XUI_WEBBASEPATH", "")
    if not all([user, password, port, base_path]):
        logger.warning("X-UI 所需变量未就绪，跳过 setting")
        return False

    _run(
        [
            "x-ui",
            "setting",
            "-username",
            user,
            "-password",
            password,
            "-port",
            port,
            "-webBasePath",
            base_path,
        ]
    )
    # fail2ban lives inside the X-UI container and guards its login form.
    rc = _run(["fail2ban-client", "-x", "start"])
    if rc != 0:
        logger.warning("Fail2ban 启动失败")
    return True


# s-ui project removed — init_sui disabled
# def init_sui() -> bool: ...


def init_panels() -> None:
    """Run X-UI init with a shared info log banner."""
    if not _flag_enabled("ENABLE_XUI"):
        logger.info("ENABLE_XUI=false，X-UI 面板已禁用，跳过初始化")
        return

    logger.info("初始化 X-UI")
    init_xui()
