"""Tests for sb_xray.stages.reload_util (F2/D4 reload helpers)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

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
