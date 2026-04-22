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
import sys
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
    """Env for every acme.sh subprocess call.

    Two transforms on top of ``os.environ``:

    1. Strip ``LOG_LEVEL``. Dockerfile sets it to the string
       ``"warning"`` for xray/sing-box, but acme.sh reads it as a
       numeric (1/2/3) inside ``[ "$LOG_LEVEL" -ge "$LOG_LEVEL_1" ]``;
       the type mismatch emits ``integer expected`` warnings
       (acme.sh L347/381/414). Dropping it lets acme.sh fall back to
       its own numeric default.

    2. Translate uppercase SECRET_FILE convention to the mixed-case
       names the acme.sh DNS plugins expect:

           ALI_KEY       → Ali_Key
           ALI_SECRET    → Ali_Secret
           CF_TOKEN      → CF_Token
           CF_ZONE_ID    → CF_Zone_ID
           CF_ACCOUNT_ID → CF_Account_ID

       Bash entrypoint.sh did this manually inside issueCertificate
       right before the --issue call. Skipping the translation in the
       Python port caused ``dns_ali`` to log
       "You don't specify aliyun api key and secret yet."
       and acme.sh exited 1 — the exact cn2 prod failure.
    """
    env = os.environ.copy()
    env.pop("LOG_LEVEL", None)
    _ACME_DNS_ALIASES = {
        "ALI_KEY": "Ali_Key",
        "ALI_SECRET": "Ali_Secret",
        "CF_TOKEN": "CF_Token",
        "CF_ZONE_ID": "CF_Zone_ID",
        "CF_ACCOUNT_ID": "CF_Account_ID",
    }
    for src, dst in _ACME_DNS_ALIASES.items():
        value = env.get(src)
        if value and not env.get(dst):
            env[dst] = value
    return env


def _issue_failure_hint(output: str, *, server: str, first_domain: str) -> str:
    """Translate common acme.sh --issue failure patterns to actionable hints.

    The raw "exited N" message is almost never enough: operators want
    to know WHY and WHAT TO DO. This helper scans acme.sh's own
    stdout+stderr for known failure signatures and emits a
    single-line operator-directed hint.
    """
    lower = output.lower()
    if "retryafter=" in lower or "rate limit" in lower or "too many" in lower:
        return (
            f"CA '{server}' rate-limited this account (retry-after / rate-limit "
            "hit — often from accumulated pending orders after earlier failed "
            "attempts). Remediation: wait 24h OR switch to a wildcard-capable "
            "CA by setting ACMESH_SERVER_NAME=letsencrypt (or google, both "
            "support wildcard via DNS-01). Do NOT switch to buypass — it "
            "does not sign wildcard certs."
        )
    if "you don't specify" in lower or ("dns_ali.sh" in lower and "error" in lower):
        return (
            "DNS plugin rejected the credentials. Verify "
            "ALI_KEY/ALI_SECRET (for aliyun) and CF_TOKEN/CF_ZONE_ID/"
            "CF_ACCOUNT_ID (for cloudflare) in SECRET_FILE."
        )
    if "dns problem" in lower or "nxdomain" in lower:
        return (
            f"DNS lookup of {first_domain} or its TXT challenge failed. "
            "Check public DNS propagation and zone delegation."
        )
    if "account" in lower and ("quota" in lower or "limit" in lower):
        return (
            f"CA '{server}' account-level quota hit. Wait or switch CA "
            "(ACMESH_SERVER_NAME=letsencrypt/google/buypass)."
        )
    return (
        "Check DNS credentials (ALI_KEY/ALI_SECRET, CF_TOKEN/CF_ZONE_ID/"
        "CF_ACCOUNT_ID) and acme.sh output above."
    )


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
    ssl_path: Path | None = None,
) -> CertStatus:
    """Ensure a valid ACME certificate for ``name`` exists under ``ssl_path``.

    ``ssl_path`` defaults to ``$SSL_PATH`` (Dockerfile sets it to
    ``/pki`` — same path the nginx / xray / sing-box JSON templates
    render for ``certificate_path`` / ``ssl_certificate`` etc). Falls
    back to ``/pki`` when the env var is unset, matching Dockerfile.

    Behavior mirrors entrypoint.sh ``issueCertificate``:
      - Skip if an existing cert is valid for more than 7 days.
      - Otherwise register (if needed), issue, and install the cert
        via acme.sh, writing ``{name}.crt`` / ``.key`` / ``-ca.crt``.
    """
    if ssl_path is None:
        ssl_path = Path(os.environ.get("SSL_PATH", "/pki"))
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

    # Always register + issue. Register is idempotent ("Already registered"
    # logged then returns 0). Issue without --force is also idempotent:
    # acme.sh skips the CA roundtrip when its own store has a fresh
    # cert, but will re-fetch when the store is stale or missing.
    #
    # The previous 'if not _acme_already_has(first_domain)' guard was
    # brittle — acme.sh --list prints the Main_Domain column even when
    # the underlying ${LE_CONFIG_HOME}/<domain>_ecc/ca.cer is gone
    # (observed on cn2 after a failed past attempt: --list lied about
    # having cn2.ansandy.com, we skipped --issue, --install-cert then
    # errored with "cat: /acmecerts/cn2.ansandy.com_ecc/ca.cer: No such
    # file or directory" and silently "succeeded" with no files on
    # disk).
    _register_account()
    issue_result = subprocess.run(
        ["acme.sh", *_build_issue_args(params, server)],
        check=False,
        env=_acme_env(),
        capture_output=True,
        text=True,
    )
    # Stream acme.sh's output live so boot logs still show it; we only
    # capture to pattern-match for hints below.
    if issue_result.stdout:
        sys.stdout.write(issue_result.stdout)
    if issue_result.stderr:
        sys.stderr.write(issue_result.stderr)
    issue_rc = issue_result.returncode
    if issue_rc not in (0, 2):
        # 0 = issued now; 2 = already valid, skip (acme.sh convention).
        hint = _issue_failure_hint(
            issue_result.stdout + issue_result.stderr,
            server=server,
            first_domain=first_domain,
        )
        raise RuntimeError(f"acme.sh --issue exited {issue_rc} for {first_domain}. {hint}")

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

    install_rc = subprocess.run(
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
    ).returncode
    if install_rc != 0:
        raise RuntimeError(f"acme.sh --install-cert exited {install_rc} for {first_domain}")
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
