"""Shared pytest fixtures for sb_xray tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def tmp_env_file(tmp_path: Path) -> Path:
    """A writable temporary file mimicking `${ENV_FILE}` (`KEY=VALUE` lines)."""
    path = tmp_path / "sb-xray.env"
    path.write_text("", encoding="utf-8")
    return path


@pytest.fixture
def isolated_workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Chdir to a fresh temp dir so tests never touch the repo working tree."""
    monkeypatch.chdir(tmp_path)
    yield tmp_path
