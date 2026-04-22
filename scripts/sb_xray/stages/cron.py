"""Root crontab install (entrypoint.sh:main_init step 14 equivalent).

Manages two periodic entries:

1. **geo-update** (daily 03:00) — refresh GeoIP / GeoSite rule-sets.
2. **isp-retest** (every N hours, driven by ``ISP_RETEST_INTERVAL_HOURS``,
   0 disables) — re-measure ISP bandwidth and hot-reconfigure the
   balancer if composition or top-1 tag changed.

Both entries are installed idempotently: each rewrite strips prior
copies before appending, so upgrading a running container converges
to the current shape without manual ``crontab -e``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_CRON = Path("/var/spool/cron/crontabs/root")
_GEO_ENTRY = "0 3 * * * /scripts/entrypoint.py geo-update >> /var/log/geo_update.log 2>&1"
_GEO_MARKER = "geo-update"
_ISP_MARKER = "isp-retest"


def _hours_to_cron_spec(hours: int) -> str:
    """Map an hours-between-runs into the minute+hour cron fields.

    - 24 mod hours == 0 → use ``*/hours`` (handles 1/2/3/4/6/8/12/24)
    - otherwise → emit an explicit comma-separated hour list from 0
      repeated every ``hours``, stopping before 24. E.g. hours=5 →
      ``0 0,5,10,15,20 * * *`` (the last interval wraps to midnight).
    """
    if hours <= 0:
        raise ValueError(f"hours must be > 0, got {hours}")
    hours = min(hours, 24)
    if 24 % hours == 0:
        return f"0 */{hours} * * *"
    slots = list(range(0, 24, hours))
    return f"0 {','.join(str(h) for h in slots)} * * *"


def _isp_retest_entry(hours: int) -> str | None:
    if hours <= 0:
        return None
    spec = _hours_to_cron_spec(hours)
    return f"{spec} /scripts/entrypoint.py isp-retest >> /var/log/isp_retest.log 2>&1"


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


def install_crontab(
    *,
    cron_file: Path = _DEFAULT_CRON,
    geo_entry: str = _GEO_ENTRY,
    isp_hours: int | None = None,
) -> None:
    """Ensure the periodic crontab entries exist, idempotently.

    Drops any prior ``geo_update.sh`` / ``geo-update`` / ``isp-retest``
    lines before re-appending the current entries, so migrating
    installations upgrade cleanly. Setting ``ISP_RETEST_INTERVAL_HOURS=0``
    (or passing ``isp_hours=0``) removes the isp-retest entry.
    """
    hours = _read_hours_env() if isp_hours is None else isp_hours
    isp_entry = _isp_retest_entry(hours)

    cron_file.parent.mkdir(parents=True, exist_ok=True)
    existing = cron_file.read_text(encoding="utf-8") if cron_file.is_file() else ""
    lines = [
        ln
        for ln in existing.splitlines()
        if _GEO_MARKER not in ln and "geo_update.sh" not in ln and _ISP_MARKER not in ln
    ]
    lines.append(geo_entry)
    if isp_entry is not None:
        lines.append(isp_entry)
    cleaned = "\n".join(lines).rstrip() + "\n"
    cron_file.write_text(cleaned, encoding="utf-8")
    cron_file.chmod(0o600)
    if isp_entry is not None:
        logger.info(
            "Cron 定时任务已安装 (geo-update daily 03:00; isp-retest every %dh)",
            hours,
        )
    else:
        logger.info("Cron 定时任务已安装 (geo-update daily 03:00; isp-retest disabled)")
