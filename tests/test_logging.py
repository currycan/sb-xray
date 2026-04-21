"""Tests for sb_xray.logging (equivalent to entrypoint.sh §2)."""

from __future__ import annotations

import re

import pytest
from sb_xray import logging as sblog


def test_log_info_writes_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    sblog.log("INFO", "hello world")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "[INFO]" in captured.err
    assert "hello world" in captured.err
    # timestamp shape: 2026-04-21 18:59:30
    assert re.search(r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]", captured.err)


@pytest.mark.parametrize("level", ["INFO", "WARN", "ERROR", "DEBUG"])
def test_log_all_levels_include_label(level: str, capsys: pytest.CaptureFixture[str]) -> None:
    sblog.log(level, "msg")
    captured = capsys.readouterr()
    assert f"[{level}]" in captured.err
    assert "msg" in captured.err


def test_log_unknown_level_falls_back(capsys: pytest.CaptureFixture[str]) -> None:
    sblog.log("WEIRD", "x")
    captured = capsys.readouterr()
    assert "[WEIRD]" in captured.err
    assert "x" in captured.err


def test_log_supports_multiple_args(capsys: pytest.CaptureFixture[str]) -> None:
    sblog.log("INFO", "part1", "part2", "part3")
    captured = capsys.readouterr()
    assert "part1 part2 part3" in captured.err


def test_summary_box_contains_title_and_fields(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FOO", "bar")
    monkeypatch.setenv("BAZ", "42")
    sblog.log_summary_box("FOO", "BAZ", "MISSING")
    captured = capsys.readouterr()
    assert "SYSTEM STRATEGY SUMMARY" in captured.err
    assert "FOO" in captured.err
    assert "bar" in captured.err
    assert "BAZ" in captured.err
    assert "42" in captured.err
    assert "N/A" in captured.err  # missing var shows as N/A


def test_show_progress_uses_carriage_return(capsys: pytest.CaptureFixture[str]) -> None:
    sblog.show_progress("loading")
    captured = capsys.readouterr()
    assert captured.err.startswith("\r")
    assert "loading" in captured.err


def test_end_progress_clears_line(capsys: pytest.CaptureFixture[str]) -> None:
    sblog.end_progress()
    captured = capsys.readouterr()
    assert "\r" in captured.err
    assert "\033[K" in captured.err


def test_color_disabled_when_no_color_env(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    sblog.log("INFO", "plain")
    captured = capsys.readouterr()
    assert "\033[" not in captured.err
