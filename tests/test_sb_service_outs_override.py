"""Tests for sb_xray.config_builder's sb.json env override (Phase 4)."""

from __future__ import annotations

import os

import pytest
from sb_xray import config_builder
from sb_xray.routing.service_spec import SERVICE_SPECS


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    for spec in SERVICE_SPECS:
        monkeypatch.delenv(spec.env_var, raising=False)
    for k in ("HAS_ISP_NODES", "ISP_PER_SERVICE_SB"):
        monkeypatch.delenv(k, raising=False)


def test_snapshot_captures_values() -> None:
    os.environ["NETFLIX_OUT"] = "isp-auto"
    os.environ["CHATGPT_OUT"] = "proxy-cn2"
    snap = config_builder._snapshot_service_outs()
    assert snap["NETFLIX_OUT"] == "isp-auto"
    assert snap["CHATGPT_OUT"] == "proxy-cn2"
    # Unset vars are captured as None.
    assert snap["DISNEY_OUT"] is None


def test_override_swaps_only_isp_auto() -> None:
    os.environ["NETFLIX_OUT"] = "isp-auto"
    os.environ["CHATGPT_OUT"] = "proxy-cn2"
    os.environ["CLAUDE_OUT"] = "direct"
    config_builder._override_service_outs_for_sb()
    assert os.environ["NETFLIX_OUT"] == "isp-auto-netflix"
    assert os.environ["CHATGPT_OUT"] == "proxy-cn2"
    assert os.environ["CLAUDE_OUT"] == "direct"


def test_restore_round_trips() -> None:
    os.environ["NETFLIX_OUT"] = "isp-auto"
    os.environ["CHATGPT_OUT"] = "proxy-cn2"
    snap = config_builder._snapshot_service_outs()
    config_builder._override_service_outs_for_sb()
    assert os.environ["NETFLIX_OUT"] == "isp-auto-netflix"
    config_builder._restore_service_outs(snap)
    assert os.environ["NETFLIX_OUT"] == "isp-auto"
    assert os.environ["CHATGPT_OUT"] == "proxy-cn2"
    # Previously-unset values are removed, not left as empty.
    assert "DISNEY_OUT" not in os.environ


def _stage_template_dir(tmp_path, monkeypatch: pytest.MonkeyPatch) -> object:  # type: ignore[no-untyped-def]
    """Redirect ``_TEMPLATES`` to a dir containing a stub sb.json."""
    templates = tmp_path / "templates"
    (templates / "sing-box").mkdir(parents=True)
    (templates / "sing-box" / "sb.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(config_builder, "_TEMPLATES", templates)
    return templates / "sing-box" / "sb.json"


def test_render_sb_templates_applies_override_flag_on(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stage_template_dir(tmp_path, monkeypatch)
    workdir = tmp_path / "work"
    monkeypatch.setenv("ISP_PER_SERVICE_SB", "true")
    monkeypatch.setenv("HAS_ISP_NODES", "true")
    monkeypatch.setenv("NETFLIX_OUT", "isp-auto")
    captured: dict[str, str] = {}

    def _fake_render(src, dest) -> None:  # type: ignore[no-untyped-def]
        captured[str(dest)] = os.environ.get("NETFLIX_OUT", "")

    monkeypatch.setattr(config_builder, "_render_json", _fake_render)
    config_builder._render_sing_box_templates(workdir)
    assert captured  # at least one render happened
    assert any(v == "isp-auto-netflix" for v in captured.values())
    assert os.environ["NETFLIX_OUT"] == "isp-auto"


def test_render_sb_templates_no_override_flag_off(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stage_template_dir(tmp_path, monkeypatch)
    workdir = tmp_path / "work"
    monkeypatch.delenv("ISP_PER_SERVICE_SB", raising=False)
    monkeypatch.setenv("HAS_ISP_NODES", "true")
    monkeypatch.setenv("NETFLIX_OUT", "isp-auto")
    captured: dict[str, str] = {}

    def _fake_render(src, dest) -> None:  # type: ignore[no-untyped-def]
        captured[str(dest)] = os.environ.get("NETFLIX_OUT", "")

    monkeypatch.setattr(config_builder, "_render_json", _fake_render)
    config_builder._render_sing_box_templates(workdir)
    assert all(v == "isp-auto" for v in captured.values())


def test_render_restores_env_even_if_render_raises(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stage_template_dir(tmp_path, monkeypatch)
    workdir = tmp_path / "work"
    monkeypatch.setenv("ISP_PER_SERVICE_SB", "true")
    monkeypatch.setenv("HAS_ISP_NODES", "true")
    monkeypatch.setenv("NETFLIX_OUT", "isp-auto")

    def _boom(*_a: object, **_kw: object) -> None:
        raise RuntimeError("render fail")

    monkeypatch.setattr(config_builder, "_render_json", _boom)
    with pytest.raises(RuntimeError):
        config_builder._render_sing_box_templates(workdir)
    assert os.environ["NETFLIX_OUT"] == "isp-auto"
