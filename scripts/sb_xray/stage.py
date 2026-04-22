"""Pipeline stage instrumentation and operator-facing summary box.

Provides:

* :class:`StageInfo` / :class:`StageStatus` / :class:`StageResult` — the
  structured data shapes that used to be encoded as loose log strings.
* :class:`StageTimer` — context manager that wraps each pipeline stage
  with consistent ``start / done / failed`` lines + millisecond timing
  + automatic traceback capture on exceptions.
* :class:`PipelineSummary` — accumulator that emits a single aggregate
  block at the end of ``run_pipeline`` instead of scattering ``✅`` lines.
* :func:`render_summary_box` — drop-in replacement for the legacy
  ``sblog.log_summary_box``. Writes to **stdout** (not stderr); the
  rendered block is a one-shot operator report, not part of the
  line-oriented log stream that a log aggregator might tail.
"""

from __future__ import annotations

import contextlib
import enum
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Final, TextIO

_SYMBOL_START: Final[str] = "▶"
_SYMBOL_OK: Final[str] = "✓"
_SYMBOL_FAIL: Final[str] = "✗"
_SYMBOL_SKIP: Final[str] = "⋯"


class StageStatus(enum.Enum):
    OK = "ok"
    SKIPPED = "skipped"
    DEGRADED = "degraded"
    FAILED = "failed"


@dataclass(frozen=True)
class StageInfo:
    """Immutable identity of a pipeline stage.

    ``name`` is machine-readable (used in the ``--skip-stage`` CLI and
    in log records) while ``label`` is the human-friendly Chinese
    heading that operators recognize at a glance.
    """

    index: int
    total: int
    name: str
    label: str


@dataclass
class StageResult:
    """Outcome of one stage, carried into :class:`PipelineSummary`."""

    info: StageInfo
    status: StageStatus
    duration_ms: int
    message: str = ""
    error: BaseException | None = None


class StageTimer(contextlib.AbstractContextManager["StageTimer"]):
    """Instrument a pipeline stage with uniform start/end log lines.

    Typical usage::

        with StageTimer(info, logger) as t:
            do_work()
            t.note(f"ISP_TAG={tag}")   # structured follow-up line
            # or, when the stage realizes it should short-circuit:
            t.skipped("cache hit")

    On exit:

    * Exceptions are logged with ``logger.exception`` (status=FAILED)
      and **re-raised** so existing fail-fast callers keep the same
      semantics.
    * ``t.skipped(msg)`` marks the stage ``SKIPPED`` but returns
      cleanly — callers use ``return`` right after.
    * Otherwise status is ``OK``.

    The constructed :class:`StageResult` is attached to ``self.result``
    for callers that want to push it onto a :class:`PipelineSummary`.
    """

    def __init__(
        self,
        info: StageInfo,
        logger: logging.Logger,
        *,
        summary: PipelineSummary | None = None,
    ) -> None:
        self.info = info
        self._logger = logger
        self._summary = summary
        self._start_ns: int = 0
        self._status = StageStatus.OK
        self._message = ""
        self.result: StageResult | None = None

    def __enter__(self) -> StageTimer:
        self._start_ns = time.monotonic_ns()
        self._logger.info(
            "%s Stage %d/%d %s: %s",
            _SYMBOL_START,
            self.info.index,
            self.info.total,
            self.info.name,
            self.info.label,
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        duration_ms = (time.monotonic_ns() - self._start_ns) // 1_000_000

        if exc is not None:
            self._status = StageStatus.FAILED
            self._message = f"{exc.__class__.__name__}: {exc}"
            self._logger.error(
                "%s Stage %d/%d %s failed in %dms: %s",
                _SYMBOL_FAIL,
                self.info.index,
                self.info.total,
                self.info.name,
                duration_ms,
                self._message,
                exc_info=(exc_type, exc, tb),
            )
        elif self._status == StageStatus.SKIPPED:
            self._logger.info(
                "%s Stage %d/%d %s skipped in %dms%s",
                _SYMBOL_SKIP,
                self.info.index,
                self.info.total,
                self.info.name,
                duration_ms,
                f" ({self._message})" if self._message else "",
            )
        else:
            self._logger.info(
                "%s Stage %d/%d %s ok in %dms",
                _SYMBOL_OK,
                self.info.index,
                self.info.total,
                self.info.name,
                duration_ms,
            )

        self.result = StageResult(
            info=self.info,
            status=self._status,
            duration_ms=int(duration_ms),
            message=self._message,
            error=exc,
        )
        if self._summary is not None:
            self._summary.record(self.result)
        return False  # don't swallow exceptions

    def skipped(self, message: str = "") -> None:
        """Mark this stage SKIPPED (logged as ``⋯`` on exit)."""
        self._status = StageStatus.SKIPPED
        self._message = message

    def degraded(self, message: str) -> None:
        """Mark this stage DEGRADED (still logs ``✓`` but summary differs)."""
        self._status = StageStatus.DEGRADED
        self._message = message


@dataclass
class PipelineSummary:
    """Accumulate stage results; emit one aggregate block at the end."""

    results: list[StageResult] = field(default_factory=list)

    def record(self, result: StageResult) -> None:
        self.results.append(result)

    @property
    def any_failed(self) -> bool:
        return any(r.status == StageStatus.FAILED for r in self.results)

    def log_overview(self, logger: logging.Logger) -> None:
        """Print one-line-per-stage rollup, suitable for log tails."""
        total_ms = sum(r.duration_ms for r in self.results)
        counts = {s: 0 for s in StageStatus}
        for r in self.results:
            counts[r.status] += 1
        logger.info(
            "Pipeline summary: %d stages total in %dms — ok=%d skipped=%d degraded=%d failed=%d",
            len(self.results),
            total_ms,
            counts[StageStatus.OK],
            counts[StageStatus.SKIPPED],
            counts[StageStatus.DEGRADED],
            counts[StageStatus.FAILED],
        )
        for r in self.results:
            marker = {
                StageStatus.OK: _SYMBOL_OK,
                StageStatus.SKIPPED: _SYMBOL_SKIP,
                StageStatus.DEGRADED: _SYMBOL_OK,
                StageStatus.FAILED: _SYMBOL_FAIL,
            }[r.status]
            tail = f" — {r.message}" if r.message else ""
            logger.info(
                "  %s %d/%d %s [%s] %dms%s",
                marker,
                r.info.index,
                r.info.total,
                r.info.name,
                r.status.value,
                r.duration_ms,
                tail,
            )


# ---------------------------------------------------------------------------
# Operator-facing SUMMARY box (one-shot report, not part of the log stream)
# ---------------------------------------------------------------------------

_SUMMARY_WIDTH: Final[int] = 65


def _colors_enabled(stream: TextIO) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def render_summary_box(
    *var_names: str,
    title: str = "SYSTEM STRATEGY SUMMARY",
    out: TextIO | None = None,
) -> None:
    """Write a boxed report of the selected env vars to ``out``.

    Replaces the legacy ``sblog.log_summary_box`` — same visual layout,
    but now deliberately writes to **stdout** (with ``sys.stderr``
    fallback) so the single block that concludes the boot sequence
    sits alongside the subscription banner rather than getting mixed
    into line-oriented log-aggregation streams.
    """
    stream = out if out is not None else sys.stdout
    use_color = _colors_enabled(stream)

    def paint(code: str, text: str) -> str:
        return f"{code}{text}\033[0m" if use_color else text

    cyan = "\033[1;36m"
    yellow = "\033[1;33m"
    green = "\033[1;32m"

    border = "=" * _SUMMARY_WIDTH
    stream.write("\n" + paint(cyan, border) + "\n")
    stream.write(
        paint(cyan, "║")
        + paint(yellow, f" {title:<{_SUMMARY_WIDTH - 4}} ")
        + paint(cyan, "║")
        + "\n"
    )
    stream.write(paint(cyan, border) + "\n")
    for name in var_names:
        value = os.environ.get(name, "N/A")
        pad = max(0, _SUMMARY_WIDTH - 4 - len(name) - len(value))
        stream.write(
            paint(cyan, "║ ")
            + paint(green, name)
            + f": {value}"
            + " " * pad
            + " "
            + paint(cyan, "║")
            + "\n"
        )
    stream.write(paint(cyan, border) + "\n\n")
    stream.flush()
