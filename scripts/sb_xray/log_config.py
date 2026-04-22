"""Stdlib-logging setup for the sb-xray entrypoint pipeline.

Replaces the hand-rolled ``sb_xray.logging`` (stderr writer) with
``logging.config.dictConfig`` + a custom formatter. Every module should
declare a module-scoped logger via ``logger = logging.getLogger(__name__)``
— the formatter picks the name up as ``%(name)s`` so the ``[sb_xray.xxx]``
prefix is added automatically (no more hand-typed ``[secrets]`` /
``[选路]`` / ``[ISP]`` drift).

Environment contract:

* ``SB_LOG_LEVEL`` (default ``INFO``): case-insensitive Python level
  (``DEBUG``/``INFO``/``WARNING``/``ERROR``/``CRITICAL``). Legacy
  ``WARN`` is accepted as an alias for ``WARNING``. **Deliberately
  named ``SB_LOG_LEVEL`` instead of ``LOG_LEVEL``** — the plain
  ``LOG_LEVEL`` env var is already used by xray / sing-box (string
  ``"warning"``) and by acme.sh (numeric ``1``/``2``/``3``), so
  piggy-backing on it here would silently suppress our INFO-level
  stage headings when operators set ``LOG_LEVEL=warning`` for xray.
* ``NO_COLOR`` (https://no-color.org/): when set to any non-empty
  value, disables ANSI escape sequences. Also disabled automatically
  when the target stream is not a TTY (container stdout, redirected
  files).

Output format (stderr, one record per line):

    [2026-04-22T13:59:33.123+08:00] [INFO] [sb_xray.routing.isp] msg
"""

from __future__ import annotations

import datetime as _dt
import logging
import logging.config
import os
import sys
from typing import Final

_LEVEL_ALIASES: Final[dict[str, str]] = {"WARN": "WARNING"}

_LEVEL_COLOR: Final[dict[int, str]] = {
    logging.DEBUG: "\033[2;37m",  # dim grey
    logging.INFO: "\033[0;32m",  # green
    logging.WARNING: "\033[0;33m",  # yellow
    logging.ERROR: "\033[0;31m",  # red
    logging.CRITICAL: "\033[1;31m",  # bold red
}
_RESET: Final[str] = "\033[0m"

_TZ: Final[_dt.tzinfo] = _dt.datetime.now().astimezone().tzinfo or _dt.UTC


def _colors_enabled(stream: object) -> bool:
    """Honor NO_COLOR and TTY detection."""
    if os.environ.get("NO_COLOR"):
        return False
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


class SbFormatter(logging.Formatter):
    """ISO-8601 + level + logger-name + message, with optional color.

    The formatter captures whether the **attached handler's stream** is
    a TTY at construction time rather than per-call for determinism:
    handler-stream identity is stable, so we only need to decide once.
    """

    def __init__(self, *, use_color: bool) -> None:
        super().__init__()
        self._use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        ts = _dt.datetime.fromtimestamp(record.created, tz=_TZ).isoformat(timespec="milliseconds")
        level = record.levelname
        message = record.getMessage()
        if record.exc_info:
            message = f"{message}\n{self.formatException(record.exc_info)}"
        if record.stack_info:
            message = f"{message}\n{record.stack_info}"
        head = f"[{ts}] [{level}] [{record.name}]"
        if self._use_color:
            color = _LEVEL_COLOR.get(record.levelno, "")
            if color:
                head = f"{color}{head}{_RESET}"
        return f"{head} {message}"


def _resolve_level(raw: str | None) -> int:
    """Accept ``"INFO"``, ``"info"``, ``"WARN"``, or numeric strings."""
    if raw is None or not raw.strip():
        return logging.INFO
    value = raw.strip().upper()
    value = _LEVEL_ALIASES.get(value, value)
    if value.isdigit():
        return int(value)
    level = logging.getLevelName(value)
    if isinstance(level, int):
        return level
    return logging.INFO


def setup_logging(level: str | int | None = None, *, stream: object | None = None) -> None:
    """Configure the root ``sb_xray`` logger and the interactive root.

    Idempotent — calling twice replaces the handler cleanly instead of
    stacking duplicate records. Called once from ``entrypoint.main()``
    and once from ``shoutrrr.run()`` (different process, same setup).

    Args:
        level: Override level (string or numeric). Falls back to the
            ``SB_LOG_LEVEL`` env var, then ``INFO``.
        stream: Output stream (defaults to ``sys.stderr``). Exposed so
            tests can attach to ``io.StringIO``.
    """
    target_stream = stream if stream is not None else sys.stderr
    if isinstance(level, int):
        numeric_level = level
    else:
        numeric_level = _resolve_level(
            level if level is not None else os.environ.get("SB_LOG_LEVEL")
        )

    use_color = _colors_enabled(target_stream)

    handler = logging.StreamHandler(target_stream)  # type: ignore[arg-type]
    handler.setLevel(numeric_level)
    handler.setFormatter(SbFormatter(use_color=use_color))

    root = logging.getLogger()
    # Replace any existing handlers (idempotent; avoids duplicates on
    # repeat calls in tests or after a supervisord re-exec).
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(numeric_level)

    sb_logger = logging.getLogger("sb_xray")
    sb_logger.setLevel(numeric_level)
    # The root handler emits the records, so sb_xray.* should not carry
    # its own handler (would double-print).
    for existing in list(sb_logger.handlers):
        sb_logger.removeHandler(existing)
    sb_logger.propagate = True
