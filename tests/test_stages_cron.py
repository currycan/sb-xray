"""Tests for scripts/sb_xray/stages/cron.py."""

from __future__ import annotations

from pathlib import Path

from sb_xray.stages import cron as sbcron


def test_installs_entry_from_empty(tmp_path: Path) -> None:
    target = tmp_path / "crontab"
    sbcron.install_crontab(cron_file=target)
    content = target.read_text(encoding="utf-8")
    assert "geo_update.sh" in content
    assert content.count("geo_update.sh") == 1
    assert oct(target.stat().st_mode)[-3:] == "600"


def test_is_idempotent(tmp_path: Path) -> None:
    target = tmp_path / "crontab"
    target.write_text(
        "0 2 * * * /usr/bin/true\n"
        "0 3 * * * /scripts/geo_update.sh >> /var/log/geo_update.log 2>&1\n"
        "# custom\n",
        encoding="utf-8",
    )
    sbcron.install_crontab(cron_file=target)
    content = target.read_text(encoding="utf-8")
    assert content.count("geo_update.sh") == 1
    assert "/usr/bin/true" in content
    assert "# custom" in content


def test_custom_entry(tmp_path: Path) -> None:
    target = tmp_path / "crontab"
    sbcron.install_crontab(
        cron_file=target,
        geo_entry="*/15 * * * * /scripts/geo_update.sh",
    )
    assert "*/15 * * * * /scripts/geo_update.sh" in target.read_text(encoding="utf-8")
