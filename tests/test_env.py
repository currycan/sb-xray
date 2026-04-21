"""Tests for sb_xray.env (entrypoint.sh §6 equivalent).

Covers the three `ensure_var` priority branches:
  T2a (shell env) — variable already exported, skip
  T2b (file)      — persisted in ${ENV_FILE}, load into current env
  T2c (generator) — compute via callable, then persist
And `ensure_key_pair` atomic writes (T7).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sb_xray.env import EnvManager


def test_shell_env_wins_over_file(tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp_env_file.write_text("export FOO='from-file'\n", encoding="utf-8")
    monkeypatch.setenv("FOO", "from-shell")
    mgr = EnvManager(tmp_env_file)
    value = mgr.ensure_var("FOO", generator=lambda: "from-gen")
    assert value == "from-shell"


def test_file_loaded_when_shell_missing(
    tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp_env_file.write_text("export BAR='persisted'\n", encoding="utf-8")
    monkeypatch.delenv("BAR", raising=False)
    mgr = EnvManager(tmp_env_file)
    value = mgr.ensure_var("BAR", generator=lambda: "new")
    assert value == "persisted"
    assert mgr.get("BAR") == "persisted"


def test_generator_called_when_both_missing(
    tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BAZ", raising=False)
    mgr = EnvManager(tmp_env_file)
    value = mgr.ensure_var("BAZ", generator=lambda: "computed")
    assert value == "computed"
    content = tmp_env_file.read_text(encoding="utf-8")
    assert "export BAZ='computed'" in content
    assert os.environ["BAZ"] == "computed"


def test_ensure_var_default_when_no_generator(
    tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("QUX", raising=False)
    mgr = EnvManager(tmp_env_file)
    value = mgr.ensure_var("QUX", default="fallback")
    assert value == "fallback"


def test_ensure_var_no_persist(tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EPHEMERAL", raising=False)
    mgr = EnvManager(tmp_env_file)
    mgr.ensure_var("EPHEMERAL", generator=lambda: "xyz", persist=False)
    assert tmp_env_file.read_text(encoding="utf-8") == ""


def test_ensure_var_missing_and_no_generator_or_default(
    tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MISSING", raising=False)
    mgr = EnvManager(tmp_env_file)
    with pytest.raises(KeyError):
        mgr.ensure_var("MISSING")


def test_ensure_key_pair_atomic(tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PRIV", raising=False)
    monkeypatch.delenv("PUB", raising=False)
    mgr = EnvManager(tmp_env_file)
    mgr.ensure_key_pair(
        "reality",
        "PRIV",
        "PUB",
        generator=lambda: {"PRIV": "p-123", "PUB": "P-456"},
    )
    content = tmp_env_file.read_text(encoding="utf-8")
    assert "export PRIV='p-123'" in content
    assert "export PUB='P-456'" in content


def test_ensure_key_pair_loaded_when_both_present(
    tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp_env_file.write_text("export K1='aaa'\nexport K2='bbb'\n", encoding="utf-8")
    monkeypatch.delenv("K1", raising=False)
    monkeypatch.delenv("K2", raising=False)

    def never_called() -> dict[str, str]:
        raise AssertionError("generator should not be called")

    mgr = EnvManager(tmp_env_file)
    mgr.ensure_key_pair("k", "K1", "K2", generator=never_called)
    assert os.environ["K1"] == "aaa"
    assert os.environ["K2"] == "bbb"


def test_check_required_env_raises_when_missing(
    tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NEED1", raising=False)
    monkeypatch.setenv("NEED2", "present")
    mgr = EnvManager(tmp_env_file)
    with pytest.raises(RuntimeError, match="NEED1"):
        mgr.check_required("NEED1", "NEED2")


def test_check_required_env_ok(tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("A", "1")
    monkeypatch.setenv("B", "2")
    mgr = EnvManager(tmp_env_file)
    mgr.check_required("A", "B")  # no raise


def test_persist_idempotent(tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("Z", raising=False)
    mgr = EnvManager(tmp_env_file)
    mgr.ensure_var("Z", generator=lambda: "v1")
    # second ensure with different generator value — file branch should win
    monkeypatch.delenv("Z", raising=False)
    mgr2 = EnvManager(tmp_env_file)
    mgr2.ensure_var("Z", generator=lambda: "v2-should-not-replace")
    content = tmp_env_file.read_text(encoding="utf-8")
    assert content.count("export Z=") == 1
    assert "export Z='v1'" in content
