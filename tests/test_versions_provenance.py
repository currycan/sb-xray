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


# ── provenance manifest contract ─────────────────────────────────────────────

# 进入最终镜像的全部 shipped 组件（与 docs/00 §4.1 组件表 + Dockerfile 一致）
_SHIPPED_COMPONENTS = {
    "xray", "sing_box", "mihomo", "dufs", "cloudflared", "xui",
    "http_meta", "sub_store_backend", "sub_store_frontend",
    "shoutrrr", "crypctl", "acme",
}

# pinned:true 的二进制组件 → 其 versions.json.digests 必须有对应锚（防声称 pin 但无锚）
_PINNED_REQUIRES_ANCHOR = {
    "xray": ("xray_amd64_sha256", "xray_arm64_sha256"),
    "shoutrrr": ("shoutrrr_amd64_sha256", "shoutrrr_arm64_sha256"),
    "mihomo": ("mihomo_amd64_sha256", "mihomo_arm64_sha256"),
    "dufs": ("dufs_amd64_sha256", "dufs_arm64_sha256"),
    "cloudflared": ("cloudflared_amd64_sha256", "cloudflared_arm64_sha256"),
    "xui": ("xui_amd64_sha256", "xui_arm64_sha256"),
    "sing_box": ("sing_box_amd64_sha256", "sing_box_arm64_sha256"),
}


@pytest.fixture(scope="module")
def provenance(versions: dict) -> dict:
    assert "provenance" in versions, "versions.json 缺 provenance 段"
    return versions["provenance"]


def test_provenance_covers_all_shipped_components(provenance: dict) -> None:
    missing = _SHIPPED_COMPONENTS - set(provenance)
    assert not missing, f"provenance 未覆盖 shipped 组件: {sorted(missing)}"


def test_provenance_entries_have_required_fields(provenance: dict) -> None:
    for name, entry in provenance.items():
        assert set(entry) >= {"pinned", "reproducible", "note"}, (
            f"provenance[{name}] 缺字段: {entry}"
        )
        assert isinstance(entry["pinned"], bool)
        assert isinstance(entry["reproducible"], bool)
        assert entry["note"].strip(), f"provenance[{name}].note 不可为空"


def test_pinned_binaries_have_real_digest_anchor(provenance: dict, digests: dict) -> None:
    for comp, keys in _PINNED_REQUIRES_ANCHOR.items():
        assert provenance[comp]["pinned"] is True, f"{comp} 应标 pinned:true"
        for k in keys:
            assert k in digests, f"{comp} 标 pinned 但 digests 缺锚 {k}"
