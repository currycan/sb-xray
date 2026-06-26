"""Supply-chain pin regression tests (WU P0-4: B1/B2/B3).

These verify that Dockerfile/build.sh/versions.json pin the three previously
unverified fetches (Sub-Store frontend, crypctl, acme.sh) to immutable refs.
All checks read the real repo files — no network, no image build.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_DOCKERFILE = _REPO / "Dockerfile"
_BUILD_SH = _REPO / "build.sh"
_VERSIONS = _REPO / "versions.json"

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _HEX40_or_64(v: str) -> bool:
    return bool(_HEX64.match(v))


def _versions() -> dict:
    return json.loads(_VERSIONS.read_text(encoding="utf-8"))


def test_frontend_commit_sha_recorded_in_versions_json() -> None:
    data = _versions()
    sha = data.get("sub_store_frontend_sha", "")
    assert _HEX40.match(sha), (
        f"sub_store_frontend_sha must be a 40-hex commit SHA, got {sha!r}"
    )


def test_build_sh_reads_and_passes_frontend_sha() -> None:
    src = _BUILD_SH.read_text(encoding="utf-8")
    assert "get_cached_version sub_store_frontend_sha" in src
    assert "_require_sha \"SUB_STORE_FRONTEND_SHA\"" in src
    assert "--build-arg SUB_STORE_FRONTEND_SHA=" in src


def test_dockerfile_frontend_checks_out_pinned_sha() -> None:
    src = _DOCKERFILE.read_text(encoding="utf-8")
    assert "ARG SUB_STORE_FRONTEND_SHA" in src, "frontend stage must declare SHA build-arg"
    # 必须在 clone 后显式 checkout 锁定 commit，而非信任可变 tag
    assert "git checkout" in src and "${SUB_STORE_FRONTEND_SHA}" in src, (
        "frontend stage must `git checkout ${SUB_STORE_FRONTEND_SHA}`"
    )
    # 空值即拒绝构建（与其它组件 _SHA256 build-arg 一致的 fail-closed 语义）
    assert 'SUB_STORE_FRONTEND_SHA' in src and 'build-arg required' in src


def test_crypctl_ref_pinned_not_floating_head() -> None:
    data = _versions()
    ref = data.get("crypctl_sha", "")
    assert _HEX40.match(ref), f"crypctl_sha must be a 40-hex commit SHA, got {ref!r}"
    src = _DOCKERFILE.read_text(encoding="utf-8")
    assert "ARG CRYPCTL_REF" in src, "crypctl stage must declare CRYPCTL_REF build-arg"
    # 不得再 checkout HEAD（漂移源）；必须 checkout 注入的 ref
    assert "git checkout HEAD -- docker/crypctl" not in src, (
        "crypctl must not checkout floating HEAD"
    )
    assert "${CRYPCTL_REF}" in src


def test_build_sh_wires_crypctl_ref() -> None:
    src = _BUILD_SH.read_text(encoding="utf-8")
    assert "get_cached_version crypctl_sha" in src
    assert "_require_sha \"CRYPCTL_REF\"" in src
    assert "--build-arg CRYPCTL_REF=" in src


def test_acme_sh_pinned_and_autoupgrade_disabled() -> None:
    src = _DOCKERFILE.read_text(encoding="utf-8")
    # AUTO_UPGRADE 必须显式关闭，杜绝运行时静默自升级
    assert "ENV AUTO_UPGRADE=1" not in src, "AUTO_UPGRADE=1 must be removed"
    assert "ENV AUTO_UPGRADE=0" in src, "AUTO_UPGRADE must be explicitly 0"
    # 不得再 curl|sh 直跑 get.acme.sh 的浮动 master
    assert "curl -L https://get.acme.sh | sh" not in src, (
        "acme.sh must not pipe floating get.acme.sh into sh"
    )
    # 必须 pin 版本 + 校验 checksum
    assert "ARG ACME_SH_VERSION" in src and "ARG ACME_SH_SHA256" in src
    assert "sha256sum -c -" in src.split("acme", 1)[1][:1200] or "${ACME_SH_SHA256}" in src

    data = _versions()
    assert _HEX40_or_64(data.get("acme_sh_sha256", "")), "acme_sh_sha256 must be 64-hex"
    assert data.get("acme_sh", "")


def test_build_sh_wires_acme_sh() -> None:
    src = _BUILD_SH.read_text(encoding="utf-8")
    assert "get_cached_version acme_sh" in src
    assert "_require_sha \"ACME_SH_SHA256\"" in src
    assert "--build-arg ACME_SH_VERSION=" in src
    assert "--build-arg ACME_SH_SHA256=" in src
