"""Tests for scripts/entrypoint.py (Phase 1 thin shell)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import entrypoint as ep  # noqa: E402


def test_bootstrap_loads_persisted_vars_into_environ(
    tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp_env_file.write_text(
        "export DOMAIN='vpn.example.com'\nexport PORT_HY='4443'\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("DOMAIN", raising=False)
    monkeypatch.delenv("PORT_HY", raising=False)
    monkeypatch.delenv("ENV_FILE", raising=False)
    ep.bootstrap(tmp_env_file)
    assert os.environ["DOMAIN"] == "vpn.example.com"
    assert os.environ["PORT_HY"] == "4443"
    # Regression: ENV_FILE itself must be exported so log_summary_box can
    # display the resolved path (cold-start N/A bug).
    assert os.environ["ENV_FILE"] == str(tmp_env_file)


def test_main_sources_status_file_for_summary_box(
    tmp_env_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """main() must source STATUS_FILE so ISP_TAG shows in log_summary_box
    (entrypoint.sh:main_init L1351-1352 equivalent).
    """
    status_file = tmp_path / "status"
    status_file.write_text(
        "export ISP_TAG='cn2gia'\nexport IS_8K_SMOOTH='false'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("STATUS_FILE", str(status_file))
    for key in ("ISP_TAG", "IS_8K_SMOOTH"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(ep, "run_legacy", lambda _s, _e=None: 0)

    rc = ep.main(["--env-file", str(tmp_env_file), "run"])
    assert rc == 0
    assert os.environ["ISP_TAG"] == "cn2gia"
    assert os.environ["IS_8K_SMOOTH"] == "false"


def test_bootstrap_shell_env_wins(tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp_env_file.write_text("export DOMAIN='old'\n", encoding="utf-8")
    monkeypatch.setenv("DOMAIN", "new")
    ep.bootstrap(tmp_env_file)
    assert os.environ["DOMAIN"] == "new"


def test_dry_run_exits_zero_without_invoking_legacy(
    tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = {"ran": False}

    def fake_run_legacy(
        _skip: list[str], _extras: list[str] | None = None
    ) -> int:  # pragma: no cover
        called["ran"] = True
        return 99

    monkeypatch.setattr(ep, "run_legacy", fake_run_legacy)
    rc = ep.main(["--env-file", str(tmp_env_file), "run", "--dry-run"])
    assert rc == 0
    assert called["ran"] is False


def test_main_delegates_to_legacy_when_not_dry_run(
    tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    received: dict[str, list[str]] = {}

    def fake_run_legacy(skip: list[str], extras: list[str] | None = None) -> int:
        received["skip"] = skip
        received["extras"] = extras or []
        return 0

    monkeypatch.setattr(ep, "run_legacy", fake_run_legacy)
    rc = ep.main(
        [
            "--env-file",
            str(tmp_env_file),
            "run",
            "--skip-stage",
            "speed_test",
            "--skip-stage",
            "media",
        ]
    )
    assert rc == 0
    assert received["skip"] == ["speed_test", "media"]


def test_run_legacy_returns_127_when_script_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ep, "_LEGACY_ENTRYPOINT", tmp_path / "nope.sh")
    assert ep.run_legacy([]) == 127


def test_probe_base_env_persists_fields(
    tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sb_xray.env import EnvManager

    for key in (
        "GEOIP_INFO",
        "IP_TYPE",
        "IS_BRUTAL",
        "STRATEGY",
        "XUI_LOCAL_PORT",
        "DUFS_PORT",
        "PASSWORD",
        "XRAY_UUID",
        "XRAY_REVERSE_UUID",
        "SB_UUID",
        "XRAY_REALITY_SHORTID",
        "XRAY_REALITY_SHORTID_2",
        "XRAY_REALITY_SHORTID_3",
        "XRAY_URL_PATH",
        "SUBSCRIBE_TOKEN",
        "SUB_STORE_FRONTEND_BACKEND_PATH",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(ep.sbnet, "get_geo_info", lambda: "Tokyo JP|8.8.8.8")
    monkeypatch.setattr(ep.sbnet, "check_ip_type", lambda: "isp")
    monkeypatch.setattr(ep.sbnet, "check_brutal_status", lambda: "true")
    monkeypatch.setattr(ep.sbnet, "probe_ip_sb", lambda: (True, False))
    ep.probe_base_env(EnvManager(tmp_env_file))
    content = tmp_env_file.read_text(encoding="utf-8")
    assert "export GEOIP_INFO='Tokyo JP|8.8.8.8'" in content
    assert "export IP_TYPE='isp'" in content
    assert "export IS_BRUTAL='true'" in content  # bash var is IS_BRUTAL (not BRUTAL_STATUS)
    assert os.environ["IP_TYPE"] == "isp"
    # Newly-ported fields
    assert "export XRAY_UUID='" in content
    assert "export PASSWORD='" in content
    assert "export SUB_STORE_FRONTEND_BACKEND_PATH='/" in content  # leading slash preserved
    assert "export XRAY_REALITY_SHORTID='" in content
    # STRATEGY comes from detect_ip_strategy(v4_ok=True, v6_ok=False) → "ipv4"
    assert "export STRATEGY='" in content


def test_python_stage_probe_invokes_probe_base_env(
    tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = {"probe": False}

    def fake_probe(_: object) -> None:
        called["probe"] = True

    monkeypatch.setattr(ep, "probe_base_env", fake_probe)
    monkeypatch.setattr(ep, "run_legacy", lambda _s, _e=None: 0)
    rc = ep.main(["--env-file", str(tmp_env_file), "run", "--python-stage", "probe"])
    assert rc == 0
    assert called["probe"] is True


def test_trailing_docker_cmd_args_are_ignored(
    tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dockerfile CMD ['supervisord'] becomes trailing argv; parser tolerates it."""
    received: dict[str, list[str]] = {}

    def fake_run_legacy(skip: list[str], extras: list[str] | None = None) -> int:
        received["skip"] = skip
        received["extras"] = extras or []
        return 0

    monkeypatch.setattr(ep, "run_legacy", fake_run_legacy)
    # Simulate docker: ENTRYPOINT [python, entrypoint.py, run] + CMD [supervisord]
    rc = ep.main(["--env-file", str(tmp_env_file), "run", "supervisord"])
    assert rc == 0
    assert "supervisord" in received["extras"]


def test_show_subcommand_runs_pipeline(
    tmp_env_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WORKDIR", str(tmp_path))
    called: dict[str, object] = {"show": False}

    def fake_show(*, archive_path: Path | None = None) -> None:
        called["show"] = True
        called["archive"] = archive_path

    # Isolate from /templates and /sources which only exist in the container.
    monkeypatch.setattr(ep, "_CLIENT_TEMPLATE_DIR", tmp_path / "noop-templates")
    monkeypatch.setattr(ep, "_SOURCES_DIR", tmp_path / "noop-sources")
    monkeypatch.setattr(ep.sbdisplay, "show_info_links", fake_show)
    rc = ep.main(["--env-file", str(tmp_env_file), "show"])
    assert rc == 0
    assert called["show"] is True
    archive = called["archive"]
    assert isinstance(archive, Path)
    assert archive.name == "show-config"
    # write_subscriptions should have produced the base64 files.
    assert (tmp_path / "subscribe" / "v2rayn").is_file()
    assert (tmp_path / "subscribe" / "v2rayn-compat").is_file()


def test_show_pipeline_loads_status_and_secret_files(
    tmp_env_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """show-config.sh:14-16 sources STATUS_FILE + SECRET_FILE so NODE_SUFFIX
    can see ISP_TAG / IS_8K_SMOOTH. The Python port must follow suit.
    """
    status_file = tmp_path / "status"
    status_file.write_text(
        "export ISP_TAG='wireguard-hk'\nexport IS_8K_SMOOTH='true'\n",
        encoding="utf-8",
    )
    secret_file = tmp_path / "secret"
    secret_file.write_text("export REMOTE_KEY='abc123'\n", encoding="utf-8")

    monkeypatch.setenv("WORKDIR", str(tmp_path))
    monkeypatch.setenv("STATUS_FILE", str(status_file))
    monkeypatch.setenv("SECRET_FILE", str(secret_file))
    for key in ("ISP_TAG", "IS_8K_SMOOTH", "REMOTE_KEY", "NODE_SUFFIX"):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setattr(ep, "_CLIENT_TEMPLATE_DIR", tmp_path / "noop-tpl")
    monkeypatch.setattr(ep, "_SOURCES_DIR", tmp_path / "noop-src")
    monkeypatch.setattr(ep.sbdisplay, "show_info_links", lambda *, archive_path=None: None)

    rc = ep.main(["--env-file", str(tmp_env_file), "show"])
    assert rc == 0
    assert os.environ["ISP_TAG"] == "wireguard-hk"
    assert os.environ["IS_8K_SMOOTH"] == "true"
    assert os.environ["REMOTE_KEY"] == "abc123"
    # NODE_SUFFIX derivation should have observed ISP_TAG + IS_8K_SMOOTH
    # → "good" tag in suffix.
    assert "✈ good" in os.environ["NODE_SUFFIX"]


def test_python_stage_probe_skipped_by_default(
    tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = {"probe": False}

    def fake_probe(_: object) -> None:
        called["probe"] = True

    monkeypatch.setattr(ep, "probe_base_env", fake_probe)
    monkeypatch.setattr(ep, "run_legacy", lambda _s, _e=None: 0)
    ep.main(["--env-file", str(tmp_env_file)])
    assert called["probe"] is False


def test_run_legacy_invokes_subprocess(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_script = tmp_path / "fake.sh"
    fake_script.write_text("#!/usr/bin/env bash\nexit 42\n", encoding="utf-8")
    fake_script.chmod(0o755)
    monkeypatch.setattr(ep, "_LEGACY_ENTRYPOINT", fake_script)

    captured: dict[str, object] = {}
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        captured["cmd"] = cmd
        captured["env_has_skip"] = "SB_XRAY_SKIP_STAGES" in kwargs["env"]
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    rc = ep.run_legacy(["stage_a"])
    assert rc == 42
    assert captured["env_has_skip"] is True
    cmd = captured["cmd"]
    assert str(fake_script) in cmd
    # Default extras ["supervisord"] forwarded as trailing $@ for bash.
    assert cmd[-1] == "supervisord"


def test_python_stage_cert_invokes_ensure_certificate(
    tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: dict[str, object] = {"n": 0}

    def fake_issue() -> None:
        called["n"] = 1

    monkeypatch.setattr(ep, "issue_bundle_certificate", fake_issue)
    monkeypatch.setattr(ep, "run_legacy", lambda _s, _e=None: 0)
    rc = ep.main(["--env-file", str(tmp_env_file), "run", "--python-stage", "cert"])
    assert rc == 0
    assert called["n"] == 1


def test_python_stage_cert_not_invoked_by_default(
    tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = {"n": 0}

    def fake_issue() -> None:
        called["n"] = 1

    monkeypatch.setattr(ep, "issue_bundle_certificate", fake_issue)
    monkeypatch.setattr(ep, "run_legacy", lambda _s, _e=None: 0)
    ep.main(["--env-file", str(tmp_env_file), "run"])
    assert called["n"] == 0


def test_issue_bundle_certificate_skips_when_domain_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DOMAIN", raising=False)
    monkeypatch.delenv("CDNDOMAIN", raising=False)

    def must_not_call(*a: object, **kw: object) -> object:
        raise AssertionError("ensure_certificate should not run when DOMAIN is empty")

    # import sb_xray.cert via the same namespace ep uses
    import sb_xray.cert as sbcert

    monkeypatch.setattr(sbcert, "ensure_certificate", must_not_call)
    ep.issue_bundle_certificate()  # no exception → success


def test_python_stage_media_invokes_probes(
    tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sb_xray.routing import media as sbmedia

    fake_results = {
        "NETFLIX_OUT": "direct",
        "DISNEY_OUT": "fallback",
        "YOUTUBE_OUT": "direct",
        "SOCIAL_MEDIA_OUT": "fallback",
        "TIKTOK_OUT": "direct",
        "CHATGPT_OUT": "direct",
        "CLAUDE_OUT": "direct",
        "GEMINI_OUT": "fallback",
    }

    def fake_check_all() -> dict[str, str]:
        return fake_results

    monkeypatch.setattr(sbmedia, "check_all", fake_check_all)
    monkeypatch.setattr(ep, "run_legacy", lambda _s, _e=None: 0)
    for key in fake_results:
        monkeypatch.delenv(key, raising=False)

    rc = ep.main(["--env-file", str(tmp_env_file), "run", "--python-stage", "media"])
    assert rc == 0
    assert os.environ["NETFLIX_OUT"] == "direct"
    assert os.environ["GEMINI_OUT"] == "fallback"


def test_trim_subcommand_invokes_trim_runtime_configs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`entrypoint.py trim` must call config_builder.trim_runtime_configs()
    exactly once and return 0 without touching the legacy Bash handoff."""
    from sb_xray import config_builder as sbcfg

    calls: list[dict] = []

    def fake_trim(**kwargs) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(sbcfg, "trim_runtime_configs", fake_trim)

    def bash_must_not_run(_s, _e=None):
        raise AssertionError("run_legacy should not be invoked by trim subcommand")

    monkeypatch.setattr(ep, "run_legacy", bash_must_not_run)

    rc = ep.main(["trim"])
    assert rc == 0
    assert len(calls) == 1


def test_python_stage_providers_invokes_generate_and_export(
    tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from sb_xray.routing import providers as sbprov

    called = {"n": 0}

    def fake_generate(*, workdir: Path | None = None) -> dict[str, str]:
        called["n"] = 1
        return {
            "CLASH_PROXY_PROVIDERS": "",
            "SURGE_PROXY_PROVIDERS": "",
            "SURGE_PROVIDER_NAMES": "",
            "STASH_PROVIDER_NAMES": "",
        }

    monkeypatch.setattr(sbprov, "generate_and_export", fake_generate)
    monkeypatch.setattr(ep, "run_legacy", lambda _s, _e=None: 0)
    rc = ep.main(["--env-file", str(tmp_env_file), "run", "--python-stage", "providers"])
    assert rc == 0
    assert called["n"] == 1


def test_python_stage_config_invokes_create_config(
    tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sb_xray import config_builder as sbcfg

    called = {"n": 0}

    def fake_create(*, workdir: Path | None = None) -> None:
        called["n"] = 1

    monkeypatch.setattr(sbcfg, "create_config", fake_create)
    monkeypatch.setattr(ep, "run_legacy", lambda _s, _e=None: 0)
    rc = ep.main(["--env-file", str(tmp_env_file), "run", "--python-stage", "config"])
    assert rc == 0
    assert called["n"] == 1
