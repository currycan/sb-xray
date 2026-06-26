"""TLS 证书续签 - the ``cert-renew`` cron entrypoint.

Invoked by ``/scripts/entrypoint.py cert-renew`` on the daily (jittered) cron
schedule installed by :mod:`sb_xray.stages.cron`. A fresh cron process lacks
the ACME credentials (they live in SECRET_FILE, not in the boot-frozen env of
the long-running PID-1), so we load them first, then delegate to
:func:`sb_xray.cert.ensure_certificate`.

On SKIPPED (cert still has >7d validity) nothing else happens.

On INSTALLED (cert was renewed and written to disk): ``ensure_certificate``
copies the new bundle files into ``$SSL_PATH`` via ``acme.sh --install-cert``.
The ``--reloadcmd /usr/sbin/nginx`` used during that call is a **boot-time**
mechanism only — it starts a short-lived standalone nginx (required by
acme.sh's protocol), which ``_install_and_cleanup`` immediately tears down.
It does NOT signal the supervisord-managed nginx master that is already
running. nginx caches cert file descriptors in worker memory at startup; to
serve the renewed cert without a container restart it needs ``nginx -s
reload`` (SIGHUP to the master), which we send explicitly here on the
INSTALLED path.

Image-default behaviour (§2b): the cron entry is registered
unconditionally; no new compose env is required to renew.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from sb_xray.cert import CertStatus, ensure_certificate
from sb_xray.events import emit_event
from sb_xray.secrets import parse_env_file

logger = logging.getLogger(__name__)

_DEFAULT_SECRET_FILE = Path("/.env/secret")

_NGINX_BIN = Path("/usr/sbin/nginx")


def _secret_file() -> Path:
    return Path(os.environ.get("SECRET_FILE", str(_DEFAULT_SECRET_FILE)))


def _load_secret_env(secret_file: Path) -> None:
    """Inject SECRET_FILE's ACME credentials into ``os.environ``.

    The cron process has no boot-frozen ACME env; ``ensure_certificate``'s
    ``_check_required_env`` would otherwise abort. We override unconditionally
    (the SECRET_FILE is the source of truth for credentials), mirroring
    secrets_refresh._apply_env. Missing file → no-op (ensure_certificate then
    surfaces the missing-env error cleanly).
    """
    if not secret_file.is_file():
        return
    for key, value in parse_env_file(secret_file).items():
        os.environ[key] = value


def _reload_nginx() -> bool:
    """Send ``nginx -s reload`` (SIGHUP) to the supervisord-managed master.

    Returns True on success, False on failure (non-zero exit or missing
    binary). The caller logs and continues regardless — a reload failure is
    not fatal to the renewal itself; the cert files on disk are already
    correct and the next container restart will pick them up.
    """
    if not _NGINX_BIN.exists():
        logger.warning("cert-renew: nginx binary not found at %s; skipping reload", _NGINX_BIN)
        return False
    rc = subprocess.run(
        [str(_NGINX_BIN), "-s", "reload"],
        check=False,
        capture_output=True,
    ).returncode
    if rc != 0:
        logger.warning("cert-renew: nginx -s reload exited %d; new cert will be served after next restart", rc)
        return False
    logger.info("cert-renew: nginx reloaded — renewed cert now served without restart")
    return True


def run() -> int:
    """Execute a single cert-renew cycle - the cron entrypoint."""
    domain = os.environ.get("DOMAIN", "")
    cdn = os.environ.get("CDNDOMAIN", "")
    if not domain or not cdn:
        logger.error("cert-renew: DOMAIN/CDNDOMAIN 未设置,跳过续签")
        emit_event("cert.renew.error", {"reason": "domain_unset"})
        return 1

    _load_secret_env(_secret_file())
    params = f"{domain}:ali|{cdn}:cf"
    try:
        status = ensure_certificate(name="sb_xray_bundle", params=params)
    except Exception as exc:
        logger.exception("cert-renew: ensure_certificate 失败")
        emit_event("cert.renew.error", {"error": repr(exc)})
        return 1

    if status is CertStatus.INSTALLED:
        _reload_nginx()

    logger.info("cert-renew: completed (status=%s)", status.value)
    emit_event("cert.renew.completed", {"status": status.value})
    return 0
