"""E1 §4: client templates must not hardcode a GitHub account."""
from __future__ import annotations

from pathlib import Path

import pytest

_TEMPLATE_DIR = Path("templates/client_template")
_YAML_TEMPLATES = sorted(_TEMPLATE_DIR.glob("*.yaml"))


@pytest.mark.parametrize("tpl", _YAML_TEMPLATES, ids=lambda p: p.name)
def test_client_template_has_no_hardcoded_account(tpl: Path) -> None:
    assert "currycan" not in tpl.read_text(encoding="utf-8")


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


def test_envsubst_render_empty_owner_no_literal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty GIST_OWNER/ICON_REPO (image default) render to empty, never crash."""
    from entrypoint import _envsubst_render

    monkeypatch.setenv("GIST_OWNER", "")
    monkeypatch.setenv("ICON_REPO", "")
    src = tmp_path / "t.yaml"
    src.write_text("a: ${GIST_OWNER}\nb: ${ICON_REPO}\n", encoding="utf-8")
    dst = tmp_path / "o.yaml"
    _envsubst_render(src, dst)
    out = dst.read_text(encoding="utf-8")
    assert "${GIST_OWNER}" not in out
    assert "${ICON_REPO}" not in out
