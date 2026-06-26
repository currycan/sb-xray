"""Tests for sb_xray.stage StageTimer non-fatal degradation."""

from __future__ import annotations

import logging

import pytest
from sb_xray.stage import (
    PipelineSummary,
    StageInfo,
    StageStatus,
    StageTimer,
)

_LOG = logging.getLogger("test.stage")


def _info() -> StageInfo:
    return StageInfo(index=5, total=17, name="speed", label="ISP 测速与选路")


def test_non_fatal_swallows_exception_and_marks_degraded() -> None:
    summary = PipelineSummary()
    info = _info()
    # non_fatal=True: the raised error must NOT propagate out of the `with`.
    with StageTimer(info, _LOG, summary=summary, non_fatal=True) as t:
        raise RuntimeError("probe boom")
    assert t.result is not None
    assert t.result.status is StageStatus.DEGRADED
    assert isinstance(t.result.error, RuntimeError)
    assert "probe boom" in t.result.message
    assert summary.results == [t.result]
    assert summary.any_failed is False


def test_default_fatal_still_reraises() -> None:
    info = _info()
    with pytest.raises(RuntimeError, match="boom"):
        with StageTimer(info, _LOG):
            raise RuntimeError("boom")


def test_non_fatal_clean_run_is_ok() -> None:
    info = _info()
    with StageTimer(info, _LOG, non_fatal=True) as t:
        pass
    assert t.result is not None
    assert t.result.status is StageStatus.OK
