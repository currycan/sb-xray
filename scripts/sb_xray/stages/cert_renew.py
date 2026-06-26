"""TLS 证书续签 - the ``cert-renew`` cron entrypoint.

Invoked by ``/scripts/entrypoint.py cert-renew`` on the daily (jittered) cron
schedule installed by :mod:`sb_xray.stages.cron`. A fresh cron process lacks
the ACME credentials (they live in SECRET_FILE, not in the boot-frozen env of
the long-running PID-1), so we load them first, then delegate to
:func:`sb_xray.cert.ensure_certificate` — which itself skips when the bundle
still has >7d validity and, on actual renewal, reloads nginx via acme.sh's
``--install-cert --reloadcmd``. Image-default behaviour (§2b): the cron entry
is registered unconditionally; no new compose env is required to renew.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from sb_xray.cert import ensure_certificate
from sb_xray.events import emit_event
from sb_xray.secrets import parse_env_file

logger = logging.getLogger(__name__)

_DEFAULT_SECRET_FILE = Path("/.env/secret")


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

    logger.info("cert-renew: completed (status=%s)", status.value)
    emit_event("cert.renew.completed", {"status": status.value})
    return 0
