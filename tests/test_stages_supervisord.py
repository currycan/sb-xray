"""Tests for scripts/sb_xray/stages/supervisord.py."""

from __future__ import annotations

import pytest
from sb_xray.stages import supervisord as sbsup


def test_default_when_no_extras() -> None:
    assert sbsup.build_supervisord_argv(None) == [
        "supervisord",
        "-n",
        "-c",
        "/etc/supervisord.conf",
    ]


def test_default_when_supervisord_passed() -> None:
    assert sbsup.build_supervisord_argv(["supervisord"]) == [
        "supervisord",
        "-n",
        "-c",
        "/etc/supervisord.conf",
    ]


def test_custom_config_path() -> None:
    argv = sbsup.build_supervisord_argv(["supervisord"], config="/custom.conf")
    assert argv == ["supervisord", "-n", "-c", "/custom.conf"]


def test_passthrough_non_supervisord() -> None:
    """Dockerfile CMD [bash] should forward argv untouched (debug shell)."""
    assert sbsup.build_supervisord_argv(["bash", "-i"]) == ["bash", "-i"]


def test_exec_supervisord_calls_execvp(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_execvp(prog: str, argv: list[str]) -> None:
        captured["prog"] = prog
        captured["argv"] = argv

    monkeypatch.setattr(sbsup.os, "execvp", fake_execvp)
    sbsup.exec_supervisord(["supervisord"])
    assert captured["prog"] == "supervisord"
    assert captured["argv"][0] == "supervisord"
