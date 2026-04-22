"""Tests for scripts/sb_xray/stages/panels.py."""

from __future__ import annotations

from pathlib import Path

import pytest
from sb_xray.stages import panels as sbpanels


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset panel-control flags so leaks from earlier tests (e.g.
    ``test_config_builder.py`` writes ``ENABLE_SUI=false`` directly into
    ``os.environ``) don't make ``init_sui`` short-circuit."""
    monkeypatch.setenv("SUI_DB_FOLDER", str(tmp_path))
    monkeypatch.delenv("ENABLE_XUI", raising=False)
    monkeypatch.delenv("ENABLE_SUI", raising=False)


def _common_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBLIC_USER", "alice")
    monkeypatch.setenv("PUBLIC_PASSWORD", "hunter2")
    monkeypatch.setenv("XUI_LOCAL_PORT", "54321")
    monkeypatch.setenv("XUI_WEBBASEPATH", "panel")
    monkeypatch.setenv("SUI_PORT", "2095")
    monkeypatch.setenv("SUI_SUB_PORT", "2096")
    monkeypatch.setenv("SUI_WEBBASEPATH", "sui")
    monkeypatch.setenv("SUI_SUB_PATH", "sub")
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


def test_init_sui_updates_suburi_when_db_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _common_env(monkeypatch)
    monkeypatch.setenv("SUI_DB_FOLDER", str(tmp_path))
    (tmp_path / "s-ui.db").write_text("placeholder", encoding="utf-8")

    commands: list[list[str]] = []

    def fake_run(cmd: list[str], capture: bool = True) -> int:
        commands.append(cmd)
        return 0

    monkeypatch.setattr(sbpanels, "_run", fake_run)
    assert sbpanels.init_sui() is True
    # sqlite3 invocation must contain the subURI update.
    assert any(
        c[0] == "sqlite3" and "subURI" in c[-1] and "https://vpn.example.com/sub/" in c[-1]
        for c in commands
    )


def test_init_panels_short_circuits_when_both_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENABLE_XUI", "false")
    monkeypatch.setenv("ENABLE_SUI", "false")

    def must_not_call(*args: object, **kwargs: object) -> object:
        raise AssertionError("init_xui/init_sui must not run when both disabled")

    monkeypatch.setattr(sbpanels, "init_xui", must_not_call)
    monkeypatch.setattr(sbpanels, "init_sui", must_not_call)
    sbpanels.init_panels()  # no raise → pass
