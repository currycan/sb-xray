#!/bin/sh
# sb-xray OpenWrt 回国出口（CN exit）客户端配置脚本
#
# 把回国代理客户端配置固化成幂等脚本，按 CN_EXIT_MODE 选择方案（与服务端一致）：
#   socks5   仅装 Tailscale（kernel TUN + subnet router + exit node + 防火墙 zone/
#            转发 + WAN UDP GRO），作为 VPS 经 OpenClash SOCKS5 回国的落地。
#   reverse  仅装 xray reverse bridge 落地机（主动拨向 VPS 建反向隧道）。
#   balance  两者都装（VPS 侧 leastPing 主备故障转移）。
# 各模式都会在检测到 OpenClash 时做解耦（VPS 域名 DIRECT + fake-ip 过滤）。
#
# 前置：fw4/nftables；能访问公网；socks5/balance 模式还需 OpenClash 已安装运行。
# 用法：cp config.env.example config.env && vi config.env && sh cn-exit-setup.sh
#
# 兼容 BusyBox ash / POSIX sh —— 不用 bashism（无 [[ ]] / 数组 / set -e / echo -e）。

# ── 公共函数 ──────────────────────────────────────────────────────

log()  { printf '[install] %s\n' "$*"; }
warn() { printf '[install] WARN: %s\n' "$*" >&2; }
die()  { printf '[install] ERROR: %s\n' "$*" >&2; exit 1; }

ok=0
bad=0
check() {
    # check "<描述>" <命令...>；命令成功计 ok，失败计 bad
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
    # （内联传参，如 CN_EXIT_MODE=reverse VPS_DOMAIN=.. sh cn-exit-setup.sh）。
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
    TS_ADVERTISE_ROUTES="${TS_ADVERTISE_ROUTES:-172.18.18.0/23}"
    RELOAD_OPENCLASH="${RELOAD_OPENCLASH:-0}"
    DOWNLOAD_RETRIES="${DOWNLOAD_RETRIES:-3}"
    case "$CN_EXIT_MODE" in
        socks5|reverse|balance) : ;;
        *) die "CN_EXIT_MODE 非法: $CN_EXIT_MODE（应为 socks5|reverse|balance）" ;;
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
    # 必填项按模式裁剪：socks5/balance 需 Tailscale 三项；reverse/balance 需 xray 版本
    _req=""
    mode_uses_tailscale && _req="$_req PEER_TS_IP TS_HOSTNAME TS_VERSION"
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
    [ -z "$_missing" ] || die "缺少必填项 (CN_EXIT_MODE=$CN_EXIT_MODE):$_missing（用 config.env 或内联环境变量提供）"

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
            die "不支持的架构: $_m（可用 config 的 ARCH_OVERRIDE=arm64|amd64 覆盖）" ;;
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
# Tailscale UDP GRO 优化：wan ifup 时对出口网卡重应用（sb-xray cn-exit-setup.sh 固化）
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
    log "运行 tailscale up —— 若打印登录 URL，请在浏览器打开授权（仅首次需要）"
    tailscale up --reset \
        --timeout=120s \
        --accept-dns=false \
        --advertise-routes="$TS_ADVERTISE_ROUTES" \
        --advertise-exit-node \
        --hostname="$TS_HOSTNAME" || \
        warn "tailscale up 返回非零（可能已在线 / 需手动授权后重跑本脚本）"
    log "提示：subnet routes(${TS_ADVERTISE_ROUTES}) 与 exit node 需到 Tailscale 管理后台批准"
    log "      https://login.tailscale.com/admin/machines -> ${TS_HOSTNAME} -> Edit route settings"
    sleep 2
    if netstat -lnup 2>/dev/null | grep -q ":${TS_PORT} "; then
        log "tailscaled 已监听 UDP ${TS_PORT}"
    else
        warn "tailscaled 未监听 ${TS_PORT}，请检查 logread | grep tailscaled"
    fi
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
# Tailscale 直连保活（cn-exit-setup.sh 生成）：ping 热备 peers 保住 openwrt 41641
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

# 放行 Tailscale CGNAT 段(100.64.0.0/10)免 SOCKS/HTTP 入站认证（sb-xray cn-exit-setup.sh 注入）。
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
        log "restart OpenClash 使解耦规则 + skip-auth + force-direct 生效..."
        /etc/init.d/openclash restart 2>/dev/null
    else
        warn "解耦/skip-auth/force-direct 需 OpenClash restart 才生效（reload 不触发 overwrite 钩子）—— 请手动 /etc/init.d/openclash restart，或设 RELOAD_OPENCLASH=1 重跑"
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
        printf '# sb-xray reverse bridge 节点清单（cn-exit-setup.sh 生成）\n'
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
    log "已生成节点清单 $_nl（$_cnt 个节点）"
}

install_cn_bridge() {
    _src="$(dirname "$0")/cn-bridge"
    if [ -f "$_src" ]; then
        cp "$_src" /usr/bin/cn-bridge
    else
        download_verify \
            "https://raw.githubusercontent.com/currycan/sb-xray/main/openwrt/cn-bridge" \
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
            "https://raw.githubusercontent.com/currycan/sb-xray/main/openwrt/cn-bridge-monitor" \
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

# ── 端到端自检 ────────────────────────────────────────────────────

verify() {
    log "── 自检 (mode=$CN_EXIT_MODE) ──"
    if mode_uses_tailscale; then
        check "tailscaled 监听 UDP ${TS_PORT}" sh -c "netstat -lnup 2>/dev/null | grep -q ':${TS_PORT} '"
        check "tailscale0 网卡存在" ip link show tailscale0
        check "tailscale 防火墙 zone 已配置" sh -c "uci show firewall 2>/dev/null | grep -q \"name='tailscale'\""
        check "WAN UDP GRO 已优化" sh -c "ethtool -k \$(ip -o route get 8.8.8.8 2>/dev/null | grep -oE 'dev [a-z0-9]+' | awk '{print \$2}') 2>/dev/null | grep -q 'rx-udp-gro-forwarding: on'"
        check "已通告 routes ${TS_ADVERTISE_ROUTES}" sh -c "tailscale debug prefs 2>/dev/null | grep -q '${TS_ADVERTISE_ROUTES}'"
        check "tailscale 已登录" sh -c "tailscale status 2>/dev/null | grep -qv 'Logged out'"
        check "tailscale ping 对端 ${PEER_TS_IP}" sh -c "tailscale ping -c 1 --timeout 5s ${PEER_TS_IP} 2>/dev/null | grep -q pong"
        check "防火墙 bypass 规则已注入" sh -c "test \$(nft list ruleset 2>/dev/null | grep -c tailscale-bypass) -ge 1"
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

    printf '\n[cn-exit] 自检结果: %d 通过 / %d 失败\n' "$ok" "$bad"
    if [ "$bad" -gt 0 ]; then
        warn "存在失败项 —— 若刚做完 tailscale up 授权，链路可能需 1-2 分钟打洞，稍后重跑 verify"
        return 1
    fi
    log "全部通过 ✅"
}

# ── 主流程 ────────────────────────────────────────────────────────

main() {
    trap 'warn "已中断"; exit 130' INT TERM
    log "=== sb-xray OpenWrt 回国出口客户端配置 ==="
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
        setup_socks_skip_auth
        setup_socks5_force_direct   # socks5 入站回国流量强制 direct，对齐 r-tunnel
    fi
    # 解耦对两套方案都适用（自身按是否存在 OpenClash 决定跑不跑）
    setup_openclash_decouple
    if mode_uses_reverse; then
        install_xray_bridge
    fi
    setup_monitor_cron
    verify
    log "=== 完成 ==="
}

main "$@"
