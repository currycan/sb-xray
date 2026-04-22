"""Tests for scripts/sb_xray/stages/cron.py."""

from __future__ import annotations

from pathlib import Path

import pytest
from sb_xray.stages import cron as sbcron


def test_installs_entry_from_empty(tmp_path: Path) -> None:
    target = tmp_path / "crontab"
    sbcron.install_crontab(cron_file=target)
    content = target.read_text(encoding="utf-8")
    assert "entrypoint.py geo-update" in content
    assert content.count("geo-update") == 1
    assert oct(target.stat().st_mode)[-3:] == "600"


def test_migrates_legacy_shell_entry(tmp_path: Path) -> None:
    """旧部署里的 ``/scripts/geo_update.sh`` 行应被清掉,替换为新入口。"""
    target = tmp_path / "crontab"
    target.write_text(
        "0 2 * * * /usr/bin/true\n"
        "0 3 * * * /scripts/geo_update.sh >> /var/log/geo_update.log 2>&1\n"
        "# custom\n",
        encoding="utf-8",
    )
    sbcron.install_crontab(cron_file=target)
    content = target.read_text(encoding="utf-8")
    assert "geo_update.sh" not in content
    assert content.count("entrypoint.py geo-update") == 1
    assert "/usr/bin/true" in content
    assert "# custom" in content


def test_is_idempotent(tmp_path: Path) -> None:
    target = tmp_path / "crontab"
    sbcron.install_crontab(cron_file=target)
    sbcron.install_crontab(cron_file=target)
    content = target.read_text(encoding="utf-8")
    assert content.count("geo-update") == 1


def test_custom_entry(tmp_path: Path) -> None:
    target = tmp_path / "crontab"
    sbcron.install_crontab(
        cron_file=target,
        geo_entry="*/15 * * * * /scripts/entrypoint.py geo-update",
    )
    assert "*/15 * * * * /scripts/entrypoint.py geo-update" in target.read_text(encoding="utf-8")


def test_installs_isp_retest_entry_default_6h(tmp_path: Path) -> None:
    target = tmp_path / "crontab"
    sbcron.install_crontab(cron_file=target, isp_hours=6)
    content = target.read_text(encoding="utf-8")
    assert "entrypoint.py isp-retest" in content
    assert "0 */6 * * * /scripts/entrypoint.py isp-retest" in content


def test_isp_retest_disabled_with_zero(tmp_path: Path) -> None:
    target = tmp_path / "crontab"
    sbcron.install_crontab(cron_file=target, isp_hours=0)
    content = target.read_text(encoding="utf-8")
    assert "isp-retest" not in content
    assert "geo-update" in content


def test_isp_retest_non_divisor_hours_uses_comma_spec(tmp_path: Path) -> None:
    target = tmp_path / "crontab"
    sbcron.install_crontab(cron_file=target, isp_hours=5)
    content = target.read_text(encoding="utf-8")
    assert "0 0,5,10,15,20 * * * /scripts/entrypoint.py isp-retest" in content


def test_hours_to_cron_spec_cases() -> None:
    assert sbcron._hours_to_cron_spec(6) == "0 */6 * * *"
    assert sbcron._hours_to_cron_spec(12) == "0 */12 * * *"
    assert sbcron._hours_to_cron_spec(24) == "0 */24 * * *"
    assert sbcron._hours_to_cron_spec(5) == "0 0,5,10,15,20 * * *"
    assert sbcron._hours_to_cron_spec(7) == "0 0,7,14,21 * * *"


def test_isp_retest_replaces_stale_entry(tmp_path: Path) -> None:
    target = tmp_path / "crontab"
    target.write_text(
        "0 */12 * * * /scripts/entrypoint.py isp-retest >> /var/log/isp_retest.log 2>&1\n",
        encoding="utf-8",
    )
    sbcron.install_crontab(cron_file=target, isp_hours=6)
    content = target.read_text(encoding="utf-8")
    assert content.count("isp-retest") == 1
    assert "0 */6 * * *" in content


def test_isp_retest_env_var_drives_interval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ISP_RETEST_INTERVAL_HOURS", "8")
    target = tmp_path / "crontab"
    sbcron.install_crontab(cron_file=target)
    content = target.read_text(encoding="utf-8")
    assert "0 */8 * * * /scripts/entrypoint.py isp-retest" in content


def test_isp_retest_env_var_invalid_disables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ISP_RETEST_INTERVAL_HOURS", "garbage")
    target = tmp_path / "crontab"
    sbcron.install_crontab(cron_file=target)
    content = target.read_text(encoding="utf-8")
    assert "isp-retest" not in content
