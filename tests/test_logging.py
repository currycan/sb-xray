"""Tests for ``sb_xray.log_config`` + ``sb_xray.stage``.

Replaces the old ``sb_xray.logging`` tests — the hand-rolled stderr
writer got traded in for stdlib ``logging`` + ``dictConfig``. The
contract is now:

* ``setup_logging()`` installs **one** handler on the root logger
  that writes ``[ISO-timestamp] [LEVEL] [logger-name] msg`` to stderr.
* ``LOG_LEVEL`` env var (case-insensitive; ``WARN`` accepted as
  ``WARNING``) controls the effective level.
* ``NO_COLOR`` / non-TTY streams disable ANSI colour.
* :class:`sb_xray.stage.StageTimer` wraps a pipeline stage with
  start / end / duration lines and captures exceptions with
  traceback.
* :func:`sb_xray.stage.render_summary_box` replaces the legacy
  ``log_summary_box`` — now writes to stdout.
"""

from __future__ import annotations

import io
import logging
import re

import pytest
from sb_xray import log_config
from sb_xray.stage import (
    PipelineSummary,
    StageInfo,
    StageStatus,
    StageTimer,
    render_summary_box,
)

# ---------------------------------------------------------------------------
# Fixture: hand each test a fresh stream + isolate the root logger state
# ---------------------------------------------------------------------------


@pytest.fixture
def log_stream(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    """Redirect the new log handler to an in-memory stream.

    We deliberately call ``setup_logging`` against a ``StringIO`` so
    captured content is deterministic, not entangled with pytest's
    own capsys redirects.
    """
    monkeypatch.delenv("SB_LOG_LEVEL", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    stream = io.StringIO()
    # Also reset level to DEBUG so every record from nested tests
    # that create their own loggers is observable.
    log_config.setup_logging(level="DEBUG", stream=stream)
    yield stream
    # Clean up: remove our handler so the next test's setup_logging
    # starts from a known-empty state.
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)


# ---------------------------------------------------------------------------
# log_config.setup_logging + SbFormatter
# ---------------------------------------------------------------------------


def test_info_record_formats_with_iso_timestamp_level_name(
    log_stream: io.StringIO,
) -> None:
    logger = logging.getLogger("sb_xray.test_info")
    logger.info("hello world")
    output = log_stream.getvalue()

    assert "[INFO]" in output
    assert "[sb_xray.test_info]" in output
    assert "hello world" in output
    # Timestamp shape: 2026-04-22T13:59:33.123+08:00 (or similar offset)
    assert re.search(r"\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+\-]\d{2}:\d{2}\]", output)


@pytest.mark.parametrize(
    "level,method,should_see",
    [
        ("DEBUG", "debug", True),
        ("INFO", "info", True),
        ("WARNING", "warning", True),
        ("ERROR", "error", True),
    ],
)
def test_each_level_formatted_with_label(
    level: str, method: str, should_see: bool, log_stream: io.StringIO
) -> None:
    logger = logging.getLogger(f"sb_xray.lvl_{level}")
    getattr(logger, method)("msg-for-%s", level)
    output = log_stream.getvalue()
    assert (f"[{level}]" in output) is should_see


def test_legacy_warn_alias_resolves_to_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("SB_LOG_LEVEL", "WARN")
    stream = io.StringIO()
    log_config.setup_logging(stream=stream)
    assert logging.getLogger().getEffectiveLevel() == logging.WARNING
    # Cleanup
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)


def test_log_level_env_filters_debug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SB_LOG_LEVEL", "INFO")
    stream = io.StringIO()
    log_config.setup_logging(stream=stream)
    logger = logging.getLogger("sb_xray.filter_test")
    logger.debug("hidden")
    logger.info("visible")
    output = stream.getvalue()
    assert "hidden" not in output
    assert "visible" in output
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)


def test_no_color_env_disables_ansi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    stream = io.StringIO()
    log_config.setup_logging(level="INFO", stream=stream)
    logging.getLogger("sb_xray.nocolor").info("plain msg")
    assert "\033[" not in stream.getvalue()
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)


def test_non_tty_stream_disables_color_even_without_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    # io.StringIO.isatty() returns False → no colour.
    stream = io.StringIO()
    log_config.setup_logging(level="INFO", stream=stream)
    logging.getLogger("sb_xray.non_tty").info("plain msg")
    assert "\033[" not in stream.getvalue()
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)


def test_setup_logging_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SB_LOG_LEVEL", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    stream = io.StringIO()
    log_config.setup_logging(level="INFO", stream=stream)
    log_config.setup_logging(level="INFO", stream=stream)
    root = logging.getLogger()
    # Exactly one handler after twice-called setup.
    assert len(root.handlers) == 1
    root.handlers.clear()


def test_exception_info_includes_traceback(log_stream: io.StringIO) -> None:
    logger = logging.getLogger("sb_xray.exc_test")
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        logger.exception("caught")
    output = log_stream.getvalue()
    assert "caught" in output
    assert "Traceback (most recent call last)" in output
    assert "RuntimeError: boom" in output


# ---------------------------------------------------------------------------
# stage.StageTimer + PipelineSummary
# ---------------------------------------------------------------------------


def test_stage_timer_logs_start_and_end(log_stream: io.StringIO) -> None:
    logger = logging.getLogger("sb_xray.stage.test")
    info = StageInfo(index=3, total=17, name="probe", label="基础环境变量初始化")
    with StageTimer(info, logger) as t:
        pass
    output = log_stream.getvalue()
    assert "▶ Stage 3/17 probe: 基础环境变量初始化" in output
    # ✓ on success, followed by millisecond duration
    assert re.search(r"✓ Stage 3/17 probe ok in \d+ms", output)
    assert t.result is not None
    assert t.result.status == StageStatus.OK


def test_stage_timer_captures_exception_and_reraises(
    log_stream: io.StringIO,
) -> None:
    logger = logging.getLogger("sb_xray.stage.err")
    info = StageInfo(index=9, total=17, name="cert", label="TLS 证书")
    with pytest.raises(RuntimeError, match="boom"):
        with StageTimer(info, logger):
            raise RuntimeError("boom")
    output = log_stream.getvalue()
    assert "✗ Stage 9/17 cert failed" in output
    assert "Traceback (most recent call last)" in output
    assert "RuntimeError: boom" in output


def test_stage_timer_skipped_records_status(log_stream: io.StringIO) -> None:
    logger = logging.getLogger("sb_xray.stage.skip")
    info = StageInfo(index=2, total=17, name="secrets", label="解密")
    summary = PipelineSummary()
    with StageTimer(info, logger, summary=summary) as t:
        t.skipped("--skip-stage")
    output = log_stream.getvalue()
    assert "⋯ Stage 2/17 secrets skipped" in output
    assert "(--skip-stage)" in output
    assert summary.results[0].status == StageStatus.SKIPPED


def test_pipeline_summary_overview(log_stream: io.StringIO) -> None:
    logger = logging.getLogger("sb_xray.stage.rollup")
    summary = PipelineSummary()
    # Happy path stage
    with StageTimer(StageInfo(1, 3, "a", "A"), logger, summary=summary):
        pass
    # Skipped stage
    with StageTimer(StageInfo(2, 3, "b", "B"), logger, summary=summary) as t:
        t.skipped("no-op")
    # Failed stage (swallowed so test doesn't crash)
    with pytest.raises(ValueError):
        with StageTimer(StageInfo(3, 3, "c", "C"), logger, summary=summary):
            raise ValueError("bad")

    log_stream.truncate(0)
    log_stream.seek(0)
    summary.log_overview(logger)
    overview = log_stream.getvalue()
    assert "Pipeline summary: 3 stages total" in overview
    assert "ok=1" in overview
    assert "skipped=1" in overview
    assert "failed=1" in overview
    assert "1/3 a [ok]" in overview
    assert "2/3 b [skipped]" in overview
    assert "3/3 c [failed]" in overview
    assert summary.any_failed


# ---------------------------------------------------------------------------
# stage.render_summary_box
# ---------------------------------------------------------------------------


def test_summary_box_contains_title_and_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FOO", "bar")
    monkeypatch.setenv("BAZ", "42")
    monkeypatch.delenv("MISSING", raising=False)
    out = io.StringIO()
    render_summary_box("FOO", "BAZ", "MISSING", out=out)
    content = out.getvalue()
    assert "SYSTEM STRATEGY SUMMARY" in content
    assert "FOO" in content and "bar" in content
    assert "BAZ" in content and "42" in content
    assert "N/A" in content  # missing var default


def test_summary_box_color_disabled_when_no_color(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    out = io.StringIO()
    render_summary_box("FOO", out=out)
    assert "\033[" not in out.getvalue()
