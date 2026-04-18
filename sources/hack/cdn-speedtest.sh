#!/bin/sh
# cdn-speedtest.sh - Cloudflare CDN IP 优选脚本
# 适用于 OpenWrt (amd64/arm64)，自动测速并将最优 IP 写入 /etc/hosts

set -eu

# ======================== 配置区 ========================

# CDN 根域名（从环境变量读取）
CDNDOMAIN="${CDNDOMAIN:-}"

# 子域名前缀配置文件（一行一个前缀）
CDN_SUBDOMAINS_FILE="${CDN_SUBDOMAINS_FILE:-/etc/subdomains.txt}"

# CloudflareST 参数
SPEED_TEST_THREADS=500        # 延迟测速线程数
SPEED_TEST_TIME=4             # 下载测速时间(秒)
SPEED_TEST_COUNT=5            # 下载测速数量
SPEED_TEST_LATENCY_MAX=200   # 延迟上限(ms)
SPEED_TEST_MIN_SPEED=5        # 最低下载速度(MB/s)，低于此值视为失败

# 安装��录
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

    tar -xzf "${INSTALL_DIR}/${tarball}" -C "$INSTALL_DIR"
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
用法: CDNDOMAIN=example.com cdn-speedtest.sh [命令]

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
  CDNDOMAIN=example.com ./cdn-speedtest.sh          # 执行测速
  CDNDOMAIN=example.com ./cdn-speedtest.sh status    # 查看状态
  CDNDOMAIN=example.com ./cdn-speedtest.sh clean     # 清除优选
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
