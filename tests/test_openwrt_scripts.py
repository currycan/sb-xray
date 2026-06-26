"""sources/openwrt 下 shell 脚本的行为测试。

覆盖五块：
1. POSIX 语法检查（sh -n）——三个脚本都必须能被 BusyBox ash 兼容解析；
2. -h/--help 用法说明——必须在做任何环境检查/副作用之前短路退出；
3. openwrt-init.sh 持久 tailscale bypass 的静态契约（nftables.d include）；
4. OpenClash 配置纳管：模板占位符契约 + 渲染函数行为（注入/裁剪）；
5. 独立 cdn-speedtest（sources/openwrt/cdn-speedtest）：完整性、可解析性、CDN_SUBDOMAINS 纯 env 解析。
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
_BACKUP = _OPENWRT / "cn-backup"
_CDN = _OPENWRT / "cdn-speedtest"
_ALL_SCRIPTS = [_SETUP, _BRIDGE, _MONITOR, _BACKUP, _CDN]

_HACK = Path(__file__).resolve().parent.parent / "sources" / "hack"
_CHECK_IP = _HACK / "check_ip_type.sh"


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


# ---- clash 核运行时动态版本发现契约 -----------------------------------------
# 背景：vernesong/mihomo 的 Prerelease-Alpha 是滚动预发布，文件名里的 hash 每次构建都
# 变、旧文件被删。任何写死的 fallback hash 早晚 404，故核版本必须运行时从 GitHub 动态
# 解析；镜像层用多候选轮询 + 直连兜底，避免单一镜像失效即全盘卡死。


def test_no_hardcoded_clash_core_hash() -> None:
    """不得写死会过期的 mihomo Smart 核 commit hash；FALLBACK_HASH 默认必须为空。"""
    src = _SETUP.read_text(encoding="utf-8")
    assert "5c165b4" not in src, "残留写死的过期 hash 5c165b4"
    assert 'CLASH_CORE_FALLBACK_HASH="${CLASH_CORE_FALLBACK_HASH:-}"' in src, (
        "CLASH_CORE_FALLBACK_HASH 默认值必须为空（纯动态发现），仅留作显式逃生阀"
    )


def test_gh_proxies_multi_mirror_default() -> None:
    """GH_PROXY 默认空（不再钉死单一镜像）；GH_PROXIES 默认提供多候选。"""
    src = _SETUP.read_text(encoding="utf-8")
    assert 'GH_PROXY="${GH_PROXY:-}"' in src, "GH_PROXY 默认应为空，候选下沉到 GH_PROXIES"
    assert "GH_PROXIES=" in src, "缺少 GH_PROXIES 多镜像候选变量"
    # 默认候选至少含两个不同镜像，单一镜像失效才有得换
    assert src.count("https://", src.index("GH_PROXIES=")) >= 2 or "ghproxy" in src


def test_dynamic_resolve_functions_present() -> None:
    """动态发现 + 多镜像 helper 必须存在，且 install_clash_core 走动态解析。"""
    src = _SETUP.read_text(encoding="utf-8")
    assert "gh_url_bases()" in src, "缺少 gh_url_bases 多镜像 helper"
    assert "clash_resolve_core_url()" in src, "缺少 clash_resolve_core_url 动态发现 helper"
    # Bug A 防回归：clash_resolve_core_url 内部 gh_download 会 log 到 stdout，
    # 必须经全局变量 RESOLVED_CORE_URL 返回，绝不能用 $() 捕获 stdout（否则日志污染返回值）。
    assert "RESOLVED_CORE_URL" in src, "应经全局 RESOLVED_CORE_URL 返回核 URL"
    assert "$(clash_resolve_core_url)" not in src, (
        "禁止用 $(clash_resolve_core_url) 捕获 stdout——内部 log 会污染返回值"
    )
    # 双源：tags API + expanded_assets HTML
    assert "releases/tags/${CLASH_CORE_TAG}" in src, "动态发现缺 tags API 源"
    assert "crfb_assets_from_tag" in src, "动态发现缺 expanded_assets HTML 源"


def test_core_install_atomic_replace() -> None:
    """Bug B 防回归：核可能正在运行，必须解压到 .new + 原子 mv，不能直接覆写（Text file busy）。"""
    src = _SETUP.read_text(encoding="utf-8")
    assert 'gunzip -c "$_gz" > "${_core}.new"' in src, "应解压到 .new 临时文件"
    assert 'mv -f "${_core}.new" "$_core"' in src, "应原子 mv 替换运行中的核"
    assert 'gunzip -c "$_gz" > "$_core"' not in src, "禁止直接覆写运行中的核"


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


def test_setup_help_documents_ipv6_subcommand() -> None:
    """ipv6 子命令是下游机/恢复出厂后单独收口公网 IPv6 的独立入口，help 必须列出。"""
    out = _run(_SETUP, "--help").stdout
    assert "ipv6" in out, "help 缺少 ipv6 子命令说明"
    assert "KEEP_IPV6" in out, "help 未说明 KEEP_IPV6 逃生阀"


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


# ---- cn-bridge-monitor 并发硬化（H2） ---------------------------------------


def test_monitor_uses_nonblocking_flock_with_degrade() -> None:
    """cron 周期调用 + tailscale ping 可能拖慢：须非阻塞自锁防两轮重叠改写计数；
    缺 flock（极简镜像）优雅降级裸跑，不中断探活。"""
    src = _MONITOR.read_text(encoding="utf-8")
    assert "flock -n" in src, "去抖计数须用非阻塞 flock -n 防并发"
    assert "command -v flock" in src, "须探测 flock 以便缺失时降级"
    assert "MON_LOCK" in src, "应有独立锁文件变量 MON_LOCK"


def test_monitor_atomic_counter_write() -> None:
    """per-key 计数写须 tmp+mv 原子落盘，杜绝并发读到截断计数。"""
    src = _MONITOR.read_text(encoding="utf-8")
    assert "_cnt_write" in src, "计数写须收口到 _cnt_write helper"
    assert 'mv -f "$_cnt_tmp" "$1"' in src or 'mv -f "$_tmp" "$2"' in src, "_cnt_write 须 mv 原子替换计数文件"
    assert 'echo "$_c" > "$_cf"' not in src, "record 不得裸 echo 直写计数文件"


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
    # rindex：main_cdn() 等子命令入口在前，外层 main() 是最后一个
    main_body = src[src.rindex("main() {"):]
    for fn in ("setup_openclash_config", "install_cdn_tooling"):
        assert fn in main_body, f"main() 未调用 {fn}"
    assert main_body.index("setup_openclash_config") < main_body.index("setup_openclash_decouple")
    assert main_body.index("setup_monitor_cron") < main_body.index("install_cdn_tooling")
    assert main_body.index("install_cdn_tooling") < main_body.index("if verify")


def test_setup_ipv6_subcommand_wired() -> None:
    """ipv6 子命令必须接入 dispatch、只复用 setup_lan_ipv6，且独立于 config.env
    （下游机单独收口入口，不进全装流程、不因缺 config.env 报错）。"""
    src = _SETUP.read_text(encoding="utf-8")
    assert "ipv6) main_ipv6 ;;" in src, "dispatch case 未接入 ipv6 子命令"
    body = src[src.index("main_ipv6() {"):]
    body = body[: body.index("\n}")]
    assert "setup_lan_ipv6" in body, "main_ipv6 未调用 setup_lan_ipv6"
    # config 无关：不得走 load_config（那会校验 CN_EXIT_MODE 并打印配置来源）；
    # config.env 只能「存在才 source」，缺失不得报错。
    assert "load_config" not in body, "main_ipv6 不应调用 load_config（应独立于 config.env）"
    assert "[ -f" in body, "main_ipv6 应对 config.env 用『存在才 source』守卫"
    # 子命令是轻量入口：不得拉起全装步骤（Tailscale/bridge/OpenClash 纳管）
    for heavy in ("install_tailscale", "install_xray_bridge", "setup_openclash_config"):
        assert heavy not in body, f"main_ipv6 误调用全装步骤 {heavy}"


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


# ---- Tailscale 身份自恢复 -----------------------------------------------------


def test_ts_identity_functions_exist_and_wired() -> None:
    """身份自恢复函数群存在，且 setup_tailscale 接入登录分支与恢复调用。"""
    src = _SETUP.read_text(encoding="utf-8")
    for fn in (
        "ts_has_oauth()",
        "ts_api()",
        "ts_mint_authkey()",
        "ts_find_device_by_ip()",
        "restore_ts_identity()",
        "approve_ts_routes()",
        "ts_routes_approved()",
    ):
        assert fn in src, f"缺少函数: {fn}"
    setup_body = src[src.index("setup_tailscale()"):src.index("# ── Tailscale 身份自恢复")]
    assert "--auth-key=" in setup_body, "setup_tailscale 缺少 auth key 登录分支"
    assert "restore_ts_identity" in setup_body
    assert "approve_ts_routes" in setup_body


def test_ts_identity_failure_paths_degrade_not_die() -> None:
    """restore/approve 的 API 失败路径必须降级（warn + 手动指引），不得 die——
    身份恢复失败不应阻塞其余安装步骤。"""
    src = _SETUP.read_text(encoding="utf-8")
    for fn in ("restore_ts_identity()", "approve_ts_routes()"):
        start = src.index(fn)
        body = src[start:src.index("\n}", start)]
        assert "die " not in body and "die\t" not in body, f"{fn} 内不得调用 die"
        assert "warn " in body, f"{fn} 失败路径应有 warn"


def test_setup_verify_guards_expected_ts_ip() -> None:
    """verify() 必须含固定 IP 硬校验——TS_EXPECTED_IP 是 VPS 侧 socks5 腿契约。"""
    src = _SETUP.read_text(encoding="utf-8")
    assert "Tailscale IP 为预期固定值" in src
    assert 'TS_EXPECTED_IP' in src


def test_config_env_example_documents_ts_identity_vars() -> None:
    src = (_OPENWRT / "config.env.example").read_text(encoding="utf-8")
    for var in (
        "TS_OAUTH_CLIENT_ID",
        "TS_OAUTH_CLIENT_SECRET",
        "TS_OAUTH_TAGS",
        "TS_EXPECTED_IP",
        "TS_AUTH_KEY",
    ):
        assert var in src, f"config.env.example 缺少变量说明: {var}"


_DEVICES_FIXTURE = """{
  "devices": [
    {"addresses": ["100.64.0.1", "fd7a:115c::1"], "id": "111",
     "nodeId": "nAAAA1", "hostname": "vps-a", "os": "linux"},
    {"addresses": ["100.91.115.115", "fd7a:115c::2"], "id": "222",
     "nodeId": "nBBBB2", "hostname": "openwrt-cn-old", "os": "linux"},
    {"addresses": ["100.99.88.77"], "id": "333",
     "nodeId": "nCCCC3", "hostname": "openwrt-cn", "os": "linux"}
  ]
}"""


def _run_find_device(tmp_path: Path, ip: str, fixture: str) -> str:
    """提取 ts_find_device_by_ip 函数，用 fixture JSON 离线驱动。"""
    src = _SETUP.read_text(encoding="utf-8")
    start = src.index("\nts_find_device_by_ip()") + 1
    func = src[start:src.index("\n}", start) + 2]
    funcs = tmp_path / "find-device.sh"
    funcs.write_text(func, encoding="utf-8")
    fx = tmp_path / "devices.json"
    fx.write_text(fixture, encoding="utf-8")
    proc = subprocess.run(
        ["sh", "-c", f". '{funcs}'\nts_find_device_by_ip '{fx}' '{ip}'"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def test_find_device_by_ip_hits_middle_device(tmp_path: Path) -> None:
    assert _run_find_device(tmp_path, "100.91.115.115", _DEVICES_FIXTURE) == "nBBBB2"


def test_find_device_by_ip_exact_quoted_match_no_prefix_collision(tmp_path: Path) -> None:
    """100.91.115.11 不得误命中 100.91.115.115（带引号精确匹配契约）。"""
    assert _run_find_device(tmp_path, "100.91.115.11", _DEVICES_FIXTURE) == ""


def test_find_device_by_ip_miss_and_empty(tmp_path: Path) -> None:
    assert _run_find_device(tmp_path, "100.1.2.3", _DEVICES_FIXTURE) == ""
    assert _run_find_device(tmp_path, "100.91.115.115", '{"devices": []}') == ""


# ---- 独立 cdn-speedtest -------------------------------------------------------


def test_cdn_speedtest_is_complete_and_parsable() -> None:
    """独立 cdn-speedtest 必须是完整、可解析的 POSIX 脚本。"""
    src = _CDN.read_text(encoding="utf-8")
    for fn in (
        "restore_proxy_env",
        "build_cdn_domains",
        "install_cloudflarest",
        "run_speedtest",
        "should_update",
        "update_hosts",
        "ensure_hosts_present",
        "clean_hosts",
    ):
        assert f"{fn}()" in src, f"cdn-speedtest 缺少函数: {fn}"
    assert 'main "$@"' in src
    proc = subprocess.run(["sh", "-n", str(_CDN)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr


def test_cdn_speedtest_reads_subdomains_from_env() -> None:
    """子域名前缀改为纯 env：cdn-speedtest 从 CDN_SUBDOMAINS（逗号分隔）读取，
    不再依赖 /etc/subdomains.txt 文件，且用 POSIX 安全的 IFS=, 拆分（非 bashism、非 tr 字符类）。"""
    src = _CDN.read_text(encoding="utf-8")
    assert "CDN_SUBDOMAINS_FILE" not in src, "应彻底移除 /etc/subdomains.txt 文件中介"
    assert "/etc/subdomains.txt" not in src, "cdn-speedtest 不应再引用 subdomains 文件"
    assert 'CDN_SUBDOMAINS="${CDN_SUBDOMAINS:-}"' in src, "缺少 CDN_SUBDOMAINS env 读取"
    assert "IFS=," in src, "应用 IFS=, 拆逗号（POSIX，非 bashism）"
    # build_cdn_domains 空 env 必须明确报错（胜过静默白测）
    bcd = src[src.index("build_cdn_domains()"):src.index("\n}\n", src.index("build_cdn_domains()"))]
    assert '[ -z "$CDN_SUBDOMAINS" ]' in bcd, "build_cdn_domains 须在 CDN_SUBDOMAINS 空时报错"


def test_cdn_run_self_heals_hosts_when_update_skipped() -> None:
    """should_update 决定保持缓存 IP 时，run 仍须保证 /etc/hosts 真有条目。

    回归防护：last_best.txt 在但 /etc/hosts CDN 条目缺（sysupgrade/cn-backup 恢复后
    可再生的优选条目未落、被 cdn clean、被其它进程重置）时，测速因 IP 未变跳过 update_hosts，
    若不回填则 install 的 verify_cdn_outcome 硬查 /etc/hosts 必死。run 必须在 should_update
    跳过分支调用 ensure_hosts_present 自愈，否则 install↔测速 陷入死结。"""
    embedded = _CDN.read_text(encoding="utf-8")
    assert "ensure_hosts_present()" in embedded
    # run 主流程：should_update 为假的 else 分支必须回填 hosts
    run_case = embedded[embedded.index("        run)"):embedded.index("        install)")]
    assert "ensure_hosts_present" in run_case, "run 缺少 should_update 跳过时的 hosts 自愈回填"


def test_cdn_install_step_guards_and_cron() -> None:
    """install_cdn_tooling 契约：CDN_DOMAIN 空则跳过；清理旧版 cdn-speedtest.sh
    cron 行；cron 注入带 grep 守卫；前缀经 env 注入而非落盘文件。"""
    src = _SETUP.read_text(encoding="utf-8")
    assert "install_cdn_tooling()" in src
    body = src[src.index("install_cdn_tooling()"):src.index("# ── 端到端自检")]
    assert '"$CDN_DOMAIN"' in body
    assert "install_cdn_speedtest" in body, "install_cdn_tooling 应调用 install_cdn_speedtest"
    assert "/usr/bin/cdn-speedtest run" in body
    assert "cdn-speedtest\\.sh" in body  # 旧路径 cron 清理
    assert "last_best.txt" in body, "install_cdn_tooling 缺少首跑结果门禁"
    assert "env $_cdn_env /usr/bin/cdn-speedtest run" in body, "缺少前台首跑"
    assert "nohup" not in body
    # 不再物化 /etc/subdomains.txt，且对存量残留做幂等清理
    assert "rm -f /etc/subdomains.txt" in body, "缺少旧版 /etc/subdomains.txt 存量清理"
    embedded = _CDN.read_text(encoding="utf-8")
    assert "${SPEED_TEST_THREADS:-500}" in embedded
    assert "${SPEED_TEST_TIME:-4}" in embedded
    assert "${SPEED_TEST_COUNT:-5}" in embedded
    assert "${SPEED_TEST_LATENCY_MAX:-200}" in embedded
    assert "${SPEED_TEST_MIN_SPEED:-5}" in embedded
    assert "verify_cdn_outcome()" in src
    assert "main_cdn()" in src
    assert "cdn) shift; main_cdn" in src
    main_cdn_start = src.index("main_cdn()")
    main_cdn = src[main_cdn_start:src.index("\nmain()", main_cdn_start)]
    assert main_cdn.index("install_cdn_tooling") < main_cdn.index("verify_cdn_outcome")
    assert "CDN 优选缓存就位（last_best.txt）" in src, "verify_cdn_outcome 缺少 CDN 首跑软自检"


def test_build_cdn_env_includes_subdomains() -> None:
    """build_cdn_env 是纯 env 化枢纽：必须把 CDN_SUBDOMAINS 注入 _cdn_env（随 cron 行/env 传递）。"""
    src = _SETUP.read_text(encoding="utf-8")
    body = src[src.index("build_cdn_env()"):src.index("\n}", src.index("build_cdn_env()"))]
    assert "CDN_SUBDOMAINS=$CDN_SUBDOMAINS" in body, "build_cdn_env 未注入 CDN_SUBDOMAINS"


def test_cdn_speedtest_mainland_filter_params() -> None:
    """大陆优选门槛：cfst 调用须支持 -tll(延迟下限)/-tlr(丢包率)/-dt(下载时长)，
    且新 env 带偏向大陆的脚本内默认（开箱生效，不依赖 config.env 设置）。"""
    src = _CDN.read_text(encoding="utf-8")
    assert "${SPEED_TEST_LATENCY_MIN:-40}" in src, "缺 -tll 大陆默认(SPEED_TEST_LATENCY_MIN=40)"
    assert "${SPEED_TEST_LOSS_MAX:-0.2}" in src, "缺 -tlr 大陆默认(SPEED_TEST_LOSS_MAX=0.2)"
    assert "${SPEED_TEST_DL_TIME:-10}" in src, "缺 -dt 下载时长(SPEED_TEST_DL_TIME=10)"
    assert "${SPEED_TEST_CN_FALLBACK:-1}" in src, "缺筛空回退开关默认(SPEED_TEST_CN_FALLBACK=1)"
    for flag in ("-tll", "-tlr", "-dt", "-tl", "-sl"):
        assert f"{flag} " in src or f"{flag}=" in src, f"cfst 调用缺 {flag}"
    # -t 注释纠正：标为「延迟测速次数」（原误标「下载测速时间」）
    assert "延迟测速次数" in src, "-t 注释应纠正为「延迟测速次数」"


def test_cdn_speedtest_empty_result_fallback() -> None:
    """筛空回退兜底：严苛门槛(tll/tlr)未筛出 IP 时须放宽（无 tll/tlr）重测一轮，避免本轮无 IP
    可用；SPEED_TEST_CN_FALLBACK=0 时不回退、直接失败。两轮共用抽出的 _run_cfst。"""
    src = _CDN.read_text(encoding="utf-8")
    assert "_run_cfst()" in src, "缺少抽出的 _run_cfst（供两轮复用）"
    rs = src[src.index("run_speedtest()"):src.index("\n}\n", src.index("run_speedtest()"))]
    assert "_run_cfst $_cn_filter" in rs, "第一轮须带大陆门槛 _cn_filter"
    assert '[ "$SPEED_TEST_CN_FALLBACK" = "1" ] && _run_cfst' in rs, "回退须受 CN_FALLBACK 守卫并放宽重测"
    assert '[ "$SPEED_TEST_LATENCY_MIN" != "0" ]' in rs, "tll=0 须视为关闭下限"
    assert "restore_proxy_env" in rs, "两轮后须保留单一代理环境恢复点"


def test_build_cdn_env_forwards_mainland_params() -> None:
    """build_cdn_env 白名单须转发新增大陆门槛 SPEED_TEST_*，使 config.env 覆盖能传到 cron 运行。"""
    src = _SETUP.read_text(encoding="utf-8")
    body = src[src.index("build_cdn_env()"):src.index("\n}", src.index("build_cdn_env()"))]
    for var in (
        "SPEED_TEST_DL_TIME",
        "SPEED_TEST_LATENCY_MIN",
        "SPEED_TEST_LOSS_MAX",
        "SPEED_TEST_CN_FALLBACK",
    ):
        assert var in body, f"build_cdn_env 未转发 {var}"


def test_config_env_example_documents_mainland_params() -> None:
    """config.env.example 须文档化新增大陆优选 env（含默认值与回退说明）。"""
    src = (_OPENWRT / "config.env.example").read_text(encoding="utf-8")
    for var in (
        "SPEED_TEST_DL_TIME",
        "SPEED_TEST_LATENCY_MIN",
        "SPEED_TEST_LOSS_MAX",
        "SPEED_TEST_CN_FALLBACK",
    ):
        assert var in src, f"config.env.example 缺新变量说明: {var}"


def test_subdomains_file_fully_removed() -> None:
    """彻底纯 env：主脚本无内嵌 heredoc；validate_config 不再查 /etc/subdomains.txt 文件；
    cdn_first_fqdn 用参数展开从 CDN_SUBDOMAINS 取首段。"""
    src = _SETUP.read_text(encoding="utf-8")
    assert "CDNEOF" not in src, "cdn-speedtest 应已拆为独立文件，主脚本无内嵌 heredoc"
    assert "write_cdn_speedtest" not in src, "write_cdn_speedtest 应已删除"
    vc = src[src.index("validate_config()"):src.index("detect_arch()")]
    assert "/etc/subdomains.txt" not in vc, "validate_config 不应再查 subdomains 文件"
    assert '[ -n "$CDN_SUBDOMAINS" ]' in vc, "validate_config 应校验 CDN_SUBDOMAINS 非空"
    fqdn = src[src.index("cdn_first_fqdn()"):src.index("\n}\n", src.index("cdn_first_fqdn()"))]
    assert "${CDN_SUBDOMAINS%%,*}" in fqdn, "cdn_first_fqdn 须从 CDN_SUBDOMAINS env 取首前缀"
    assert "/etc/subdomains.txt" not in fqdn, "cdn_first_fqdn 不应再读文件"


def test_install_local_or_fetch_helper_shared() -> None:
    """四个随包工具脚本共用 install_local_or_fetch（cp 本地优先 / raw 下载兜底），消除复制粘贴漂移。"""
    src = _SETUP.read_text(encoding="utf-8")
    assert "install_local_or_fetch()" in src, "缺少 install_local_or_fetch helper"
    assert "sources/openwrt/$_name" in src, "helper 应按名拼 raw 下载 URL"
    for name in ("cn-bridge", "cn-bridge-monitor", "cn-backup", "cdn-speedtest"):
        assert f"install_local_or_fetch {name}" in src, f"{name} 未委托 install_local_or_fetch"


def test_no_busybox_broken_tr_space_class() -> None:
    """禁用 `tr -d '[:space:]'`：busybox tr 把 [:space:] 当字面字符集（[ : s p a c e ]）处理，
    会吃掉数据里的 s/p/a/c/e 等字母（真机实测 "jp"→"j"）。主脚本与 cdn-speedtest 都不得出现。"""
    for path in (_SETUP, _CDN):
        src = path.read_text(encoding="utf-8")
        assert "tr -d '[:space:]'" not in src, f"{path.name}: busybox tr 误解 [:space:] 字符类"
        assert 'tr -d "[:space:]"' not in src, f"{path.name}: busybox tr 误解 [:space:] 字符类"
    # cdn_first_fqdn 改用参数展开取首段（无 tr、无子进程）
    src = _SETUP.read_text(encoding="utf-8")
    fqdn = src[src.index("cdn_first_fqdn()"):src.index("\n}\n", src.index("cdn_first_fqdn()"))]
    assert "${CDN_SUBDOMAINS%%,*}" in fqdn, "cdn_first_fqdn 须用参数展开取首段"


def test_embedded_cfst_extract_via_gnu_tar() -> None:
    """busybox tar 不识别上游无 ustar 魔数归档：失败时自动装 GNU tar 解全量
    （cfst + ip.txt + ipv6.txt 一并解出），结构上消除「漏抽某文件」类 bug。"""
    src = _CDN.read_text(encoding="utf-8")
    assert "ensure_gnu_tar()" in src, "缺少 GNU tar 自动安装器"
    assert "if ! ensure_gnu_tar || ! tar -xzf" in src, "tar 失败路径未接 ensure_gnu_tar"


def test_embedded_speedtest_trap_recovery() -> None:
    """测速窗口必须有 trap 兜底：HUP/INT/TERM/EXIT 都恢复 DNS 与 OpenClash。"""
    embedded = _CDN.read_text(encoding="utf-8")
    assert "restore_proxy_env()" in embedded, "缺少恢复函数"
    assert "trap " in embedded and "HUP INT TERM" in embedded, "缺少信号 trap"
    assert "trap 'restore_proxy_env' EXIT" in embedded, "缺少 EXIT 兜底"
    assert "trap - HUP INT TERM EXIT" in embedded, "正常路径未清 trap"
    # 子 shell 陷阱：run_speedtest 经 $() 执行，父进程必须有独立 trap（含杀残留 cfst）
    assert "测速父进程被中断" in embedded, "main() run 分支缺父进程 trap"
    assert "pkill -x cfst" in embedded, "父进程 trap 应清理残留 cfst"
    # 用唯一的实际代码行（含 ``|| exit 1``）定位命令替换；裸字符串 best_ip=$(run_speedtest)
    # 在上方 stderr 诊断注释里也出现，naive .index() 会误命中注释而非真正的命令替换。
    assert embedded.index("测速父进程被中断") < embedded.index("best_ip=$(run_speedtest) || exit 1"), (
        "父 trap 须先于命令替换安装"
    )
    # 恢复函数不得依赖子 shell 局部变量；OpenClash 恢复须锁串行 + 无条件 restart
    assert "OPENCLASH_STOPPED" not in embedded, "恢复判定不得用 shell 变量"
    assert "mkdir /tmp/.cdn-speedtest-oc-restore.lock" in embedded, "恢复缺原子锁"
    assert "/etc/init.d/openclash restart" in embedded, "恢复须用 restart（三态收敛）"
    assert "pgrep -f" not in embedded.split("restore_proxy_env()")[1].split("\n}")[0], "恢复不得用进程探测（拖尾/旁观假象）"
    # 防陈旧结果:测速前必须清掉上一轮 result.csv
    assert "rm -f result.csv" in embedded, "缺少测速前旧结果清理"
    assert embedded.index("rm -f result.csv") < embedded.index("./cfst"), "清理须在 cfst 之前"


# ---- cn-backup 配置备份 / 恢复 ------------------------------------------------


def test_backup_help_documents_subcommands() -> None:
    out = _run(_BACKUP, "--help").stdout
    for sub in ("save", "restore", "list"):
        assert sub in out, f"help 缺少子命令说明: {sub}"


def test_backup_unknown_command_fails_fast() -> None:
    proc = _run(_BACKUP, "bogus-subcommand")
    assert proc.returncode != 0


def test_backup_uses_official_sysupgrade_mechanism() -> None:
    """契约核心：备份/恢复完全复用官方 sysupgrade，不自造打包/解包。"""
    src = _BACKUP.read_text(encoding="utf-8")
    assert "sysupgrade -b" in src, "备份必须用官方 sysupgrade -b"
    assert "sysupgrade -r" in src, "恢复必须用官方 sysupgrade -r"


def test_backup_produces_safe_and_full_variants() -> None:
    """每次 save 产两份：safe（官方清单）+ full（额外并入 /etc/tailscale/）。"""
    src = _BACKUP.read_text(encoding="utf-8")
    assert "-safe.tar.gz" in src
    assert "-full.tar.gz" in src
    assert "/etc/tailscale/" in src, "full 变体须并入 Tailscale 身份"


def test_backup_retries_transient_sysupgrade_failure() -> None:
    """sysupgrade -b 打包 /etc/openclash 时会因 OpenClash 写文件偶发 tar『file changed』
    非零退出——备份保留重试作兜底(冻结失败等),否则每日 cron 会随机失败。"""
    src = _BACKUP.read_text(encoding="utf-8")
    assert "_sysupgrade_b()" in src, "缺少带重试的 sysupgrade 包装"
    body = src[src.index("_sysupgrade_b()"):src.index("_backup_safe()")]
    assert "BACKUP_RETRIES" in body and "while" in body, "_sysupgrade_b 须循环重试"
    # safe/full 都走重试包装,不直接裸调 sysupgrade -b
    assert "_sysupgrade_b" in src[src.index("_backup_safe()"):src.index("_prune_local()")]


def test_backup_freezes_clash_core_during_pack() -> None:
    """根因:clash core 持续写 /etc/openclash/(smart_weight_data.csv 上百 MB、秒级追加),
    与 sysupgrade 打包竞争 → tar『file changed』每次必中,重试采不到安静窗口。
    对策:打包瞬间 SIGSTOP 冻结 core、打包毕 SIGCONT,trap 保证任何退出路径都解冻。"""
    src = _BACKUP.read_text(encoding="utf-8")
    body = src[src.index("_sysupgrade_b()"):src.index("_backup_safe()")]
    # 冻结 / 解冻成对存在,且包住 sysupgrade -b
    assert "kill -STOP" in src and "kill -CONT" in src, "须 SIGSTOP/SIGCONT 冻结 clash core"
    assert "_freeze_clash" in body and "_thaw_clash" in body, "打包循环须冻结/解冻 core"
    # trap 兜底覆盖正常 + 中断路径,保证 core 必解冻(防永久冻结)
    freeze_block = src[src.index("_sysupgrade_b()"):src.index("_backup_safe()")]
    assert "trap " in freeze_block and "_thaw_clash" in freeze_block, "解冻须由 trap 兜底"
    # 取 core PID 用 argv[0] 精确匹配,规避 pgrep -f 自匹配坑
    assert "/proc/" in src and "cmdline" in src, "core PID 须按 argv[0] 精确匹配,非 pgrep -f"


def test_backup_full_variant_restores_sysupgrade_conf() -> None:
    """full 变体临时改 /etc/sysupgrade.conf 后必须还原（trap 兜底，含中断路径）。"""
    src = _BACKUP.read_text(encoding="utf-8")
    body = src[src.index("_backup_full()"):src.index("_prune_local()")]
    assert "trap " in body and "EXIT" in body, "full 变体缺少 trap 还原 sysupgrade.conf"
    assert "$SUCONF" in body


def test_backup_encrypts_before_cloud_push() -> None:
    """两份上云前一律 openssl 加密；不存在明文上云路径。"""
    src = _BACKUP.read_text(encoding="utf-8")
    assert "openssl enc" in src, "云端备份必须加密"
    assert "scp " in src, "云端推送用 scp"
    # 加密与推送在同一流程里成对出现（先 _encrypt 再 _push_to）
    save_body = src[src.index("cmd_save()"):src.index("cmd_restore()")]
    assert save_body.index("_encrypt") < save_body.index("_push_to"), "必须先加密再上传"


def test_backup_targets_bridge_hot_nodes_via_fqdn() -> None:
    """云端目标来自 BRIDGE_HOT（backup.env 的 HOT），按节点名从 nodes.list 取 FQDN 直连。"""
    src = _BACKUP.read_text(encoding="utf-8")
    assert "_node_fqdn()" in src, "缺少节点名→FQDN 解析"
    assert "$NODES" in src, "FQDN 解析须读 nodes.list"
    assert "for _name in $HOT" in src, "上传须遍历 BRIDGE_HOT 节点"
    assert "${REMOTE_USER}@${_fqdn}" in src, "scp/ssh 须用 user@FQDN 直连"


def test_backup_alerts_on_failure_via_telegram() -> None:
    """失败（含部分节点上传失败）须 curl Telegram 告警；无 TG 配置则静默。"""
    src = _BACKUP.read_text(encoding="utf-8")
    assert "_notify()" in src, "缺少告警函数"
    assert "api.telegram.org" in src, "告警须走 Telegram"
    notify_body = src[src.index("_notify()"):src.index("_backup_safe()")]
    assert "$TG_TOKEN" in notify_body and "$TG_CHAT" in notify_body, "无 TG 配置须静默跳过"
    # cmd_save 全程 EXIT trap，未走到成功哨兵即告警
    assert "_save_on_exit" in src and "trap '_save_on_exit' EXIT" in src, "save 须有失败兜底告警"


def test_backup_help_independent_of_environment() -> None:
    """help 必须在 source backup.env / 任何 sysupgrade 调用之前短路退出。"""
    src = _BACKUP.read_text(encoding="utf-8")
    help_short = src.index("usage; exit 0")
    assert src.index('. "$ENVFILE"') > help_short, "help 短路必须早于 source backup.env"


# ---- openwrt-init.sh 备份接线契约 --------------------------------------------


def test_setup_backup_functions_exist_and_wired() -> None:
    src = _SETUP.read_text(encoding="utf-8")
    for fn in (
        "setup_sysupgrade_conf()",
        "install_cn_backup()",
        "setup_backup_cron()",
        "ensure_openssl()",
        "ensure_ssh_client()",
        "main_backup()",
    ):
        assert fn in src, f"缺少函数: {fn}"
    assert "backup) shift; main_backup" in src, "dispatch 未接入 backup 子命令"
    main_body = src[src.rindex("main() {"):]
    assert "setup_backup_cron" in main_body, "main() 未调用 setup_backup_cron"


def test_setup_backup_cron_writes_targets_and_alert_creds() -> None:
    """backup.env 必须带：上传目标 HOT（来自 BRIDGE_HOT）、SSH 直连参数、Telegram 告警凭据。"""
    src = _SETUP.read_text(encoding="utf-8")
    body = src[src.index("setup_backup_cron()"):src.index("# ── CDN IP 优选")]
    assert "BRIDGE_HOT" in body, "上传目标须复用 BRIDGE_HOT"
    assert "HOT=" in body and "REMOTE_USER=" in body and "REMOTE_DIR=" in body
    assert "TG_TOKEN=" in body and "ALERT_TG_TOKEN" in body, "告警凭据须取自 ALERT_TG_*"


def test_setup_sysupgrade_conf_portable_and_excludes_identity() -> None:
    """补全清单必须可移植（init.d 反向桥用 glob、不写死节点名），且不把 Tailscale
    活动身份并入官方清单（身份单例敏感，由 cn-backup full 变体临时并入）。"""
    src = _SETUP.read_text(encoding="utf-8")
    body = src[src.index("setup_sysupgrade_conf()"):src.index("install_cn_backup()")]
    assert "/etc/init.d/xray-bridge-*" in body, "init.d 反向桥应用 glob 保持可移植"
    for hardcoded in ("dc99", "xray-bridge-jp"):
        assert hardcoded not in body, f"清单不得写死环境特定节点名: {hardcoded}"
    for path in (
        "/etc/cn-exit/",
        "/root/sb-xray-openwrt/",
        "/usr/bin/cn-bridge",
        "/root/.ssh/",
        "/etc/crontabs/root",             # 所有 sb-xray cron（CDN 前缀随优选 cron 行携带）
        "/etc/CloudflareST/last_best.txt",  # CDN 优选门禁/结果
        "/etc/hotplug.d/iface/99-tailscale-udp-gro",  # UDP GRO hotplug 钩子
        "/etc/init.d/tailscale",          # 被改的 Tailscale 启动脚本
        "/etc/sb-xray/",                  # CRFB 版本 marker
    ):
        assert path in body, f"清单缺少有用路径: {path}"
    # /etc/tailscale/ 不得作为 for 循环里的备份路径（注释提及不算；循环行以反斜杠续行）
    loop_paths = [ln.strip().rstrip("\\").strip() for ln in body.splitlines() if ln.strip().endswith("\\")]
    assert "/etc/tailscale/" not in loop_paths, "官方清单不得并入 Tailscale 活动身份"


def test_config_env_example_documents_backup_vars() -> None:
    src = (_OPENWRT / "config.env.example").read_text(encoding="utf-8")
    for var in (
        "BACKUP_ENABLE",
        "BACKUP_ENC_PASS",
        "BACKUP_REMOTE_DIR",
        "BACKUP_REMOTE_PORT",
        "BACKUP_RETENTION_DAYS",
    ):
        assert var in src, f"config.env.example 缺少备份变量说明: {var}"
    # 云端目标复用 BRIDGE_HOT；告警复用 Telegram 通道
    assert "BRIDGE_HOT" in src
    assert "ALERT_TG_TOKEN" in src


# ---- check_ip_type.sh 评分硬化与去重 declare（H3） ---------------------------


def test_check_ip_no_duplicate_declare() -> None:
    """行 58/59 完全重复的 `declare -A tiktok ... chatgpt` 须去重为一行。"""
    src = _CHECK_IP.read_text(encoding="utf-8")
    dup = "declare -A tiktok disney netflix youtube amazon reddit chatgpt"
    assert src.count(dup) == 1, "媒体数组 declare 行重复，应只声明一次"


def test_check_ip_no_unused_db_arrays() -> None:
    """声明但从不赋值/读取的关联数组（dbip ipwhois ipdata ipqs）应删除。"""
    src = _CHECK_IP.read_text(encoding="utf-8")
    for arr in ("dbip", "ipwhois", "ipdata", "ipqs"):
        assert f"{arr}[" not in src, f"{arr} 既无赋值也无读取，declare 应删除"
    db_line = next(ln for ln in src.splitlines() if ln.startswith("declare -A maxmind"))
    for arr in ("dbip", "ipwhois", "ipdata", "ipqs"):
        assert arr not in db_line, f"declare -A 行残留未用数组 {arr}"


# ---- openwrt-init.sh 网卡迭代硬化（H4） -------------------------------------


def test_setup_iface_loop_uses_glob_not_ls() -> None:
    """禁止 `for _i in $(ls /sys/class/net)`（SC2045，网卡名分词/特殊字符会断）；
    改 glob 遍历 + basename 取名。"""
    src = _SETUP.read_text(encoding="utf-8")
    assert "$(ls /sys/class/net" not in src, "不得遍历 ls 输出（SC2045）"
    assert "for _p in /sys/class/net/*" in src, "须用 glob 遍历网卡目录"
    assert '_i=$(basename "$_p")' in src, "须经 basename 从 glob 路径取网卡名"
    # glob 无匹配守卫：POSIX sh 无 nullglob，未展开时 [ -e ] 跳过
    loop = src[src.index("for _p in /sys/class/net/*"):]
    loop = loop[: loop.index("done") + 4]
    assert "[ -e " in loop, "glob 无匹配时须 [ -e ] 守卫跳过未展开的字面量"


def test_check_ip_score_coerced_to_integer() -> None:
    """score 经整数比较前必须强制取整——API 回小数(12.5)会令 bash [[ -lt ]] 语法报错。
    三处取分（scamalytics/ip2location/abuseipdb）统一经 _int_or_zero 过滤。"""
    src = _CHECK_IP.read_text(encoding="utf-8")
    assert "_int_or_zero()" in src, "缺少取整 helper _int_or_zero"
    # helper：截小数 + 非数字归 0
    fn = src[src.index("_int_or_zero()"):src.index("\n}", src.index("_int_or_zero()"))]
    assert "%%.*" in fn, "_int_or_zero 须用 ${v%%.*} 截掉小数部分"
    assert "*[!0-9]*" in fn, "_int_or_zero 须把非纯数字归零"
    # scamalytics 整数比较前已取整
    sc = src[src.index("db_scamalytics()"):src.index("end_progress\n}", src.index("db_scamalytics()"))]
    assert "_int_or_zero" in sc, "db_scamalytics 的 score 须经 _int_or_zero 取整再比较"
    # ip2location / abuseipdb 的 score 字段同样取整（一致硬化）
    for fnname in ("db_ip2location()", "db_abuseipdb()"):
        body = src[src.index(fnname):src.index("end_progress\n}", src.index(fnname))]
        assert "_int_or_zero" in body, f"{fnname} 的 score 须经 _int_or_zero 取整"
