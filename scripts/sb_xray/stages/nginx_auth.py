"""Nginx Basic Auth htpasswd (entrypoint.sh:main_init step 13 equivalent).

``openssl passwd -apr1`` is the classic Apache $apr1$ MD5 crypt format that
every nginx install understands. We shell out to openssl rather than
implementing $apr1$ by hand to stay bit-identical to the Bash flow.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from sb_xray import logging as sblog

_DEFAULT_PATH = Path("/etc/nginx/.htpasswd")


def _apr1(password: str) -> str:
    result = subprocess.run(
        ["openssl", "passwd", "-apr1", password],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def setup_basic_auth(
    *,
    user: str | None = None,
    password: str | None = None,
    path: Path = _DEFAULT_PATH,
) -> bool:
    """Write ``${user}:${apr1(password)}`` to ``path``.

    When either credential is missing, logs a warning and returns False
    instead of raising — nginx can still serve unauthenticated routes.
    """
    user = user if user is not None else os.environ.get("PUBLIC_USER", "")
    password = password if password is not None else os.environ.get("PUBLIC_PASSWORD", "")
    if not user or not password:
        sblog.log("WARN", "[nginx-auth] PUBLIC_USER/PASSWORD 未设置，跳过 Basic Auth")
        return False

    encoded = _apr1(password)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{user}:{encoded}\n", encoding="utf-8")
    path.chmod(0o644)
    sblog.log("INFO", f"[nginx-auth] HTTP Basic Auth 已配置 (用户: {user})")
    return True
