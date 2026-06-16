"""Tests for sources/deploy-config/gen_deploy_config.py —— 部署配置生成器。

覆盖：node.list / env 解析、nodes.list / VPS .env / OpenWrt config.env 渲染、
canary 节点逻辑、必需键校验、diff 漂移检测、gen/diff CLI 端到端。

注意：fixture 全用虚构值（node-a/b/c、example.com、RFC5737 / CGNAT 测试网段），
不含任何真实节点名/域名/IP（项目纪律 §4）。
"""

from __future__ import annotations

from pathlib import Path

import gen_deploy_config as gen
import pytest

NODE_LIST = """\
# 注释行
# 名      域名               token
node-a    node-a.example.com  tok_a
node-b    node-b.example.com  tok_b

node-c    node-c.example.com  tok_c
"""

OPENWRT_ENV = """\
# OpenWrt 回国出口配置
CN_EXIT_MODE=balance
BRIDGE_HOT=node-a,node-b
TS_HOSTNAME=openwrt-test
TS_EXPECTED_IP=100.64.0.1
TS_ADVERTISE_ROUTES=192.0.2.0/24
PEER_TS_IP=100.64.0.2
TS_OAUTH_CLIENT_SECRET=should-not-leak
"""


@pytest.fixture
def truth_dir(tmp_path: Path) -> Path:
    """构造一个最小可用的 .credentials/ 真相源目录。"""
    (tmp_path / "node.list").write_text(NODE_LIST, encoding="utf-8")
    (tmp_path / "openwrt-config.env").write_text(OPENWRT_ENV, encoding="utf-8")
    return tmp_path


# ── 解析 ────────────────────────────────────────────────────────────


def test_parse_node_list_skips_comments_and_blanks() -> None:
    nodes = gen.parse_node_list(NODE_LIST)
    assert [n.name for n in nodes] == ["node-a", "node-b", "node-c"]
    assert nodes[0].fqdn == "node-a.example.com"
    assert nodes[0].token == "tok_a"


def test_parse_node_list_bad_columns_raises() -> None:
    with pytest.raises(gen.GenError, match="3 列"):
        gen.parse_node_list("node-a node-a.example.com")


def test_parse_node_list_empty_raises() -> None:
    with pytest.raises(gen.GenError, match="任何节点"):
        gen.parse_node_list("# 只有注释\n\n")


def test_parse_env_basic() -> None:
    env = gen.parse_env("A=1\n# c\n\nB = two \n")
    assert env == {"A": "1", "B": "two"}


# ── 渲染 ────────────────────────────────────────────────────────────


def test_render_nodes_list_strips_comments() -> None:
    out = gen.render_nodes_list(gen.parse_node_list(NODE_LIST))
    assert out == (
        "node-a node-a.example.com tok_a\n"
        "node-b node-b.example.com tok_b\n"
        "node-c node-c.example.com tok_c\n"
    )


def test_render_vps_env_worker_has_no_watchtower_schedule() -> None:
    truth = gen.parse_env(OPENWRT_ENV)
    node = gen.Node("node-b", "node-b.example.com", "tok_b")
    out = gen.render_vps_env(node, truth, canary=False)
    assert "CN_EXIT_MODE=balance" in out
    assert "ENABLE_REVERSE=true" in out
    assert "ENABLE_SOCKS5_PROXY=true" in out
    assert "tsip=100.64.0.1" in out  # tsip 来自 TS_EXPECTED_IP
    assert "domain=node-b.example.com" in out
    assert "WATCHTOWER_SCHEDULE" not in out


def test_render_vps_env_canary_adds_watchtower_schedule() -> None:
    truth = gen.parse_env(OPENWRT_ENV)
    node = gen.Node("node-c", "node-c.example.com", "tok_c")
    out = gen.render_vps_env(node, truth, canary=True)
    assert f"WATCHTOWER_SCHEDULE={gen.CANARY_WATCHTOWER_SCHEDULE}" in out


def test_render_vps_env_missing_key_raises() -> None:
    node = gen.Node("node-a", "node-a.example.com", "tok_a")
    with pytest.raises(gen.GenError, match="TS_EXPECTED_IP"):
        gen.render_vps_env(node, {"CN_EXIT_MODE": "balance"}, canary=False)


def test_render_openwrt_config_validates_and_passes_through() -> None:
    truth = gen.parse_env(OPENWRT_ENV)
    out = gen.render_openwrt_config(OPENWRT_ENV, truth)
    assert out.endswith("\n")
    assert "CN_EXIT_MODE=balance" in out
    assert "TS_HOSTNAME=openwrt-test" in out


def test_render_openwrt_config_missing_key_raises() -> None:
    bad = "CN_EXIT_MODE=balance\n"
    with pytest.raises(gen.GenError, match="TS_HOSTNAME"):
        gen.render_openwrt_config(bad, gen.parse_env(bad))


# ── diff ────────────────────────────────────────────────────────────


def test_diff_identical_is_empty() -> None:
    assert gen.diff_configs("A=1\n", "A=1\n", label="x") == []


def test_diff_detects_drift() -> None:
    drift = gen.diff_configs("A=2\n", "A=1\n", label="x")
    assert drift  # 非空
    assert any("A=2" in line for line in drift)


def test_load_operator_conf_reads_canary(tmp_path: Path) -> None:
    conf = tmp_path / "operator.conf"
    conf.write_text("# c\nSBX_CANARY_NODE=node-c\n", encoding="utf-8")
    assert gen.load_operator_conf(conf).get("SBX_CANARY_NODE") == "node-c"
    assert gen.load_operator_conf(tmp_path / "nope.conf") == {}


# ── CLI 端到端 ──────────────────────────────────────────────────────


def test_cli_gen_writes_all_artifacts(truth_dir: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    conf = truth_dir / "operator.conf"
    conf.write_text("SBX_CANARY_NODE=node-c\n", encoding="utf-8")
    rc = gen.main(
        [
            "--credentials-dir",
            str(truth_dir),
            "--operator-conf",
            str(conf),
            "gen",
            "--out-dir",
            str(out_dir),
        ]
    )
    assert rc == 0
    assert (out_dir / "vps" / "node-a.env").is_file()
    assert (out_dir / "vps" / "node-c.env").is_file()
    assert (out_dir / "openwrt" / "config.env").is_file()
    assert (out_dir / "openwrt" / "nodes.list").is_file()
    # canary=node-c 的 .env 应带 WATCHTOWER_SCHEDULE，node-a（worker）不带
    assert "WATCHTOWER_SCHEDULE" in (out_dir / "vps" / "node-c.env").read_text(encoding="utf-8")
    assert "WATCHTOWER_SCHEDULE" not in (out_dir / "vps" / "node-a.env").read_text(encoding="utf-8")


def test_cli_gen_single_node(truth_dir: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    rc = gen.main(
        [
            "--credentials-dir",
            str(truth_dir),
            "gen",
            "--target",
            "vps",
            "--node",
            "node-a",
            "--out-dir",
            str(out_dir),
        ]
    )
    assert rc == 0
    assert (out_dir / "vps" / "node-a.env").is_file()
    assert not (out_dir / "vps" / "node-b.env").exists()


def test_cli_gen_unknown_node_errors(truth_dir: Path, tmp_path: Path) -> None:
    rc = gen.main(
        [
            "--credentials-dir",
            str(truth_dir),
            "gen",
            "--target",
            "vps",
            "--node",
            "ghost",
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )
    assert rc == 2  # GenError → 退出码 2


def test_cli_diff_zero_drift_then_drift(truth_dir: Path, tmp_path: Path) -> None:
    # 先生成 node-a.env 作为「设备现配置」，diff 应零漂移
    out_dir = tmp_path / "out"
    gen.main(["--credentials-dir", str(truth_dir), "gen", "--target", "vps", "--node", "node-a",
              "--out-dir", str(out_dir)])
    actual = out_dir / "vps" / "node-a.env"
    rc_same = gen.main(
        ["--credentials-dir", str(truth_dir), "diff", "--node", "node-a", "--actual", str(actual)]
    )
    assert rc_same == 0

    drifted = tmp_path / "drift.env"
    drifted.write_text(actual.read_text(encoding="utf-8") + "EXTRA=1\n", encoding="utf-8")
    rc_drift = gen.main(
        ["--credentials-dir", str(truth_dir), "diff", "--node", "node-a", "--actual", str(drifted)]
    )
    assert rc_drift == 1


def test_cli_diff_vps_without_node_errors(truth_dir: Path, tmp_path: Path) -> None:
    actual = tmp_path / "a.env"
    actual.write_text("x\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        gen.main(["--credentials-dir", str(truth_dir), "diff", "--actual", str(actual)])
