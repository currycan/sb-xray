"""Root crontab install (entrypoint.sh:main_init step 14 equivalent)."""

from __future__ import annotations

from pathlib import Path

from sb_xray import logging as sblog

_DEFAULT_CRON = Path("/var/spool/cron/crontabs/root")
_GEO_ENTRY = "0 3 * * * /scripts/geo_update.sh >> /var/log/geo_update.log 2>&1"


def install_crontab(
    *,
    cron_file: Path = _DEFAULT_CRON,
    geo_entry: str = _GEO_ENTRY,
) -> None:
    """Ensure the daily geo_update crontab entry exists, idempotently."""
    cron_file.parent.mkdir(parents=True, exist_ok=True)
    existing = cron_file.read_text(encoding="utf-8") if cron_file.is_file() else ""
    lines = [ln for ln in existing.splitlines() if "geo_update.sh" not in ln]
    lines.append(geo_entry)
    cleaned = "\n".join(lines).rstrip() + "\n"
    cron_file.write_text(cleaned, encoding="utf-8")
    cron_file.chmod(0o600)
    sblog.log("INFO", "[步骤 14] Cron 定时任务已安装 (geo_update daily 03:00)")
