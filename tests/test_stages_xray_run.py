"""Tests for scripts/sb_xray/stages/xray_run.py."""

from __future__ import annotations

from pathlib import Path

import pytest
from sb_xray.stages import xray_run


def test_cleanup_removes_only_uds_sock(tmp_path: Path) -> None:
    """``uds*.sock`` 被清理,其它文件保留。"""
    targets = ["udsxhttp.sock", "udsxhttp-compat.sock", "udsreality.sock", "udsvmessws.sock"]
    keep = ["nginx.sock", "cdnh2.sock", "uds_notes.txt", "udsleftover"]
    for name in targets + keep:
        (tmp_path / name).write_bytes(b"")

    removed = xray_run.cleanup_stale_uds(shm=tmp_path)

    assert sorted(removed) == sorted(targets)
    for name in keep:
        assert (tmp_path / name).exists(), f"non-target file {name} was deleted"
    for name in targets:
        assert not (tmp_path / name).exists(), f"target {name} still present"


def test_cleanup_idempotent_when_empty(tmp_path: Path) -> None:
    assert xray_run.cleanup_stale_uds(shm=tmp_path) == []


def test_cleanup_handles_missing_dir(tmp_path: Path) -> None:
    """传入不存在目录时不抛错,返回空列表。"""
    assert xray_run.cleanup_stale_uds(shm=tmp_path / "nonexistent") == []


def test_cleanup_tolerates_file_disappearing_midflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模拟另一进程在 unlink 前删掉了文件,我们不能崩。"""
    sock = tmp_path / "udsxhttp.sock"
    sock.write_bytes(b"")

    real_unlink = Path.unlink

    def racy_unlink(self: Path, *args: object, **kwargs: object) -> None:
        if self == sock:
            raise FileNotFoundError(self)
        real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", racy_unlink)

    # Should not raise; sock won't be in `removed` because unlink raised.
    removed = xray_run.cleanup_stale_uds(shm=tmp_path)
    assert removed == []


def test_exec_xray_invokes_execvp_after_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """清理在 exec 之前发生,argv 正确。"""
    call_order: list[str] = []
    captured: dict[str, object] = {}

    monkeypatch.setattr(xray_run, "SHM_DIR", tmp_path)

    real_cleanup = xray_run.cleanup_stale_uds

    def spy_cleanup(shm: Path = tmp_path) -> list[str]:
        call_order.append("cleanup")
        return real_cleanup(shm=shm)

    def fake_execvp(prog: str, argv: list[str]) -> None:
        call_order.append("execvp")
        captured["prog"] = prog
        captured["argv"] = argv

    monkeypatch.setattr(xray_run, "cleanup_stale_uds", spy_cleanup)
    monkeypatch.setattr(xray_run.os, "execvp", fake_execvp)

    xray_run.exec_xray()

    assert call_order == ["cleanup", "execvp"]
    assert captured["prog"] == "xray"
    assert captured["argv"] == ["xray", "run", "-confdir", "/sb-xray/xray/"]


def test_exec_xray_respects_custom_confdir(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, list[str]] = {}

    monkeypatch.setattr(xray_run, "cleanup_stale_uds", lambda shm=None: [])
    monkeypatch.setattr(xray_run.os, "execvp", lambda prog, argv: captured.setdefault("argv", argv))

    xray_run.exec_xray(confdir="/custom/xray/")

    assert captured["argv"] == ["xray", "run", "-confdir", "/custom/xray/"]
