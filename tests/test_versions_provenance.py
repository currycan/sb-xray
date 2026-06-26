import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_VERSIONS_JSON = _REPO_ROOT / "versions.json"


@pytest.fixture(scope="module")
def versions() -> dict:
    return json.loads(_VERSIONS_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def digests(versions: dict) -> dict:
    assert "digests" in versions, "versions.json 缺 digests 段"
    return versions["digests"]


# 所有「进入最终镜像的二进制」都必须在 versions.json 留有 out-of-band SHA 锚。
# xray/shoutrrr 历史上只在 Dockerfile 内联校验同源 .dgst/checksums，未留账（B7）。
_REQUIRED_DIGEST_KEYS = {
    "mihomo_amd64_sha256",
    "mihomo_arm64_sha256",
    "dufs_amd64_sha256",
    "dufs_arm64_sha256",
    "cloudflared_amd64_sha256",
    "cloudflared_arm64_sha256",
    "xui_amd64_sha256",
    "xui_arm64_sha256",
    "sing_box_amd64_sha256",
    "sing_box_arm64_sha256",
    "http_meta_bundle_sha256",
    "http_meta_tpl_sha256",
    "sub_store_backend_sha256",
    "xray_amd64_sha256",
    "xray_arm64_sha256",
    "shoutrrr_amd64_sha256",
    "shoutrrr_arm64_sha256",
}


def test_all_shipped_binaries_have_digest_anchor(digests: dict) -> None:
    missing = _REQUIRED_DIGEST_KEYS - set(digests)
    assert not missing, f"versions.json.digests 缺少 out-of-band 锚: {sorted(missing)}"


def test_digests_are_64_hex_sha256(digests: dict) -> None:
    for key in _REQUIRED_DIGEST_KEYS:
        val = digests.get(key, "")
        assert len(val) == 64 and all(c in "0123456789abcdef" for c in val), (
            f"{key} 非合法 64 位 hex sha256: {val!r}"
        )
