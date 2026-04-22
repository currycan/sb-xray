"""X-UI / S-UI panel bootstrap (entrypoint.sh:main_init step 12 equivalent).

Both panels ship their own ``setting`` subcommand that writes a SQLite
config file in-place. We invoke them exactly as ``entrypoint.sh`` did and
add the same post-hooks (fail2ban start, sqlite3 subURI patch for S-UI).
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

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


def init_sui() -> bool:
    """Initialise S-UI. Returns True iff the CLI was invoked.

    Required env: ``PUBLIC_USER``, ``PUBLIC_PASSWORD``, ``SUI_PORT``,
    ``SUI_SUB_PORT``, ``SUI_WEBBASEPATH``, ``SUI_SUB_PATH``, ``DOMAIN``.
    Optional: ``SUI_DB_FOLDER`` (default ``/opt/s-ui``).
    """
    if not _flag_enabled("ENABLE_SUI"):
        return False

    port = os.environ.get("SUI_PORT", "")
    sub_port = os.environ.get("SUI_SUB_PORT", "")
    base_path = os.environ.get("SUI_WEBBASEPATH", "")
    sub_path = os.environ.get("SUI_SUB_PATH", "")
    user = os.environ.get("PUBLIC_USER", "")
    password = os.environ.get("PUBLIC_PASSWORD", "")
    if not all([port, sub_port, base_path, sub_path, user, password]):
        logger.warning("S-UI 所需变量未就绪，跳过 setting")
        return False

    _run(
        [
            "sui",
            "setting",
            "-port",
            port,
            "-subPort",
            sub_port,
            "-path",
            f"/{base_path}",
            "-subPath",
            f"/{sub_path}",
        ]
    )
    _run(["sui", "admin", "-password", password, "-username", user])

    db_folder = Path(os.environ.get("SUI_DB_FOLDER", "/opt/s-ui"))
    db_file = db_folder / "s-ui.db"
    domain = os.environ.get("DOMAIN", "")
    if db_file.is_file() and domain:
        sub_uri = f"https://{domain}/{sub_path}/"
        _run(
            [
                "sqlite3",
                str(db_file),
                f"UPDATE settings SET value='{sub_uri}' WHERE key='subURI';",
            ]
        )
    return True


def init_panels() -> None:
    """Run X-UI + S-UI init with a shared info log banner."""
    xui_on = _flag_enabled("ENABLE_XUI")
    sui_on = _flag_enabled("ENABLE_SUI")
    if not xui_on and not sui_on:
        logger.info("ENABLE_XUI=ENABLE_SUI=false，两个面板均已禁用，跳过初始化")
        return

    parts: list[str] = []
    if xui_on:
        parts.append("X-UI")
    if sui_on:
        parts.append("S-UI")
    logger.info("初始化 %s", " / ".join(parts))
    init_xui()
    init_sui()
