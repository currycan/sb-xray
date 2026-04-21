"""Tests for sb_xray.templates (entrypoint.sh §5 equivalent)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sb_xray import templates as tpl


def test_render_string_substitutes_dollar_brace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FOO", "bar")
    # Bash-style ${FOO} placeholder should be recognized
    assert tpl.render_string("hello ${FOO}") == "hello bar"


def test_render_string_missing_var_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NEVER_SET", raising=False)
    with pytest.raises(tpl.TemplateError):
        tpl.render_string("value=${NEVER_SET}")


def test_render_string_explicit_context_overrides_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("X", "from-env")
    assert tpl.render_string("${X}", context={"X": "from-ctx"}) == "from-ctx"


def test_render_file_produces_bytes_on_dest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "inbound.tpl.json"
    dest = tmp_path / "out" / "inbound.json"
    src.write_text('{"uuid": "${XRAY_UUID}", "port": ${PORT}}\n', encoding="utf-8")
    monkeypatch.setenv("XRAY_UUID", "abcd-1234")
    monkeypatch.setenv("PORT", "443")
    tpl.render_file(src, dest)
    data = json.loads(dest.read_text(encoding="utf-8"))
    assert data == {"uuid": "abcd-1234", "port": 443}


def test_render_file_non_json_copies_verbatim_after_subst(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "nginx.conf.tpl"
    dest = tmp_path / "nginx.conf"
    src.write_text("server_name ${DOMAIN};\n", encoding="utf-8")
    monkeypatch.setenv("DOMAIN", "vpn.example.com")
    tpl.render_file(src, dest)
    assert dest.read_text(encoding="utf-8") == "server_name vpn.example.com;\n"


def test_render_file_creates_parent_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "x.tpl"
    dest = tmp_path / "deep" / "nest" / "x.out"
    src.write_text("${FOO}", encoding="utf-8")
    monkeypatch.setenv("FOO", "ok")
    tpl.render_file(src, dest)
    assert dest.read_text(encoding="utf-8") == "ok"


def test_render_string_preserves_literals_no_placeholder() -> None:
    assert tpl.render_string("plain text 123") == "plain text 123"


def test_render_file_invalid_json_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "broken.json"
    dest = tmp_path / "out.json"
    src.write_text('{"broken": ${VAL}', encoding="utf-8")  # missing brace
    monkeypatch.setenv("VAL", '"x"')
    with pytest.raises(tpl.TemplateError, match="invalid JSON"):
        tpl.render_file(src, dest)
