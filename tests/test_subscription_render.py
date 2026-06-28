"""客户端订阅渲染契约。

E1 §4: client templates 不得硬编码 GitHub 账号。
事故 2026-06-28: GIST_OWNER/ICON_REPO 未注入(镜像空默认)时,客户端模板渲染出空
owner 段死链(``gist.githubusercontent.com//``)与残留 ``${GIST_CODE}``,客户端 apply
整份订阅失败。下方测试**从客户端成品视角**校验:渲染 + sanitize 后不得含死链。
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


# --- §4: 无硬编码账号 ----------------------------------------------------------


@pytest.mark.parametrize("tpl", _YAML_TEMPLATES, ids=lambda p: p.name)
def test_client_template_has_no_hardcoded_account(tpl: Path) -> None:
    assert "currycan" not in tpl.read_text(encoding="utf-8")


# --- envsubst 语义:owner 展开、GIST_CODE 保持字面(客户端自填)-------------------


def test_envsubst_render_expands_owner_keeps_gist_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """entrypoint._envsubst_render expands GIST_OWNER/ICON_REPO; GIST_CODE stays literal."""
    from entrypoint import _envsubst_render

    monkeypatch.setenv("GIST_OWNER", "acme")
    monkeypatch.setenv("ICON_REPO", "acme/icons")
    monkeypatch.delenv("GIST_CODE", raising=False)
    src = tmp_path / "t.yaml"
    src.write_text(
        'a: "https://gh-proxy.com/gist.githubusercontent.com/${GIST_OWNER}/${GIST_CODE}/raw/X"\n'
        "b: https://gh-proxy.com/raw.githubusercontent.com/${ICON_REPO}/master/icons/AI.png\n",
        encoding="utf-8",
    )
    dst = tmp_path / "out.yaml"
    _envsubst_render(src, dst)
    out = dst.read_text(encoding="utf-8")
    assert "gist.githubusercontent.com/acme/${GIST_CODE}/raw/X" in out
    assert "raw.githubusercontent.com/acme/icons/master/icons/AI.png" in out


# --- sanitize_subscription 单元覆盖 -------------------------------------------


def test_sanitize_drops_empty_owner_gist_provider() -> None:
    """空 owner 段(//)的 gist provider 行被丢弃。"""
    from sb_xray.config_builder import sanitize_subscription

    text = (
        '  AllOne: {<<: *BaseProvider, url: "https://gh-proxy.com/'
        'gist.githubusercontent.com//${GIST_CODE}/raw/AllOne-Common"}\n'
        "  KeepMe: {type: http}\n"
    )
    out = sanitize_subscription(text)
    assert "AllOne" not in out
    assert "gist.githubusercontent.com//" not in out
    assert "KeepMe" in out  # 无关行保留


def test_sanitize_drops_unrendered_owner_gist_provider() -> None:
    """未渲染的 ${GIST_OWNER} gist provider 行被丢弃(向后兼容旧 unset 语义)。"""
    from sb_xray.config_builder import sanitize_subscription

    text = (
        '  AllOne: {url: "https://gh-proxy.com/'
        'gist.githubusercontent.com/${GIST_OWNER}/${GIST_CODE}/raw/X"}\n'
    )
    assert sanitize_subscription(text).strip() == ""


def test_sanitize_keeps_real_owner_and_literal_gist_code() -> None:
    """owner 真值在 → 行保留,且设计内的字面 ${GIST_CODE} 不被误删。"""
    from sb_xray.config_builder import sanitize_subscription

    text = (
        '  AllOne: {url: "https://gh-proxy.com/'
        'gist.githubusercontent.com/acme/${GIST_CODE}/raw/X"}\n'
    )
    out = sanitize_subscription(text)
    assert "gist.githubusercontent.com/acme/${GIST_CODE}/raw/X" in out
    assert "${GIST_CODE}" in out  # 客户端自填,不得清理


def test_sanitize_strips_empty_icon_field_keeps_entry() -> None:
    """空 repo 段的 icon 字段被剥离,但所在条目(name 等)保留。"""
    from sb_xray.config_builder import sanitize_subscription

    text = (
        "  - {name: Claude, <<: *SelectAI, icon: https://gh-proxy.com/"
        "raw.githubusercontent.com//master/icons/ClaudeCode.png}\n"
    )
    out = sanitize_subscription(text)
    assert "raw.githubusercontent.com//" not in out
    assert "name: Claude" in out  # 条目本身不丢
    assert "}" in out  # YAML map 闭合未被破坏


def test_sanitize_keeps_real_icon() -> None:
    """icon repo 真值在 → icon 字段保留。"""
    from sb_xray.config_builder import sanitize_subscription

    text = (
        "  - {name: Gemini, icon: https://gh-proxy.com/"
        "raw.githubusercontent.com/acme/icons/master/icons/gemini.png}\n"
    )
    assert "raw.githubusercontent.com/acme/icons/" in sanitize_subscription(text)


def test_sanitize_noop_on_clean_text() -> None:
    """无 GitHub 死链的文本原样返回(幂等)。"""
    from sb_xray.config_builder import sanitize_subscription

    text = "proxies:\n  - {name: a, type: vless}\n"
    assert sanitize_subscription(text) == text


# --- 集成:真实客户端模板成品不得含死链(此前缺失的"假绿灯"补强)-----------------


@pytest.mark.parametrize("tpl", _YAML_TEMPLATES, ids=lambda p: p.name)
def test_real_template_empty_env_no_dead_links(
    tpl: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GIST_OWNER/ICON_REPO 空(镜像默认未注入)时,渲染+清理后的订阅无死链、无残留占位符。"""
    out = _render_then_sanitize(
        tpl,
        {"GIST_OWNER": "", "ICON_REPO": "", "GIST_CODE": None},
        monkeypatch,
        tmp_path,
    )
    assert "gist.githubusercontent.com//" not in out, "空 owner gist 死链未清理"
    assert "raw.githubusercontent.com//" not in out, "空 repo icon 死链未清理"
    assert "${GIST_OWNER}" not in out
    assert "${ICON_REPO}" not in out


def test_real_template_owner_set_renders_usable_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """owner 注入后,gist provider 以真值渲染、字面 ${GIST_CODE} 保留供客户端自填、无死链。"""
    tpl = _TEMPLATE_DIR / "OneSmartLite.yaml"
    out = _render_then_sanitize(
        tpl,
        {"GIST_OWNER": "acme", "ICON_REPO": "acme/icons", "GIST_CODE": None},
        monkeypatch,
        tmp_path,
    )
    assert "gist.githubusercontent.com/acme/" in out  # 真 owner 渲染入 URL
    assert "${GIST_CODE}" in out  # 客户端自填占位符保留
    assert "gist.githubusercontent.com//" not in out  # 无死链
