"""ACME certificate issuance / renewal (entrypoint.sh §12 equivalent).

Thin subprocess wrapper around ``openssl x509 -checkend`` and
``acme.sh``. Raises on misconfiguration (missing required env vars or
Google CA without EAB key) so the caller can surface the problem
clearly instead of silently producing a broken config.
"""

from __future__ import annotations

import enum
import os
import subprocess
from pathlib import Path
from typing import Final

_VALID_WINDOW_SECONDS: Final[int] = 7 * 24 * 3600  # 7 days

_REQUIRED_ENV: Final[tuple[str, ...]] = (
    "ACMESH_REGISTER_EMAIL",
    "ACMESH_SERVER_NAME",
    "ALI_KEY",
    "ALI_SECRET",
    "CF_TOKEN",
    "CF_ZONE_ID",
    "CF_ACCOUNT_ID",
)


class CertStatus(enum.Enum):
    SKIPPED = "skipped"
    ISSUED = "issued"
    INSTALLED = "installed"


def _check_required_env() -> None:
    missing = [v for v in _REQUIRED_ENV if not os.environ.get(v)]
    if missing:
        raise RuntimeError(f"required environment variables missing: {', '.join(missing)}")


def _cert_is_valid(cert_path: Path) -> bool:
    """Return True if the cert has > 7 days of validity remaining."""
    if not cert_path.is_file():
        return False
    result = subprocess.run(
        [
            "openssl",
            "x509",
            "-checkend",
            str(_VALID_WINDOW_SECONDS),
            "-noout",
            "-in",
            str(cert_path),
        ],
        check=False,
        capture_output=True,
    )
    return result.returncode == 0


def _acme_env() -> dict[str, str]:
    """Return env with ``LOG_LEVEL`` stripped.

    Dockerfile sets ``LOG_LEVEL=warning`` for xray/sing-box (string).
    acme.sh's internal ``LOG_LEVEL`` is numeric (1/2/3) and ends up in
    ``[ "$LOG_LEVEL" -ge "$LOG_LEVEL_1" ]`` — the string collision triggers
    ``integer expected`` warnings (L347/381/414 of acme.sh). Dropping the
    var in the subprocess env lets acme.sh fall back to its numeric default.
    """
    env = os.environ.copy()
    env.pop("LOG_LEVEL", None)
    return env


def _parse_params(params: str) -> list[tuple[str, str]]:
    """Translate ``"d1:p1|d2:p2"`` → ``[("d1","p1"), ("d2","p2")]``."""
    entries: list[tuple[str, str]] = []
    for raw in params.split("|"):
        if ":" not in raw:
            continue
        dom, _, prov = raw.partition(":")
        entries.append((dom.strip(), prov.strip()))
    return entries


def _build_issue_args(params: str, server: str) -> list[str]:
    args = ["--issue", "--ecc", "--server", server]
    for domain, provider in _parse_params(params):
        args += ["-d", domain, "--dns", f"dns_{provider}"]
        if not all(ch.isdigit() or ch == "." for ch in domain):
            args += ["-d", f"*.{domain}", "--dns", f"dns_{provider}"]
    return args


def _register_account() -> None:
    server = os.environ["ACMESH_SERVER_NAME"]
    email = os.environ["ACMESH_REGISTER_EMAIL"]
    args = ["acme.sh", "--register-account", "-m", email, "--server", server]

    if server == "google":
        kid = os.environ.get("ACMESH_EAB_KID", "")
        hmac = os.environ.get("ACMESH_EAB_HMAC_KEY", "")
        if not kid or not hmac:
            raise RuntimeError("Google CA requires ACMESH_EAB_KID and ACMESH_EAB_HMAC_KEY")
        args += ["--eab-kid", kid, "--eab-hmac-key", hmac]
    elif os.environ.get("ACMESH_EAB_KID") and os.environ.get("ACMESH_EAB_HMAC_KEY"):
        args += [
            "--eab-kid",
            os.environ["ACMESH_EAB_KID"],
            "--eab-hmac-key",
            os.environ["ACMESH_EAB_HMAC_KEY"],
        ]
    subprocess.run(args, check=False, env=_acme_env())


def _acme_already_has(first_domain: str) -> bool:
    result = subprocess.run(
        ["acme.sh", "--list"],
        check=False,
        capture_output=True,
        text=True,
        env=_acme_env(),
    )
    return first_domain in (result.stdout or "")


def ensure_certificate(
    *,
    name: str,
    params: str,
    ssl_path: Path = Path("/ssl"),
) -> CertStatus:
    """Ensure a valid ACME certificate for ``name`` exists under ``ssl_path``.

    Behavior mirrors entrypoint.sh ``issueCertificate``:
      - Skip if an existing cert is valid for more than 7 days.
      - Otherwise register (if needed), issue, and install the cert
        via acme.sh, writing ``{name}.crt`` / ``.key`` / ``-ca.crt``.
    """
    entries = _parse_params(params)
    if not entries:
        raise ValueError(f"invalid params (no domains found): {params!r}")
    first_domain, _ = entries[0]

    cert_path = ssl_path / f"{name}.crt"
    key_path = ssl_path / f"{name}.key"
    ca_path = ssl_path / f"{name}-ca.crt"

    if (
        cert_path.is_file()
        and key_path.is_file()
        and ca_path.is_file()
        and _cert_is_valid(cert_path)
    ):
        return CertStatus.SKIPPED

    _check_required_env()
    server = os.environ["ACMESH_SERVER_NAME"]

    if not _acme_already_has(first_domain):
        _register_account()
        subprocess.run(
            ["acme.sh", *_build_issue_args(params, server)],
            check=False,
            env=_acme_env(),
        )

    ssl_path.mkdir(parents=True, exist_ok=True)
    # 证书安装前清空 nginx 动态配置目录 (entrypoint.sh:899 等价)。
    # acme.sh --install-cert 会用 --reloadcmd /usr/sbin/nginx 启动 nginx；
    # 若上一轮残留的 conf.d/ 或 stream.d/ 里有过期 upstream 引用，nginx 会
    # 加载失败或拉起 orphan worker。createConfig 阶段稍后会重新渲染模板。
    for d in (Path("/etc/nginx/conf.d"), Path("/etc/nginx/stream.d")):
        if d.is_dir():
            for item in d.iterdir():
                if item.is_file():
                    item.unlink()

    subprocess.run(
        [
            "acme.sh",
            "--install-cert",
            "--ecc",
            "-d",
            first_domain,
            "--key-file",
            str(key_path),
            "--fullchain-file",
            str(cert_path),
            "--ca-file",
            str(ca_path),
            "--reloadcmd",
            "/usr/sbin/nginx",
        ],
        check=False,
        env=_acme_env(),
    )
    # acme.sh --reloadcmd 拉起了一个独立的 nginx 进程，但服务生命周期实际由
    # supervisord 管理。优雅关闭它并清掉 PID 文件，后续 supervisord 才能干净
    # fork 自己的 nginx（entrypoint.sh:903 等价）。
    _quit_rc = subprocess.run(
        ["/usr/sbin/nginx", "-s", "quit"],
        check=False,
        capture_output=True,
    ).returncode
    if _quit_rc == 0:
        pid_file = Path("/var/run/nginx/nginx.pid")
        if pid_file.is_file():
            pid_file.unlink()
    return CertStatus.INSTALLED
