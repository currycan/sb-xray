"""A3 gomemlimit-no-image-default: Go GC 软上限必须有镜像内默认(§2b)。

watchtower 不读 docker-compose.yml,而是从旧容器 inspect 出已实例化 env 重建。
若 GOMEMLIMIT/GOGC 仅存在于 compose,旧 env 集重建的容器会丢失这层针对
≤512MB 受限节点的软上限保护。本测试锁定 Dockerfile final stage 自带默认值,
并保证默认值与 compose 文档化覆盖值一致(防漂移)。
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO_ROOT / "Dockerfile"
COMPOSE = REPO_ROOT / "docker-compose.yml"

# 镜像内默认值的单一真相源,与 docker-compose.yml 文档化覆盖值保持一致
EXPECTED_GOMEMLIMIT = "320MiB"
EXPECTED_GOGC = "50"


def _final_stage_text() -> str:
    """返回最终镜像层(最后一个 FROM 之后)的 Dockerfile 文本。

    GOMEMLIMIT/GOGC 必须落在 final stage,builder/sub-store-builder 的 ENV
    不进最终镜像,无法被 watchtower 重建的运行容器继承。
    """
    text = DOCKERFILE.read_text(encoding="utf-8")
    from_positions = [m.start() for m in re.finditer(r"(?m)^FROM\s", text)]
    assert from_positions, "Dockerfile 必须至少有一个 FROM"
    return text[from_positions[-1] :]


def test_dockerfile_final_stage_sets_gomemlimit_default() -> None:
    stage = _final_stage_text()
    m = re.search(r'(?m)^ENV\s+GOMEMLIMIT="?([^"\s]+)"?\s*$', stage)
    assert m is not None, "final stage 缺少 ENV GOMEMLIMIT 镜像内默认(§2b/A3)"
    assert m.group(1) == EXPECTED_GOMEMLIMIT


def test_dockerfile_final_stage_sets_gogc_default() -> None:
    stage = _final_stage_text()
    m = re.search(r'(?m)^ENV\s+GOGC="?([^"\s]+)"?\s*$', stage)
    assert m is not None, "final stage 缺少 ENV GOGC 镜像内默认(§2b/A3)"
    assert m.group(1) == EXPECTED_GOGC


def test_image_defaults_match_compose_documented_overrides() -> None:
    """镜像默认值与 compose 文档化覆盖值一致,否则二者漂移会误导运维。"""
    compose = COMPOSE.read_text(encoding="utf-8")
    assert re.search(
        rf'(?m)^\s*-\s*GOMEMLIMIT={re.escape(EXPECTED_GOMEMLIMIT)}\s*$', compose
    ), "docker-compose.yml 的 GOMEMLIMIT 与镜像默认值不一致"
    assert re.search(
        rf'(?m)^\s*-\s*GOGC={re.escape(EXPECTED_GOGC)}\s*$', compose
    ), "docker-compose.yml 的 GOGC 与镜像默认值不一致"
