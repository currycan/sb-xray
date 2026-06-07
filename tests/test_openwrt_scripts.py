"""sources/openwrt 下 shell 脚本的行为测试。

覆盖三块：
1. POSIX 语法检查（sh -n）——三个脚本都必须能被 BusyBox ash 兼容解析；
2. -h/--help 用法说明——必须在做任何环境检查/副作用之前短路退出；
3. cn-exit-setup.sh 持久 tailscale bypass 的静态契约（nftables.d include）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_OPENWRT = Path(__file__).resolve().parent.parent / "sources" / "openwrt"
_SETUP = _OPENWRT / "cn-exit-setup.sh"
_BRIDGE = _OPENWRT / "cn-bridge"
_MONITOR = _OPENWRT / "cn-bridge-monitor"
_ALL_SCRIPTS = [_SETUP, _BRIDGE, _MONITOR]


def _run(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(script), *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


# ---- 语法 -------------------------------------------------------------------


@pytest.mark.parametrize("script", _ALL_SCRIPTS, ids=lambda p: p.name)
def test_posix_syntax_ok(script: Path) -> None:
    proc = subprocess.run(["sh", "-n", str(script)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr


# ---- help -------------------------------------------------------------------


@pytest.mark.parametrize("flag", ["-h", "--help"])
@pytest.mark.parametrize("script", _ALL_SCRIPTS, ids=lambda p: p.name)
def test_help_exits_zero_with_usage(script: Path, flag: str) -> None:
    """help 必须 rc=0、打印用法，且不依赖 OpenWrt 环境（uci/nft/节点清单）。"""
    proc = _run(script, flag)
    assert proc.returncode == 0, proc.stderr
    assert "用法" in proc.stdout
    assert script.name in proc.stdout


def test_setup_help_documents_modes_and_config() -> None:
    out = _run(_SETUP, "--help").stdout
    for keyword in ("socks5", "reverse", "balance", "config.env", "CN_EXIT_MODE"):
        assert keyword in out, f"help 缺少关键说明: {keyword}"


def test_bridge_help_documents_subcommands() -> None:
    out = _run(_BRIDGE, "--help").stdout
    for sub in ("list", "up", "down", "status", "menu"):
        assert sub in out, f"help 缺少子命令说明: {sub}"


def test_monitor_help_documents_env_keys() -> None:
    out = _run(_MONITOR, "--help").stdout
    for key in ("monitor.env", "TG_TOKEN", "HOT", "MON_THRESHOLD"):
        assert key in out, f"help 缺少配置说明: {key}"


# ---- 未知参数 ---------------------------------------------------------------


def test_setup_unknown_option_fails_fast() -> None:
    proc = _run(_SETUP, "--bogus")
    assert proc.returncode != 0
    assert "未知参数" in proc.stderr


def test_monitor_unknown_option_fails_fast() -> None:
    proc = _run(_MONITOR, "--bogus")
    assert proc.returncode != 0
    assert "未知参数" in proc.stderr


def test_bridge_unknown_command_fails_fast() -> None:
    # cn-bridge 的位置参数是子命令：未知值必须报错而非进交互菜单。
    # 注：节点清单检查在子命令解析之前，开发机上以 ERROR 退出同样满足 fail-fast。
    proc = _run(_BRIDGE, "bogus-subcommand")
    assert proc.returncode != 0


# ---- 持久 tailscale bypass 契约 ---------------------------------------------


def test_setup_writes_persistent_nft_bypass() -> None:
    """cn-exit-setup.sh 必须固化 fw4 持久 include，消除 OpenClash 重启窗口期。

    契约要点（任一缺失都意味着窗口期兜底失效）：
    - include 文件路径在 /etc/nftables.d/ 下（fw4 reload 自动并入 inet fw4 表）；
    - output 链是 route 类型（清 fwmark 后触发重路由回主表）；
    - 优先级 -149（紧跟 openclash_mangle* 的 mangle -150 之后）;
    - 只匹配 OpenClash 的 fwmark 0x162（不能用 mark != 0 ——会误清 tailscaled
      自身的 0x80000 防环路标记）；
    - 对 UDP sport/dport ${TS_PORT} 清零 fwmark。
    """
    src = _SETUP.read_text(encoding="utf-8")
    assert "/etc/nftables.d/99-cn-exit-tailscale.nft" in src
    assert "cn_exit_ts_output" in src
    assert "cn_exit_ts_prerouting" in src
    assert "type route hook output priority -149" in src
    assert "type filter hook prerouting priority -149" in src
    assert "meta mark set 0" in src
    assert "meta mark 0x162" in src
    assert "meta mark != 0" not in src
    assert "udp sport ${TS_PORT}" in src
    assert "udp dport ${TS_PORT}" in src


def test_setup_verify_covers_persistent_bypass() -> None:
    """verify() 自检必须包含持久链存在性检查。"""
    src = _SETUP.read_text(encoding="utf-8")
    assert "nft list chain inet fw4 cn_exit_ts_output" in src
