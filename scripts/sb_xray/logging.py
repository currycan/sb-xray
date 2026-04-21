"""Structured stderr logging (entrypoint.sh §2 equivalent).

Honors `NO_COLOR` env var (https://no-color.org/) — when set to any
non-empty value, ANSI escape sequences are stripped.
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
from typing import Final

_RESET: Final[str] = "\033[0m"
_BOLD: Final[str] = "\033[1m"

_LEVEL_COLOR: Final[dict[str, str]] = {
    "INFO": "\033[1;32m",  # green
    "WARN": "\033[1;33m",  # yellow
    "ERROR": "\033[1;31m",  # red
    "DEBUG": "\033[1;36m",  # cyan
}
_CYAN: Final[str] = "\033[1;36m"
_GREEN: Final[str] = "\033[1;32m"
_YELLOW: Final[str] = "\033[1;33m"

_SUMMARY_WIDTH: Final[int] = 65


def _colors_enabled() -> bool:
    """Disable color when NO_COLOR is set (see https://no-color.org/)."""
    return not os.environ.get("NO_COLOR")


def _paint(code: str, text: str) -> str:
    return f"{code}{text}{_RESET}" if _colors_enabled() else text


def _now() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(level: str, *parts: str) -> None:
    """Write `[ts] [LEVEL] joined parts` to stderr (space-joined)."""
    color = _LEVEL_COLOR.get(level, "")
    msg = " ".join(str(p) for p in parts)
    line = f"[{_now()}] [{level}] {msg}"
    sys.stderr.write(_paint(color, line) + "\n")
    sys.stderr.flush()


def log_summary_box(*var_names: str) -> None:
    """Print a bordered box summarizing selected environment variables.

    Missing variables render as "N/A" (mirrors the Bash ${VAR:-N/A}).
    """
    line = "=" * _SUMMARY_WIDTH
    out = sys.stderr
    out.write("\n" + _paint(_CYAN, line) + "\n")
    title = "SYSTEM STRATEGY SUMMARY"
    out.write(
        _paint(_CYAN, "║")
        + _paint(_YELLOW, f" {title:<{_SUMMARY_WIDTH - 4}} ")
        + _paint(_CYAN, "║")
        + "\n"
    )
    out.write(_paint(_CYAN, line) + "\n")
    for name in var_names:
        value = os.environ.get(name, "N/A")
        pad = max(0, _SUMMARY_WIDTH - 4 - len(name) - len(value))
        out.write(
            _paint(_CYAN, "║ ")
            + _paint(_GREEN, name)
            + f": {value}"
            + " " * pad
            + " "
            + _paint(_CYAN, "║")
            + "\n"
        )
    out.write(_paint(_CYAN, line) + "\n\n")
    out.flush()


def show_progress(label: str) -> None:
    """In-place progress indicator written to stderr (no trailing newline)."""
    text = f"\r{_BOLD}[*] {label} ...{_RESET}" if _colors_enabled() else f"\r[*] {label} ..."
    sys.stderr.write(text)
    sys.stderr.flush()


def end_progress() -> None:
    """Erase the current stderr line (used after `show_progress`)."""
    sys.stderr.write("\r\033[K")
    sys.stderr.flush()
