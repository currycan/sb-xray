"""Secret refresh orchestrator - the ``secrets-refresh`` cron entrypoint.

Invoked by ``/scripts/entrypoint.py secrets-refresh`` on the cron schedule
installed by :mod:`sb_xray.stages.cron`. Re-fetches and decrypts the remote
``tmp.bin``; when the decrypted credentials actually changed it overrides the
boot-frozen ISP node env vars, re-measures + re-renders the xray / sing-box
configs and restarts both daemons - so a rotated secret reaches a long-running
container without a manual ``.envs/secret`` wipe + container recreate.

Emits one of three structured events:

- ``secret.refresh.completed`` - credentials changed, configs re-rendered, daemons reloaded
- ``secret.refresh.noop``      - upstream identical / offline / disabled; nothing changed
- ``secret.refresh.error``     - fetch/decrypt or reload raised; daemons untouched
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from sb_xray.events import emit_event
from sb_xray.secrets import parse_env_file, refresh_remote_secrets
from sb_xray.stages.reload_util import reload_nginx, restart_daemons, restore_media_routing

logger = logging.getLogger(__name__)

_DEFAULT_SECRET_FILE = Path("/.env/secret")


def _secret_file() -> Path:
    return Path(os.environ.get("SECRET_FILE", str(_DEFAULT_SECRET_FILE)))


def _enabled() -> bool:
    return os.environ.get("SECRET_REFRESH_ENABLED", "true").strip().lower() != "false"


def _apply_env(changed: frozenset[str], removed: frozenset[str], secret_file: Path) -> None:
    """Force the refreshed secret's values into ``os.environ``.

    ``entrypoint._load_env_file`` is setdefault - a key already present in the
    boot-frozen env is never overwritten, so re-sourcing alone would leave the
    stale credentials in place. Override the changed keys and drop the removed
    ones so the subsequent config render reads the rotated values.
    """
    if not changed and not removed:
        return
    new_vars = parse_env_file(secret_file)
    for key in changed:
        if key in new_vars:
            os.environ[key] = new_vars[key]
    for key in removed:
        os.environ.pop(key, None)


def run() -> int:
    """Execute a single secret-refresh cycle - the cron entrypoint."""
    secret_file = _secret_file()

    if not _enabled():
        logger.info("secrets-refresh: disabled via SECRET_REFRESH_ENABLED=false")
        emit_event("secret.refresh.noop", {"reason": "disabled"})
        return 0

    try:
        result = refresh_remote_secrets(secret_file=secret_file)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("secrets-refresh: fetch/decrypt failed")
        emit_event("secret.refresh.error", {"error": repr(exc), "stage": "decrypt"})
        return 1

    if not result.content_changed:
        logger.info("secrets-refresh: noop (status=%s)", result.status.value)
        emit_event("secret.refresh.noop", {"reason": result.status.value})
        return 0

    # Credentials changed - override the boot-frozen env, re-measure (so an
    # added node joins the balancer and a removed one is dropped), re-render the
    # daemon configs and restart so the rotation actually takes effect.
    try:
        from sb_xray.config_builder import create_config
        from sb_xray.routing.isp import build_client_and_server_configs
        from sb_xray.speed_test import run_isp_speed_tests

        _apply_env(result.changed_keys, result.removed_keys, secret_file)
        run_isp_speed_tests(force=True, suppress_result_push=True)
        restore_media_routing()
        build_client_and_server_configs()
        create_config()
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("secrets-refresh: reconfigure failed")
        emit_event("secret.refresh.error", {"error": repr(exc), "stage": "reconfigure"})
        return 1

    restarted = restart_daemons()
    reload_nginx()
    payload: dict[str, object] = {
        "status": result.status.value,
        "changed": len(result.changed_keys),
        "removed": len(result.removed_keys),
        "restarted": restarted,
    }
    emit_event("secret.refresh.completed", payload)
    logger.info(
        "secrets-refresh: completed (status=%s changed=%d removed=%d restarted=%s)",
        result.status.value,
        len(result.changed_keys),
        len(result.removed_keys),
        restarted,
    )
    return 0
