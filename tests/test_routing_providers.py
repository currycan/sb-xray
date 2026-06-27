"""Tests for sb_xray.routing.providers (generateProxyProvidersConfig port)."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
from sb_xray.routing import providers


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WORKDIR", str(tmp_path))
    for key in (
        "PROVIDERS",
        "CLASH_PROXY_PROVIDERS",
        "SURGE_PROXY_PROVIDERS",
        "SURGE_PROVIDER_NAMES",
        "STASH_PROVIDER_NAMES",
    ):
        monkeypatch.delenv(key, raising=False)
    return tmp_path


def test_empty_state_exports_all_empty(workdir: Path) -> None:
    result = providers.generate_and_export()
    assert result == {
        "CLASH_PROXY_PROVIDERS": "",
        "SURGE_PROXY_PROVIDERS": "",
        "SURGE_PROVIDER_NAMES": "",
        "STASH_PROVIDER_NAMES": "",
    }
    assert os.environ["CLASH_PROXY_PROVIDERS"] == ""


def test_reads_providers_file_strips_yaml_headers(workdir: Path) -> None:
    (workdir / "providers").write_text(
        "proxy-providers:\n"
        "# this is a comment\n"
        "\n"
        '  AllOne: {<<: *BaseProvider, url: "https://sub.example.com/AllOne-Common"}\n'
        '  Extra: {<<: *BaseProvider, url: "https://sub.example.com/Extra"}\n',
        encoding="utf-8",
    )
    result = providers.generate_and_export()
    block = result["CLASH_PROXY_PROVIDERS"]
    assert "proxy-providers:" not in block
    assert "# this is a comment" not in block
    assert "AllOne" in block
    assert "Extra" in block


def test_env_provider_triples_merged(workdir: Path) -> None:
    os.environ["PROVIDERS"] = "Foo|https://foo.example/sub|remark1|Bar|https://bar.example/sub|"
    result = providers.generate_and_export()
    block = result["CLASH_PROXY_PROVIDERS"]
    assert 'Foo: {<<: *BaseProvider, url: "https://foo.example/sub"' in block
    assert 'additional-suffix: " [remark1]"' in block
    assert "Bar:" in block
    assert 'additional-suffix: ""' in block


def test_env_skips_entries_with_missing_name_or_url(workdir: Path) -> None:
    os.environ["PROVIDERS"] = "|https://no-name||Name|||Skip||remark"
    result = providers.generate_and_export()
    assert result["CLASH_PROXY_PROVIDERS"] == ""


def test_surge_policy_path_only_for_allone(workdir: Path) -> None:
    (workdir / "providers").write_text(
        '  AllOne: {<<: *BaseProvider, url: "https://sub.example.com/AllOne-Common"}\n'
        '  Other: {<<: *BaseProvider, url: "https://sub.example.com/Other"}\n',
        encoding="utf-8",
    )
    result = providers.generate_and_export()
    surge = result["SURGE_PROXY_PROVIDERS"]
    assert surge.startswith("AllOne = smart, policy-path=https://sub.example.com/AllOne-Surge")
    assert "update-interval=86400" in surge
    assert "include-all-proxies=0" in surge
    assert "Other" not in surge


def test_surge_provider_names_leading_comma(workdir: Path) -> None:
    (workdir / "providers").write_text(
        '  AllOne: {<<: *BaseProvider, url: "https://sub.example.com/AllOne-Common"}\n',
        encoding="utf-8",
    )
    result = providers.generate_and_export()
    assert result["SURGE_PROVIDER_NAMES"] == ", AllOne"


def test_surge_provider_names_empty_when_no_allone(workdir: Path) -> None:
    (workdir / "providers").write_text(
        '  OnlyOther: {<<: *BaseProvider, url: "https://other.example/sub"}\n',
        encoding="utf-8",
    )
    result = providers.generate_and_export()
    assert result["SURGE_PROVIDER_NAMES"] == ""
    assert result["SURGE_PROXY_PROVIDERS"] == ""


def test_stash_names_joined_comma_space(workdir: Path) -> None:
    (workdir / "providers").write_text(
        '  AllOne: {<<: *BaseProvider, url: "https://sub.example.com/AllOne-Common"}\n'
        '  Extra1: {<<: *BaseProvider, url: "https://sub.example.com/Extra1"}\n'
        '  Extra2: {<<: *BaseProvider, url: "https://sub.example.com/Extra2"}\n',
        encoding="utf-8",
    )
    result = providers.generate_and_export()
    assert result["STASH_PROVIDER_NAMES"] == "AllOne, Extra1, Extra2"


def test_file_and_env_merged_env_after_file(workdir: Path) -> None:
    (workdir / "providers").write_text(
        '  AllOne: {<<: *BaseProvider, url: "https://sub.example.com/AllOne-Common"}\n',
        encoding="utf-8",
    )
    os.environ["PROVIDERS"] = "Dynamic|https://dyn.example/sub|"
    result = providers.generate_and_export()
    block = result["CLASH_PROXY_PROVIDERS"]
    lines = block.splitlines()
    assert lines[0].lstrip().startswith("AllOne")
    assert any(line.lstrip().startswith("Dynamic") for line in lines[1:])


def test_surge_lowercase_common_suffix_also_replaced(workdir: Path) -> None:
    (workdir / "providers").write_text(
        '  AllOne: {<<: *BaseProvider, url: "https://sub.example.com/AllOne-common"}\n',
        encoding="utf-8",
    )
    result = providers.generate_and_export()
    assert "policy-path=https://sub.example.com/AllOne-Surge" in result["SURGE_PROXY_PROVIDERS"]


def test_unrendered_gist_placeholder_dropped_from_file(workdir: Path) -> None:
    """未设 GIST_CODE → providers 文件里字面 ${GIST_CODE} 行被丢弃,不进 CLASH_PROXY_PROVIDERS。"""
    (workdir / "providers").write_text(
        "proxy-providers:\n"
        '  AllOne: {<<: *BaseProvider, url: '
        '"https://gh-proxy.com/gist.githubusercontent.com/testowner/${GIST_CODE}/raw/AllOne-Common"}\n'
        '  Good: {<<: *BaseProvider, url: "https://sub.example.com/Good"}\n',
        encoding="utf-8",
    )
    result = providers.generate_and_export()
    block = result["CLASH_PROXY_PROVIDERS"]
    assert "${GIST_CODE}" not in block
    assert "AllOne" not in block
    assert "Good" in block
    # AllOne 被丢弃 → 无 Surge policy-path,STASH 名单只剩 Good
    assert result["SURGE_PROXY_PROVIDERS"] == ""
    assert result["STASH_PROVIDER_NAMES"] == "Good"


def test_unrendered_gist_placeholder_dropped_from_env(workdir: Path) -> None:
    """PROVIDERS env 里带字面 ${GIST_CODE} 的条目同样被丢弃。"""
    os.environ["PROVIDERS"] = (
        "Bad|https://gh-proxy.com/testowner/${GIST_CODE}/raw/Bad|"
        "|Foo|https://foo.example/sub|"
    )
    result = providers.generate_and_export()
    block = result["CLASH_PROXY_PROVIDERS"]
    assert "${GIST_CODE}" not in block
    assert "Bad" not in block
    assert "Foo:" in block


def test_unrendered_gist_placeholder_warns(
    workdir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """丢弃字面占位符行时 emit 一条 warning(便于运维发现未设 GIST_CODE)。"""
    (workdir / "providers").write_text(
        '  AllOne: {<<: *BaseProvider, url: '
        '"https://gh-proxy.com/testowner/${GIST_CODE}/raw/AllOne-Common"}\n',
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="sb_xray.routing.providers"):
        providers.generate_and_export()
    assert any("GIST_CODE" in rec.message for rec in caplog.records)
