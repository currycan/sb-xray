"""Tests for sb_xray.stages.reload_util (F2/D4 reload helpers)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from sb_xray.stages import reload_util


def _socket_path(*, exists: bool) -> Path:
    """Return a MagicMock Path whose .is_socket() returns *exists*."""
    p = MagicMock(spec=Path)
    p.is_socket.return_value = exists
    return p


def test_reload_nginx_emits_signal_reload() -> None:
    runner = MagicMock()
    sock = _socket_path(exists=True)

    assert reload_util.reload_nginx(socket_path=sock, runner=runner) is True

    runner.run.assert_called_once_with(
        ["nginx", "-s", "reload"], check=False, timeout=10
    )


def test_reload_nginx_skips_when_socket_absent() -> None:
    runner = MagicMock()
    sock = _socket_path(exists=False)

    assert reload_util.reload_nginx(socket_path=sock, runner=runner) is False

    runner.run.assert_not_called()


def test_reload_nginx_swallows_timeout_error() -> None:
    runner = MagicMock()
    runner.run.side_effect = subprocess.TimeoutExpired(cmd="nginx", timeout=10)
    sock = _socket_path(exists=True)

    # error swallowed but not reported as success
    assert reload_util.reload_nginx(socket_path=sock, runner=runner) is False


def test_reload_nginx_swallows_oserror() -> None:
    runner = MagicMock()
    runner.run.side_effect = OSError("permission denied")
    sock = _socket_path(exists=True)

    # OSError swallowed but not reported as success
    assert reload_util.reload_nginx(socket_path=sock, runner=runner) is False


# ---------------------------------------------------------------------------
# restart_daemons — direct tests (D4)
# ---------------------------------------------------------------------------


class _FakeRunner:
    """Records supervisorctl invocations; configurable per-call side effect."""

    def __init__(self, side_effect: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._side_effect = side_effect

    def run(self, argv: list[str], *, check: bool, timeout: int) -> None:
        self.calls.append({"argv": argv, "check": check, "timeout": timeout})
        if self._side_effect is not None:
            raise self._side_effect


class _SocketStub:
    """Stand-in for a Path whose is_socket() returns a fixed value."""

    def __init__(self, present: bool) -> None:
        self._present = present

    def is_socket(self) -> bool:
        return self._present


def test_restart_emits_both_services_with_check_false_timeout_10() -> None:
    runner = _FakeRunner()
    result = reload_util.restart_daemons(
        socket_path=_SocketStub(present=True),  # type: ignore[arg-type]
        runner=runner,
    )
    assert result is True
    assert [c["argv"] for c in runner.calls] == [
        ["supervisorctl", "restart", "xray"],
        ["supervisorctl", "restart", "sing-box"],
    ]
    assert all(c["check"] is False and c["timeout"] == 10 for c in runner.calls)


@pytest.mark.parametrize(
    "exc",
    [
        subprocess.TimeoutExpired(cmd="supervisorctl", timeout=10),
        OSError("supervisorctl not found"),
    ],
)
def test_restart_swallows_timeout_and_oserror_still_true(exc: Exception) -> None:
    runner = _FakeRunner(side_effect=exc)
    result = reload_util.restart_daemons(
        socket_path=_SocketStub(present=True),  # type: ignore[arg-type]
        runner=runner,
    )
    # 每个服务都尝试一次(异常不中断循环),整体仍返回 True。
    assert len(runner.calls) == 2
    assert result is True


def test_restart_skips_when_socket_absent() -> None:
    runner = _FakeRunner()
    result = reload_util.restart_daemons(
        socket_path=_SocketStub(present=False),  # type: ignore[arg-type]
        runner=runner,
    )
    assert result is False
    assert runner.calls == []
