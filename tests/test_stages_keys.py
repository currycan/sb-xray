"""Tests for scripts/sb_xray/stages/keys.py."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sb_xray.env import EnvManager
from sb_xray.stages import keys as sbkeys
from sb_xray.stages.keys import _XRAY_TIMEOUT_SEC


def test_parse_two_line_pair_strips_labels() -> None:
    assert sbkeys._parse_two_line_pair(["Private key: abc123", "Public key: xyz789"]) == (
        "abc123",
        "xyz789",
    )


def test_parse_two_line_pair_tolerates_label_free_output() -> None:
    assert sbkeys._parse_two_line_pair(["abc", "xyz"]) == ("abc", "xyz")


def test_parse_two_line_pair_rejects_too_short() -> None:
    with pytest.raises(RuntimeError, match="too short"):
        sbkeys._parse_two_line_pair(["only one"])


def test_ensure_reality_keys_runs_xray_when_file_missing(
    tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mgr = EnvManager(tmp_env_file)
    monkeypatch.delenv("XRAY_REALITY_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("XRAY_REALITY_PUBLIC_KEY", raising=False)

    def fake_run(
        cmd: list[str], check: bool, capture_output: bool, text: bool, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        assert cmd == ["xray", "x25519"]
        assert timeout == _XRAY_TIMEOUT_SEC
        return subprocess.CompletedProcess(
            cmd, 0, stdout="Private key: priv-xyz\nPublic key: pub-xyz\n"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = sbkeys.ensure_reality_keys(mgr)
    assert out == {
        "XRAY_REALITY_PRIVATE_KEY": "priv-xyz",
        "XRAY_REALITY_PUBLIC_KEY": "pub-xyz",
    }
    content = tmp_env_file.read_text(encoding="utf-8")
    assert "export XRAY_REALITY_PRIVATE_KEY='priv-xyz'" in content
    assert "export XRAY_REALITY_PUBLIC_KEY='pub-xyz'" in content


def test_ensure_reality_keys_reuses_persisted_values(
    tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp_env_file.write_text(
        "export XRAY_REALITY_PRIVATE_KEY='priv-old'\nexport XRAY_REALITY_PUBLIC_KEY='pub-old'\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("XRAY_REALITY_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("XRAY_REALITY_PUBLIC_KEY", raising=False)

    def must_not_call(*args: object, **kwargs: object) -> object:
        raise AssertionError("xray CLI should not run when both keys persisted")

    monkeypatch.setattr(subprocess, "run", must_not_call)
    mgr = EnvManager(tmp_env_file)
    out = sbkeys.ensure_reality_keys(mgr)
    assert out == {
        "XRAY_REALITY_PRIVATE_KEY": "priv-old",
        "XRAY_REALITY_PUBLIC_KEY": "pub-old",
    }


def test_ensure_mlkem_keys_persists_seed_and_client(
    tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mgr = EnvManager(tmp_env_file)
    monkeypatch.delenv("XRAY_MLKEM768_SEED", raising=False)
    monkeypatch.delenv("XRAY_MLKEM768_CLIENT", raising=False)

    def fake_run(
        cmd: list[str], check: bool, capture_output: bool, text: bool, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        assert timeout == _XRAY_TIMEOUT_SEC
        return subprocess.CompletedProcess(cmd, 0, stdout="Seed: seed-v\nClient: client-v\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = sbkeys.ensure_mlkem_keys(mgr)
    assert out == {
        "XRAY_MLKEM768_SEED": "seed-v",
        "XRAY_MLKEM768_CLIENT": "client-v",
    }


def test_run_xray_passes_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        seen["timeout"] = kw.get("timeout")
        return subprocess.CompletedProcess(
            cmd, 0, stdout="Private key: a\nPublic key: b\n", stderr=""
        )

    monkeypatch.setattr(sbkeys.subprocess, "run", fake_run)
    lines = sbkeys._run_xray(["xray", "x25519"])
    assert seen["timeout"] == _XRAY_TIMEOUT_SEC
    assert lines == ["Private key: a", "Public key: b"]


def test_run_xray_raises_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))

    monkeypatch.setattr(sbkeys.subprocess, "run", boom)
    with pytest.raises(subprocess.TimeoutExpired):
        sbkeys._run_xray(["xray", "x25519"])
