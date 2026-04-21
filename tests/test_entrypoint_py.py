"""Tests for scripts/entrypoint.py (Phase 1 thin shell)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import entrypoint as ep  # noqa: E402


def test_bootstrap_loads_persisted_vars_into_environ(
    tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp_env_file.write_text(
        "export DOMAIN='vpn.example.com'\nexport PORT_HY='4443'\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("DOMAIN", raising=False)
    monkeypatch.delenv("PORT_HY", raising=False)
    ep.bootstrap(tmp_env_file)
    assert os.environ["DOMAIN"] == "vpn.example.com"
    assert os.environ["PORT_HY"] == "4443"


def test_bootstrap_shell_env_wins(tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp_env_file.write_text("export DOMAIN='old'\n", encoding="utf-8")
    monkeypatch.setenv("DOMAIN", "new")
    ep.bootstrap(tmp_env_file)
    assert os.environ["DOMAIN"] == "new"


def test_dry_run_exits_zero_without_invoking_legacy(
    tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = {"ran": False}

    def fake_run_legacy(_: list[str]) -> int:  # pragma: no cover
        called["ran"] = True
        return 99

    monkeypatch.setattr(ep, "run_legacy", fake_run_legacy)
    rc = ep.main(["--dry-run", "--env-file", str(tmp_env_file)])
    assert rc == 0
    assert called["ran"] is False


def test_main_delegates_to_legacy_when_not_dry_run(
    tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    received: dict[str, list[str]] = {}

    def fake_run_legacy(skip: list[str]) -> int:
        received["skip"] = skip
        return 0

    monkeypatch.setattr(ep, "run_legacy", fake_run_legacy)
    rc = ep.main(
        [
            "--env-file",
            str(tmp_env_file),
            "--skip-stage",
            "speed_test",
            "--skip-stage",
            "media",
        ]
    )
    assert rc == 0
    assert received["skip"] == ["speed_test", "media"]


def test_run_legacy_returns_127_when_script_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ep, "_LEGACY_ENTRYPOINT", tmp_path / "nope.sh")
    assert ep.run_legacy([]) == 127


def test_run_legacy_invokes_subprocess(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_script = tmp_path / "fake.sh"
    fake_script.write_text("#!/usr/bin/env bash\nexit 42\n", encoding="utf-8")
    fake_script.chmod(0o755)
    monkeypatch.setattr(ep, "_LEGACY_ENTRYPOINT", fake_script)

    captured: dict[str, object] = {}
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        captured["cmd"] = cmd
        captured["env_has_skip"] = "SB_XRAY_SKIP_STAGES" in kwargs["env"]
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    rc = ep.run_legacy(["stage_a"])
    assert rc == 42
    assert captured["env_has_skip"] is True
    assert str(fake_script) in captured["cmd"][-1]
