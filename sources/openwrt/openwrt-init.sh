#!/bin/sh
# sb-xray OpenWrt 一键初始化脚本（原 cn-exit-setup.sh）
#
# 把 OpenWrt 侧全部手动操作固化成幂等脚本，涵盖三块：
# ① 回国出口（CN exit），按 CN_EXIT_MODE 选择方案（与服务端一致）：
#   socks5   仅装 Tailscale（kernel TUN + subnet router + exit node + 防火墙 zone/
#            转发 + WAN UDP GRO），作为 VPS 经 OpenClash SOCKS5 回国的落地。
#   reverse  仅装 xray reverse bridge 落地机（主动拨向 VPS 建反向隧道）。
#   balance  两者都装（VPS 侧 leastPing 主备故障转移）。
# 各模式都会在检测到 OpenClash 时做解耦（VPS 域名 DIRECT + fake-ip 过滤）。
# ② OpenClash 配置纳管（OPENCLASH_MANAGE=1 默认开）：按架构选 op-amd/op-arm 模板，
#    注入私有值（dashboard 密码、订阅地址）后幂等应用到 /etc/config/openclash。
# ③ CDN IP 优选（CDN_DOMAIN 非空启用）：内嵌 cdn-speedtest 写出 /usr/bin/cdn-speedtest
#    + /etc/subdomains.txt + 安装时前台同步首跑一次，此后每日 cron，Cloudflare 优选 IP 进 /etc/hosts。
#
# 前置：fw4/nftables；能访问公网；socks5/balance 模式还需 OpenClash 已安装运行
# （本脚本不安装 OpenClash 本体，只管配置）。
# 用法：cp config.env.example config.env && vi config.env && sh openwrt-init.sh
#
# 兼容 BusyBox ash / POSIX sh —— 不用 bashism（无 [[ ]] / 数组 / set -e / echo -e）。

# ── 公共函数 ──────────────────────────────────────────────────────

log()  { printf '[install] %s\n' "$*"; }
warn() { printf '[install] WARN: %s\n' "$*" >&2; }
die()  { printf '[install] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'USAGE'
用法: sh openwrt-init.sh [cdn [run|status|clean]] [-h|--help]

sb-xray OpenWrt 一键初始化。幂等可重跑，覆盖回国出口 + OpenClash 配置纳管 + CDN 优选。

回国出口按 CN_EXIT_MODE 选方案:
  socks5    仅装 Tailscale（kernel TUN + subnet router + exit node），VPS 经
            OpenClash SOCKS5 回国的落地
  reverse   仅装 xray reverse bridge 落地机（主动拨向 VPS 建反向隧道）
  balance   两者都装（VPS 侧 leastPing 主备故障转移，默认/推荐）

配置来源（config.env 存在时覆盖同名环境变量；CONFIG=<path> 可改路径）:
  cp config.env.example config.env && vi config.env && sh openwrt-init.sh
  或内联: CN_EXIT_MODE=reverse VPS_DOMAIN=x.com SUBSCRIBE_TOKEN=xx sh openwrt-init.sh

关键变量（完整说明见 config.env.example）:
  节点清单    NODES_FILE(默认同目录 nodes.list) / BRIDGE_NODES / VPS_DOMAIN+SUBSCRIBE_TOKEN
  Tailscale   PEER_TS_IP TS_HOSTNAME TS_VERSION TS_PORT(默认 41641) TS_ADVERTISE_ROUTES(必填,本机 lan 网段)
  reverse     XRAY_VERSION BRIDGE_HOT；监控 ALERT_TG_TOKEN ALERT_TG_CHAT
  OpenClash   OPENCLASH_MANAGE(默认 1) OPENCLASH_DASHBOARD_PASSWORD OPENCLASH_SUBS("名=URL ...")
  CDN 优选    CDN_DOMAIN(非空启用) CDN_SUBDOMAINS(逗号分隔前缀) CDN_CRON_SCHEDULE(默认 0 4 * * *)
  其他        RELOAD_OPENCLASH=1（完成后自动重启 OpenClash）ARCH_OVERRIDE=arm64|amd64

CDN 子命令:
  sh openwrt-init.sh cdn              安装 cdn-speedtest + 同步首跑 + CDN 自检
  sh openwrt-init.sh cdn run|status|clean  透传内嵌 cdn-speedtest 工具

前置: fw4/nftables 的 OpenWrt；socks5/balance 模式需已装 OpenClash（本脚本不装本体）。
完成后自动自检（verify）：硬失败非 0 退出，时序软项只 warn 可稍后重跑复查。
配套工具: cn-bridge（隧道拨号管理）、cn-bridge-monitor（探活告警）、cdn-speedtest
（CDN 优选，本脚本内嵌生成），见 README.md。
USAGE
}

ok=0
bad=0
check() {
    # check "<描述>" <命令...>；命令成功计 ok，失败计 bad（硬失败，影响退出码）
    _desc=$1
    shift
    if "$@" >/dev/null 2>&1; then
        printf '  [ OK ] %s\n' "$_desc"
        ok=$((ok + 1))
    else
        printf '  [FAIL] %s\n' "$_desc"
        bad=$((bad + 1))
    fi
}

check_soft() {
    # check_soft "<描述>" <命令...>：时序敏感项（DERP 打洞预热 / OpenClash 重启后
    # 异步注入规则），重试若干次；仍失败只 warn，不计 bad、不影响退出码。
    _desc=$1
    shift
    _n=1
    while [ "$_n" -le 4 ]; do
        if "$@" >/dev/null 2>&1; then
            printf '  [ OK ] %s\n' "$_desc"
            return 0
        fi
        _n=$((_n + 1))
        sleep 3
    done
    printf '  [warn] %s（时序未就绪，keepalive/OpenClash 稍后自愈，可重跑 verify）\n' "$_desc"
    return 0
}

backup_file() {
    # 存在则备份为 .bak.<时间戳>，重复跑互不覆盖
    [ -f "$1" ] || return 0
    cp -p "$1" "$1.bak.$(date +%Y%m%d%H%M%S)" || die "备份失败: $1"
    log "已备份 $1 -> $1.bak.<ts>"
}

download_verify() {
    # download_verify <url> <dest> <kind: tgz|zip|json|raw>
    _url=$1
    _dest=$2
    _kind=$3
    _n=0
    while [ "$_n" -lt "$DOWNLOAD_RETRIES" ]; do
        _n=$((_n + 1))
        log "下载($_n/$DOWNLOAD_RETRIES): $_url"
        if wget -q -O "$_dest" "$_url"; then
            [ -s "$_dest" ] || { warn "下载为空，重试"; continue; }
            case "$_kind" in
                tgz)  gzip -t "$_dest" 2>/dev/null && return 0 ;;
                zip)  unzip -t "$_dest" >/dev/null 2>&1 && return 0 ;;
                json) grep -q '{' "$_dest" && return 0 ;;
                *)    return 0 ;;
            esac
            warn "完整性校验失败，重试"
        else
            warn "wget 失败，重试"
        fi
        sleep 2
    done
    die "下载失败（已重试 $DOWNLOAD_RETRIES 次）: $_url"
}

# ── 配置加载与校验 ────────────────────────────────────────────────

load_config() {
    _dir=$(dirname "$0")
    CONFIG="${CONFIG:-$_dir/config.env}"
    # config.env 可选：存在则 source（持久、可反复重跑）；不存在则直接用环境变量
    # （内联传参，如 CN_EXIT_MODE=reverse VPS_DOMAIN=.. sh openwrt-init.sh）。
    if [ -f "$CONFIG" ]; then
        # shellcheck disable=SC1090
        . "$CONFIG"
        _cfg_src="$CONFIG"
    else
        _cfg_src="环境变量（未找到 $CONFIG）"
    fi
    # 默认值
    # CN_EXIT_MODE 未设默认 balance（两套都装，与历史 install.sh 行为一致，不破坏既有部署）
    CN_EXIT_MODE="${CN_EXIT_MODE:-balance}"
    TS_PORT="${TS_PORT:-41641}"
    # TS_ADVERTISE_ROUTES 无默认值：lan 网段因部署而异，必须显式提供（validate_config 校验）
    RELOAD_OPENCLASH="${RELOAD_OPENCLASH:-0}"
    DOWNLOAD_RETRIES="${DOWNLOAD_RETRIES:-3}"
    # OpenClash 配置纳管默认开（检测到 OpenClash 才实际执行）；CDN 优选默认关（CDN_DOMAIN 非空启用）
    OPENCLASH_MANAGE="${OPENCLASH_MANAGE:-1}"
    # Tailscale 身份自恢复（OAuth admin API）：全部可选，不设维持交互式登录与后台手动操作
    TS_OAUTH_TAGS="${TS_OAUTH_TAGS:-tag:openwrt}"
    _TS_TOKEN=""
    # config.env 含 OAuth secret 时收紧权限（幂等）
    if [ -f "$CONFIG" ] && grep -q "^TS_OAUTH_CLIENT_SECRET=." "$CONFIG" 2>/dev/null; then
        chmod 600 "$CONFIG" 2>/dev/null && log "config.env 含 OAuth secret，权限已收紧为 600"
    fi
    case "$CN_EXIT_MODE" in
        socks5|reverse|balance) : ;;
        *) die "CN_EXIT_MODE 非法: ${CN_EXIT_MODE}（应为 socks5|reverse|balance）" ;;
    esac
    log "配置来源: $_cfg_src (CN_EXIT_MODE=$CN_EXIT_MODE)"
}

# 模式开关：socks5/balance 走 Tailscale；reverse/balance 走 xray bridge
mode_uses_tailscale() { [ "$CN_EXIT_MODE" = socks5 ] || [ "$CN_EXIT_MODE" = balance ]; }
mode_uses_reverse()   { [ "$CN_EXIT_MODE" = reverse ] || [ "$CN_EXIT_MODE" = balance ]; }

validate_config() {
    # 节点来源：nodes.list 文件（NODES_FILE / 脚本同目录 nodes.list）、BRIDGE_NODES
    # 内联，或单节点旧用法 VPS_DOMAIN，至少一种。
    _nsrc="${NODES_FILE:-$(dirname "$0")/nodes.list}"
    if [ ! -f "$_nsrc" ] && [ -z "$BRIDGE_NODES" ] && [ -z "$VPS_DOMAIN" ]; then
        die "需提供节点清单文件（NODES_FILE / 同目录 nodes.list）、BRIDGE_NODES 或 VPS_DOMAIN"
    fi
    # 必填项按模式裁剪：socks5/balance 需 Tailscale 四项；reverse/balance 需 xray 版本
    _req=""
    mode_uses_tailscale && _req="$_req PEER_TS_IP TS_HOSTNAME TS_VERSION TS_ADVERTISE_ROUTES"
    mode_uses_reverse && _req="$_req XRAY_VERSION"
    # 单节点旧用法（无 nodes 文件、无 BRIDGE_NODES）的 reverse/balance 还需
    # SUBSCRIBE_TOKEN；多节点的 token 已随清单每项提供。
    if mode_uses_reverse && [ -z "$BRIDGE_NODES" ] && [ ! -f "$_nsrc" ]; then
        _req="$_req SUBSCRIBE_TOKEN"
    fi
    _missing=""
    for _v in $_req; do
        eval "_val=\$$_v"
        [ -n "$_val" ] || _missing="$_missing $_v"
    done
    [ -z "$_missing" ] || die "缺少必填项 (CN_EXIT_MODE=$CN_EXIT_MODE):${_missing}（用 config.env 或内联环境变量提供）"

    # 轻量格式校验（仅 warn，不阻断）
    if [ -n "$VPS_DOMAIN" ]; then
        case "$VPS_DOMAIN" in
            http://*|https://*) die "VPS_DOMAIN 不要带 http(s):// 前缀，只填域名" ;;
        esac
    fi
    if mode_uses_tailscale; then
        case "$PEER_TS_IP" in
            100.0.0.0) die "PEER_TS_IP 仍是示例占位符 100.0.0.0 —— 请填 VPS 的 tailscale ip -4 输出" ;;
            100.*) : ;;
            *) warn "PEER_TS_IP=$PEER_TS_IP 不像 Tailscale IP（应为 100.x 网段）" ;;
        esac
    fi
    # XRAY_VERSION 归一化：去掉可能的 v 前缀（仅 reverse/balance 用得到）
    mode_uses_reverse && XRAY_VERSION=$(printf '%s' "$XRAY_VERSION" | sed 's/^v//')

    # 环境前置
    command -v nft >/dev/null 2>&1 || die "未找到 nft，需要 fw4/nftables 的 OpenWrt"
    command -v uci >/dev/null 2>&1 || die "未找到 uci，这不是 OpenWrt？"
    command -v wget >/dev/null 2>&1 || die "未找到 wget"
    # socks5/balance 用 OpenClash 当 SOCKS5 服务端，必须存在；reverse 模式 OpenClash 可选
    if mode_uses_tailscale; then
        [ -x /etc/init.d/openclash ] || die "未找到 /etc/init.d/openclash —— socks5/balance 模式需先安装 OpenClash"
    fi
    log "配置校验通过"
}

# ── 架构检测 ──────────────────────────────────────────────────────

detect_arch() {
    _m="${ARCH_OVERRIDE:-$(uname -m)}"
    case "$_m" in
        aarch64|arm64)
            TS_ARCH=arm64
            XRAY_ZIP=Xray-linux-arm64-v8a.zip ;;
        x86_64|amd64)
            TS_ARCH=amd64
            XRAY_ZIP=Xray-linux-64.zip ;;
        *)
            die "不支持的架构: ${_m}（可用 config 的 ARCH_OVERRIDE=arm64|amd64 覆盖）" ;;
    esac
    log "架构: $_m -> tailscale=$TS_ARCH xray=$XRAY_ZIP"
}

# ── TUN 前置 ──────────────────────────────────────────────────────

ensure_tun() {
    # kernel TUN 模式必需 /dev/net/tun；x86_64 + OpenClash 的设备通常已有
    if [ -c /dev/net/tun ]; then
        log "/dev/net/tun 已存在"
        return 0
    fi
    log "缺少 /dev/net/tun，尝试安装 kmod-tun..."
    opkg update >/dev/null 2>&1
    opkg install kmod-tun >/dev/null 2>&1
    [ -c /dev/net/tun ] || die "无法获得 /dev/net/tun（kmod-tun 安装失败）—— kernel TUN 模式必需"
    log "kmod-tun 安装完成"
}

# ── Tailscale ─────────────────────────────────────────────────────

install_tailscale() {
    if [ -x /usr/sbin/tailscaled ] && tailscale version 2>/dev/null | grep -q "^$TS_VERSION"; then
        log "Tailscale $TS_VERSION 已安装，跳过下载"
    else
        _tgz="/tmp/tailscale_${TS_VERSION}_${TS_ARCH}.tgz"
        download_verify \
            "https://pkgs.tailscale.com/stable/tailscale_${TS_VERSION}_${TS_ARCH}.tgz" \
            "$_tgz" tgz
        _tmp=/tmp/ts_extract
        rm -rf "$_tmp"; mkdir -p "$_tmp"
        tar -xzf "$_tgz" -C "$_tmp" || die "解压 tailscale 失败"
        cp "$_tmp"/tailscale_*/tailscale  /usr/sbin/tailscale  || die "复制 tailscale 失败"
        cp "$_tmp"/tailscale_*/tailscaled /usr/sbin/tailscaled || die "复制 tailscaled 失败"
        chmod +x /usr/sbin/tailscale /usr/sbin/tailscaled
        rm -rf "$_tmp" "$_tgz"
        log "Tailscale $TS_VERSION 安装完成"
    fi

    # state 必须放持久化路径：OpenWrt 的 /var 是 tmpfs，放那里重启即丢登录态、
    # 每次重启都会注册成新节点（踩坑固化）。
    mkdir -p /etc/tailscale /var/run/tailscale
    backup_file /etc/init.d/tailscale
    # 关键固化：--port 固定端口、kernel TUN 模式（默认 tailscale0）、显式 state/socket 路径
    cat > /etc/init.d/tailscale <<EOF
#!/bin/sh /etc/rc.common

START=95
STOP=10

USE_PROCD=0

start_service() {
    procd_open_instance
    procd_set_param command /usr/sbin/tailscaled \\
        --state=/etc/tailscale/tailscaled.state \\
        --socket=/var/run/tailscale/tailscaled.sock \\
        --port=${TS_PORT}
    procd_set_param respawn
    procd_set_param stdout 1
    procd_set_param stderr 1
    procd_close_instance

    # 开机确保 up：tailscaled 仅在 prefs 的 WantRunning=true 时自动连接，而该位
    # 可能被历史操作置 false（排障/误操作），导致 daemon 起来却 stopped、回国服务
    # 不恢复。后台显式 up（与安装同源 flags、已登录无需 auth-key）兜底，让断电重启
    # 无条件回到声明状态。flags 与 prefs 一致时 up 幂等。
    (sleep 8; /usr/sbin/tailscale up --timeout=60s --accept-dns=false \\
        --advertise-routes=${TS_ADVERTISE_ROUTES} --advertise-exit-node \\
        --hostname=${TS_HOSTNAME} >/dev/null 2>&1) &
}

stop_service() {
    /usr/sbin/tailscale down 2>/dev/null
    killall tailscaled 2>/dev/null
}
EOF
    chmod +x /etc/init.d/tailscale
    log "已写入 /etc/init.d/tailscale (port=${TS_PORT})"
}

has_forwarding() {
    # has_forwarding <src> <dest>：探测 uci forwarding 对是否已存在
    _i=0
    while uci -q get "firewall.@forwarding[$_i]" >/dev/null 2>&1; do
        _s=$(uci -q get "firewall.@forwarding[$_i].src")
        _d=$(uci -q get "firewall.@forwarding[$_i].dest")
        [ "$_s" = "$1" ] && [ "$_d" = "$2" ] && return 0
        _i=$((_i + 1))
    done
    return 1
}

add_forwarding() {
    # add_forwarding <src> <dest>，幂等
    if has_forwarding "$1" "$2"; then
        log "forwarding $1->$2 已存在，跳过"
        return 0
    fi
    uci add firewall forwarding >/dev/null || die "uci add forwarding 失败"
    uci set firewall.@forwarding[-1].src="$1"
    uci set firewall.@forwarding[-1].dest="$2"
    uci commit firewall || die "uci commit firewall 失败"
    log "已添加 forwarding $1->$2"
}

setup_tun_network() {
    # kernel TUN 模式下 tailscale0 的流量真正经过 netfilter：
    #   input  链：VPS 经 Tailscale 访问 OpenClash SOCKS5(7891) 需 zone input ACCEPT
    #   forward链：subnet router(tailscale<->lan) 与 exit node(tailscale->wan) 需放行
    # 全部 uci 操作带探测守卫，重复执行不叠加。

    # 1. network 接口：注册 tailscale0 供防火墙 zone 匹配
    if uci -q get network.tailscale >/dev/null 2>&1; then
        log "network.tailscale 接口已存在，跳过"
    else
        uci set network.tailscale=interface
        uci set network.tailscale.proto='none'
        uci set network.tailscale.device='tailscale0'
        uci commit network || die "uci commit network 失败"
        log "已创建 network.tailscale 接口"
    fi

    # 2. tailscale 防火墙 zone：input/output/forward ACCEPT + masq
    if uci show firewall 2>/dev/null | grep -q "name='tailscale'"; then
        log "tailscale 防火墙 zone 已存在，跳过"
    else
        uci add firewall zone >/dev/null || die "uci add zone 失败"
        uci set firewall.@zone[-1].name='tailscale'
        uci set firewall.@zone[-1].input='ACCEPT'
        uci set firewall.@zone[-1].output='ACCEPT'
        uci set firewall.@zone[-1].forward='ACCEPT'
        uci set firewall.@zone[-1].masq='1'
        uci add_list firewall.@zone[-1].network='tailscale'
        uci commit firewall || die "uci commit firewall 失败"
        log "已创建 tailscale 防火墙 zone"
    fi

    # 3. 转发：tailscale<->lan（subnet router）、tailscale->wan（exit node 出公网）
    add_forwarding tailscale lan
    add_forwarding lan tailscale
    add_forwarding tailscale wan

    /etc/init.d/network reload 2>/dev/null
    /etc/init.d/firewall reload 2>/dev/null
    log "network/firewall 已 reload"
}

# ── UDP GRO 转发优化（subnet router / exit node 吞吐）──────────────

setup_udp_gro() {
    # 对 WAN 物理网卡开 rx-udp-gro-forwarding、关 rx-gro-list：聚合入站 UDP
    # 降低转发 CPU 开销（需内核≥6.2 + TS≥1.54）。tailscaled 启动时检测到次优会
    # 打印 GRO 警告。ethtool 设置重启即失，故同时写 hotplug 在 wan ifup 时重应用。
    command -v ethtool >/dev/null 2>&1 || { opkg update >/dev/null 2>&1; opkg install ethtool >/dev/null 2>&1; }
    command -v ethtool >/dev/null 2>&1 || { warn "未找到 ethtool，跳过 UDP GRO 优化（非关键）"; return 0; }

    _netdev=$(ip -o route get 8.8.8.8 2>/dev/null | grep -oE 'dev [a-z0-9]+' | awk '{print $2}')
    if [ -n "$_netdev" ]; then
        if ethtool -K "$_netdev" rx-udp-gro-forwarding on rx-gro-list off 2>/dev/null; then
            log "已对 $_netdev 应用 UDP GRO 优化"
        else
            warn "ethtool 应用 GRO 失败（网卡/驱动可能不支持，可忽略）"
        fi
    else
        warn "无法确定 WAN 出口网卡，跳过即时 GRO 应用"
    fi

    # hotplug 持久化：wan 每次 ifup 后重应用（PPPoE 重拨 / DHCP 续约断流后兜底）
    _hook=/etc/hotplug.d/iface/99-tailscale-udp-gro
    backup_file "$_hook"
    cat > "$_hook" <<'EOF'
#!/bin/sh
# Tailscale UDP GRO 优化：wan ifup 时对出口网卡重应用（sb-xray openwrt-init.sh 固化）
[ "$ACTION" = ifup ] || exit 0
[ "$INTERFACE" = wan ] || exit 0
NETDEV=$(ip -o route get 8.8.8.8 2>/dev/null | grep -oE 'dev [a-z0-9]+' | awk '{print $2}')
[ -n "$NETDEV" ] && ethtool -K "$NETDEV" rx-udp-gro-forwarding on rx-gro-list off 2>/dev/null
exit 0
EOF
    chmod +x "$_hook"
    log "已写入 UDP GRO hotplug 持久化: $_hook"
}

setup_tailscale() {
    /etc/init.d/tailscale enable 2>/dev/null
    /etc/init.d/tailscale restart 2>/dev/null
    sleep 4
    # 踩坑固化：手写 init restart 后 daemon 常停在 Stopped，必须显式 up。
    # --reset：up 是全量替换 prefs 的语义，清掉历史残留再应用本次 flag（登录态不受影响）。
    # --timeout：up 默认无限等 backend 进入 Running，挂住会卡死整个安装流程。
    # 严禁加 --accept-routes：kernel 模式下它会把其他节点（含已下线旧路由器）
    # 被批准的本 LAN 网段路由装进内核 → 发往 LAN 的回包全进隧道黑洞 → 整机失联，
    # 表现与死机无异（2026-06-05 实测三次"死机"均由此引起）。本机是 subnet
    # router 本体，不需要接受任何对端路由。
    # 登录态分支（设备重置后 state 丢失场景）：TS_AUTH_KEY > OAuth 现场铸 key > 交互式 URL
    _akflag=""
    if tailscale status 2>&1 | grep -qiE "logged out|needslogin|login required"; then
        if [ -n "$TS_AUTH_KEY" ]; then
            log "未登录：使用 TS_AUTH_KEY 免交互登录"
            _akflag="--auth-key=$TS_AUTH_KEY"
        elif ts_has_oauth; then
            log "未登录：用 OAuth client 铸造一次性 auth key 免交互登录..."
            _ak=$(ts_mint_authkey)
            if [ -n "$_ak" ]; then
                _akflag="--auth-key=$_ak"
            else
                warn "铸造 auth key 失败，回退交互式登录"
            fi
        else
            warn "未登录且未配 TS_AUTH_KEY / TS_OAUTH_*：将打印登录 URL 等待手动授权（配 OAuth 可无人值守，见 config.env.example）"
        fi
    fi
    log "运行 tailscale up —— 若打印登录 URL，请在浏览器打开授权（仅首次需要）"
    # $_akflag 不加引号：为空时须整体消失（auth key 无空格，ash 分词安全）
    tailscale up --reset $_akflag \
        --timeout=120s \
        --accept-dns=false \
        --advertise-routes="$TS_ADVERTISE_ROUTES" \
        --advertise-exit-node \
        --hostname="$TS_HOSTNAME" || \
        warn "tailscale up 返回非零（可能已在线 / 需手动授权后重跑本脚本）"
    log "提示：subnet routes(${TS_ADVERTISE_ROUTES}) 与 exit node 需 Tailscale 后台批准（配了 OAuth 则下面自动批准）"
    log "      https://login.tailscale.com/admin/machines -> ${TS_HOSTNAME} -> Edit route settings"
    sleep 2
    if netstat -lnup 2>/dev/null | grep -q ":${TS_PORT} "; then
        log "tailscaled 已监听 UDP ${TS_PORT}"
    else
        warn "tailscaled 未监听 ${TS_PORT}，请检查 logread | grep tailscaled"
    fi
    # 身份自恢复（固定 IP）+ 路由批准——均按配置自裁剪、失败降级为手动指引
    restore_ts_identity
    approve_ts_routes
}

# ── Tailscale 身份自恢复（OAuth admin API）──────────────────────────
# 设备重置后 state 丢失 → 新身份新 IP，而 VPS 侧 CN_EXIT_SOCKS5_HOST 写死本机
# 固定 IP——三件套（免交互登录 / 恢复固定 IP / 批准 routes）用 admin API 闭环，
# 使恢复语义收敛为「上传文件 + 跑脚本」。全部可选：未配 OAuth 时维持旧行为。
# 所有 API 失败路径只 warn + 打印手动后台步骤，不 die、不阻塞其余安装。

ts_has_oauth() { [ -n "$TS_OAUTH_CLIENT_ID" ] && [ -n "$TS_OAUTH_CLIENT_SECRET" ]; }

ts_api() {
    # ts_api <METHOD> <path> [json-body]，path 形如 /tailnet/-/devices。
    # 输出响应体；非 2xx / 网络失败 / 未配 OAuth 返回非 0（调用方降级）。
    ts_has_oauth || return 1
    command -v curl >/dev/null 2>&1 || { opkg update >/dev/null 2>&1; opkg install curl >/dev/null 2>&1; }
    command -v curl >/dev/null 2>&1 || { warn "未找到 curl，无法调用 Tailscale API"; return 1; }
    if [ -z "$_TS_TOKEN" ]; then
        _TS_TOKEN=$(curl -s --max-time 15 \
            -d "client_id=$TS_OAUTH_CLIENT_ID" -d "client_secret=$TS_OAUTH_CLIENT_SECRET" \
            https://api.tailscale.com/api/v2/oauth/token 2>/dev/null | \
            sed -n 's/.*"access_token"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
        [ -n "$_TS_TOKEN" ] || { warn "Tailscale OAuth 换取 access token 失败"; return 1; }
    fi
    _m=$1; _p=$2; _b=${3:-}
    _n=0
    while [ "$_n" -lt "$DOWNLOAD_RETRIES" ]; do
        _n=$((_n + 1))
        if [ -n "$_b" ]; then
            _resp=$(curl -s --max-time 20 -X "$_m" -H "Authorization: Bearer $_TS_TOKEN" \
                -H "Content-Type: application/json" -d "$_b" \
                -w '\n%{http_code}' "https://api.tailscale.com/api/v2$_p" 2>/dev/null)
        else
            _resp=$(curl -s --max-time 20 -X "$_m" -H "Authorization: Bearer $_TS_TOKEN" \
                -w '\n%{http_code}' "https://api.tailscale.com/api/v2$_p" 2>/dev/null)
        fi
        _code=$(printf '%s' "$_resp" | tail -1)
        case "$_code" in
            2*) printf '%s' "$_resp" | sed '$d'; return 0 ;;
        esac
        sleep 2
    done
    warn "Tailscale API $_m $_p 失败 (HTTP ${_code:-无响应})"
    return 1
}

ts_mint_authkey() {
    # 铸一把短时效（10 分钟）、preauthorized、一次性 auth key——只为本次登录用，
    # 凭据的「不腐烂性」由 OAuth client 承担。OAuth 铸 key 平台要求必须带 tag。
    _tags=$(printf '"%s"' "$TS_OAUTH_TAGS" | sed 's/,/","/g')
    _body="{\"capabilities\":{\"devices\":{\"create\":{\"reusable\":false,\"ephemeral\":false,\"preauthorized\":true,\"tags\":[${_tags}]}}},\"expirySeconds\":600,\"description\":\"openwrt-init recovery\"}"
    ts_api POST /tailnet/-/keys "$_body" | \
        sed -n 's/.*"key"[[:space:]]*:[[:space:]]*"\(tskey-[^"]*\)".*/\1/p'
}

ts_find_device_by_ip() {
    # ts_find_device_by_ip <devices.json> <ip> —— 打印 addresses 含 <ip> 的设备 nodeId。
    # 纯文本解析（BusyBox 无 jq）：设备对象无嵌套 object，按 "{" 分块后每块即一台
    # 设备的全部字段；带引号精确匹配 IP 防前缀误命中（.11 vs .115）。
    awk -v ip="$2" -v q='"' '
        BEGIN { RS = "{" }
        index($0, q ip q) && index($0, "nodeId") {
            s = $0
            sub(/.*"nodeId"[[:space:]]*:[[:space:]]*"/, "", s)
            sub(/".*/, "", s)
            if (s != "") { print s; exit }
        }
    ' "$1"
}

restore_ts_identity() {
    # 把本机 Tailscale IP 恢复为 TS_EXPECTED_IP（VPS 侧 socks5 腿指向的固定值）。
    [ -n "$TS_EXPECTED_IP" ] || { log "未设 TS_EXPECTED_IP，跳过固定 IP 自恢复"; return 0; }
    _cur=$(tailscale ip -4 2>/dev/null)
    if [ "$_cur" = "$TS_EXPECTED_IP" ]; then
        log "Tailscale IP 已是预期固定值 $TS_EXPECTED_IP"
        return 0
    fi
    warn "Tailscale IP 漂移: 当前 ${_cur:-未知} ≠ 预期 $TS_EXPECTED_IP（设备重置后的新身份），尝试 API 自动恢复..."
    [ -n "$_cur" ] || { warn "本机尚无 Tailscale IP（未登录成功？），跳过"; return 0; }
    if ! ts_has_oauth; then
        warn "未配 TS_OAUTH_CLIENT_ID/SECRET，请手动恢复：后台删除旧设备条目，再把本机 IP 改回 $TS_EXPECTED_IP"
        warn "  https://login.tailscale.com/admin/machines -> 本机 -> ⋯ -> Edit machine IP"
        return 0
    fi
    _devjson=/tmp/ts-devices.json
    ts_api GET /tailnet/-/devices > "$_devjson" || { warn "拉取设备列表失败，请按上述后台步骤手动恢复"; rm -f "$_devjson"; return 0; }
    _selfid=$(ts_find_device_by_ip "$_devjson" "$_cur")
    _oldid=$(ts_find_device_by_ip "$_devjson" "$TS_EXPECTED_IP")
    rm -f "$_devjson"
    [ -n "$_selfid" ] || { warn "设备列表中找不到本机（$_cur），请手动恢复"; return 0; }
    if [ -n "$_oldid" ] && [ "$_oldid" != "$_selfid" ]; then
        log "删除占用 $TS_EXPECTED_IP 的旧设备条目（$_oldid）..."
        ts_api DELETE "/device/$_oldid" >/dev/null || { warn "删除旧设备失败，请后台手动删除后重跑本脚本"; return 0; }
    fi
    log "把本机（$_selfid）IP 设为 $TS_EXPECTED_IP..."
    ts_api POST "/device/$_selfid/ip" "{\"ipv4\":\"$TS_EXPECTED_IP\"}" >/dev/null || {
        warn "API 设置 IP 失败，请后台手动改：machines -> 本机 -> Edit machine IP"
        return 0
    }
    # IP 变更经控制面下发，本地稍候生效；过半仍未生效则重启 tailscaled 强制拉取
    _n=0
    while [ "$_n" -lt 12 ]; do
        [ "$(tailscale ip -4 2>/dev/null)" = "$TS_EXPECTED_IP" ] && { log "Tailscale IP 已恢复为 $TS_EXPECTED_IP ✅"; return 0; }
        _n=$((_n + 1))
        sleep 5
        [ "$_n" -eq 6 ] && /etc/init.d/tailscale restart 2>/dev/null
    done
    warn "IP 设置已提交但 60s 内本地未生效——稍后 tailscale ip -4 复查（verify 也会复核）"
}

approve_ts_routes() {
    # API 批准本机 advertised routes（subnet + exit node 双栈默认路由），消除后台手动点击。
    ts_has_oauth || return 0
    _cur=$(tailscale ip -4 2>/dev/null)
    [ -n "$_cur" ] || { warn "tailscale 未就绪，跳过路由批准"; return 0; }
    _devjson=/tmp/ts-devices.json
    ts_api GET /tailnet/-/devices > "$_devjson" || { warn "拉取设备列表失败，跳过路由批准（可重跑补）"; rm -f "$_devjson"; return 0; }
    _selfid=$(ts_find_device_by_ip "$_devjson" "$_cur")
    rm -f "$_devjson"
    [ -n "$_selfid" ] || { warn "设备列表中找不到本机，跳过路由批准"; return 0; }
    # 期望批准集 = 通告网段各项 + exit node（0.0.0.0/0 与 ::/0）
    _want=""
    for _r in $(printf '%s' "$TS_ADVERTISE_ROUTES" | tr ',' ' ') 0.0.0.0/0 ::/0; do
        _want="$_want,\"$_r\""
    done
    _want="[${_want#,}]"
    # 幂等：enabledRoutes 已含全部期望项则跳过
    _en=$(ts_api GET "/device/$_selfid/routes" | sed -n 's/.*"enabledRoutes"[[:space:]]*:[[:space:]]*\[\([^]]*\)\].*/\1/p')
    _missing=0
    for _r in $(printf '%s' "$TS_ADVERTISE_ROUTES" | tr ',' ' ') 0.0.0.0/0 ::/0; do
        printf '%s' "$_en" | grep -qF "\"$_r\"" || _missing=1
    done
    if [ "$_missing" = "0" ] && [ -n "$_en" ]; then
        log "subnet routes / exit node 已全部批准，跳过"
        return 0
    fi
    if ts_api POST "/device/$_selfid/routes" "{\"routes\":${_want}}" >/dev/null; then
        log "已 API 批准 routes: ${TS_ADVERTISE_ROUTES} + exit node"
    else
        warn "API 批准 routes 失败，请后台手动批准：machines -> 本机 -> Edit route settings"
    fi
}

ts_routes_approved() {
    # verify 辅助：本机 enabledRoutes 是否已含首个通告网段
    ts_has_oauth || return 1
    _ip=$(tailscale ip -4 2>/dev/null)
    [ -n "$_ip" ] || return 1
    _j=/tmp/ts-devices.verify.json
    ts_api GET /tailnet/-/devices > "$_j" 2>/dev/null || { rm -f "$_j"; return 1; }
    _id=$(ts_find_device_by_ip "$_j" "$_ip")
    rm -f "$_j"
    [ -n "$_id" ] || return 1
    ts_api GET "/device/$_id/routes" | \
        sed -n 's/.*"enabledRoutes"[[:space:]]*:[[:space:]]*\[\([^]]*\)\].*/\1/p' | \
        grep -qF "\"${TS_ADVERTISE_ROUTES%%,*}\""
}

install_keepalive_cron() {
    # 保活目标：默认 PEER_TS_IP；多热备时用 KEEPALIVE_PEERS 覆盖（逗号分隔，任一
    # 在线即维持映射）。EIM NAT 下保住 openwrt 41641 出站映射即惠及全部 VPS。
    _peers=$(printf '%s' "${KEEPALIVE_PEERS:-$PEER_TS_IP}" | tr ',' ' ')
    # 生成共享保活脚本：无参=cron 模式（一分钟内 4 轮、~15s 粒度，缩短 OpenClash
    # 重启后直连恢复窗口）；once=单轮（供 OpenClash 防火墙钩子重启后立即唤醒）。
    _ka=/usr/bin/cn-ts-keepalive
    backup_file "$_ka"
    cat > "$_ka" <<EOF
#!/bin/sh
# Tailscale 直连保活（openwrt-init.sh 生成）：ping 热备 peers 保住 openwrt 41641
# 出站 NAT 映射，EIM 特性下惠及全部 VPS 的直连。用法：cn-ts-keepalive [once]
PEERS="$_peers"
ping_round() { for p in \$PEERS; do /usr/sbin/tailscale ping -c 1 --timeout 5s "\$p" >/dev/null 2>&1; done; }
[ "\$1" = once ] && { ping_round; exit 0; }
# cron 模式：单实例锁，防 ping 持续超时使脚本逼近 60s 时与下一次 cron 叠加（flock 不可用则降级不加锁）
if command -v flock >/dev/null 2>&1; then exec 9>/var/run/cn-ts-keepalive.lock; flock -n 9 || exit 0; fi
i=0
while [ \$i -lt 4 ]; do ping_round; i=\$((i+1)); [ \$i -lt 4 ] && sleep 15; done
EOF
    chmod +x "$_ka"
    _line="* * * * * $_ka >/dev/null 2>&1"
    _crontab=/etc/crontabs/root
    touch "$_crontab"
    # 迁移：清理历史的旧版单行 ping（直接 cron ping，无脚本封装）
    if grep -q "tailscale ping -c 1 --timeout 5s" "$_crontab" && ! grep -qF "$_ka" "$_crontab"; then
        sed -i '/tailscale ping -c 1 --timeout 5s/d' "$_crontab"
    fi
    if grep -qF "$_ka" "$_crontab"; then
        log "keepalive cron 已存在，跳过"
    else
        printf '%s\n' "$_line" >> "$_crontab"
        /etc/init.d/cron enable 2>/dev/null
        /etc/init.d/cron restart 2>/dev/null
        log "已添加 keepalive cron（每分钟 4 轮 ~15s ping: $_peers）"
    fi
}

# ── OpenClash 防火墙放行 Tailscale ────────────────────────────────

setup_tailscale_firewall_bypass() {
    _hook=/etc/openclash/custom/openclash_custom_firewall_rules.sh
    mkdir -p /etc/openclash/custom
    backup_file "$_hook"
    # OpenClash 原生钩子：每次 OpenClash 重启后自动重跑，把 tailscale UDP
    # 在 mangle 链顶部 return，绕过 tproxy 接管。钩子自身带去重，防叠加。
    cat > "$_hook" <<EOF
#!/bin/sh
. /usr/share/openclash/log.sh 2>/dev/null
. /lib/functions.sh 2>/dev/null

# This script is called by /etc/init.d/openclash after OpenClash builds its rules.
# 放行 Tailscale UDP ${TS_PORT}：让 tailscaled 的 disco/STUN 流量绕过 tproxy 打标。

LOG_TIP "Start Add Custom Firewall Rules..." 2>/dev/null
TS_PORT=${TS_PORT}
for CHAIN in openclash_mangle openclash_mangle_output openclash_mangle_v6 openclash_mangle_output_v6; do
    nft list chain inet fw4 "\$CHAIN" >/dev/null 2>&1 || continue
    # 去重：本链已有 tailscale-bypass 注释则跳过
    if nft list chain inet fw4 "\$CHAIN" 2>/dev/null | grep -q "tailscale-bypass"; then
        continue
    fi
    nft insert rule inet fw4 "\$CHAIN" udp sport \$TS_PORT counter return comment "tailscale-bypass" 2>/dev/null
    nft insert rule inet fw4 "\$CHAIN" udp dport \$TS_PORT counter return comment "tailscale-bypass" 2>/dev/null
done
LOG_TIP "Tailscale UDP \$TS_PORT bypass injected." 2>/dev/null
# OpenClash 重启会 flush fw4 + 打断 tailscale 直连；注入 bypass 后立即唤醒一轮保活，
# 重建 41641 出站映射，把直连恢复窗口从"等下一个 cron 分钟"压到秒级。
[ -x /usr/bin/cn-ts-keepalive ] && /usr/bin/cn-ts-keepalive once >/dev/null 2>&1 &
exit 0
EOF
    chmod +x "$_hook"
    log "已写入 OpenClash 防火墙钩子: $_hook"
    # 立即生效一次（不等下次 OpenClash 重启）
    sh "$_hook" 2>/dev/null
    _cnt=$(nft list ruleset 2>/dev/null | grep -c "tailscale-bypass")
    log "当前 tailscale-bypass 规则数: $_cnt"
}

setup_tailscale_persistent_bypass() {
    # 上面钩子注入的 mangle return 规则随 openclash_mangle* 链在 OpenClash 重启时
    # 被 flush，钩子要到启动 Step 6 才重新注入 —— 窗口期（~20s）tailscaled 的 UDP
    # 被打标进 TUN，mihomo EIN NAT 绑同源端口 ${TS_PORT} 与 tailscaled 冲突
    # （listenLocalConn address already in use 刷屏 + 直连退化 DERP）。
    # 兜底：fw4 把 /etc/nftables.d/*.nft 持久 include 进 inet fw4 表，OpenClash
    # 只 flush 自己的 openclash_* 链不会动它。挂在 mangle(-150) 之后（-149）把
    # ${TS_PORT} 流量的 fwmark 清零；output 链用 route 类型，改 mark 触发重路由
    # 回主表，不再进 utun。只匹配 OpenClash 的 fwmark 0x162（hardcode，OpenClash
    # 多年固定值 → ip rule lookup 354），不能写 mark != 0 —— 那会误清 tailscaled
    # 自身的 0x80000 防环路标记。窗口期之外无 0x162 标记，规则不命中、零开销。
    _inc=/etc/nftables.d/99-cn-exit-tailscale.nft
    mkdir -p /etc/nftables.d
    backup_file "$_inc"
    cat > "$_inc" <<EOF
# sb-xray openwrt-init.sh 生成：tailscaled UDP ${TS_PORT} 持久绕过（OpenClash 重启窗口期兜底）
chain cn_exit_ts_output {
    type route hook output priority -149; policy accept;
    meta mark 0x162 udp sport ${TS_PORT} counter meta mark set 0 comment "tailscale-bypass-persist"
    meta mark 0x162 udp dport ${TS_PORT} counter meta mark set 0 comment "tailscale-bypass-persist"
}
chain cn_exit_ts_prerouting {
    type filter hook prerouting priority -149; policy accept;
    meta mark 0x162 udp sport ${TS_PORT} counter meta mark set 0 comment "tailscale-bypass-persist"
    meta mark 0x162 udp dport ${TS_PORT} counter meta mark set 0 comment "tailscale-bypass-persist"
}
EOF
    /etc/init.d/firewall reload >/dev/null 2>&1
    if nft list chain inet fw4 cn_exit_ts_output >/dev/null 2>&1; then
        log "持久 bypass 链已生效: cn_exit_ts_output/cn_exit_ts_prerouting (UDP ${TS_PORT})"
    else
        warn "持久 bypass 链未生效 —— 检查 fw4 是否 include /etc/nftables.d（fw4 print 排查）"
    fi
}

# ── reverse bridge 与 OpenClash 解耦 ──────────────────────────────

# ── mihomo SOCKS 入站放行 Tailscale 免认证 ───────────────────────

setup_socks_skip_auth() {
    # 机场订阅可能给 mihomo 的 SOCKS/HTTP 入站开了 authentication，且下发的
    # skip-auth-prefixes 通常只含 RFC1918。kernel TUN 模式下 VPS 经 Tailscale
    # 访问本机 7891(cn-exit SOCKS5) 的源 IP 落在 100.64.0.0/10（CGNAT），不在
    # 豁免内 → SOCKS 握手被要求认证而失败、cn-exit 整体不通。
    # 用 OpenClash 官方覆写钩子（restart 时对生成配置生效，订阅更新后自动重补）
    # 幂等注入该网段。机场未开认证时多一个豁免段也无害。
    _hook=/etc/openclash/custom/openclash_custom_overwrite.sh
    [ -f "$_hook" ] || { warn "未找到 $_hook，跳过 skip-auth 注入"; return 0; }
    if grep -q "skip-auth-prefixes.*100.64" "$_hook"; then
        log "overwrite 钩子已含 Tailscale 免认证注入，跳过"
        return 0
    fi
    backup_file "$_hook"
    sed -i '/^exit 0$/d' "$_hook"
    # quoted here-doc：$CONFIG_FILE/$LOG_FILE 保持字面，留给 overwrite.sh 运行时展开
    cat >> "$_hook" <<'RUBYEOF'

# 放行 Tailscale CGNAT 段(100.64.0.0/10)免 SOCKS/HTTP 入站认证（sb-xray openwrt-init.sh 注入）。
# 幂等：include? 去重；订阅更新后由本钩子自动重补。
ruby -ryaml -rYAML -I "/usr/share/openclash" -E UTF-8 -e "
   begin
      Value = YAML.load_file('$CONFIG_FILE');
      Value['skip-auth-prefixes'] ||= [];
      Value['skip-auth-prefixes'].unshift('100.64.0.0/10') unless Value['skip-auth-prefixes'].include?('100.64.0.0/10');
      File.open('$CONFIG_FILE','w') {|f| YAML.dump(Value, f)};
   rescue Exception => e
      puts '[skip-auth-prefixes inject] ' + e.message;
   end" 2>/dev/null >> "$LOG_FILE"
RUBYEOF
    echo "exit 0" >> "$_hook"
    log "已注入 Tailscale 免认证到 OpenClash overwrite 钩子（需 restart 生效）"
}

setup_global_reorder() {
    # 注入自定义 GLOBAL select 组，把内置 DIRECT/REJECT 排到选单最后（纯 UI 顺序，不影响路由）。
    # 默认 mihomo 自动生成的 GLOBAL 把这两个固定排最前；显式定义同名组即可由用户接管顺序。
    # 成员动态取自现有 proxy-groups 组名 + 末尾补 DIRECT/REJECT；幂等；订阅更新后本钩子自动重补。
    _hook=/etc/openclash/custom/openclash_custom_overwrite.sh
    [ -f "$_hook" ] || { warn "未找到 $_hook，跳过 GLOBAL 重排注入"; return 0; }
    if grep -q "GLOBAL reorder inject" "$_hook"; then
        log "overwrite 钩子已含 GLOBAL 重排注入，跳过"
        return 0
    fi
    backup_file "$_hook"
    sed -i '/^exit 0$/d' "$_hook"
    # quoted here-doc：$CONFIG_FILE/$LOG_FILE 保持字面，留给 overwrite.sh 运行时展开
    cat >> "$_hook" <<'RUBYEOF'

# 自定义 GLOBAL 组：内置 DIRECT/REJECT 排到选单最后（sb-xray openwrt-init.sh 注入）。
# 幂等：已有 GLOBAL 组则跳过；成员动态取组名，订阅改名也不会引用到不存在的代理。
ruby -ryaml -rYAML -I "/usr/share/openclash" -E UTF-8 -e "
   begin
      Value = YAML.load_file('$CONFIG_FILE');
      Value['proxy-groups'] ||= [];
      unless Value['proxy-groups'].any?{|g| g['name'].to_s=='GLOBAL'};
        names = Value['proxy-groups'].map{|g| g['name'].to_s}.reject{|n| n=='GLOBAL' || n.empty?};
        Value['proxy-groups'].push({'name'=>'GLOBAL','type'=>'select','proxies'=>names + ['DIRECT','REJECT']});
        File.open('$CONFIG_FILE','w') {|f| YAML.dump(Value, f)};
      end;
   rescue Exception => e
      puts '[GLOBAL reorder inject] ' + e.message;
   end" 2>/dev/null >> "$LOG_FILE"
RUBYEOF
    echo "exit 0" >> "$_hook"
    log "已注入 GLOBAL 重排到 OpenClash overwrite 钩子（需 restart 生效）"
}

setup_openclash_decouple() {
    # 未装 OpenClash（纯 reverse 模式可能如此）则跳过：解耦只对 OpenClash 有意义
    [ -x /etc/init.d/openclash ] || { log "未检测到 OpenClash，跳过解耦"; return 0; }
    _rules=/etc/openclash/custom/openclash_custom_rules.list
    _filter=/etc/openclash/custom/openclash_custom_fake_filter.list
    _nl=/etc/cn-exit/nodes.list
    mkdir -p /etc/openclash/custom
    touch "$_rules" "$_filter"
    [ -f "$_nl" ] || { warn "节点清单 $_nl 不存在，跳过解耦"; return 0; }

    # 对清单内每个 VPS 域名解耦：DIRECT（OpenClash 直连不进代理 —— socks5 腿防环路
    # + bridge 直连真实 IP）+ fake-ip 过滤（解析真实 IP 而非 fake-ip）。与拨不拨
    # 无关，所有已知 VPS 域名都解耦。
    backup_file "$_rules"
    backup_file "$_filter"
    _added=0
    while read -r _n _dom _t; do
        case "$_n" in ''|\#*) continue ;; esac
        [ -n "$_dom" ] || continue
        if ! grep -qF "DOMAIN,${_dom},DIRECT" "$_rules"; then
            if grep -q '^rules:' "$_rules"; then
                sed -i "/^rules:/a - DOMAIN,${_dom},DIRECT" "$_rules"
            else
                printf 'rules:\n- DOMAIN,%s,DIRECT\n' "$_dom" >> "$_rules"
            fi
            _added=$((_added + 1))
        fi
        grep -qxF "$_dom" "$_filter" || printf '%s\n' "$_dom" >> "$_filter"
    done < "$_nl"
    log "解耦完成：本次新增 $_added 条 DIRECT 规则"

    if [ "$RELOAD_OPENCLASH" = "1" ]; then
        # 必须 restart 而非 reload：skip-auth/force-direct 的 overwrite 钩子只在
        # restart 的配置生成流程里跑，reload 仅热载规则、不触发 overwrite。
        # 配置纳管（setup_openclash_config）应用的新 /etc/config/openclash 也由这次
        # restart 统一生效（该步骤自身不重启，避免双重重启）。
        log "restart OpenClash 使配置 + 解耦规则 + skip-auth + force-direct 生效..."
        /etc/init.d/openclash restart 2>/dev/null
    else
        warn "配置/解耦/skip-auth/force-direct 需 OpenClash restart 才生效（reload 不触发 overwrite 钩子）—— 请手动 /etc/init.d/openclash restart，或设 RELOAD_OPENCLASH=1 重跑"
    fi
}

# ── socks5 入站回国流量强制 direct（两腿质量对齐）─────────────────

setup_socks5_force_direct() {
    # socks5 腿（VPS 经 Tailscale 连本机 OpenClash SOCKS5 7891 回国）的流量会被
    # OpenClash 二次分流，灰色域名可能被判海外走代理 → 回国失败。注入按入站端口
    # 7891 强制 direct 的规则，对齐 r-tunnel 的纯直出，使冷备台（长期仅 socks5）
    # 也满质量。关键：按 IN-PORT 区分，不能按来源 IP —— socks5 来源与 exit-node
    # 终端同为 Tailscale CGNAT 100.64.0.0/10，按 IP 会误伤终端的海外流量。
    [ -x /etc/init.d/openclash ] || { log "未检测到 OpenClash，跳过 socks5 force-direct"; return 0; }
    _rules=/etc/openclash/custom/openclash_custom_rules.list
    mkdir -p /etc/openclash/custom
    touch "$_rules"
    if grep -qF "IN-PORT,7891,DIRECT" "$_rules"; then
        log "socks5 force-direct 规则已存在，跳过"
        return 0
    fi
    backup_file "$_rules"
    if grep -q '^rules:' "$_rules"; then
        sed -i "/^rules:/a - IN-PORT,7891,DIRECT" "$_rules"
    else
        printf 'rules:\n- IN-PORT,7891,DIRECT\n' >> "$_rules"
    fi
    log "已注入 socks5 入站强制 direct 规则（IN-PORT,7891）"
}

# ── OpenClash 配置纳管（op-amd/op-arm 模板渲染 + 幂等应用）─────────

openclash_cfg_same() {
    # 规范化比对：剥行尾空白；$(...) 自动吞尾部空行，故末尾空行差异不计
    _ca=$(sed -e 's/[[:space:]]*$//' "$1" 2>/dev/null)
    _cb=$(sed -e 's/[[:space:]]*$//' "$2" 2>/dev/null)
    [ "$_ca" = "$_cb" ]
}

render_openclash_config() {
    # render_openclash_config <模板> <输出>
    # ① 占位符注入：<OPENCLASH_DASHBOARD_PASSWORD> ← 同名变量
    # ② 订阅块处理：OPENCLASH_SUBS（"名=URL 名=URL" 空格分隔）按 option name 匹配，
    #    命中的 config_subscribe 块在 name 行后注入 option address；未命中的订阅块
    #    （模板里的 AllOne / 占位示例）整块裁剪 —— 模板保留示例，路由器产物只含实配。
    sed "s|<OPENCLASH_DASHBOARD_PASSWORD>|${OPENCLASH_DASHBOARD_PASSWORD}|" "$1" | \
    awk -v subs="$OPENCLASH_SUBS" -v q="'" '
        BEGIN {
            n = split(subs, a, " ")
            for (i = 1; i <= n; i++) {
                p = index(a[i], "=")
                if (p > 1) url[substr(a[i], 1, p - 1)] = substr(a[i], p + 1)
            }
        }
        function flush_block(    j, k) {
            if (!inblk) return
            if (name != "" && (name in url)) {
                # address 注入在块尾（与 LuCI 实际保存顺序一致），空行分隔符之前
                k = bn
                while (k > 0 && blk[k] == "") k--
                for (j = 1; j <= k; j++) print blk[j]
                printf "\toption address %s%s%s\n", q, url[name], q
                for (j = k + 1; j <= bn; j++) print blk[j]
            }
            inblk = 0; bn = 0; name = ""
        }
        /^config config_subscribe/ { flush_block(); inblk = 1 }
        {
            if (inblk) {
                if ($0 ~ /^config / && $0 !~ /config_subscribe/) { flush_block(); print; next }
                if ($0 ~ /option address /) next
                if ($0 ~ /option name /) { split($0, parts, q); name = parts[2] }
                bn++; blk[bn] = $0
                next
            }
            print
        }
        END { flush_block() }
    ' > "$2"
    chmod 600 "$2"
}

setup_openclash_config() {
    # 把 op-amd/op-arm 模板渲染后幂等应用到 /etc/config/openclash。
    # OPENCLASH_MANAGE=0 关闭（只走钩子/规则注入，不碰配置文件本体）。
    [ "$OPENCLASH_MANAGE" = "1" ] || { log "OPENCLASH_MANAGE=$OPENCLASH_MANAGE，跳过 OpenClash 配置纳管"; return 0; }
    [ -x /etc/init.d/openclash ] || { log "未检测到 OpenClash，跳过配置纳管"; return 0; }
    [ -n "$OPENCLASH_DASHBOARD_PASSWORD" ] || \
        die "OpenClash 配置纳管需 OPENCLASH_DASHBOARD_PASSWORD（或设 OPENCLASH_MANAGE=0 跳过纳管）"
    [ -n "$OPENCLASH_SUBS" ] || warn "OPENCLASH_SUBS 为空 —— 渲染产物将不含任何订阅块"

    # 模板按架构选择；同目录文件优先，否则从 GitHub raw 的 sources/openclash/ 下载
    case "$TS_ARCH" in
        amd64) _tpl_name=op-amd ;;
        *)     _tpl_name=op-arm ;;
    esac
    _tpl="$(dirname "$0")/$_tpl_name"
    if [ ! -f "$_tpl" ]; then
        _tpl="/tmp/$_tpl_name"
        download_verify \
            "https://raw.githubusercontent.com/currycan/sb-xray/main/sources/openclash/$_tpl_name" \
            "$_tpl" raw
    fi
    grep -q "config_subscribe" "$_tpl" || die "OpenClash 模板不完整: $_tpl"

    # 渲染产物含密码/token，置 /tmp（tmpfs，重启即清）且 600；保留到 verify 做漂移比对
    _rendered=/tmp/openclash.rendered
    render_openclash_config "$_tpl" "$_rendered"

    _live=/etc/config/openclash
    if [ -f "$_live" ] && openclash_cfg_same "$_rendered" "$_live"; then
        log "OpenClash 配置无漂移，跳过应用"
        return 0
    fi
    backup_file "$_live"
    cp "$_rendered" "$_live"
    log "已应用 OpenClash 配置: $_live（模板 $_tpl_name）"
    # 不在此处 restart：统一由解耦步骤末尾的 RELOAD_OPENCLASH 逻辑触发，避免双重重启
    [ "$RELOAD_OPENCLASH" = "1" ] || \
        warn "OpenClash 配置已更新，需 /etc/init.d/openclash restart 生效（或设 RELOAD_OPENCLASH=1 重跑）"
}

# ── 节点清单与 cn-bridge 工具 ─────────────────────────────────────

generate_nodes_list() {
    # 生成 /etc/cn-exit/nodes.list（每行 <名> <FQDN> <token>），供 cn-bridge 拨号
    # 与 OpenClash 解耦遍历。来源优先级：① NODES_FILE 多行文件（推荐，默认脚本同目录
    # nodes.list）；② BRIDGE_NODES 内联（名:域名:token 空格分隔）；③ 单节点旧用法
    # VPS_DOMAIN + SUBSCRIBE_TOKEN。
    mkdir -p /etc/cn-exit
    _nl=/etc/cn-exit/nodes.list
    _src="${NODES_FILE:-$(dirname "$0")/nodes.list}"
    if [ -f "$_src" ] && [ "$_src" != "$_nl" ]; then
        backup_file "$_nl"
        cp "$_src" "$_nl"
        chmod 600 "$_nl"
        _cnt=$(awk '!/^#/&&NF{c++} END{print c+0}' "$_nl")
        log "已从 $_src 装入节点清单（$_cnt 个节点）"
        return 0
    fi
    backup_file "$_nl"
    {
        printf '# sb-xray reverse bridge 节点清单（openwrt-init.sh 生成）\n'
        printf '# 格式：<名> <FQDN> <token>\n'
        if [ -n "$BRIDGE_NODES" ]; then
            for _it in $BRIDGE_NODES; do
                _nm=${_it%%:*}; _rest=${_it#*:}; _dm=${_rest%%:*}; _tk=${_rest#*:}
                [ -n "$_nm" ] && [ -n "$_dm" ] && [ -n "$_tk" ] && \
                    printf '%s %s %s\n' "$_nm" "$_dm" "$_tk"
            done
        elif [ -n "$VPS_DOMAIN" ] && [ -n "$SUBSCRIBE_TOKEN" ]; then
            printf '%s %s %s\n' "${VPS_DOMAIN%%.*}" "$VPS_DOMAIN" "$SUBSCRIBE_TOKEN"
        fi
    } > "$_nl"
    chmod 600 "$_nl"
    _cnt=$(awk '!/^#/&&NF{c++} END{print c+0}' "$_nl")
    log "已生成节点清单 ${_nl}（$_cnt 个节点）"
}

install_cn_bridge() {
    _src="$(dirname "$0")/cn-bridge"
    if [ -f "$_src" ]; then
        cp "$_src" /usr/bin/cn-bridge
    else
        download_verify \
            "https://raw.githubusercontent.com/currycan/sb-xray/main/sources/openwrt/cn-bridge" \
            /usr/bin/cn-bridge raw
    fi
    chmod +x /usr/bin/cn-bridge
    log "已安装 cn-bridge 拨号工具"
}

# ── xray reverse bridge 落地机 ────────────────────────────────────

install_xray_bridge() {
    if [ -x /usr/bin/xray ] && xray version 2>/dev/null | grep -q "$XRAY_VERSION"; then
        log "Xray $XRAY_VERSION 已安装，跳过下载"
    else
        command -v unzip >/dev/null 2>&1 || { opkg update >/dev/null 2>&1; opkg install unzip >/dev/null 2>&1; }
        command -v unzip >/dev/null 2>&1 || die "无法安装 unzip"
        _zip=/tmp/xray.zip
        download_verify \
            "https://github.com/XTLS/Xray-core/releases/download/v${XRAY_VERSION}/${XRAY_ZIP}" \
            "$_zip" zip
        unzip -o "$_zip" xray -d /usr/bin/ >/dev/null || die "解压 xray 失败"
        chmod +x /usr/bin/xray
        rm -f "$_zip"
        log "Xray $XRAY_VERSION 安装完成"
    fi

    mkdir -p /etc/xray
    install_cn_bridge

    # 清理历史单 bridge 残留（旧版 /etc/init.d/xray-bridge + 单 client.json），
    # 多节点改用 per-node 的 xray-bridge-<名> + client-<名>.json。
    if [ -x /etc/init.d/xray-bridge ]; then
        /etc/init.d/xray-bridge stop 2>/dev/null
        /etc/init.d/xray-bridge disable 2>/dev/null
        rm -f /etc/init.d/xray-bridge /etc/xray/client.json
        log "已清理旧版单 xray-bridge 服务"
    fi

    # 拨通热备（BRIDGE_HOT；未指定则拨清单内全部，兼容单节点旧用法）。
    # 节点清单已由 main 的 generate_nodes_list 生成。
    _hot="${BRIDGE_HOT:-$(awk '!/^#/&&NF{print $1}' /etc/cn-exit/nodes.list)}"
    _hot=$(printf '%s' "$_hot" | tr ',' ' ')
    [ -n "$_hot" ] || { warn "无热备节点可拨（BRIDGE_HOT 与节点清单均空）"; return 0; }
    for _bn in $_hot; do
        cn-bridge up "$_bn" || warn "拨通 $_bn 失败"
    done
    log "热备拨号完成: $_hot"
}

# ── 双腿监控告警 ──────────────────────────────────────────────────

install_monitor() {
    _src="$(dirname "$0")/cn-bridge-monitor"
    if [ -f "$_src" ]; then
        cp "$_src" /usr/bin/cn-bridge-monitor
    else
        download_verify \
            "https://raw.githubusercontent.com/currycan/sb-xray/main/sources/openwrt/cn-bridge-monitor" \
            /usr/bin/cn-bridge-monitor raw
    fi
    chmod +x /usr/bin/cn-bridge-monitor
}

setup_monitor_cron() {
    # 探活热备 r-tunnel 隧道 + Tailscale 链路，异常去抖后 telegram 告警。
    # 未设 ALERT_TG_TOKEN 时监控仍跑，只记录不告警。
    install_monitor
    _hot="${BRIDGE_HOT:-$(awk '!/^#/&&NF{print $1}' /etc/cn-exit/nodes.list 2>/dev/null)}"
    _hot=$(printf '%s' "$_hot" | tr ',' ' ')
    mkdir -p /etc/cn-exit
    _menv=/etc/cn-exit/monitor.env
    backup_file "$_menv"
    {
        printf 'TG_TOKEN=%s\n' "${ALERT_TG_TOKEN}"
        printf 'TG_CHAT=%s\n' "${ALERT_TG_CHAT}"
        printf 'HOT="%s"\n' "$_hot"
        printf 'MON_THRESHOLD=%s\n' "${MON_THRESHOLD:-3}"
    } > "$_menv"
    chmod 600 "$_menv"
    _line="*/${MON_INTERVAL:-2} * * * * /usr/bin/cn-bridge-monitor >/dev/null 2>&1"
    _crontab=/etc/crontabs/root
    touch "$_crontab"
    if grep -qF "/usr/bin/cn-bridge-monitor" "$_crontab"; then
        log "监控 cron 已存在，跳过"
    else
        printf '%s\n' "$_line" >> "$_crontab"
        /etc/init.d/cron enable 2>/dev/null
        /etc/init.d/cron restart 2>/dev/null
        log "已添加监控 cron（每 ${MON_INTERVAL:-2} 分钟）"
    fi
    [ -n "$ALERT_TG_TOKEN" ] || warn "未设 ALERT_TG_TOKEN —— 监控只记录、不发 telegram 告警"
}

# ── CDN IP 优选（内嵌 cdn-speedtest，写出 /usr/bin/cdn-speedtest）──

write_cdn_speedtest() {
    # 内嵌完整 cdn-speedtest（原 sources/hack/cdn-speedtest.sh，已并入本脚本）。
    # quoted here-doc：内容原样写出，运行时变量留给 cdn-speedtest 自己展开。
    cat > "$1" <<'CDNEOF'
#!/bin/sh
# cdn-speedtest - Cloudflare CDN IP 优选脚本（openwrt-init.sh 内嵌生成，勿手改）
# 适用于 OpenWrt (amd64/arm64)，自动测速并将最优 IP 写入 /etc/hosts

set -eu

# ======================== 配置区 ========================

# CDN 根域名（从环境变量读取）
CDNDOMAIN="${CDNDOMAIN:-}"

# 子域名前缀配置文件（一行一个前缀）
CDN_SUBDOMAINS_FILE="${CDN_SUBDOMAINS_FILE:-/etc/subdomains.txt}"

# CloudflareST 参数
SPEED_TEST_THREADS="${SPEED_TEST_THREADS:-500}"        # 延迟测速线程数
SPEED_TEST_TIME="${SPEED_TEST_TIME:-4}"             # 下载测速时间(秒)
SPEED_TEST_COUNT="${SPEED_TEST_COUNT:-5}"             # 下载测速数量
SPEED_TEST_LATENCY_MAX="${SPEED_TEST_LATENCY_MAX:-200}"   # 延迟上限(ms)
SPEED_TEST_MIN_SPEED="${SPEED_TEST_MIN_SPEED:-5}"        # 最低下载速度(MB/s)，低于此值视为失败

# 安装目录
INSTALL_DIR="/etc/CloudflareST"
LOG_FILE="/var/log/cdn-speedtest.log"
# 上次优选结果记录（IP|速度|延迟）
LAST_RESULT_FILE="${INSTALL_DIR}/last_best.txt"

# ======================== 函数 ========================

log() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $1"
    echo "$msg"
    echo "$msg" >> "$LOG_FILE"
}

# 根据前缀配置文件 + CDNDOMAIN 拼接完整域名
build_cdn_domains() {
    if [ -z "$CDNDOMAIN" ]; then
        log "ERROR: 未设置 CDNDOMAIN 环境变量，用法: CDNDOMAIN=example.com $0"
        exit 1
    fi

    if [ ! -f "$CDN_SUBDOMAINS_FILE" ]; then
        log "ERROR: 子域名配置文件不存在: ${CDN_SUBDOMAINS_FILE}"
        log "请创建该文件，每行一个子域名前缀，例如:"
        log "  echo -e 'jp\nbig\ncn2' > ${CDN_SUBDOMAINS_FILE}"
        exit 1
    fi

    CDN_DOMAINS=""
    while read -r prefix; do
        # 跳过空行和注释
        case "$prefix" in
            ""|\#*) continue ;;
        esac
        CDN_DOMAINS="${CDN_DOMAINS}${prefix}.${CDNDOMAIN}
"
    done < "$CDN_SUBDOMAINS_FILE"

    local count
    count=$(echo "$CDN_DOMAINS" | grep -c '\S')
    if [ "$count" -eq 0 ]; then
        log "ERROR: 配置文件 ${CDN_SUBDOMAINS_FILE} 中无有效前缀"
        exit 1
    fi

    log "共 ${count} 个 CDN 域名 (*.${CDNDOMAIN}):"
    echo "$CDN_DOMAINS" | while read -r d; do
        [ -z "$d" ] && continue
        log "  - ${d}"
    done
}

detect_arch() {
    case "$(uname -m)" in
        x86_64|amd64)   echo "amd64" ;;
        aarch64|arm64)   echo "arm64" ;;
        armv7*)          echo "armv7" ;;
        *)
            log "ERROR: 不支持的架构: $(uname -m)"
            exit 1
            ;;
    esac
}

# busybox tar 兼容回退：上游 cfst tar.gz 是无 ustar 魔数的老式 tar（bsdtar/GNU tar
# 可读；busybox tar 硬性要求魔数，-z 与流式管道均报 invalid tar magic，且历代资产
# 均如此）。按 512 字节块手工走档头，仅抽出 cfst 二进制——零依赖，与上游版本无关。
extract_cfst_fallback() {
    local tarball="$1" dest="$2" raw name size_oct size blocks off total
    raw="${dest}/.cfst_raw.$$"
    gunzip -c "$tarball" > "$raw" || { rm -f "$raw"; return 1; }
    total=$(wc -c < "$raw")
    off=0
    while [ $((off + 512)) -le "$total" ]; do
        name=$(dd if="$raw" bs=1 skip="$off" count=100 2>/dev/null | tr -d '\0')
        [ -n "$name" ] || break
        size_oct=$(dd if="$raw" bs=1 skip=$((off + 124)) count=12 2>/dev/null | tr -d '\0 ')
        case "$size_oct" in ''|*[!0-7]*) size=0 ;; *) size=$((0$size_oct)) ;; esac
        blocks=$(( (size + 511) / 512 ))
        if [ "$name" = "cfst" ]; then
            dd if="$raw" bs=512 skip=$((off / 512 + 1)) count="$blocks" 2>/dev/null | head -c "$size" > "${dest}/cfst"
            rm -f "$raw"
            [ -s "${dest}/cfst" ] && return 0
            return 1
        fi
        off=$((off + 512 + blocks * 512))
    done
    rm -f "$raw"
    return 1
}

install_cloudflarest() {
    local arch="$1"
    local tarball="cfst_linux_${arch}.tar.gz"
    local github_url="https://github.com/XIU2/CloudflareSpeedTest/releases/latest/download/${tarball}"
    local proxy_url="https://gh-proxy.com/${github_url}"

    mkdir -p "$INSTALL_DIR"

    if [ -x "${INSTALL_DIR}/cfst" ]; then
        log "CloudflareST 已安装"
        return 0
    fi

    local download_cmd
    if command -v wget > /dev/null 2>&1; then
        download_cmd="wget -q -O"
    elif command -v curl > /dev/null 2>&1; then
        download_cmd="curl -sL -o"
    else
        log "ERROR: 需要 wget 或 curl"
        exit 1
    fi

    log "下载 CloudflareST (${arch})..."
    if ! $download_cmd "${INSTALL_DIR}/${tarball}" "$github_url" 2>/dev/null; then
        log "GitHub 直连失败，尝试代理镜像..."
        if ! $download_cmd "${INSTALL_DIR}/${tarball}" "$proxy_url" 2>/dev/null; then
            log "ERROR: 下载失败，请手动下载 ${tarball} 到 ${INSTALL_DIR}/"
            exit 1
        fi
    fi

    if ! tar -xzf "${INSTALL_DIR}/${tarball}" -C "$INSTALL_DIR" 2>/dev/null; then
        log "busybox tar 解包失败（归档无 ustar 魔数），使用内置回退解析..."
        if ! extract_cfst_fallback "${INSTALL_DIR}/${tarball}" "$INSTALL_DIR"; then
            log "ERROR: 解包失败，请手动解出 cfst 到 ${INSTALL_DIR}/"
            exit 1
        fi
    fi
    chmod +x "${INSTALL_DIR}/cfst"
    rm -f "${INSTALL_DIR}/${tarball}"
    log "CloudflareST 安装完成: ${INSTALL_DIR}/cfst"
}

run_speedtest() {
    log "开始 Cloudflare IP 优选测速..."
    log "参数: 线程=${SPEED_TEST_THREADS} 测速时间=${SPEED_TEST_TIME}s 数量=${SPEED_TEST_COUNT} 延迟上限=${SPEED_TEST_LATENCY_MAX}ms 最低速度=${SPEED_TEST_MIN_SPEED}MB/s"

    if [ -f /etc/init.d/openclash ]; then
        log "检测到 OpenClash，停止服务以确保测速直连..."
        /etc/init.d/openclash stop > /dev/null 2>&1
        log "OpenClash 已停止"
        # OpenClash 停止后 DNS 失效，临时使用公共 DNS
        log "备份 /etc/resolv.conf"
        cp /etc/resolv.conf /etc/resolv.conf.bak.cdn-speedtest
        printf 'nameserver 1.1.1.1\nnameserver 8.8.8.8\n' > /etc/resolv.conf
        log "已临时切换至公共 DNS (1.1.1.1, 8.8.8.8)"
        # 验证 DNS 是否可用
        if nslookup speed.cloudflare.com > /dev/null 2>&1; then
            log "DNS 验证通过"
        else
            log "WARN: DNS 验证失败，测速可能受影响"
        fi
    else
        log "未检测到 OpenClash，直接测速"
    fi

    cd "$INSTALL_DIR"

    ./cfst \
        -n "$SPEED_TEST_THREADS" \
        -t "$SPEED_TEST_TIME" \
        -dn "$SPEED_TEST_COUNT" \
        -tl "$SPEED_TEST_LATENCY_MAX" \
        -sl "$SPEED_TEST_MIN_SPEED" \
        -o result.csv

    if [ -f /etc/init.d/openclash ]; then
        if [ -f /etc/resolv.conf.bak.cdn-speedtest ]; then
            log "恢复 /etc/resolv.conf"
            cp /etc/resolv.conf.bak.cdn-speedtest /etc/resolv.conf
            rm -f /etc/resolv.conf.bak.cdn-speedtest
        fi
        log "启动 OpenClash..."
        /etc/init.d/openclash start > /dev/null 2>&1
        log "OpenClash 已启动"
    fi

    log "测速完成"

    if [ ! -f result.csv ]; then
        log "ERROR: 测速结果文件不存在"
        return 1
    fi

    # 取最优 IP（第二行第一列）
    BEST_IP=$(sed -n '2p' result.csv | cut -d',' -f1)

    if [ -z "$BEST_IP" ]; then
        log "ERROR: 未找到满足条件的优选 IP"
        return 1
    fi

    # 打印 Top 10 结果
    log "测速结果 Top 10:"
    log "  IP 地址          | 延迟(ms) | 速度(MB/s)"
    sed -n '2,11p' result.csv | while IFS=',' read -r ip _ _ _ latency speed; do
        log "  ${ip} | ${latency} | ${speed}"
    done

    BEST_LATENCY=$(sed -n '2p' result.csv | cut -d',' -f5)
    BEST_SPEED=$(sed -n '2p' result.csv | cut -d',' -f6)
    log "最优 IP: ${BEST_IP} (延迟: ${BEST_LATENCY}ms, 速度: ${BEST_SPEED}MB/s)"

    echo "$BEST_IP"
}

# 保存本次优选结果
save_result() {
    local ip="$1" speed="$2" latency="$3"
    echo "${ip}|${speed}|${latency}" > "$LAST_RESULT_FILE"
    log "已保存优选记录: IP=${ip}, 速度=${speed}MB/s, 延迟=${latency}ms"
}

# 比较新旧 IP，判断是否需要更新
# 返回 0 = 需要更新，返回 1 = 跳过
should_update() {
    local new_ip="$1" new_speed="$2" new_latency="$3"

    # 无历史记录，首次运行
    if [ ! -f "$LAST_RESULT_FILE" ]; then
        log "首次运行，无历史记录，直接更新"
        return 0
    fi

    local old_ip old_speed old_latency
    old_ip=$(cut -d'|' -f1 "$LAST_RESULT_FILE")
    old_speed=$(cut -d'|' -f2 "$LAST_RESULT_FILE")
    old_latency=$(cut -d'|' -f3 "$LAST_RESULT_FILE")

    log "对比: 新 IP ${new_ip} (速度: ${new_speed}MB/s, 延迟: ${new_latency}ms) vs 上次 IP ${old_ip} (速度: ${old_speed}MB/s, 延迟: ${old_latency}ms)"

    # IP 相同
    if [ "$new_ip" = "$old_ip" ]; then
        log "优选 IP 未变化 (${new_ip})，跳过更新"
        return 1
    fi

    # 新 IP 速度超过上次 10% 则更新
    local speed_better
    speed_better=$(echo "$new_speed $old_speed" | awk '{if ($1 > $2 * 1.1) print "1"; else print "0"}')
    if [ "$speed_better" = "1" ]; then
        log "新 IP 速度提升超过 10% (${new_speed} vs ${old_speed})，更新"
        return 0
    fi

    # 速度相当（差距在 10% 以内），比较延迟
    local speed_similar
    speed_similar=$(echo "$new_speed $old_speed" | awk '{if ($1 >= $2 * 0.9) print "1"; else print "0"}')
    if [ "$speed_similar" = "1" ]; then
        local latency_better
        latency_better=$(echo "$new_latency $old_latency" | awk '{if ($1 < $2) print "1"; else print "0"}')
        if [ "$latency_better" = "1" ]; then
            log "速度相当但新 IP 延迟更低 (${new_latency}ms < ${old_latency}ms)，更新"
            return 0
        fi
    fi

    # 新 IP 速度明显更差
    local speed_worse
    speed_worse=$(echo "$new_speed $old_speed" | awk '{if ($1 < $2 * 0.9) print "1"; else print "0"}')
    if [ "$speed_worse" = "1" ]; then
        log "新 IP 速度更差 (${new_speed} vs ${old_speed})，保持当前 IP ${old_ip}"
        return 1
    fi

    log "新旧 IP 质量相当，保持当前 IP ${old_ip} 不变"
    return 1
}

update_hosts() {
    local ip="$1"
    local count=0
    local backup="/etc/hosts.bak.cdn-speedtest"

    log "备份 /etc/hosts → ${backup}"
    cp /etc/hosts "$backup"

    log "写入优选 IP 到 /etc/hosts..."
    for domain in $CDN_DOMAINS; do
        [ -z "$domain" ] && continue
        sed -i "/ ${domain}$/d" /etc/hosts
        echo "${ip} ${domain}" >> /etc/hosts
        count=$((count + 1))
    done

    log "已更新 ${count} 个域名 → ${ip}"
}

restart_dns() {
    if [ -f /etc/init.d/dnsmasq ]; then
        log "重启 dnsmasq 使 hosts 生效..."
        /etc/init.d/dnsmasq restart > /dev/null 2>&1
        log "dnsmasq 已重启"
    else
        log "WARN: 未检测到 dnsmasq，hosts 可能不会立即生效"
    fi
}

show_status() {
    echo "=============================="
    echo " CDN 优选 IP 当前状态"
    echo "=============================="
    echo ""

    local first_domain
    first_domain=$(echo "$CDN_DOMAINS" | grep -m1 '\S')

    local current_ip
    current_ip=$(grep " ${first_domain}$" /etc/hosts 2>/dev/null | awk '{print $1}' | tail -1)

    if [ -n "$current_ip" ]; then
        echo "当前优选 IP: ${current_ip}"
        echo ""
        echo "已覆盖域名:"
        echo "$CDN_DOMAINS" | while read -r d; do
            [ -z "$d" ] && continue
            echo "  - ${d}"
        done
    else
        echo "未配置优选 IP（域名走 DNS 正常解析）"
    fi

    echo ""
    if [ -f "${INSTALL_DIR}/result.csv" ]; then
        echo "上次测速 Top 5:"
        echo "  IP | 延迟(ms) | 速度(MB/s)"
        sed -n '2,6p' "${INSTALL_DIR}/result.csv" | while IFS=',' read -r ip _ _ _ latency speed; do
            echo "  ${ip} | ${latency} | ${speed}"
        done
    fi
}

clean_hosts() {
    for domain in $CDN_DOMAINS; do
        [ -z "$domain" ] && continue
        sed -i "/ ${domain}$/d" /etc/hosts
    done
    restart_dns
    log "已清除所有 CDN 优选记录，恢复 DNS 正常解析"
}

usage() {
    cat <<'EOF'
用法: CDNDOMAIN=example.com cdn-speedtest [命令]

环境变量:
  CDNDOMAIN           CDN 根域名（必须）
  CDN_SUBDOMAINS_FILE 子域名前缀配置文件路径（默认: /etc/subdomains.txt）

配置文件格式（每行一个前缀，# 开头为注释）:
  jp
  big
  cn2
  # 这是注释

命令:
  run       执行测速并更新 /etc/hosts (默认)
  install   仅安装 CloudflareST
  status    查看当前优选状态
  clean     清除优选记录，恢复 DNS 正常解析
  help      显示此帮助

示例:
  CDNDOMAIN=example.com cdn-speedtest          # 执行测速
  CDNDOMAIN=example.com cdn-speedtest status   # 查看状态
  CDNDOMAIN=example.com cdn-speedtest clean    # 清除优选
EOF
}

# ======================== 主流程 ========================

main() {
    local cmd="${1:-run}"

    case "$cmd" in
        run)
            build_cdn_domains
            local arch
            arch=$(detect_arch)
            install_cloudflarest "$arch"
            local best_ip
            best_ip=$(run_speedtest) || exit 1
            best_ip=$(echo "$best_ip" | tail -1)
            local best_speed best_latency
            best_speed=$(sed -n '2p' "${INSTALL_DIR}/result.csv" | cut -d',' -f6)
            best_latency=$(sed -n '2p' "${INSTALL_DIR}/result.csv" | cut -d',' -f5)
            if should_update "$best_ip" "$best_speed" "$best_latency"; then
                update_hosts "$best_ip"
                restart_dns
                save_result "$best_ip" "$best_speed" "$best_latency"
            fi
            show_status
            ;;
        install)
            local arch
            arch=$(detect_arch)
            install_cloudflarest "$arch"
            ;;
        status)
            build_cdn_domains
            show_status
            ;;
        clean)
            build_cdn_domains
            clean_hosts
            restart_dns
            ;;
        help|--help|-h)
            usage
            ;;
        *)
            echo "未知命令: $cmd"
            usage
            exit 1
            ;;
    esac
}

main "$@"
CDNEOF
}

build_cdn_env() {
    _cdn_env="CDNDOMAIN=$CDN_DOMAIN"
    for _v in SPEED_TEST_THREADS SPEED_TEST_TIME SPEED_TEST_COUNT SPEED_TEST_LATENCY_MAX SPEED_TEST_MIN_SPEED; do
        eval "_val=\$$_v"
        [ -n "$_val" ] && _cdn_env="$_cdn_env $_v=$_val"
    done
}

install_cdn_speedtest() {
    # CDN IP 优选：CDN_DOMAIN 非空才启用。内嵌脚本写出 /usr/bin/cdn-speedtest +
    # /etc/subdomains.txt + 每日 cron + 预装 CloudflareST，全部幂等。
    if [ -z "$CDN_DOMAIN" ]; then
        log "未设 CDN_DOMAIN，跳过 CDN IP 优选安装"
        return 0
    fi
    _bin=/usr/bin/cdn-speedtest
    write_cdn_speedtest /tmp/cdn-speedtest.new
    if [ -f "$_bin" ] && cmp -s /tmp/cdn-speedtest.new "$_bin"; then
        log "cdn-speedtest 已是当前版本，跳过"
    else
        backup_file "$_bin"
        cp /tmp/cdn-speedtest.new "$_bin"
        chmod +x "$_bin"
        log "已安装 $_bin"
    fi
    rm -f /tmp/cdn-speedtest.new
    # 清理历史手动安装的独立脚本（已并入本脚本内嵌版本）
    if [ -f /usr/bin/cdn-speedtest.sh ]; then
        rm -f /usr/bin/cdn-speedtest.sh
        log "已移除旧版 /usr/bin/cdn-speedtest.sh（由内嵌版本取代）"
    fi

    # /etc/subdomains.txt：CDN_SUBDOMAINS 逗号分隔前缀 → 每行一个
    if [ -n "$CDN_SUBDOMAINS" ]; then
        _sd=/tmp/subdomains.new
        {
            printf '# 每行一个子域名前缀（openwrt-init.sh 生成），与 CDN_DOMAIN 拼接成完整域名\n'
            printf '%s' "$CDN_SUBDOMAINS" | tr ',' '\n'
            printf '\n'
        } > "$_sd"
        if [ -f /etc/subdomains.txt ] && cmp -s "$_sd" /etc/subdomains.txt; then
            log "/etc/subdomains.txt 无变化，跳过"
        else
            backup_file /etc/subdomains.txt
            cp "$_sd" /etc/subdomains.txt
            log "已写入 /etc/subdomains.txt（前缀: $CDN_SUBDOMAINS）"
        fi
        rm -f "$_sd"
    elif [ ! -s /etc/subdomains.txt ]; then
        warn "未设 CDN_SUBDOMAINS 且 /etc/subdomains.txt 不存在 —— cdn-speedtest run 将无域名可用"
    fi

    # cron：先清旧路径行（cdn-speedtest.sh），再 grep 守卫注入/更新
    _crontab=/etc/crontabs/root
    touch "$_crontab"
    if grep -q "cdn-speedtest\.sh" "$_crontab"; then
        sed -i '/cdn-speedtest\.sh/d' "$_crontab"
        log "已清理旧版 cdn-speedtest.sh cron 行"
    fi
    build_cdn_env
    _line="${CDN_CRON_SCHEDULE:-0 4 * * *} $_cdn_env /usr/bin/cdn-speedtest run  # optimize CDN IP"
    if grep -qxF "$_line" "$_crontab"; then
        log "CDN 优选 cron 已存在，跳过"
    else
        sed -i '\#/usr/bin/cdn-speedtest run#d' "$_crontab"
        printf '%s\n' "$_line" >> "$_crontab"
        /etc/init.d/cron enable 2>/dev/null
        /etc/init.d/cron restart 2>/dev/null
        log "已配置 CDN 优选 cron: $_line"
    fi

    # 预装 CloudflareST（幂等：已装直接返回；失败不阻断，首次 run 会重试）
    "$_bin" install || warn "CloudflareST 预装失败（首次 cdn-speedtest run 时会重试）"

    # 首跑只处理“从无到有”；新鲜度交给每日 cron；last_best.txt 门禁保 init 重跑幂等。
    if [ -f /etc/CloudflareST/last_best.txt ]; then
        log "已有优选结果（last_best.txt），跳过首跑——由每日 cron 维持"
    else
        log "首次启用：同步执行 CDN 优选首跑（约需几分钟，期间 OpenClash 暂停、测完自动恢复）"
        env $_cdn_env /usr/bin/cdn-speedtest run || \
            warn "CDN 优选首跑失败（不阻断；每日 cron 会重试，或手动: sh openwrt-init.sh cdn run）"
    fi
}

# ── 端到端自检 ────────────────────────────────────────────────────

verify_cdn() {
    # CDN 优选自检
    if [ -n "$CDN_DOMAIN" ]; then
        check "cdn-speedtest 已安装" test -x /usr/bin/cdn-speedtest
        check "CDN 优选 cron 已配置" sh -c "grep -qF '/usr/bin/cdn-speedtest run' /etc/crontabs/root"
        check "/etc/subdomains.txt 非空" test -s /etc/subdomains.txt
        check "CloudflareST 已安装" test -x /etc/CloudflareST/cfst
        check_soft "CDN 优选已生效（last_best.txt）" test -f /etc/CloudflareST/last_best.txt
    fi
}

verify() {
    log "── 自检 (mode=$CN_EXIT_MODE) ──"
    if mode_uses_tailscale; then
        check "tailscaled 监听 UDP ${TS_PORT}" sh -c "netstat -lnup 2>/dev/null | grep -q ':${TS_PORT} '"
        check "tailscale0 网卡存在" ip link show tailscale0
        check "tailscale 防火墙 zone 已配置" sh -c "uci show firewall 2>/dev/null | grep -q \"name='tailscale'\""
        check "WAN UDP GRO 已优化" sh -c "ethtool -k \$(ip -o route get 8.8.8.8 2>/dev/null | grep -oE 'dev [a-z0-9]+' | awk '{print \$2}') 2>/dev/null | grep -q 'rx-udp-gro-forwarding: on'"
        check "已通告 routes ${TS_ADVERTISE_ROUTES}" sh -c "tailscale debug prefs 2>/dev/null | grep -q '${TS_ADVERTISE_ROUTES}'"
        # LAN 网段迁移护栏：通告列表必须包含本机实际 LAN 网段——改了路由器网段
        # 却忘改 config.env 时，脚本会继续通告旧网段（tailnet 访问家内网静默失效），
        # 在此抓住。内核路由表直接给出网段基址（如 192.168.168.0/23），无需位运算；
        # 取不到（异形拓扑）则跳过不检查。
        _lan_ip=$(uci -q get network.lan.ipaddr)
        _lan_cidr=""
        [ -n "$_lan_ip" ] && _lan_cidr=$(ip -4 route show proto kernel 2>/dev/null | \
            awk -v ip="$_lan_ip" '{ for (i = 1; i < NF; i++) if ($i == "src" && $(i+1) == ip) { print $1; exit } }')
        if [ -n "$_lan_cidr" ]; then
            check "通告网段含本机 LAN 实际网段 ${_lan_cidr}（变更网段后须同步 config.env 重跑）" \
                sh -c "printf '%s' ',${TS_ADVERTISE_ROUTES},' | grep -qF ',${_lan_cidr},'"
        fi
        # 固定 IP 契约：VPS 侧 CN_EXIT_SOCKS5_HOST 指向 TS_EXPECTED_IP，漂移 = 全部
        # VPS 的 socks5 腿断（设备重置后新身份的典型症状），硬失败
        if [ -n "$TS_EXPECTED_IP" ]; then
            check "Tailscale IP 为预期固定值 ${TS_EXPECTED_IP}（VPS 侧 socks5 腿契约）" \
                sh -c "[ \"\$(tailscale ip -4 2>/dev/null)\" = '$TS_EXPECTED_IP' ]"
        fi
        if ts_has_oauth; then
            check_soft "subnet routes 已批准 (API enabledRoutes)" ts_routes_approved
        fi
        check "tailscale 已登录" sh -c "tailscale status 2>/dev/null | grep -qv 'Logged out'"
        check_soft "tailscale ping 对端 ${PEER_TS_IP}" sh -c "tailscale ping -c 1 --timeout 5s ${PEER_TS_IP} 2>/dev/null | grep -q pong"
        check_soft "防火墙 bypass 规则已注入" sh -c "test \$(nft list ruleset 2>/dev/null | grep -c tailscale-bypass) -ge 1"
        check "持久 bypass 链已加载 (nftables.d)" nft list chain inet fw4 cn_exit_ts_output
        check "tailscale 开机自启" /etc/init.d/tailscale enabled
    fi
    if mode_uses_reverse; then
        _hot="${BRIDGE_HOT:-$(awk '!/^#/&&NF{print $1}' /etc/cn-exit/nodes.list 2>/dev/null)}"
        _hot=$(printf '%s' "$_hot" | tr ',' ' ')
        check "cn-bridge 工具已安装" test -x /usr/bin/cn-bridge
        check "节点清单已生成" test -s /etc/cn-exit/nodes.list
        check "至少一条 bridge 隧道到 VPS:443 ESTABLISHED" sh -c "netstat -tn 2>/dev/null | grep -q ':443 .*ESTABLISHED'"
        for _bn in $_hot; do
            check "热备 $_bn 服务开机自启" /etc/init.d/xray-bridge-$_bn enabled
            check "热备 $_bn client 无占位符" sh -c "! grep -q '\${' /etc/xray/client-$_bn.json 2>/dev/null"
        done
    fi
    # lan_ac_traffic enabled='1' 时 OpenClash 仅拦截 FakeIP 段流量，裸 IP 直连
    # （Telegram 原生客户端等不查 DNS 的应用）绕过代理出墙被黑洞；该开关在
    # LuCI 上易被误开，且症状隐蔽（域名通、裸 IP 不通），此处兜底拦截。
    check "OpenClash 未开启「仅代理 FakeIP」绕过 (lan_ac_traffic)" \
        sh -c "[ \"\$(uci -q get 'openclash.@lan_ac_traffic[0].enabled' 2>/dev/null)\" != '1' ]"
    # OpenClash 配置纳管自检（渲染产物由 setup_openclash_config 留在 /tmp 供比对）。
    # 漂移比对必须用软检查：OpenClash 启动头 ~10s 会把 redirect_dns/cachesize_dns
    # 临时翻 0、移除 dnsmasq_cachesize（DNS 接管交接期状态暂存），随后自行恢复——
    # restart 后立即硬比对必然误报（真机实测）。真漂移在 apply 步骤已被捕获重写。
    if [ "$OPENCLASH_MANAGE" = "1" ] && [ -x /etc/init.d/openclash ] && [ -f /tmp/openclash.rendered ]; then
        check_soft "OpenClash 配置与渲染产物无漂移" openclash_cfg_same /tmp/openclash.rendered /etc/config/openclash
        check "OpenClash dashboard 密码已注入（非占位符）" \
            sh -c "! grep -q '<OPENCLASH_DASHBOARD_PASSWORD>' /etc/config/openclash"
    fi
    verify_cdn

    printf '\n[cn-exit] 自检结果: %d 通过 / %d 失败\n' "$ok" "$bad"
    if [ "$bad" -gt 0 ]; then
        warn "存在失败项 —— 若刚做完 tailscale up 授权，链路可能需 1-2 分钟打洞，稍后重跑 verify"
        return 1
    fi
    log "全部通过 ✅"
}

# ── 主流程 ────────────────────────────────────────────────────────

main_cdn() {
    _sub="${1:-}"
    load_config
    [ -n "$CDN_DOMAIN" ] || die "cdn 子命令需要 CDN_DOMAIN（config.env 或内联环境变量提供）"
    case "$_sub" in
        '')
            install_cdn_speedtest
            verify_cdn
            printf '\n[cn-exit] CDN 自检结果: %d 通过 / %d 失败\n' "$ok" "$bad"
            [ "$bad" -eq 0 ] || exit 1 ;;
        run|status|clean|help)
            [ -x /usr/bin/cdn-speedtest ] || die "cdn-speedtest 未安装，先跑: sh $0 cdn"
            build_cdn_env
            exec env $_cdn_env /usr/bin/cdn-speedtest "$_sub" ;;
        *) die "未知 cdn 子命令: $_sub（支持 run|status|clean|help）" ;;
    esac
}

main() {
    trap 'warn "已中断"; exit 130' INT TERM
    log "=== sb-xray OpenWrt 一键初始化 ==="
    load_config
    validate_config
    detect_arch
    generate_nodes_list          # 所有模式：清单供解耦遍历 + cn-bridge 拨号
    if mode_uses_tailscale; then
        ensure_tun
        install_tailscale
        setup_tun_network
        setup_udp_gro
        setup_tailscale
        install_keepalive_cron
        setup_tailscale_firewall_bypass
        setup_tailscale_persistent_bypass
        setup_socks_skip_auth
        setup_socks5_force_direct   # socks5 入站回国流量强制 direct，对齐 r-tunnel
    fi
    # OpenClash 配置纳管：模板渲染 + 幂等应用（自身按 OPENCLASH_MANAGE 与 OpenClash
    # 存在性决定跑不跑）；放在解耦/重排前，统一由解耦末尾的 restart 生效
    setup_openclash_config
    # 解耦对两套方案都适用（自身按是否存在 OpenClash 决定跑不跑）
    setup_openclash_decouple
    # GLOBAL 组重排同样对两套方案适用（自身按 overwrite 钩子是否存在决定跑不跑）
    setup_global_reorder
    if mode_uses_reverse; then
        install_xray_bridge
    fi
    setup_monitor_cron
    # CDN IP 优选（自身按 CDN_DOMAIN 非空决定跑不跑）
    install_cdn_speedtest
    if verify; then
        log "=== 完成 ==="
    else
        warn "=== 完成：自检存在硬失败，请按上面 [FAIL] 排查（时序软项见 [warn]，可稍后重跑 verify）==="
        exit 1
    fi
}

case "${1:-}" in
    -h|--help) usage; exit 0 ;;
    cdn) shift; main_cdn "$@" ;;
    '') main ;;
    *) die "未知参数: $1（-h|--help 查看用法）" ;;
esac
