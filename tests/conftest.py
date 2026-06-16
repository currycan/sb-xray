"""Shared pytest fixtures for sb_xray tests."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

# 让 tests 能 import sources/deploy-config/ 下的独立脚本（gen_deploy_config），该目录不在 pyproject pythonpath。
_DEPLOY_CONFIG_DIR = Path(__file__).resolve().parent.parent / "sources" / "deploy-config"
if str(_DEPLOY_CONFIG_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOY_CONFIG_DIR))


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
