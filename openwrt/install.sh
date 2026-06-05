#!/bin/sh
# sb-xray OpenWrt 客户端一键安装脚本
#
# 把回国代理客户端的全部配置固化成幂等脚本：
#   1. 安装 + 配置 Tailscale（固定 UDP 端口，kernel TUN 模式，
#      subnet router + exit node + 防火墙 zone/转发 + WAN UDP GRO 优化）
#   2. OpenClash 防火墙放行 Tailscale（重启自动重跑的原生钩子）
#   3. reverse bridge 与 OpenClash 解耦（DIRECT 规则 + fake-ip 过滤）
#   4. 安装 + 配置 xray reverse bridge 落地机
#   5. 端到端自检
#
# 前置：OpenClash 已安装并运行；fw4/nftables；能访问公网。
# 用法：cp config.env.example config.env && vi config.env && sh install.sh
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
    [ -f "$CONFIG" ] || die "找不到 $CONFIG —— 请先 cp config.env.example config.env 并填值"
    # shellcheck disable=SC1090
    . "$CONFIG"
    # 默认值
    TS_PORT="${TS_PORT:-41641}"
    TS_ADVERTISE_ROUTES="${TS_ADVERTISE_ROUTES:-172.18.18.0/23}"
    RELOAD_OPENCLASH="${RELOAD_OPENCLASH:-0}"
    DOWNLOAD_RETRIES="${DOWNLOAD_RETRIES:-3}"
    log "已加载配置: $CONFIG"
}

validate_config() {
    _missing=""
    for _v in VPS_DOMAIN SUBSCRIBE_TOKEN PEER_TS_IP TS_HOSTNAME TS_VERSION XRAY_VERSION; do
        eval "_val=\$$_v"
        [ -n "$_val" ] || _missing="$_missing $_v"
    done
    [ -z "$_missing" ] || die "config.env 缺少必填项:$_missing"

    # 轻量格式校验（仅 warn，不阻断）
    case "$VPS_DOMAIN" in
        http://*|https://*) die "VPS_DOMAIN 不要带 http(s):// 前缀，只填域名" ;;
    esac
    case "$PEER_TS_IP" in
        100.0.0.0) die "PEER_TS_IP 仍是示例占位符 100.0.0.0 —— 请填 VPS 的 tailscale ip -4 输出" ;;
        100.*) : ;;
        *) warn "PEER_TS_IP=$PEER_TS_IP 不像 Tailscale IP（应为 100.x 网段）" ;;
    esac
    # XRAY_VERSION 归一化：去掉可能的 v 前缀
    XRAY_VERSION=$(printf '%s' "$XRAY_VERSION" | sed 's/^v//')

    # 环境前置
    command -v nft >/dev/null 2>&1 || die "未找到 nft，需要 fw4/nftables 的 OpenWrt"
    command -v uci >/dev/null 2>&1 || die "未找到 uci，这不是 OpenWrt？"
    [ -x /etc/init.d/openclash ] || die "未找到 /etc/init.d/openclash —— 请先安装 OpenClash"
    command -v wget >/dev/null 2>&1 || die "未找到 wget"
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
# Tailscale UDP GRO 优化：wan ifup 时对出口网卡重应用（sb-xray install.sh 固化）
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
    _line="* * * * * /usr/sbin/tailscale ping -c 1 --timeout 5s ${PEER_TS_IP} >/dev/null 2>&1"
    _crontab=/etc/crontabs/root
    touch "$_crontab"
    if grep -qF "tailscale ping -c 1 --timeout 5s ${PEER_TS_IP}" "$_crontab"; then
        log "keepalive cron 已存在，跳过"
    else
        printf '%s\n' "$_line" >> "$_crontab"
        /etc/init.d/cron enable 2>/dev/null
        /etc/init.d/cron restart 2>/dev/null
        log "已添加 keepalive cron（每分钟 ping $PEER_TS_IP）"
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

# 放行 Tailscale CGNAT 段(100.64.0.0/10)免 SOCKS/HTTP 入站认证（sb-xray install.sh 注入）。
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
    _rules=/etc/openclash/custom/openclash_custom_rules.list
    _filter=/etc/openclash/custom/openclash_custom_fake_filter.list

    # DIRECT 规则：让 OpenClash 把 VPS 域名直连，不进代理
    touch "$_rules"
    if grep -qF "DOMAIN,${VPS_DOMAIN},DIRECT" "$_rules"; then
        log "DIRECT 规则已存在，跳过"
    else
        backup_file "$_rules"
        if grep -q '^rules:' "$_rules"; then
            sed -i "/^rules:/a - DOMAIN,${VPS_DOMAIN},DIRECT" "$_rules"
        else
            # 没有 rules: 头则补一个再插
            printf 'rules:\n- DOMAIN,%s,DIRECT\n' "$VPS_DOMAIN" >> "$_rules"
        fi
        log "已加入 DIRECT 规则: $VPS_DOMAIN"
    fi

    # fake-ip 过滤：让 VPS 域名解析真实 IP，不发 fake-ip（bridge 才能直连）
    touch "$_filter"
    if grep -qxF "$VPS_DOMAIN" "$_filter"; then
        log "fake-ip 过滤已含 $VPS_DOMAIN，跳过"
    else
        backup_file "$_filter"
        printf '%s\n' "$VPS_DOMAIN" >> "$_filter"
        log "已加入 fake-ip 过滤: $VPS_DOMAIN"
    fi

    if [ "$RELOAD_OPENCLASH" = "1" ]; then
        # 必须 restart 而非 reload：skip-auth 的 overwrite 钩子只在 restart 的
        # 配置生成流程里跑，reload 仅热载规则、不触发 overwrite。
        log "restart OpenClash 使解耦规则 + skip-auth 注入生效..."
        /etc/init.d/openclash restart 2>/dev/null
    else
        warn "解耦规则与 skip-auth 注入需 OpenClash restart 才生效（reload 不触发 overwrite 钩子）—— 请手动 /etc/init.d/openclash restart，或设 RELOAD_OPENCLASH=1 重跑"
    fi
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
    backup_file /etc/xray/client.json
    download_verify \
        "https://${VPS_DOMAIN}/sb-xray/reverse_bridge_client.json?token=${SUBSCRIBE_TOKEN}" \
        /etc/xray/client.json json

    # 校验：无占位符残留（确认服务端已渲染）
    if grep -q '${' /etc/xray/client.json; then
        die "client.json 仍含占位符 \${...}，服务端可能未开 ENABLE_REVERSE 或 token 错误"
    fi
    # JSON 合法性
    xray run -test -config /etc/xray/client.json >/dev/null 2>&1 || \
        warn "xray -test 校验未通过，请人工检查 /etc/xray/client.json"
    # 保险修正：routing inboundTag 必须是 r-tunnel（防御历史 bug，服务端已修）
    if grep -q '"reverse-bridge"' /etc/xray/client.json && \
       grep -q 'inboundTag' /etc/xray/client.json; then
        if ! grep -q '"r-tunnel"' /etc/xray/client.json; then
            sed -i 's/"inboundTag": \["reverse-bridge"\]/"inboundTag": ["r-tunnel"]/' /etc/xray/client.json
            warn "已就地修正 client.json 的 inboundTag -> r-tunnel"
        fi
    fi

    backup_file /etc/init.d/xray-bridge
    cat > /etc/init.d/xray-bridge <<'EOF'
#!/bin/sh /etc/rc.common
START=99
USE_PROCD=1
start_service() {
    procd_open_instance
    procd_set_param command /usr/bin/xray run -config /etc/xray/client.json
    procd_set_param respawn
    procd_set_param stdout 1
    procd_set_param stderr 1
    procd_close_instance
}
EOF
    chmod +x /etc/init.d/xray-bridge
    /etc/init.d/xray-bridge enable 2>/dev/null
    /etc/init.d/xray-bridge restart 2>/dev/null
    log "已写入 /etc/init.d/xray-bridge 并启动"
}

# ── 端到端自检 ────────────────────────────────────────────────────

verify() {
    log "── 自检 ──"
    check "tailscaled 监听 UDP ${TS_PORT}" sh -c "netstat -lnup 2>/dev/null | grep -q ':${TS_PORT} '"
    check "tailscale0 网卡存在" ip link show tailscale0
    check "tailscale 防火墙 zone 已配置" sh -c "uci show firewall 2>/dev/null | grep -q \"name='tailscale'\""
    check "WAN UDP GRO 已优化" sh -c "ethtool -k \$(ip -o route get 8.8.8.8 2>/dev/null | grep -oE 'dev [a-z0-9]+' | awk '{print \$2}') 2>/dev/null | grep -q 'rx-udp-gro-forwarding: on'"
    check "已通告 routes ${TS_ADVERTISE_ROUTES}" sh -c "tailscale debug prefs 2>/dev/null | grep -q '${TS_ADVERTISE_ROUTES}'"
    check "tailscale 已登录" sh -c "tailscale status 2>/dev/null | grep -qv 'Logged out'"
    check "tailscale ping 对端 ${PEER_TS_IP}" sh -c "tailscale ping -c 1 --timeout 5s ${PEER_TS_IP} 2>/dev/null | grep -q pong"
    check "防火墙 bypass 规则已注入" sh -c "test \$(nft list ruleset 2>/dev/null | grep -c tailscale-bypass) -ge 1"
    check "bridge 隧道到 VPS:443 ESTABLISHED" sh -c "netstat -tn 2>/dev/null | grep -q ':443 .*ESTABLISHED'"
    check "client.json 无占位符残留" sh -c "! grep -q '\${' /etc/xray/client.json"
    check "client.json routing 为 r-tunnel" sh -c "grep -q 'r-tunnel' /etc/xray/client.json"
    check "tailscale 开机自启" /etc/init.d/tailscale enabled
    check "xray-bridge 开机自启" /etc/init.d/xray-bridge enabled

    printf '\n[install] 自检结果: %d 通过 / %d 失败\n' "$ok" "$bad"
    if [ "$bad" -gt 0 ]; then
        warn "存在失败项 —— 若刚做完 tailscale up 授权，链路可能需 1-2 分钟打洞，稍后重跑 verify"
        return 1
    fi
    log "全部通过 ✅"
}

# ── 主流程 ────────────────────────────────────────────────────────

main() {
    trap 'warn "已中断"; exit 130' INT TERM
    log "=== sb-xray OpenWrt 客户端安装 ==="
    load_config
    validate_config
    detect_arch
    ensure_tun
    install_tailscale
    setup_tun_network
    setup_udp_gro
    setup_tailscale
    install_keepalive_cron
    setup_tailscale_firewall_bypass
    setup_socks_skip_auth
    setup_openclash_decouple
    install_xray_bridge
    verify
    log "=== 完成 ==="
}

main "$@"
