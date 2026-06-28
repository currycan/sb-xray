"""客户端订阅渲染契约。

事故 2026-06-28:GIST_OWNER 未注入(镜像空默认)时,客户端模板渲染出空 owner 段死链
(``gist.githubusercontent.com//``),客户端 apply 整份订阅失败。下方测试从客户端成品
视角校验:渲染 + sanitize 后不得含空 owner 死链。

§4 边界:gist provider 的 owner 段保持参数化(``${GIST_OWNER}``,经节点 .env 注入,
不硬编码账号);icon 仓库为公开 ``currycan/key``,有意硬编码(非敏感),不在 §4 约束内;
GIST_CODE(私有 gist ID)是敏感信息,走加密 secret blob,不在订阅模板里明文出现。
"""
from __future__ import annotations

from pathlib import Path

import pytest

_TEMPLATE_DIR = Path("templates/client_template")
_YAML_TEMPLATES = sorted(_TEMPLATE_DIR.glob("*.yaml"))


def _render_then_sanitize(
    tpl: Path, env: dict[str, str | None], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> str:
    """复刻生产路径:entrypoint._envsubst_render → config_builder.sanitize_subscription。"""
    from entrypoint import _envsubst_render
    from sb_xray.config_builder import sanitize_subscription

    for key, val in env.items():
        if val is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, val)
    dst = tmp_path / tpl.name
    _envsubst_render(tpl, dst)
    return sanitize_subscription(dst.read_text(encoding="utf-8"))


# --- §4: gist owner 段保持参数化(不硬编码账号)-------------------------------


@pytest.mark.parametrize("tpl", _YAML_TEMPLATES, ids=lambda p: p.name)
def test_gist_owner_not_hardcoded(tpl: Path) -> None:
    """gist provider URL 必须用 ${GIST_OWNER},不得硬编码 owner(§4 敏感资源)。"""
    for line in tpl.read_text(encoding="utf-8").splitlines():
        if "gist.githubusercontent.com/" in line:
            assert "${GIST_OWNER}" in line, f"gist owner 段疑似硬编码: {line.strip()}"


# --- envsubst 语义:owner 展开 -------------------------------------------------


def test_envsubst_render_expands_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """entrypoint._envsubst_render 展开 ${GIST_OWNER};GIST_CODE 未设时留字面。"""
    from entrypoint import _envsubst_render

    monkeypatch.setenv("GIST_OWNER", "acme")
    monkeypatch.delenv("GIST_CODE", raising=False)
    src = tmp_path / "t.yaml"
    src.write_text(
        'a: "https://gh-proxy.com/gist.githubusercontent.com/${GIST_OWNER}/${GIST_CODE}/raw/X"\n',
        encoding="utf-8",
    )
    dst = tmp_path / "out.yaml"
    _envsubst_render(src, dst)
    assert "gist.githubusercontent.com/acme/${GIST_CODE}/raw/X" in dst.read_text(encoding="utf-8")


# --- sanitize_subscription 单元覆盖 -------------------------------------------


def test_sanitize_drops_empty_owner_gist_provider() -> None:
    """空 owner 段(//)的 gist provider 行被丢弃,无关行保留。"""
    from sb_xray.config_builder import sanitize_subscription

    text = (
        '  AllOne: {<<: *BaseProvider, url: "https://gh-proxy.com/'
        'gist.githubusercontent.com//somecode/raw/AllOne-Common"}\n'
        "  KeepMe: {type: http}\n"
    )
    out = sanitize_subscription(text)
    assert "AllOne" not in out
    assert "gist.githubusercontent.com//" not in out
    assert "KeepMe" in out


def test_sanitize_drops_unrendered_owner_gist_provider() -> None:
    """未渲染的 ${GIST_OWNER} gist provider 行被丢弃(向后兼容旧 unset 语义)。"""
    from sb_xray.config_builder import sanitize_subscription

    text = (
        '  AllOne: {url: "https://gh-proxy.com/'
        'gist.githubusercontent.com/${GIST_OWNER}/${GIST_CODE}/raw/X"}\n'
    )
    assert sanitize_subscription(text).strip() == ""


def test_sanitize_keeps_real_owner() -> None:
    """owner 真值在 → 行原样保留(含其后任意 code 段)。"""
    from sb_xray.config_builder import sanitize_subscription

    text = '  AllOne: {url: "https://gh-proxy.com/gist.githubusercontent.com/acme/somecode/raw/X"}\n'
    assert sanitize_subscription(text) == text


def test_sanitize_noop_on_clean_text() -> None:
    """无 gist 死链的文本原样返回(幂等)。"""
    from sb_xray.config_builder import sanitize_subscription

    text = "proxies:\n  - {name: a, type: vless}\n"
    assert sanitize_subscription(text) == text


# --- 集成:真实客户端模板成品无空 owner 死链(此前缺失的"假绿灯"补强)-----------


# GIST_CODE 两种缺省态都覆盖(清单 #7 边界):未设(safe_substitute 留字面)与空串。
@pytest.mark.parametrize("gist_code", [None, ""], ids=["code-unset", "code-empty"])
@pytest.mark.parametrize("tpl", _YAML_TEMPLATES, ids=lambda p: p.name)
def test_real_template_empty_owner_no_dead_links(
    tpl: Path, gist_code: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GIST_OWNER 空(镜像默认未注入)时,渲染+清理后无空 owner 死链、无残留 ${GIST_OWNER}。"""
    out = _render_then_sanitize(
        tpl, {"GIST_OWNER": "", "GIST_CODE": gist_code}, monkeypatch, tmp_path
    )
    assert "gist.githubusercontent.com//" not in out, "空 owner gist 死链未清理"
    assert "${GIST_OWNER}" not in out


def test_real_template_owner_set_renders_usable_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """owner+code 注入后,gist provider 以真值完整渲染、无死链。"""
    tpl = _TEMPLATE_DIR / "OneSmartLite.yaml"
    out = _render_then_sanitize(
        tpl, {"GIST_OWNER": "acme", "GIST_CODE": "code123"}, monkeypatch, tmp_path
    )
    assert "gist.githubusercontent.com/acme/code123/raw/" in out  # owner+code 真值入 URL
    assert "gist.githubusercontent.com//" not in out  # 无死链
