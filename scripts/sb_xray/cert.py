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
       and acme.sh exited 1 — the exact production failure.
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


def _bundle_paths(ssl_path: Path, name: str) -> tuple[Path, Path, Path]:
    """Standard acme.sh --install-cert output trio for ``name``."""
    return (
        ssl_path / f"{name}.crt",
        ssl_path / f"{name}.key",
        ssl_path / f"{name}-ca.crt",
    )


def _existing_bundle_is_fresh(cert_path: Path, key_path: Path, ca_path: Path) -> bool:
    """All three files present + cert valid for > 7 days."""
    return (
        cert_path.is_file()
        and key_path.is_file()
        and ca_path.is_file()
        and _cert_is_valid(cert_path)
    )


def _issue_with_acme(params: str, *, first_domain: str, server: str) -> None:
    """Run ``acme.sh --issue`` (register + issue, idempotent).

    Register is idempotent; issue without ``--force`` is also idempotent
    (acme.sh skips the CA roundtrip when its own store holds a fresh
    cert, but re-fetches when stale/missing). The previous
    ``_acme_already_has`` guard was brittle — acme.sh --list would
    report a domain present even after ``${LE_CONFIG_HOME}/<d>_ecc/``
    was wiped, causing a silent empty install.
    """
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


def _purge_nginx_dynamic_dirs() -> None:
    """Wipe ``/etc/nginx/{conf.d,stream.d}`` pre-install (entrypoint.sh:899).

    ``acme.sh --install-cert --reloadcmd /usr/sbin/nginx`` starts nginx
    with whatever's in those dirs. If a previous boot left stale
    upstream references nginx will abort or spawn orphan workers.
    ``createConfig`` stage re-renders the templates afterwards.
    """
    for d in (Path("/etc/nginx/conf.d"), Path("/etc/nginx/stream.d")):
        if d.is_dir():
            for item in d.iterdir():
                if item.is_file():
                    item.unlink()


def _install_and_cleanup(
    *,
    first_domain: str,
    cert_path: Path,
    key_path: Path,
    ca_path: Path,
) -> None:
    """Install the cert bundle + stop the short-lived reloadcmd nginx."""
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

    # acme.sh --reloadcmd spawned a standalone nginx but the service
    # lifecycle is owned by supervisord. Gracefully stop that copy and
    # drop its PID file so supervisord can fork cleanly (entrypoint.sh:903).
    quit_rc = subprocess.run(
        ["/usr/sbin/nginx", "-s", "quit"],
        check=False,
        capture_output=True,
    ).returncode
    if quit_rc == 0:
        pid_file = Path("/var/run/nginx/nginx.pid")
        if pid_file.is_file():
            pid_file.unlink()


def ensure_certificate(
    *,
    name: str,
    params: str,
    ssl_path: Path | None = None,
) -> CertStatus:
    """Ensure a valid ACME certificate for ``name`` exists under ``ssl_path``.

    Orchestrator — delegates to 4 single-purpose helpers:

      * :func:`_existing_bundle_is_fresh` — 7-day validity check.
      * :func:`_issue_with_acme` — register + ``--issue`` with DNS plugin.
      * :func:`_purge_nginx_dynamic_dirs` — pre-install cleanup.
      * :func:`_install_and_cleanup` — install cert + stop reloadcmd nginx.

    ``ssl_path`` defaults to ``$SSL_PATH`` (Dockerfile sets it to
    ``/pki`` — same path the nginx / xray / sing-box JSON templates
    render). Behavior mirrors entrypoint.sh ``issueCertificate``:
    skip when fresh, otherwise register+issue+install via acme.sh.
    """
    if ssl_path is None:
        ssl_path = Path(os.environ.get("SSL_PATH", "/pki"))
    entries = _parse_params(params)
    if not entries:
        raise ValueError(f"invalid params (no domains found): {params!r}")
    first_domain, _ = entries[0]

    cert_path, key_path, ca_path = _bundle_paths(ssl_path, name)

    if _existing_bundle_is_fresh(cert_path, key_path, ca_path):
        return CertStatus.SKIPPED

    _check_required_env()
    server = os.environ["ACMESH_SERVER_NAME"]

    _issue_with_acme(params, first_domain=first_domain, server=server)

    ssl_path.mkdir(parents=True, exist_ok=True)
    _purge_nginx_dynamic_dirs()
    _install_and_cleanup(
        first_domain=first_domain,
        cert_path=cert_path,
        key_path=key_path,
        ca_path=ca_path,
    )
    return CertStatus.INSTALLED
