"""sources/openwrt 下 shell 脚本的行为测试。

覆盖五块：
1. POSIX 语法检查（sh -n）——三个脚本都必须能被 BusyBox ash 兼容解析；
2. -h/--help 用法说明——必须在做任何环境检查/副作用之前短路退出；
3. openwrt-init.sh 持久 tailscale bypass 的静态契约（nftables.d include）；
4. OpenClash 配置纳管：模板占位符契约 + 渲染函数行为（注入/裁剪）；
5. 内嵌 cdn-speedtest：heredoc 完整性与可解析性。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_OPENWRT = Path(__file__).resolve().parent.parent / "sources" / "openwrt"
_OPENCLASH = Path(__file__).resolve().parent.parent / "sources" / "openclash"
_SETUP = _OPENWRT / "openwrt-init.sh"
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
    """openwrt-init.sh 必须固化 fw4 持久 include，消除 OpenClash 重启窗口期。

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


def test_setup_verify_guards_lan_subnet_drift() -> None:
    """verify() 必须含 LAN 网段迁移护栏：从内核路由表取本机实际网段，
    比对 TS_ADVERTISE_ROUTES——改了路由器网段忘改 config.env 不能静默漏过。"""
    src = _SETUP.read_text(encoding="utf-8")
    assert "network.lan.ipaddr" in src
    assert "ip -4 route show proto kernel" in src
    assert "通告网段含本机 LAN 实际网段" in src


def test_setup_verify_guards_openclash_fakeip_only_bypass() -> None:
    """verify() 必须自检 OpenClash 的 lan_ac_traffic 绕过开关未被打开。

    该开关 enabled='1' 时 OpenClash 仅拦截 FakeIP 段（198.19.0.0/16）流量，
    裸 IP 直连（Telegram 原生客户端等不查 DNS 的应用）会绕过代理直接出墙
    被黑洞——症状是「域名流量通、裸 IP 流量不通」，极难排查。
    """
    src = _SETUP.read_text(encoding="utf-8")
    assert "openclash.@lan_ac_traffic[0].enabled" in src
    assert "lan_ac_traffic" in src


# ---- OpenClash 配置纳管 -------------------------------------------------------


def test_setup_main_wires_new_steps_in_order() -> None:
    """main() 必须接入新步骤：配置纳管在解耦之前（共用解耦末尾的 restart），
    CDN 优选在监控之后、自检之前。"""
    src = _SETUP.read_text(encoding="utf-8")
    # rindex：内嵌 cdn-speedtest heredoc 里也有自己的 main()，外层 main() 是最后一个
    main_body = src[src.rindex("main() {"):]
    for fn in ("setup_openclash_config", "install_cdn_speedtest"):
        assert fn in main_body, f"main() 未调用 {fn}"
    assert main_body.index("setup_openclash_config") < main_body.index("setup_openclash_decouple")
    assert main_body.index("setup_monitor_cron") < main_body.index("install_cdn_speedtest")
    assert main_body.index("install_cdn_speedtest") < main_body.index("if verify")


@pytest.mark.parametrize("template", ["op-amd", "op-arm"])
def test_openclash_templates_have_no_real_secrets(template: str) -> None:
    """模板是公共仓库文件，dashboard 密码行的值必须恰为占位符（任何其他值都视为
    真实密码泄露）。不在断言里写真实值本身，避免测试文件二次泄密。"""
    src = (_OPENCLASH / template).read_text(encoding="utf-8")
    pw_lines = [ln.strip() for ln in src.splitlines() if "dashboard_password" in ln]
    assert pw_lines, "模板缺少 dashboard_password 行"
    for ln in pw_lines:
        assert ln == "option dashboard_password '<OPENCLASH_DASHBOARD_PASSWORD>'", (
            f"dashboard_password 必须是占位符，实际: {ln}"
        )


def test_config_env_example_documents_new_vars() -> None:
    src = (_OPENWRT / "config.env.example").read_text(encoding="utf-8")
    for var in (
        "OPENCLASH_MANAGE",
        "OPENCLASH_DASHBOARD_PASSWORD",
        "OPENCLASH_SUBS",
        "CDN_DOMAIN",
        "CDN_SUBDOMAINS",
        "CDN_CRON_SCHEDULE",
    ):
        assert var in src, f"config.env.example 缺少新变量说明: {var}"


def _extract_render_funcs(tmp_path: Path) -> Path:
    """从主脚本抽出渲染相关函数（列 0 的 `}` 为函数终止符）。"""
    src = _SETUP.read_text(encoding="utf-8")
    chunks = []
    for fn in ("openclash_cfg_same()", "render_openclash_config()"):
        start = src.index(f"\n{fn}") + 1
        end = src.index("\n}", start) + 2
        chunks.append(src[start:end])
    out = tmp_path / "render-funcs.sh"
    out.write_text("\n".join(chunks), encoding="utf-8")
    return out


_MINI_TEMPLATE = """
config config_subscribe
\toption name 'KeepMe'
\toption sub_ua 'clash.meta'
\toption enabled '1'

config config_subscribe
\toption enabled '1'
\toption name 'DropMe'
\toption address '<YOUR_SUBSCRIBE_LINK-1>'

config dashboard
\toption dashboard_password '<OPENCLASH_DASHBOARD_PASSWORD>'
"""


def test_render_injects_address_and_prunes_unconfigured_blocks(tmp_path: Path) -> None:
    """渲染契约：① 密码占位符替换；② OPENCLASH_SUBS 命中的订阅块在块尾注入
    address；③ 未命中的订阅块（占位示例）整块裁剪；④ 其余 stanza 原样保留。"""
    funcs = _extract_render_funcs(tmp_path)
    tpl = tmp_path / "mini-template"
    tpl.write_text(_MINI_TEMPLATE, encoding="utf-8")
    rendered = tmp_path / "rendered"
    driver = (
        f". '{funcs}'\n"
        "OPENCLASH_DASHBOARD_PASSWORD='s3cret'\n"
        "OPENCLASH_SUBS='KeepMe=https://example.com/sub?token=abc'\n"
        f"render_openclash_config '{tpl}' '{rendered}'\n"
    )
    proc = subprocess.run(["sh", "-c", driver], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    out = rendered.read_text(encoding="utf-8")
    assert "dashboard_password 's3cret'" in out
    assert "<OPENCLASH_DASHBOARD_PASSWORD>" not in out
    assert "DropMe" not in out and "<YOUR_SUBSCRIBE_LINK" not in out
    assert "config dashboard" in out
    # address 注入在 KeepMe 块尾（enabled 行之后）
    keep_block = out[out.index("KeepMe"):out.index("config dashboard")]
    assert "option address 'https://example.com/sub?token=abc'" in keep_block
    assert keep_block.index("enabled") < keep_block.index("option address")


def test_render_idempotent_normalized_compare(tmp_path: Path) -> None:
    """openclash_cfg_same 必须忽略行尾空白与末尾空行——幂等跳过的判据。"""
    funcs = _extract_render_funcs(tmp_path)
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.write_text("config x\n\toption y '1'\n", encoding="utf-8")
    b.write_text("config x\n\toption y '1' \n\n\n", encoding="utf-8")
    driver = f". '{funcs}'\nopenclash_cfg_same '{a}' '{b}'\n"
    proc = subprocess.run(["sh", "-c", driver], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, "规范化比对应忽略行尾空白/末尾空行"


# ---- 内嵌 cdn-speedtest -------------------------------------------------------


def _extract_embedded_cdn(tmp_path: Path) -> Path:
    src = _SETUP.read_text(encoding="utf-8")
    start = src.index("<<'CDNEOF'") + len("<<'CDNEOF'") + 1
    end = src.index("\nCDNEOF\n", start) + 1
    out = tmp_path / "cdn-speedtest"
    out.write_text(src[start:end], encoding="utf-8")
    return out


def test_embedded_cdn_speedtest_is_complete_and_parsable(tmp_path: Path) -> None:
    """heredoc 内嵌的 cdn-speedtest 必须是完整、可解析的 POSIX 脚本。"""
    script = _extract_embedded_cdn(tmp_path)
    src = script.read_text(encoding="utf-8")
    for fn in (
        "build_cdn_domains",
        "install_cloudflarest",
        "run_speedtest",
        "should_update",
        "update_hosts",
        "clean_hosts",
    ):
        assert f"{fn}()" in src, f"内嵌 cdn-speedtest 缺少函数: {fn}"
    assert 'main "$@"' in src
    proc = subprocess.run(["sh", "-n", str(script)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr


def test_cdn_install_step_guards_and_cron() -> None:
    """install_cdn_speedtest 契约：CDN_DOMAIN 空则跳过；清理旧版 cdn-speedtest.sh
    cron 行；cron 注入带 grep 守卫。"""
    src = _SETUP.read_text(encoding="utf-8")
    assert "install_cdn_speedtest()" in src
    body = src[src.index("install_cdn_speedtest()"):src.index("# ── 端到端自检")]
    assert '"$CDN_DOMAIN"' in body
    assert "/usr/bin/cdn-speedtest run" in body
    assert "cdn-speedtest\\.sh" in body  # 旧路径 cron 清理
