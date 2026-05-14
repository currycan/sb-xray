"""Tests for scripts/sb_xray/stages/panels.py."""

from __future__ import annotations

from pathlib import Path

import pytest
from sb_xray.stages import panels as sbpanels


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset panel-control flags so leaks from earlier tests don't affect init_xui."""
    monkeypatch.delenv("ENABLE_XUI", raising=False)
    # monkeypatch.delenv("ENABLE_SUI", raising=False)  # s-ui removed


def _common_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBLIC_USER", "alice")
    monkeypatch.setenv("PUBLIC_PASSWORD", "hunter2")
    monkeypatch.setenv("XUI_LOCAL_PORT", "54321")
    monkeypatch.setenv("XUI_WEBBASEPATH", "panel")
    # s-ui removed
    # monkeypatch.setenv("SUI_PORT", "2095")
    # monkeypatch.setenv("SUI_SUB_PORT", "2096")
    # monkeypatch.setenv("SUI_WEBBASEPATH", "sui")
    # monkeypatch.setenv("SUI_SUB_PATH", "sub")
    monkeypatch.setenv("DOMAIN", "vpn.example.com")


def test_init_xui_skipped_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _common_env(monkeypatch)
    monkeypatch.setenv("ENABLE_XUI", "false")

    called: dict[str, object] = {}
    monkeypatch.setattr(sbpanels, "_run", lambda cmd, capture=True: called.setdefault("cmd", cmd))
    assert sbpanels.init_xui() is False
    assert "cmd" not in called


def test_init_xui_invokes_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    _common_env(monkeypatch)
    monkeypatch.delenv("ENABLE_XUI", raising=False)

    commands: list[list[str]] = []

    def fake_run(cmd: list[str], capture: bool = True) -> int:
        commands.append(cmd)
        return 0

    monkeypatch.setattr(sbpanels, "_run", fake_run)
    assert sbpanels.init_xui() is True
    assert ["x-ui", "setting"] == commands[0][:2]
    assert "-username" in commands[0] and "alice" in commands[0]
    assert commands[1][:2] == ["fail2ban-client", "-x"]


# s-ui removed — test disabled
# def test_init_sui_updates_suburi_when_db_exists(
#     monkeypatch: pytest.MonkeyPatch, tmp_path: Path
# ) -> None:
#     ...


# s-ui removed — test updated to only check XUI
def test_init_panels_short_circuits_when_xui_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENABLE_XUI", "false")

    def must_not_call(*args: object, **kwargs: object) -> object:
        raise AssertionError("init_xui must not run when disabled")

    monkeypatch.setattr(sbpanels, "init_xui", must_not_call)
    sbpanels.init_panels()  # no raise → pass
