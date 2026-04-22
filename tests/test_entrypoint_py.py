"""Tests for scripts/entrypoint.py (100% Python orchestration)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import entrypoint as ep  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_persist_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point STATUS_FILE / SECRET_FILE / LOGDIR at the test tmp_path so no
    test ever touches the real ``/.env`` or ``/var/log`` trees."""
    monkeypatch.setenv("STATUS_FILE", str(tmp_path / "status"))
    monkeypatch.setenv("SECRET_FILE", str(tmp_path / "secret"))
    monkeypatch.setenv("LOGDIR", str(tmp_path / "log"))


def _patch_supervisord(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Capture ``stages.supervisord.exec_supervisord`` calls without exec."""
    from sb_xray.stages import supervisord as sbsup

    called: dict[str, object] = {"extras": None, "invoked": False}

    def fake(extras: list[str] | None = None) -> None:
        called["invoked"] = True
        called["extras"] = list(extras or [])

    monkeypatch.setattr(sbsup, "exec_supervisord", fake)
    return called


def _patch_stage_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace every heavy stage with a no-op so tests stay hermetic."""
    from sb_xray import cert as sbcert
    from sb_xray import config_builder as sbcfg
    from sb_xray import secrets as sbsecrets
    from sb_xray import speed_test as sbspeed
    from sb_xray.routing import isp as sbisp
    from sb_xray.routing import media as sbmedia
    from sb_xray.routing import providers as sbprov
    from sb_xray.stages import cron as sbcron
    from sb_xray.stages import dhparam as sbdh
    from sb_xray.stages import geoip as sbgeo
    from sb_xray.stages import keys as sbkeys
    from sb_xray.stages import nginx_auth as sbauth
    from sb_xray.stages import panels as sbpanels

    monkeypatch.setattr(
        sbsecrets,
        "decrypt_remote_secrets",
        lambda *, secret_file, tmp_path=None: sbsecrets.SecretStatus.SKIPPED,
    )
    monkeypatch.setattr(ep, "probe_base_env", lambda _mgr: None)
    monkeypatch.setattr(sbspeed, "run_isp_speed_tests", lambda *, samples=None, url="": None)
    monkeypatch.setattr(sbmedia, "check_all", lambda: {})
    monkeypatch.setattr(sbkeys, "ensure_all_keys", lambda _mgr: None)
    monkeypatch.setattr(sbisp, "build_client_and_server_configs", lambda **_: {})
    monkeypatch.setattr(
        sbcert,
        "ensure_certificate",
        lambda **_kw: sbcert.CertStatus.SKIPPED,
    )
    monkeypatch.setattr(sbdh, "ensure_dhparam", lambda **_: False)
    monkeypatch.setattr(sbgeo, "update_geo_data", lambda **_: 0)
    monkeypatch.setattr(sbcfg, "create_config", lambda **_: None)
    monkeypatch.setattr(sbprov, "generate_and_export", lambda **_: {})
    monkeypatch.setattr(sbcfg, "trim_runtime_configs", lambda **_: None)
    monkeypatch.setattr(sbpanels, "init_panels", lambda: None)
    monkeypatch.setattr(sbauth, "setup_basic_auth", lambda **_: True)
    monkeypatch.setattr(sbcron, "install_crontab", lambda **_: None)
    # `show` banner inside run_pipeline swallows all exceptions; stub
    # `_banner_best_effort` so it doesn't chdir into /sources in tests.
    monkeypatch.setattr(ep, "_banner_best_effort", lambda _ef: None)


# ---------------------------------------------------------------------------
# bootstrap
# ---------------------------------------------------------------------------


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
    assert os.environ["ENV_FILE"] == str(tmp_env_file)


def test_bootstrap_shell_env_wins(tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp_env_file.write_text("export DOMAIN='old'\n", encoding="utf-8")
    monkeypatch.setenv("DOMAIN", "new")
    ep.bootstrap(tmp_env_file)
    assert os.environ["DOMAIN"] == "new"


def test_load_env_file_accepts_bareword_assignment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SECRET_FILE produced by ``crypctl decrypt`` has plain ``KEY=value``
    lines (no ``export``). bash ``source`` takes both; Python loader must
    too or ACMESH_* / ALI_* / CF_* never reach ``os.environ`` and
    ``cert.ensure_certificate`` raises 'required environment variables
    missing' even though the decrypted file lives on disk."""
    secret_file = tmp_path / "secret"
    secret_file.write_text(
        "# comment\n"
        "ACMESH_REGISTER_EMAIL=alice@example.com\n"
        "ACMESH_SERVER_NAME=letsencrypt\n"
        'ALI_KEY="ali-key-123"\n'
        "ALI_SECRET='ali-secret-abc'\n"
        "CF_TOKEN=cf-token-xyz  # inline comment\n"
        "\n"
        "export CF_ZONE_ID='zone-1'\n"  # mixed: export also works
        "CF_ACCOUNT_ID='acct-1'\n",
        encoding="utf-8",
    )
    for key in (
        "ACMESH_REGISTER_EMAIL",
        "ACMESH_SERVER_NAME",
        "ALI_KEY",
        "ALI_SECRET",
        "CF_TOKEN",
        "CF_ZONE_ID",
        "CF_ACCOUNT_ID",
    ):
        monkeypatch.delenv(key, raising=False)

    injected = ep._load_env_file(secret_file)
    assert injected >= 7
    assert os.environ["ACMESH_REGISTER_EMAIL"] == "alice@example.com"
    assert os.environ["ACMESH_SERVER_NAME"] == "letsencrypt"
    assert os.environ["ALI_KEY"] == "ali-key-123"
    assert os.environ["ALI_SECRET"] == "ali-secret-abc"
    assert os.environ["CF_TOKEN"] == "cf-token-xyz"
    assert os.environ["CF_ZONE_ID"] == "zone-1"
    assert os.environ["CF_ACCOUNT_ID"] == "acct-1"


def test_load_env_file_handles_shell_constructs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Delegating to bash means the loader gets ``$()`` command
    substitution, multi-line values, spaces, and special chars for free
    — the same behavior as ``source "${SECRET_FILE}"`` in the original
    bash entrypoint. Critical because crypctl-decrypted files have been
    observed using all of these constructs across different sb-xray
    deployments."""
    secret_file = tmp_path / "secret"
    secret_file.write_text(
        "SIMPLE_VAL=plain\n"
        "COMPUTED=$(printf hello)\n"
        'SPACED="value with spaces"\n'
        "MULTILINE=$(printf 'line1\\nline2')\n"
        "LITERAL='$(NOT_EXPANDED)'\n",
        encoding="utf-8",
    )
    for key in ("SIMPLE_VAL", "COMPUTED", "SPACED", "MULTILINE", "LITERAL"):
        monkeypatch.delenv(key, raising=False)

    ep._load_env_file(secret_file)
    assert os.environ["SIMPLE_VAL"] == "plain"
    assert os.environ["COMPUTED"] == "hello"
    assert os.environ["SPACED"] == "value with spaces"
    assert os.environ["MULTILINE"] == "line1\nline2"
    assert os.environ["LITERAL"] == "$(NOT_EXPANDED)"


def test_load_env_file_preserves_parent_shell_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Values set in the parent process (``docker-compose environment:``)
    must win over whatever SECRET_FILE says — setdefault semantics."""
    secret_file = tmp_path / "secret"
    secret_file.write_text("MY_VAR=from_file\n", encoding="utf-8")
    monkeypatch.setenv("MY_VAR", "from_shell")

    injected = ep._load_env_file(secret_file)
    assert injected == 0
    assert os.environ["MY_VAR"] == "from_shell"


def test_load_env_file_missing_file_is_noop(tmp_path: Path) -> None:
    assert ep._load_env_file(tmp_path / "does-not-exist") == 0


def test_load_env_file_skips_bash_internal_vars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``SHLVL`` / ``PWD`` / ``_`` are set inside the bash subshell but
    must not leak into the parent's ``os.environ``."""
    secret_file = tmp_path / "secret"
    secret_file.write_text("REAL_VAR=value\n", encoding="utf-8")
    monkeypatch.delenv("REAL_VAR", raising=False)
    old_shlvl = os.environ.get("SHLVL")

    ep._load_env_file(secret_file)
    assert os.environ["REAL_VAR"] == "value"
    assert os.environ.get("SHLVL") == old_shlvl


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
    assert "export IS_BRUTAL='true'" in content
    assert os.environ["IP_TYPE"] == "isp"
    assert "export XRAY_UUID='" in content
    assert "export PASSWORD='" in content
    assert "export SUB_STORE_FRONTEND_BACKEND_PATH='/" in content
    assert "export XRAY_REALITY_SHORTID='" in content
    assert "export STRATEGY='" in content


# ---------------------------------------------------------------------------
# run subcommand
# ---------------------------------------------------------------------------


def test_dry_run_exits_zero_without_exec_supervisord(
    tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _patch_supervisord(monkeypatch)
    _patch_stage_stubs(monkeypatch)

    rc = ep.main(["--env-file", str(tmp_env_file), "run", "--dry-run"])
    assert rc == 0
    assert captured["invoked"] is False


def test_default_run_executes_supervisord(
    tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _patch_supervisord(monkeypatch)
    _patch_stage_stubs(monkeypatch)

    rc = ep.main(["--env-file", str(tmp_env_file), "run"])
    assert rc == 0
    assert captured["invoked"] is True


def test_trailing_docker_cmd_args_are_forwarded_to_supervisord(
    tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dockerfile CMD ['supervisord'] becomes trailing argv."""
    captured = _patch_supervisord(monkeypatch)
    _patch_stage_stubs(monkeypatch)

    rc = ep.main(["--env-file", str(tmp_env_file), "run", "supervisord"])
    assert rc == 0
    assert captured["extras"] == ["supervisord"]


def test_skip_stage_skips_named_stage(tmp_env_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``--skip-stage cert`` prevents ``issue_bundle_certificate`` from running."""
    captured = _patch_supervisord(monkeypatch)
    _patch_stage_stubs(monkeypatch)

    called = {"cert": False}

    def fake_issue() -> None:
        called["cert"] = True

    monkeypatch.setattr(ep, "issue_bundle_certificate", fake_issue)

    rc = ep.main(
        [
            "--env-file",
            str(tmp_env_file),
            "run",
            "--skip-stage",
            "cert",
            "--dry-run",
        ]
    )
    assert rc == 0
    assert called["cert"] is False
    assert captured["invoked"] is False


def test_main_sources_status_file_for_summary_box(
    tmp_env_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run() must source STATUS_FILE so ISP_TAG shows in log_summary_box."""
    status_file = tmp_path / "status"
    status_file.write_text(
        "export ISP_TAG='cn2gia'\nexport IS_8K_SMOOTH='false'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("STATUS_FILE", str(status_file))
    for key in ("ISP_TAG", "IS_8K_SMOOTH"):
        monkeypatch.delenv(key, raising=False)
    _patch_supervisord(monkeypatch)
    _patch_stage_stubs(monkeypatch)

    rc = ep.main(["--env-file", str(tmp_env_file), "run", "--dry-run"])
    assert rc == 0
    assert os.environ["ISP_TAG"] == "cn2gia"
    assert os.environ["IS_8K_SMOOTH"] == "false"


# ---------------------------------------------------------------------------
# show subcommand
# ---------------------------------------------------------------------------


def test_show_subcommand_runs_pipeline(
    tmp_env_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WORKDIR", str(tmp_path))
    called: dict[str, object] = {"show": False}

    def fake_show(*, archive_path: Path | None = None) -> None:
        called["show"] = True
        called["archive"] = archive_path

    monkeypatch.setattr(ep, "_CLIENT_TEMPLATE_DIR", tmp_path / "noop-templates")
    monkeypatch.setattr(ep, "_SOURCES_DIR", tmp_path / "noop-sources")
    monkeypatch.setattr(ep.sbdisplay, "show_info_links", fake_show)
    rc = ep.main(["--env-file", str(tmp_env_file), "show"])
    assert rc == 0
    assert called["show"] is True
    archive = called["archive"]
    assert isinstance(archive, Path)
    assert archive.name == "show-config"
    assert (tmp_path / "subscribe" / "v2rayn").is_file()
    assert (tmp_path / "subscribe" / "v2rayn-compat").is_file()


def test_show_pipeline_loads_status_and_secret_files(
    tmp_env_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """show-config.sh:14-16 sources STATUS_FILE + SECRET_FILE."""
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
    assert "✈ good" in os.environ["NODE_SUFFIX"]


# ---------------------------------------------------------------------------
# trim subcommand
# ---------------------------------------------------------------------------


def test_trim_subcommand_invokes_trim_runtime_configs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``entrypoint.py trim`` must call ``trim_runtime_configs`` exactly once."""
    from sb_xray import config_builder as sbcfg

    calls: list[dict] = []

    def fake_trim(**kwargs) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(sbcfg, "trim_runtime_configs", fake_trim)
    rc = ep.main(["trim"])
    assert rc == 0
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# guards
# ---------------------------------------------------------------------------


def test_issue_bundle_certificate_skips_when_domain_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DOMAIN", raising=False)
    monkeypatch.delenv("CDNDOMAIN", raising=False)

    def must_not_call(*a: object, **kw: object) -> object:
        raise AssertionError("ensure_certificate should not run when DOMAIN is empty")

    import sb_xray.cert as sbcert

    monkeypatch.setattr(sbcert, "ensure_certificate", must_not_call)
    ep.issue_bundle_certificate()
