"""Root crontab install (entrypoint.sh:main_init step 14 equivalent).

Manages three periodic entries:

1. **geo-update** (daily 03:00) — refresh GeoIP / GeoSite rule-sets.
2. **isp-retest** (every N hours, driven by ``ISP_RETEST_INTERVAL_HOURS``,
   0 disables) — re-measure ISP bandwidth and hot-reconfigure the
   balancer if composition or top-1 tag changed.
3. **substore-check** (daily, driven by ``SUBSTORE_CHECK_CRON``, default
   ``30 4 * * *``, empty disables) — produce every remote Sub-Store
   subscription and alert if any fails to fetch.

All entries are installed idempotently: each rewrite strips prior
copies before appending, so upgrading a running container converges
to the current shape without manual ``crontab -e``.
"""

from __future__ import annotations

import hashlib
import logging
import os
import socket
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_CRON = Path("/var/spool/cron/crontabs/root")
_GEO_ENTRY = "0 3 * * * /scripts/entrypoint.py geo-update >> /var/log/geo_update.log 2>&1"
_GEO_MARKER = "geo-update"
_ISP_MARKER = "isp-retest"
_SUBSTORE_MARKER = "substore-check"
_SUBSTORE_DEFAULT_CRON = "30 4 * * *"  # daily; SUBSTORE_CHECK_CRON="" disables
_SECRET_MARKER = "secrets-refresh"


def _hours_to_cron_spec(hours: int, minute: int = 0) -> str:
    """Map an hours-between-runs into the minute+hour cron fields.

    - 24 mod hours == 0 → use ``*/hours`` (handles 1/2/3/4/6/8/12/24)
    - otherwise → emit an explicit comma-separated hour list from 0
      repeated every ``hours``, stopping before 24. E.g. hours=5 →
      ``0 0,5,10,15,20 * * *`` (the last interval wraps to midnight).

    ``minute`` sets the minute field (default 0) — see :func:`_jitter_minute`.
    """
    if hours <= 0:
        raise ValueError(f"hours must be > 0, got {hours}")
    hours = min(hours, 24)
    if 24 % hours == 0:
        return f"{minute} */{hours} * * *"
    slots = list(range(0, 24, hours))
    return f"{minute} {','.join(str(h) for h in slots)} * * *"


def _jitter_minute() -> int:
    """Per-node deterministic retest minute in ``[0, 59]``.

    Every node shares the same upstream ISP proxies, so a fixed ``0 */Nh``
    schedule makes the whole fleet probe them in the same second — a
    self-inflicted thundering herd that depresses every node's reading at
    once and fires a fleet-wide "sluggish" false alarm. Hashing the hostname
    spreads the fleet across the hour deterministically (no persistence, no
    collisions to track). ``ISP_RETEST_JITTER=false`` restores minute 0 for
    debugging / single-node deployments.
    """
    if os.environ.get("ISP_RETEST_JITTER", "true").strip().lower() == "false":
        return 0
    host = socket.gethostname() or "sb-xray"
    digest = hashlib.sha1(host.encode("utf-8")).hexdigest()
    return int(digest, 16) % 60


def _isp_retest_entry(hours: int) -> str | None:
    if hours <= 0:
        return None
    spec = _hours_to_cron_spec(hours, _jitter_minute())
    return f"{spec} /scripts/entrypoint.py isp-retest >> /var/log/isp_retest.log 2>&1"


def _substore_check_entry() -> str | None:
    """Daily Sub-Store fetch health check; ``SUBSTORE_CHECK_CRON=""`` disables.

    The env value, when set, is a full cron spec (5 fields); unset falls
    back to the daily default.
    """
    raw = os.environ.get("SUBSTORE_CHECK_CRON")
    spec = _SUBSTORE_DEFAULT_CRON if raw is None else raw.strip()
    if not spec:
        return None
    return f"{spec} /scripts/entrypoint.py substore-check >> /var/log/substore_check.log 2>&1"


def _read_hours_env() -> int:
    raw = os.environ.get("ISP_RETEST_INTERVAL_HOURS", "").strip()
    if not raw:
        return 6  # default cadence
    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning(
            "invalid ISP_RETEST_INTERVAL_HOURS=%r — disabling cron retest",
            raw,
        )
        return 0


def _secret_refresh_entry(hours: int) -> str | None:
    """Cron line that re-decrypts ``tmp.bin`` and hot-reloads on change.

    Hourly by default so a rotated secret reaches a long-running container
    within the interval, decoupled from image-release cadence (watchtower only
    recreates on a new image). Shares :func:`_jitter_minute` fleet spread.
    """
    if hours <= 0:
        return None
    spec = _hours_to_cron_spec(hours, _jitter_minute())
    return f"{spec} /scripts/entrypoint.py secrets-refresh >> /var/log/secret_refresh.log 2>&1"


def _read_secret_hours_env() -> int:
    raw = os.environ.get("SECRET_REFRESH_INTERVAL_HOURS", "").strip()
    if not raw:
        return 1  # default cadence: hourly upstream poll
    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning(
            "invalid SECRET_REFRESH_INTERVAL_HOURS=%r — disabling cron secret refresh",
            raw,
        )
        return 0


def install_crontab(
    *,
    cron_file: Path = _DEFAULT_CRON,
    geo_entry: str = _GEO_ENTRY,
    isp_hours: int | None = None,
    secret_hours: int | None = None,
) -> None:
    """Ensure the periodic crontab entries exist, idempotently.

    Drops any prior ``geo_update.sh`` / ``geo-update`` / ``isp-retest``
    lines before re-appending the current entries, so migrating
    installations upgrade cleanly. Setting ``ISP_RETEST_INTERVAL_HOURS=0``
    (or passing ``isp_hours=0``) removes the isp-retest entry.
    """
    hours = _read_hours_env() if isp_hours is None else isp_hours
    isp_entry = _isp_retest_entry(hours)
    substore_entry = _substore_check_entry()
    secret_hours_val = _read_secret_hours_env() if secret_hours is None else secret_hours
    secret_entry = _secret_refresh_entry(secret_hours_val)

    cron_file.parent.mkdir(parents=True, exist_ok=True)
    existing = cron_file.read_text(encoding="utf-8") if cron_file.is_file() else ""
    lines = [
        ln
        for ln in existing.splitlines()
        if _GEO_MARKER not in ln
        and "geo_update.sh" not in ln
        and _ISP_MARKER not in ln
        and _SUBSTORE_MARKER not in ln
        and _SECRET_MARKER not in ln
    ]
    lines.append(geo_entry)
    if isp_entry is not None:
        lines.append(isp_entry)
    if substore_entry is not None:
        lines.append(substore_entry)
    if secret_entry is not None:
        lines.append(secret_entry)
    cleaned = "\n".join(lines).rstrip() + "\n"
    cron_file.write_text(cleaned, encoding="utf-8")
    cron_file.chmod(0o600)
    isp_desc = f"isp-retest every {hours}h" if isp_entry is not None else "isp-retest disabled"
    secret_desc = (
        f"secrets-refresh every {secret_hours_val}h"
        if secret_entry is not None
        else "secrets-refresh disabled"
    )
    logger.info(
        "Cron 定时任务已安装 (geo-update daily 03:00; %s; %s)",
        isp_desc,
        secret_desc,
    )
