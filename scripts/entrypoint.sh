#!/usr/bin/env bash
# ==============================================================================
# entrypoint.sh — SB-Xray 容器启动入口
#
# 函数声明顺序规则:
#   1. 基础工具（无业务状态依赖）优先声明
#   2. 业务函数按运行依赖逻辑从浅到深排列
#   3. 主流程各阶段严格对应 daemon.ini 服务启动顺序
#
# 段落索引:
#   §1  颜色与常量
#   §2  日志工具
#   §3  HTTP 工具
#   §4  随机值生成
#   §5  模板渲染工具
#   §6  环境变量持久化
#   §7  网络环境探测
#   §8  选路辅助
#   §9  测速
#   §10 ISP 节点处理
#   §11 流媒体/AI 可达性检测
#   §12 证书管理
#   §13 配置渲染
#   §14 远端密钥解密
#   §15 主流程各阶段
#   §16 主入口
# ==============================================================================

set -eou pipefail

# ==============================================================================
# §1  颜色与常量
# ==============================================================================
if [ -t 1 ]; then
    RED=$'\033[1;31m' GREEN=$'\033[1;32m' YELLOW=$'\033[1;33m' CYAN=$'\033[1;36m' NC=$'\033[0m'
    BOLD=$'\033[1m'   RESET_BOLD=$'\033[22m'
else
    RED='' GREEN='' YELLOW='' CYAN='' NC=''
    BOLD='' RESET_BOLD=''
fi

# 持久化文件路径（支持测试覆盖：先检查环境变量，未设则用默认值）
ENV_FILE="${ENV_FILE:-/.env/sb-xray}"
SECRET_FILE="${SECRET_FILE:-/.env/secret}"
STATUS_FILE="${STATUS_FILE:-/.env/status}"

# 测速目标 URL
_SPEED_TEST_URL="https://speed.cloudflare.com/__down?bytes=25000000"
# 每节点采样次数（urltest/balancer 运行时做延迟选优，启动测速仅影响初始排序）
SPEED_SAMPLES=2

# ==============================================================================
# §2  日志工具
# ==============================================================================

# 跨平台 sed -i（macOS BSD sed 需要额外空字符串参数，GNU sed 不需要）
if [[ "$(uname)" == "Darwin" ]]; then
    _sed_i() { sed -i '' "$@"; }
else
    _sed_i() { sed -i "$@"; }
fi

# 输出带时间戳和级别的日志到 stderr
# 用法: log <INFO|WARN|ERROR|DEBUG> <message...>
log() {
    local level=$1; shift
    local color
    case $level in
        INFO)  color="$GREEN"  ;;
        WARN)  color="$YELLOW" ;;
        ERROR) color="$RED"    ;;
        DEBUG) color="$CYAN"   ;;
        *)     color="$NC"     ;;
    esac
    echo -e "${color}[$(date +"%Y-%m-%d %H:%M:%S")] [${level}] $*${NC}" >&2
}

# 关键变量汇总仪表盘
# 用法: log_summary_box VAR1 VAR2 ...
log_summary_box() {
    local width=65
    local line; line=$(printf '%*s' "$width" '' | tr ' ' '=')
    echo -e "\n${CYAN}${line}"
    printf "${CYAN}║${NC}${YELLOW} %-$((width - 4))s ${CYAN}║${NC}\n" "SYSTEM STRATEGY SUMMARY"
    echo -e "${CYAN}${line}${NC}"
    for k in "$@"; do
        local val="${!k:-N/A}"
        local pad=$(( width - 4 - ${#k} - ${#val} ))
        (( pad < 0 )) && pad=0
        printf "${CYAN}║ ${NC}${GREEN}%s${NC}: %s%*s ${CYAN}║${NC}\n" "$k" "$val" "$pad" ""
    done
    echo -e "${CYAN}${line}${NC}\n"
}

# 进度提示（行内覆写，不换行）
show_progress() { echo -ne "\r${CYAN}${BOLD}[*] $1 ...${NC}" >&2; }
end_progress()  { echo -ne "\r\033[K" >&2; }

# ==============================================================================
# §3  HTTP 工具
# ==============================================================================

# 发送 HEAD 请求，返回 HTTP 状态码；超时返回 "Timeout"
# 用法: http_probe <url> [follow_redirect=false]
# 修复: 原版使用 eval 拼接 URL，存在命令注入风险；改为数组参数
http_probe() {
    local url=$1 follow=${2:-false}
    local args=(-I -s --max-time 3 --retry 2 -A "Mozilla/5.0")
    [[ "$follow" == "true" ]] && args+=(-L)
    local res
    res=$(curl "${args[@]}" "$url" 2>/dev/null | sed -n '1p' | awk '{print $2}')
    echo "${res:-Timeout}"
}

# 跟随重定向，返回最终落地 URL（用于 Claude 检测）
# 用法: http_trace_url <url>
http_trace_url() {
    curl -sSL -I --max-time 5 --retry 2 \
        -w "%{url_effective}" -A "Mozilla/5.0" "$1" 2>/dev/null | tail -1
}

# ==============================================================================
# §4  随机值生成
# ==============================================================================

# 生成随机值
# 用法: generateRandomStr <port|uuid|password|path> [length=12]
generateRandomStr() {
    local type=$1 length=${2:-12}
    case $type in
        port)     echo $(( RANDOM % 6001 + 32000 )) ;;
        uuid)     xray uuid ;;
        # || true: 避免 tr | head -c 管道在 pipefail 模式下因 SIGPIPE 误退出
        password) LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c "$length" || true ;;
        path)     LC_ALL=C tr -dc 'a-z0-9'    < /dev/urandom | head -c "$length" || true ;;
    esac
}

# ==============================================================================
# §5  模板渲染工具
# ==============================================================================

# 将环境变量注入模板文件并写入目标路径；JSON 文件经 jq 格式化校验
# 用法: _apply_tpl <src> <dest>
_apply_tpl() {
    local src=$1 dest=$2
    log DEBUG "[模板] ${src} → ${dest}"
    mkdir -p "$(dirname "$dest")"
    local tmp; tmp=$(mktemp)
    local env_list; env_list=$(env | grep -v '^_' | cut -d= -f1 | sed 's/^/${/;s/$/}/' | xargs)
    envsubst "$env_list" < "$src" > "$tmp"
    if [[ "$dest" == *.json ]]; then
        if jq . "$tmp" > "$dest" 2>/dev/null; then
            rm -f "$tmp"; return
        fi
        log ERROR "[模板] JSON 格式错误: ${dest}，直接覆写"
    fi
    mv "$tmp" "$dest"
}

# ==============================================================================
# §6  环境变量持久化
# ==============================================================================

# 检测必要环境变量是否全部存在，缺失则报错退出
# 用法: checkRequiredEnv VAR1 VAR2 ...
checkRequiredEnv() {
    local missing=()
    for var in "$@"; do
        [ -z "${!var:-}" ] && missing+=("$var")
    done
    if (( ${#missing[@]} > 0 )); then
        log ERROR "缺少必要环境变量: ${missing[*]}"
        exit 1
    fi
}

# 确保变量已设置，遵循优先级：docker-compose > auto-gen (ENV_FILE) > Dockerfile 默认值
#
# 分支逻辑（见 Bug #015）：
#   1. shell 中有值 → docker-compose 显式设置，直接返回
#   2. 已在 ENV_FILE → 从文件加载并 export 到当前 shell（Bug #015 修复）
#   3. 两者都没有   → 执行命令计算，按需写入文件
#
# 用法: ensure_var <KEY> [--no-persist] <cmd...>
ensure_var() {
    local key=$1; shift
    local persist=true

    while [[ "${1:-}" == --* ]]; do
        case "${1:-}" in
            --no-persist) persist=false; shift ;;
            *)            break ;;
        esac
    done
    local cmd="$*"

    # 分支 1: 已在当前 shell 环境中（docker-compose 显式设置）
    if [[ -n "${!key:-}" ]]; then
        log DEBUG "[${key}] 已在当前环境，跳过"
        return
    fi

    # 分支 2: 已在持久化文件中 → 加载到当前 shell（Bug #015 修复）
    if grep -q "^export ${key}=" "${ENV_FILE}" 2>/dev/null; then
        local val
        val=$(grep "^export ${key}=" "${ENV_FILE}" | tail -1 \
              | sed "s/^export ${key}='//;s/'$//")
        export "${key}=${val}"
        log DEBUG "[${key}] 从文件加载: ${val}"
        return
    fi

    # 分支 3: 执行命令计算
    log DEBUG "[${key}] 计算中: ${cmd}"
    local val
    if ! val=$($cmd); then
        log ERROR "[${key}] 计算失败: ${cmd}"
        return 1
    fi
    export "${key}=${val}"

    if [[ "$persist" == "true" ]]; then
        _sed_i "/^export ${key}=/d" "${ENV_FILE}"
        echo "export ${key}='${val}'" >> "${ENV_FILE}"
        log DEBUG "[${key}] 已持久化: ${val}"
    else
        log DEBUG "[${key}] 内存变量: ${val}"
    fi
}

# 确保密钥对已生成（两个值原子产生，同进同出）
# 用法: ensure_key_pair <name> <gen_cmd> <KEY1> <KEY2>
ensure_key_pair() {
    local name=$1 cmd=$2 key1=$3 key2=$4

    # 两个 key 均已在文件中 → 加载
    if grep -q "^export ${key1}=" "${ENV_FILE}" 2>/dev/null && \
       grep -q "^export ${key2}=" "${ENV_FILE}" 2>/dev/null; then
        local v1 v2
        v1=$(grep "^export ${key1}=" "${ENV_FILE}" | tail -1 | sed "s/^export ${key1}='//;s/'$//")
        v2=$(grep "^export ${key2}=" "${ENV_FILE}" | tail -1 | sed "s/^export ${key2}='//;s/'$//")
        export "${key1}=${v1}" "${key2}=${v2}"
        log DEBUG "[${name}] 密钥对从文件加载"
        return
    fi

    log INFO "[${name}] 生成密钥对..."
    local out; out=$($cmd)
    local v1; v1=$(echo "$out" | sed -n '1p' | awk -F': ' '{print $2}')
    local v2; v2=$(echo "$out" | sed -n '2p' | awk -F': ' '{print $2}')
    export "${key1}=${v1}" "${key2}=${v2}"
    {   _sed_i "/^export ${key1}=/d; /^export ${key2}=/d" "${ENV_FILE}"
        echo "export ${key1}='${v1}'" >> "${ENV_FILE}"
        echo "export ${key2}='${v2}'" >> "${ENV_FILE}"
    }
    log INFO "[${name}] 密钥对已生成并持久化"
}

# ==============================================================================
# §7  网络环境探测
# ==============================================================================

# 检测 IPv4/v6 可用性
# 返回: prefer_ipv4 | ipv6_only | ipv4_only
detect_ip_strategy_api() {
    local v4="" v6=""
    curl -4 -s --connect-timeout 2 https://api.ip.sb/ip >/dev/null 2>&1 && v4="yes"
    curl -6 -s --connect-timeout 2 https://api.ip.sb/ip >/dev/null 2>&1 && v6="yes"
    if   [[ "$v4" == "yes" && "$v6" == "yes" ]]; then echo "prefer_ipv4"
    elif [[ "$v6" == "yes" ]];                    then echo "ipv6_only"
    else                                               echo "ipv4_only"
    fi
}

# 通过 ipapi.is 查询本机 IP 的 ASN 类型
# 返回: isp | hosting | vpn | unknown 等
check_ip_type() {
    if [ ! -f /tmp/ipapi.json ]; then
        curl -sSL --max-time 5 --retry 2 "https://api.ipapi.is/" > /tmp/ipapi.json 2>/dev/null \
            || echo "{}" > /tmp/ipapi.json
    fi
    jq -r '.asn.type // "unknown"' /tmp/ipapi.json
}

# 从 ip111.cn 获取 GeoIP 地理信息
# 返回格式: 城市省份|IP地址
get_geo_info() {
    curl -fsSL --max-time 10 --retry 2 https://ip111.cn/ 2>/dev/null \
        | grep '这是您访问国内网站所使用的IP' -B 2 | sed -n '1p' \
        | awk -F' ' '{print $2$3"|"$1}' | tr -d '</p>'
}

# 检测 TCP_Brutal 内核模块是否已加载
# 返回: true | false
check_brutal_status() {
    [ -d "/sys/module/brutal" ] && echo "true" || echo "false"
}

# ==============================================================================
# §8  选路辅助
# ==============================================================================
# 纯逻辑函数，无网络调用，无副作用。
# 依赖: GEOIP_INFO (§7 get_geo_info), ISP_TAG (§10 apply_isp_routing_logic)
# 必须在 §11 流媒体/AI 可达性检测之前声明，因为检测函数会调用这些辅助函数。

# 判断当前 GeoIP 是否处于受限地区（中国大陆/香港/澳门/俄罗斯）
# 返回值: 0=是受限地区  1=否
_is_restricted_region() {
    [[ "${GEOIP_INFO:-}" =~ (香港|Hong|HK|中国|China|CN|俄罗斯|Russia|RU|澳门|Macao|MO) ]]
}

# 有 ISP 节点时返回 "isp-auto"（urltest/balancer），无则返回 "direct"
# 用法: get_fallback_proxy
get_fallback_proxy() {
    if [[ -n "${HAS_ISP_NODES:-}" ]]; then
        echo "isp-auto"
    else
        echo "direct"
    fi
}

# 综合判断当前主 ISP 出口策略
# 返回: "isp-auto"（有 ISP 节点时）或 "direct"
get_isp_preferred_strategy() {
    if [[ -n "${HAS_ISP_NODES:-}" ]]; then
        echo "isp-auto"
    else
        echo "direct"
    fi
}

# ==============================================================================
# §9  测速
# ==============================================================================

# 下载测速，返回截断均值（Mbps，保留两位小数）
# 用法: speed_test <url> <name> [socks5h://ip:port] [user:pass]
# 采样: 执行 SPEED_SAMPLES 次；有效样本≥3时去最大最小取中间均值（截断均值）；
#       全部失败返回 0；日志输出标准差与稳定性标注（[稳定]/[轻微波动]/[波动较大]）
speed_test() {
    local url=$1 name=$2 proxy=${3:-} proxy_auth=${4:-}
    local args=(-s --connect-timeout 5 -L -o /dev/null -m 5 -w '%{speed_download}')
    [[ -n "$proxy" ]]      && args+=(-x "$proxy")
    [[ -n "$proxy_auth" ]] && args+=(--proxy-user "$proxy_auth")
    log INFO "[测速] 开始: ${name}${proxy:+ | 代理: ${proxy}} | 测速源: ${url} | 采样: ${SPEED_SAMPLES}次"

    local samples=() i
    for (( i=1; i<=SPEED_SAMPLES; i++ )); do
        local raw; raw=$(curl "${args[@]}" "$url" 2>/dev/null || echo "0")
        local kbps mbps
        kbps=$(awk -v s="$raw" 'BEGIN { printf "%.0f", s / 1024 }')
        mbps=$(awk -v s="$raw" 'BEGIN { printf "%.2f", s * 8 / 1024 / 1024 }')
        log INFO "[测速] ${name} | 第 ${i}/${SPEED_SAMPLES} 轮: ${kbps} KB/s → ${mbps} Mbps"
        # 有效样本阈值: > 1024 bytes/sec (1 KB/s)；低于此值视为连接失败
        if awk -v r="$raw" 'BEGIN { exit (r + 0 > 1024 ? 0 : 1) }'; then
            samples+=("$raw")
        fi
    done

    local count=${#samples[@]}
    local result="0.00"

    if [[ "$count" -eq 0 ]]; then
        log WARN "[测速] ${name}: 全部 ${SPEED_SAMPLES} 次采样失败，返回 0"
        echo "$result"
        return
    fi

    # 截断均值 + 标准差：全部在一个 awk 脚本中完成
    # 输入：每行一个原始 bytes/sec 值
    # 输出：3 个空格分隔的值 → 截断均值(Mbps)  标准差(Mbps)  稳定性标注
    local stats
    stats=$(printf '%s\n' "${samples[@]}" | awk '
    $1 + 0 == $1 { vals[NR] = $1 }
    END {
        n = NR
        # 冒泡排序（样本量≤5，够用）
        for (i = 1; i <= n; i++)
            for (j = i+1; j <= n; j++)
                if (vals[j] < vals[i]) { t=vals[i]; vals[i]=vals[j]; vals[j]=t }

        # 截断范围：≥3 个样本时去掉最小(1)和最大(n)
        s = (n >= 3) ? 2 : 1
        e = (n >= 3) ? n-1 : n

        # 截断均值（bytes/s → Mbps）
        tsum = 0; tn = 0
        for (i = s; i <= e; i++) { tsum += vals[i]; tn++ }
        tmean = tsum * 8 / tn / 1024 / 1024

        # 全样本均值（用于标准差基准；用全样本而非截断样本，使标准差反映原始波动幅度）
        fsum = 0
        for (i = 1; i <= n; i++) fsum += vals[i] * 8 / 1024 / 1024
        fmean = fsum / n

        # 总体标准差
        sum2 = 0
        for (i = 1; i <= n; i++) sum2 += (vals[i] * 8 / 1024 / 1024 - fmean)^2
        sd = sqrt(sum2 / n)

        # CV 阈值参考统计学经验值：<0.2 低离散，0.2~0.5 中等，>0.5 高离散
        # 变异系数 → 稳定性标注
        cv = (tmean > 0) ? sd / tmean : 0
        if      (cv < 0.2) lbl = "[稳定]"
        else if (cv < 0.5) lbl = "[轻微波动]"
        else               lbl = "[波动较大]"

        printf "%.2f %.2f %s\n", tmean, sd, lbl
    }')

    local stddev lbl
    read -r result stddev lbl <<< "$stats"

    log INFO "[测速] ${name}: ${count}/${SPEED_SAMPLES} 有效样本，截断均值 ${result} Mbps，标准差 ${stddev} Mbps ${lbl}"
    echo "$result"
}

# 将测速结果格式化输出到 stderr（8K 播放能力评级）
# 用法: show_report <speed_mbps> [name]
show_report() {
    local speed=$1 name=${2:-直连}
    speed=$(echo "$speed" | sed 's/[^0-9.]//g')
    local status color
    if   awk -v s="$speed" 'BEGIN { exit (s + 0 > 100 ? 0 : 1) }'; then status="极速，流畅播放 8K (HDR/60fps)"; color=$GREEN
    elif awk -v s="$speed" 'BEGIN { exit (s + 0 > 60  ? 0 : 1) }'; then status="流畅播放 8K";                   color=$GREEN
    elif awk -v s="$speed" 'BEGIN { exit (s + 0 > 25  ? 0 : 1) }'; then status="流畅 4K，8K 可能卡顿";          color=$YELLOW
    elif awk -v s="$speed" 'BEGIN { exit (s + 0 > 10  ? 0 : 1) }'; then status="满足 1080P/4K";                 color=$YELLOW
    else                                                                     status="网络较慢";                       color=$RED
    fi
    cat >&2 <<EOF
========================================
 8K 测速报告 — ${name}
========================================
 速度: ${BOLD}${color}${speed} Mbps${NC}
 评级: ${BOLD}${color}${status}${NC}
========================================
EOF
}

# ==============================================================================
# §10 ISP 节点处理
# ==============================================================================
# 依赖: speed_test (§9), _is_restricted_region (§8), ENV_FILE (§6)

# 构建单个 ISP 代理的 Xray / Sing-box SOCKS5 出站 JSON
# 用法: process_single_isp <prefix> <ip> <port> <user> <pass> <tag>
process_single_isp() {
    local prefix=$1 ip=$2 port=$3 user=$4 pass=$5 tag=$6
    log DEBUG "[ISP] 构建出站 JSON: tag=${tag}"

    export CUSTOM_OUTBOUNDS
    CUSTOM_OUTBOUNDS=$(cat <<EOF
{
  "tag": "${tag}",
  "protocol": "socks",
  "settings": {
    "servers": [
      {
        "address": "${ip}",
        "port": ${port},
        "users": [{"user": "${user}", "pass": "${pass}"}]
      }
    ]
  }
},
EOF
)

    export SB_CUSTOM_OUTBOUNDS
    SB_CUSTOM_OUTBOUNDS=$(cat <<EOF
{
  "type": "socks",
  "tag": "${tag}",
  "server": "${ip}",
  "server_port": ${port},
  "username": "${user}",
  "password": "${pass}"
},
EOF
)
}

# 生成 Sing-box urltest 出站（包裹所有 ISP + direct，运行时自动选优/回退）
# 依赖: ISP_SPEEDS 关联数组（由 _test_isp_node 填充）
# 无 ISP 节点时输出空字符串
build_sb_urltest() {
    if [[ -z "${HAS_ISP_NODES:-}" ]]; then
        export SB_ISP_URLTEST=""
        return
    fi
    # 按速度降序排列 ISP tags
    local sorted_tags
    sorted_tags=$(for tag in "${!ISP_SPEEDS[@]}"; do
        echo "${ISP_SPEEDS[$tag]} $tag"
    done | sort -t' ' -k1 -rn | awk '{print $2}')

    # 构建 outbounds 数组: ISP tags + direct
    local outbounds_json=""
    for tag in $sorted_tags; do
        outbounds_json="${outbounds_json:+${outbounds_json}, }\"${tag}\""
    done
    outbounds_json="${outbounds_json}, \"direct\""

    export SB_ISP_URLTEST
    SB_ISP_URLTEST=$(cat <<EOF
{
  "type": "urltest",
  "tag": "isp-auto",
  "outbounds": [${outbounds_json}],
  "url": "https://www.gstatic.com/generate_204",
  "interval": "1m",
  "tolerance": 300,
  "interrupt_exist_connections": true
},
EOF
)
    log INFO "[ISP] Sing-box urltest 已生成: outbounds=[${outbounds_json}]"
}

# 生成 Xray observatory + balancer 配置（运行时健康检测与自动回退）
# 依赖: ISP_SPEEDS 关联数组
# 无 ISP 节点时所有变量置空
build_xray_balancer() {
    if [[ -z "${HAS_ISP_NODES:-}" ]]; then
        export XRAY_OBSERVATORY_SECTION="" XRAY_BALANCERS_SECTION=""
        return
    fi
    # 按速度降序排列 ISP tags
    local sorted_tags
    sorted_tags=$(for tag in "${!ISP_SPEEDS[@]}"; do
        echo "${ISP_SPEEDS[$tag]} $tag"
    done | sort -t' ' -k1 -rn | awk '{print $2}')

    # 构建精确 selector 列表（不用前缀匹配，避免误匹配）
    local selector_json=""
    for tag in $sorted_tags; do
        selector_json="${selector_json:+${selector_json}, }\"${tag}\""
    done

    export XRAY_OBSERVATORY_SECTION
    XRAY_OBSERVATORY_SECTION=$(cat <<EOF
"observatory": {
    "subjectSelector": [${selector_json}],
    "probeUrl": "https://www.gstatic.com/generate_204",
    "probeInterval": "1m",
    "enableConcurrency": true
},
EOF
)

    export XRAY_BALANCERS_SECTION
    XRAY_BALANCERS_SECTION=$(cat <<EOF
"balancers": [{
    "tag": "isp-auto",
    "selector": [${selector_json}],
    "fallbackTag": "direct",
    "strategy": {"type": "leastPing"}
}],
EOF
)
    log INFO "[ISP] Xray observatory + balancer 已生成: selector=[${selector_json}]"
}

# 动态生成 Xray 服务路由规则（balancerTag / outboundTag 按 *_OUT 值自动切换）
# 依赖: 所有 *_OUT 变量（由 analyze_ai_routing_env 填充）
# 必须在 analyze_ai_routing_env 之后调用
build_xray_service_rules() {
    local rules=""

    # 服务列表: geosite域名 | 环境变量名 | ruleTag | 额外属性
    local -a services=(
        'geosite:openai|CHATGPT_OUT|fix_openai|"marktag": "fix_openai",'
        'geosite:netflix|NETFLIX_OUT||'
        'geosite:disney|DISNEY_OUT||'
        'geosite:anthropic|CLAUDE_OUT||'
        'geosite:google|GEMINI_OUT||'
        'geosite:google-gemini|GEMINI_OUT||'
        'geosite:youtube|YOUTUBE_OUT||'
        'geosite:category-social-media-!cn|SOCIAL_MEDIA_OUT||'
        'geosite:tiktok|TIKTOK_OUT||'
        '"geosite:amazon","geosite:paypal","geosite:ebay"|ISP_OUT||'
    )

    for entry in "${services[@]}"; do
        IFS='|' read -r domains out_var rule_tag extra <<< "$entry"
        local out_val="${!out_var:-direct}"

        # 构建 domain 数组
        local domain_json="\"${domains}\""
        # 如果 domains 已包含 ","（多域名情况），直接使用
        if [[ "$domains" == *'","'* ]]; then
            domain_json="${domains}"
        fi

        local rule
        if [[ "$out_val" == "isp-auto" ]]; then
            rule="{\"type\":\"field\",\"domain\":[${domain_json}],${extra}\"balancerTag\":\"isp-auto\"}"
        else
            rule="{\"type\":\"field\",\"domain\":[${domain_json}],${extra}\"outboundTag\":\"${out_val}\"}"
        fi
        rules="${rules:+${rules},
            }${rule}"
    done

    # 末尾追加逗号，因为模板中 private-ip 规则紧随其后
    export XRAY_SERVICE_RULES="${rules},"
    log DEBUG "[ISP] Xray 服务路由规则已动态生成"
}

# 对单个 ISP 节点执行测速，记录速度到 ISP_SPEEDS 并追踪最快节点
# 用法: _test_isp_node <prefix> <ip> <port> <user> <pass> <tag>
_test_isp_node() {
    local prefix=$1 ip=$2 port=$3 user=$4 pass=$5 tag=$6
    local speed
    speed=$(speed_test "$_SPEED_TEST_URL" "$prefix" \
                       "socks5h://${ip}:${port}" "${user}:${pass}")
    show_report "$speed" "$prefix"

    # 保存每个 ISP 的速度用于排序
    ISP_SPEEDS["$tag"]="$speed"

    # 追踪最快节点（用于 IS_8K_SMOOTH 计算）
    if awk -v s="$speed" -v m="${proxy_max_speed:-0}" 'BEGIN { exit (s + 0 > m + 0 ? 0 : 1) }'; then
        export proxy_max_speed="$speed"
        export FASTEST_PROXY_TAG="$tag"
        log INFO "[测速] ${tag}: ${speed} Mbps → 新最优"
    else
        log INFO "[测速] ${tag}: ${speed} Mbps (最优仍: ${FASTEST_PROXY_TAG:-未定} ${proxy_max_speed:-0} Mbps)"
    fi
}

# 根据测速结果和环境信息决定最终 ISP_TAG，并计算 IS_8K_SMOOTH
# 依赖外部变量（由 run_speed_tests_if_needed 注入）:
#   DIRECT_SPEED, proxy_max_speed, FASTEST_PROXY_TAG
#
# 选路原则：
#   1. 优选最快 ISP 代理（FASTEST_PROXY_TAG，由测速阶段确定）
#   2. 受限地区 OR 非住宅 IP → 需要代理（解锁 geo / 流媒体 / AI）
#   3. 需要代理 + 有可用节点 → ISP_TAG = FASTEST_PROXY_TAG
#   4. 需要代理 + 无可用节点 → 回退直连（ERROR）
#   5. 条件均不满足（住宅 IP + 非受限）→ 直连兜底
apply_isp_routing_logic() {
    local manual_isp_tag=""
    if [[ -n "${DEFAULT_ISP:-}" ]]; then
        manual_isp_tag="proxy-$(echo "${DEFAULT_ISP%_ISP}" | tr '[:upper:]_ ' '[:lower:]-')"
    fi

    # 打印完整决策上下文
    local _region="${GEOIP_INFO%%|*}"
    local _ip_label; case "${IP_TYPE:-unknown}" in isp) _ip_label="住宅/ISP IP" ;; hosting) _ip_label="机房/托管 IP" ;; *) _ip_label="未知" ;; esac
    log INFO "[选路] ════════════════════════════════════════════"
    log INFO "[选路] 决策输入:"
    log INFO "[选路]   IP_TYPE       = ${IP_TYPE:-未知} (${_ip_label})"
    log INFO "[选路]   地区          = ${_region:-未知}"
    log INFO "[选路]   DEFAULT_ISP   = ${DEFAULT_ISP:-未设置（自动选路）}"
    log INFO "[选路]   直连速度      = ${DIRECT_SPEED:-0} Mbps（不参与选路）"
    log INFO "[选路]   最优 ISP 代理 = ${FASTEST_PROXY_TAG:-无} (${proxy_max_speed:-0} Mbps)"
    log INFO "[选路] 原则: 受限地区/非住宅IP→需代理解锁; 住宅IP+非受限→直连兜底"
    log INFO "[选路] ────────────────────────────────────────────"

    if [[ -n "${manual_isp_tag}" ]]; then
        # 锁定模式：DEFAULT_ISP 非空，强制使用指定出口，跳过所有条件判断
        log INFO "[选路] 手动覆盖 DEFAULT_ISP=${DEFAULT_ISP} → ${manual_isp_tag}"
        export ISP_TAG="$manual_isp_tag"

    elif _is_restricted_region || [[ "${IP_TYPE:-unknown}" != "isp" ]]; then
        # 受限地区 或 非住宅 IP → 需要 ISP 代理解锁
        _is_restricted_region \
            && log WARN "[选路] 受限地区 (${GEOIP_INFO%%|*})，需要 ISP 代理"
        [[ "${IP_TYPE:-unknown}" != "isp" ]] \
            && log INFO "[选路] 非住宅 IP (${IP_TYPE:-unknown})，需 ISP 代理解锁流媒体/AI"
        if [[ -n "${FASTEST_PROXY_TAG:-}" ]]; then
            log INFO "[选路] 使用最优 ISP 代理: ${FASTEST_PROXY_TAG} (${proxy_max_speed:-0} Mbps)"
            export ISP_TAG="${FASTEST_PROXY_TAG}"
        else
            log ERROR "[选路] 需要 ISP 代理但无可用节点！回退直连"
            export ISP_TAG="direct"
        fi

    else
        # 住宅 ISP IP + 非受限地区：兜底直连
        log INFO "[选路] 住宅 ISP IP + 非受限地区，无需代理，直连"
        export ISP_TAG="direct"
    fi

    # IS_8K_SMOOTH：基于实际出口的均值速度，阈值 100 Mbps
    # - 使用 ISP 代理 → 以代理均值为准 → show-config.sh 生成 "good" 标签
    # - 回退直连     → 以直连均值为准 → show-config.sh 结合 IP_TYPE 决定 "super" 标签
    local ref_speed
    if [[ "${ISP_TAG:-}" != "direct" ]]; then
        ref_speed="${proxy_max_speed:-0}"
    else
        ref_speed="${DIRECT_SPEED:-0}"
    fi
    if awk -v s="$ref_speed" 'BEGIN { exit (s + 0 > 100 ? 0 : 1) }'; then
        export IS_8K_SMOOTH="true"
    else
        export IS_8K_SMOOTH="false"
    fi

    # 持久化到 STATUS_FILE（与流媒体检测结果同文件，删除可触发重新测速）
    {   _sed_i '/^export IS_8K_SMOOTH=/d; /^export ISP_TAG=/d' "${STATUS_FILE}" 2>/dev/null || true
        echo "export IS_8K_SMOOTH='${IS_8K_SMOOTH:-false}'" >> "${STATUS_FILE}"
        echo "export ISP_TAG='${ISP_TAG:-}'"                >> "${STATUS_FILE}"
    }

    # IS_8K_SMOOTH → 节点标签映射说明
    local _label_hint
    if [[ "${IS_8K_SMOOTH}" == "true" ]]; then
        if [[ "${ISP_TAG:-}" != "direct" ]]; then
            _label_hint="→ show-config.sh 将生成 ✈ good 标签 (OpenClash +10分)"
        else
            _label_hint="→ IP_TYPE=isp 时 show-config.sh 将生成 ✈ super 标签 (OpenClash +30分)"
        fi
    else
        _label_hint="→ 无质量标签（速度 ${ref_speed} Mbps 未达 100 Mbps 阈值）"
    fi
    log INFO "[选路] IS_8K_SMOOTH: 出口=${ISP_TAG} | 参考速度=${ref_speed} Mbps | 阈值=100 Mbps → ${IS_8K_SMOOTH}  ${_label_hint}"
    log INFO "[选路] ✓ 最终决策: ISP_TAG=${ISP_TAG:-direct} | IS_8K_SMOOTH=${IS_8K_SMOOTH:-false}"
    log INFO "[选路] ════════════════════════════════════════════"
}

# ==============================================================================
# §11 流媒体/AI 可达性检测
# ==============================================================================
# 依赖: http_probe (§3), http_trace_url (§3), _is_restricted_region (§8),
#       get_fallback_proxy (§8), IP_TYPE (§7 check_ip_type), ISP_TAG (§10)
# 注意: 这些函数在 analyze_ai_routing_env (§15) 中批量调用，结果缓存至 STATUS_FILE

# Netflix 可达性检测（住宅 IP 默认直连；机房 IP 发起探测）
check_netflix_access() {
    [[ "${IP_TYPE:-unknown}" == "isp" ]] && { echo "direct"; return; }
    local code; code=$(http_probe "https://www.netflix.com/title/81249783" "true")
    if [[ "$code" =~ ^(2|3) ]]; then
        echo "direct"
    else
        log WARN "[Netflix] 访问受限 (${code})，转 ISP 代理"
        get_fallback_proxy
    fi
}

# Disney+ 可达性检测
check_disney_access() {
    [[ "${IP_TYPE:-unknown}" == "isp" ]] && { echo "direct"; return; }
    local code; code=$(http_probe "https://www.disneyplus.com/" "true")
    if [[ "$code" =~ ^(2|3) ]]; then
        echo "direct"
    else
        log WARN "[Disney+] 访问受限 (${code})，转 ISP 代理"
        get_fallback_proxy
    fi
}

# YouTube 可达性检测
check_youtube_access() {
    [[ "${IP_TYPE:-unknown}" == "isp" ]] && { echo "direct"; return; }
    local code; code=$(http_probe "https://www.youtube.com/" "true")
    if [[ "$code" =~ ^(2|3) ]]; then
        echo "direct"
    else
        log WARN "[YouTube] 访问受限 (${code})，转 ISP 代理"
        get_fallback_proxy
    fi
}

# 社交媒体可达性检测（受限地区或非住宅 IP 均转 ISP 代理）
check_social_media_access() {
    if _is_restricted_region; then
        log WARN "[社交] 受限地区 (${GEOIP_INFO%%|*})，转 ISP 代理"
        get_fallback_proxy
    elif [[ "${IP_TYPE:-unknown}" != "isp" ]]; then
        log WARN "[社交] 非住宅 IP，转 ISP 代理"
        get_fallback_proxy
    else
        echo "direct"
    fi
}

# TikTok 可达性检测（对受限地区有额外限制）
check_tiktok_access() {
    if _is_restricted_region; then
        log WARN "[TikTok] 受限地区 (${GEOIP_INFO%%|*})，转 ISP 代理"
        get_fallback_proxy
    elif [[ "${IP_TYPE:-unknown}" != "isp" ]]; then
        log WARN "[TikTok] 非住宅 IP，转 ISP 代理"
        get_fallback_proxy
    else
        echo "direct"
    fi
}

# ChatGPT 可达性检测
check_chatgpt_access() {
    if _is_restricted_region; then
        log WARN "[ChatGPT] 受限地区，转 ISP 代理"; get_fallback_proxy; return
    fi
    [[ "${IP_TYPE:-unknown}" == "isp" ]] && { echo "direct"; return; }
    local code; code=$(http_probe "https://chatgpt.com" "false")
    if [[ "$code" =~ ^(2|3) ]]; then
        echo "direct"
    else
        log WARN "[ChatGPT] 访问受限 (${code})，转 ISP 代理"
        get_fallback_proxy
    fi
}

# Claude 可达性检测（通过重定向终点判断是否被封锁）
check_claude_access() {
    if _is_restricted_region; then
        log WARN "[Claude] 受限地区，转 ISP 代理"; get_fallback_proxy; return
    fi
    [[ "${IP_TYPE:-unknown}" == "isp" ]] && { echo "direct"; return; }
    local final_url; final_url=$(http_trace_url "https://claude.ai/login")
    if [[ "$final_url" =~ claude\.ai/(login|chats) || -z "$final_url" ]]; then
        echo "direct"
    else
        log WARN "[Claude] 重定向异常 (${final_url})，转 ISP 代理"
        get_fallback_proxy
    fi
}

# Gemini 可达性检测（支持手动覆盖 GEMINI_DIRECT=true/false）
check_gemini_access() {
    case "${GEMINI_DIRECT:-}" in
        "true")  echo "direct";      return ;;
        "false") get_fallback_proxy; return ;;
    esac
    if _is_restricted_region; then
        log WARN "[Gemini] 受限地区，转 ISP 代理"; get_fallback_proxy; return
    fi
    [[ "${IP_TYPE:-unknown}" == "isp" ]] && { echo "direct"; return; }
    local code; code=$(http_probe "https://gemini.google.com/app" "true")
    if [[ "$code" =~ ^(2|3) ]]; then
        echo "direct"
    else
        log WARN "[Gemini] 访问受限 (${code})，转 ISP 代理"
        get_fallback_proxy
    fi
}

# ==============================================================================
# §12 证书管理
# ==============================================================================
# 依赖: checkRequiredEnv (§6), 外部工具 acme.sh / openssl / nginx

# 申请或续签 TLS 证书（有效期 >7 天则跳过）
# 用法: issueCertificate <name> "<domain1:dns_provider1|domain2:dns_provider2>"
issueCertificate() {
    local name="$1" params="$2"
    local first_dom="${params%%:*}"
    local cert="${SSL_PATH}/${name}.crt" key="${SSL_PATH}/${name}.key" ca="${SSL_PATH}/${name}-ca.crt"

    # 证书有效期检查
    if [[ -f "$cert" && -f "$key" && -f "$ca" ]]; then
        if openssl x509 -checkend 604800 -noout -in "$cert" >/dev/null 2>&1; then
            log INFO "[证书] ${name} 有效期充足 (>7d)，跳过续签"
            return 0
        fi
        log WARN "[证书] ${name} 即将过期，启动续签"
    fi

    # 首次申请
    if ! acme.sh --list | grep -q "${first_dom}"; then
        checkRequiredEnv "ACMESH_SERVER_NAME" "ACMESH_REGISTER_EMAIL" \
                         "ALI_KEY" "ALI_SECRET" "CF_TOKEN" "CF_ZONE_ID" "CF_ACCOUNT_ID"
        export Ali_Key="${ALI_KEY}" Ali_Secret="${ALI_SECRET}" \
               CF_Token="${CF_TOKEN}" CF_Zone_ID="${CF_ZONE_ID}" CF_Account_ID="${CF_ACCOUNT_ID}"

        local reg_args=("-m" "${ACMESH_REGISTER_EMAIL}" "--server" "${ACMESH_SERVER_NAME}")
        if [[ "${ACMESH_SERVER_NAME}" == "google" ]]; then
            [[ -z "${ACMESH_EAB_KID:-}" || -z "${ACMESH_EAB_HMAC_KEY:-}" ]] && {
                log ERROR "[证书] Google CA 需要提供 EAB_KID 和 EAB_HMAC_KEY"; return 1
            }
        fi
        [[ -n "${ACMESH_EAB_KID:-}" && -n "${ACMESH_EAB_HMAC_KEY:-}" ]] && \
            reg_args+=("--eab-kid" "${ACMESH_EAB_KID}" "--eab-hmac-key" "${ACMESH_EAB_HMAC_KEY}")

        acme.sh --register-account "${reg_args[@]}" >/dev/null 2>&1

        local issue_args=("--issue" "--ecc" "--server" "${ACMESH_SERVER_NAME}")
        IFS='|' read -ra ENTRIES <<< "$params"
        for e in "${ENTRIES[@]}"; do
            local d="${e%%:*}" p="${e#*:}"
            issue_args+=("-d" "$d" "--dns" "$p")
            [[ ! "$d" =~ ^[0-9.]+$ ]] && issue_args+=("-d" "*.$d" "--dns" "$p")
        done
        acme.sh "${issue_args[@]}" || { log ERROR "[证书] 申请失败"; return 1; }
    fi

    # 安装证书
    log INFO "[证书] 安装 ${name}..."
    rm -f /etc/nginx/conf.d/* /etc/nginx/stream.d/*
    acme.sh --install-cert --ecc -d "${first_dom}" \
        --key-file "$key" --fullchain-file "$cert" --ca-file "$ca" \
        --reloadcmd "/usr/sbin/nginx"
    /usr/sbin/nginx -s quit 2>/dev/null && rm -f /var/run/nginx/nginx.pid || true
}

# ==============================================================================
# §13 配置渲染
# ==============================================================================
# 依赖: _apply_tpl (§5), generateRandomStr (§4)
# 必须在所有环境变量（UUID/端口/ISP/流媒体可达性）全部就绪后调用
# daemon.ini 服务启动顺序（priority 值越小越先启动）:
#   priority=5  : s-ui, x-ui    → 需要 x-ui/s-ui 设置（§15 analyze 阶段完成后执行）
#   priority=10 : cron          → 需要 cron 配置
#   priority=15 : dufs, http-meta, sub-store → 需要 ${WORKDIR}/dufs/conf.yml 和 providers
#   priority=20 : xray, sing-box → 需要 ${WORKDIR}/xray/ 和 ${WORKDIR}/sing-box/
#   priority=25 : nginx         → 需要 nginx.conf + htpasswd + 证书

# 渲染所有服务配置模板
createConfig() {
    log INFO "[配置] 渲染所有模板..."
    # 修复: shuf 是 Linux 专属命令，macOS 不可用；改用 bash 内置 RANDOM
    export RANDOM_NUM=$(( RANDOM % 10 ))

    _apply_tpl "/templates/supervisord/supervisord.conf" "/etc/supervisord.conf"
    _apply_tpl "/templates/supervisord/daemon.ini"       "/etc/supervisor.d/daemon.ini"
    _apply_tpl "/templates/nginx/nginx.conf"             "/etc/nginx/nginx.conf"
    cp -f /templates/nginx/network_internal.conf         "/etc/nginx/network_internal.conf"
    _apply_tpl "/templates/nginx/http.conf"              "/etc/nginx/conf.d/http.conf"
    _apply_tpl "/templates/nginx/tcp.conf"               "/etc/nginx/stream.d/tcp.conf"
    _apply_tpl "/templates/dufs/conf.yml"                "${WORKDIR}/dufs/conf.yml"
    _apply_tpl "/templates/providers/providers.yaml"     "${WORKDIR}/providers"

    # 清理挂载目录内已不存在于模板源的孤儿文件（例如：升级镜像时删除了某个入站模板）
    # 否则 xray/sing-box 会继续加载孤儿 JSON，产生"模板已删但仍生效"的诡异状态
    for dir in "${WORKDIR}/xray" "${WORKDIR}/sing-box"; do
        mkdir -p "$dir"
        src_type="${dir##*/}"
        for f in "$dir"/*.json; do
            [ -e "$f" ] || continue
            name=$(basename "$f")
            if [ ! -f "/templates/${src_type}/${name}" ]; then
                log INFO "[配置] 清理孤儿 JSON：${dir}/${name}"
                rm -f "$f"
            fi
        done
    done

    # M4 feature flag：按开关决定是否加载对应入站模板；关闭时主动删除 WORKDIR 里的老文件，
    # 避免"升级后开关关掉但老配置残留"的情况
    local xray_dst="${WORKDIR}/xray"
    for t in /templates/xray/*.json; do
        local name; name=$(basename "$t")
        local enable_flag=""
        case "$name" in
            05_xicmp_emergency_inbounds.json)   enable_flag="${ENABLE_XICMP}" ;;
            06_xdns_emergency_inbounds.json)    enable_flag="${ENABLE_XDNS}" ;;
        esac
        if [ -n "$enable_flag" ] && [ "$enable_flag" != "true" ]; then
            rm -f "${xray_dst}/${name}"
            log INFO "[配置][M4] 跳过禁用模板：${name}"
            continue
        fi
        _apply_tpl "$t" "${xray_dst}/${name}"
    done
    for t in /templates/sing-box/*.json; do _apply_tpl "$t" "${WORKDIR}/sing-box/$(basename "$t")"; done

    # VLESS Reverse Proxy（M3）：ENABLE_REVERSE=true 时往 01_reality_inbounds.json 的
    # clients 数组追加一个带 reverse.tag 标记的 UUID；同时往 xr.json 追加 outboundTag=r-tunnel 的
    # 路由规则（命中 REVERSE_DOMAINS 列表的域名走 reverse 隧道到家宽落地机）
    if [ "${ENABLE_REVERSE:-false}" = "true" ]; then
        local reality_file="${WORKDIR}/xray/01_reality_inbounds.json"
        local xr_file="${WORKDIR}/xray/xr.json"
        local reverse_domains_json="[]"
        if [ -n "${REVERSE_DOMAINS:-}" ]; then
            reverse_domains_json=$(printf '%s' "${REVERSE_DOMAINS}" | awk -v RS=',' 'NF{printf "\"%s\",",$0}' | sed 's/,$//; s/^/[/; s/$/]/')
        fi
        log INFO "[配置][Reverse] 注入 reverse client（UUID=...${XRAY_REVERSE_UUID: -8}）+ routing 规则（domains=${REVERSE_DOMAINS:-<none>}）"
        # 1) 追加 reverse client 到 01_reality_inbounds.json
        jq --arg uuid "${XRAY_REVERSE_UUID}" '
            .inbounds[0].settings.clients += [{
                "id": $uuid,
                "level": 0,
                "email": "reverse@portal.bridge",
                "flow": "xtls-rprx-vision",
                "reverse": { "tag": "r-tunnel" }
            }]
        ' "$reality_file" > "${reality_file}.tmp" && mv "${reality_file}.tmp" "$reality_file"
        # 2) 追加 routing 规则到 xr.json（仅当有 REVERSE_DOMAINS 时；否则只建立隧道不路由任何流量）
        if [ "$reverse_domains_json" != "[]" ]; then
            jq --argjson domains "$reverse_domains_json" '
                .routing.rules |= ([{
                    "type": "field",
                    "ruleTag": "reverse-bridge",
                    "domain": $domains,
                    "outboundTag": "r-tunnel"
                }] + .)
            ' "$xr_file" > "${xr_file}.tmp" && mv "${xr_file}.tmp" "$xr_file"
        fi
    fi

    log INFO "[配置] 所有模板渲染完成"
}

# 注：M1-4 原计划 `buildMphCache` 已作废
# PR #5505 (v26.2.6) 引入的 `xray buildMphCache` CLI 于 2026-04-13 被 PR #5814 revert
# 新方案是运行时自动生效的 matcher group + 初始化后主动 GC，无需任何 CLI/env 配置
# 详见 docs/10-implementation-notes.md §M1-4

# 生成 CLASH_PROXY_PROVIDERS / SURGE_PROXY_PROVIDERS / STASH_PROVIDER_NAMES
# 依赖: ${WORKDIR}/providers（由 createConfig 渲染）
generateProxyProvidersConfig() {
    local provider_config="${WORKDIR}/providers"
    local clash_providers=""

    if [ -f "$provider_config" ]; then
        clash_providers=$(sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$/d' \
                              -e '/^providers:/d' -e '/^proxy-providers:/d' "$provider_config")
    else
        log WARN "[Providers] ${provider_config} 不存在，跳过文件读取"
    fi

    # 合并环境变量 PROVIDERS（格式: "名称|URL|备注"，管道分隔多条）
    if [ -n "${PROVIDERS:-}" ]; then
        local env_content
        env_content=$(echo "$PROVIDERS" | awk -F'|' '
            NF>=2 && $1!="" && $2!="" {
                suffix = ($3 != "") ? " [" $3 "]" : ""
                printf "  %s: {<<: *BaseProvider, url: \"%s\", override: {additional-prefix: \"[%s] \", additional-suffix: \"%s\"}}\n",
                    $1, $2, $1, suffix
            }')
        [ -n "$env_content" ] && \
            clash_providers="${clash_providers:+${clash_providers}
}${env_content}"
    fi
    export CLASH_PROXY_PROVIDERS="${clash_providers}"

    # Surge Policy-Path 格式（仅 AllOne 机场）
    local surge_providers=""
    if [ -n "${clash_providers}" ]; then
        surge_providers=$(echo "$clash_providers" | awk -F':' '
            $0 ~ /url:/ {
                name = $1; gsub(/^[ \t]+|[ \t]+$/, "", name)
                if (name != "AllOne") next
                match($0, /url:[ \t]*"[^"]+"/)
                if (RSTART > 0) {
                    url_part = substr($0, RSTART, RLENGTH)
                    match(url_part, /"[^"]+"/)
                    url = substr(url_part, RSTART+1, RLENGTH-2)
                    sub(/-Common$/, "-Surge", url); sub(/-common$/, "-Surge", url)
                    printf "%s = smart, policy-path=%s, update-interval=86400, no-alert=0, hidden=1, include-all-proxies=0\n",
                        name, url
                }
            }')
    fi
    export SURGE_PROXY_PROVIDERS="${surge_providers}"

    local surge_names=""
    [ -n "${surge_providers}" ] && \
        surge_names=$(echo "$surge_providers" | awk -F'=' '{print $1}' | awk '{$1=$1};1' | paste -sd "," -)
    export SURGE_PROVIDER_NAMES="${surge_names:+, ${surge_names}}"

    local stash_names=""
    [ -n "${clash_providers}" ] && \
        stash_names=$(echo "${clash_providers}" | awk -F':' \
            'NF>=2 && $1 !~ /^[[:space:]]*#/ {
                name=$1; gsub(/^[ \t]+|[ \t]+$/, "", name)
                if (name != "") print name
            }' | paste -sd ", " -)
    export STASH_PROVIDER_NAMES="${stash_names}"
}

# ==============================================================================
# §14 远端密钥解密
# ==============================================================================
# 依赖: checkRequiredEnv (§6), curl, crypctl
# 在主流程第一步（_init_dirs 之后）调用，SECRET_FILE 解密后由 main_init source

# 下载并解密远端密钥文件到 SECRET_FILE（已存在则跳过）
decryptSecretsEnv() {
    # Private repo — fork users should replace with their own secrets endpoint
    local base_url="https://raw.githubusercontent.com/currycan/key/master"
    checkRequiredEnv DECODE
    if [ -f "${SECRET_FILE}" ]; then
        log DEBUG "[密钥] 本地密钥文件已存在，跳过下载"
        return
    fi
    mkdir -p "$(dirname "${SECRET_FILE}")"
    log INFO "[密钥] 下载远端密钥文件..."
    curl -fsSLo /tmp/tmp.bin "${base_url}/tmp.bin" || {
        log ERROR "[密钥] 下载失败，终止启动"; exit 1
    }
    # 使用 --key-env 从环境变量读取密钥，避免明文出现在 ps/argv
    # 要求 crypctl >= 支持 --key-env 的版本（currycan/key docker/crypctl）
    crypctl decrypt -i /tmp/tmp.bin -o "${SECRET_FILE}" --key-env DECODE || {
        log ERROR "[密钥] 解密失败，终止启动"; exit 1
    }
    rm -f /tmp/tmp.bin
    log INFO "[密钥] 解密完成: ${SECRET_FILE}"
}

# ==============================================================================
# §15 主流程各阶段
# ==============================================================================
# 以下函数按运行时执行顺序声明，严格对应 main_init 的调用序列。

# 初始化所有必要目录和文件（整个启动流程只调用一次）
_init_dirs() {
    mkdir -p "$(dirname "${ENV_FILE}")" "$(dirname "${STATUS_FILE}")"
    touch "${ENV_FILE}" "${STATUS_FILE}"
    # 迁移清理: ISP_TAG/IS_8K_SMOOTH 曾错误写入 ENV_FILE（Bug #023），确保只存于 STATUS_FILE
    _sed_i '/^export ISP_TAG=/d; /^export IS_8K_SMOOTH=/d' "${ENV_FILE}" 2>/dev/null || true
    mkdir -p "${LOGDIR:=/var/log}"/{supervisor,xray,sing-box,dufs,nginx,x-ui,s-ui}
    mkdir -p "${SUI_DB_FOLDER:-/opt/s-ui}" "${SUB_STORE_DATA_BASE_PATH:-/opt/substore}"
}

# 阶段 1: 初始化持久化基础变量（UUID/端口/GeoIP 等）
# 依赖: ensure_var (§6), generateRandomStr (§4), 网络探测函数 (§7)
analyze_base_env() {
    log INFO "[阶段 1] 初始化基础环境变量..."

    # 格式: "KEY|生成命令"
    # auto-gen 变量在 Dockerfile 中不设默认值（置空），确保此处 auto-gen 优先于 Dockerfile
    local -a vars=(
        "XUI_LOCAL_PORT|generateRandomStr port"
        "DUFS_PORT|generateRandomStr port"
        "PASSWORD|generateRandomStr password 16"
        "XRAY_UUID|generateRandomStr uuid"
        "XRAY_REVERSE_UUID|generateRandomStr uuid"
        "SB_UUID|generateRandomStr uuid"
        "XRAY_REALITY_SHORTID|openssl rand -hex 8"
        "XRAY_REALITY_SHORTID_2|openssl rand -hex 4"
        "XRAY_REALITY_SHORTID_3|openssl rand -hex 6"
        "XRAY_URL_PATH|generateRandomStr path 32"
        "SUBSCRIBE_TOKEN|generateRandomStr path 32"
        "STRATEGY|detect_ip_strategy_api"
        "GEOIP_INFO|get_geo_info"
        "IS_BRUTAL|check_brutal_status"
        "SUB_STORE_FRONTEND_BACKEND_PATH|echo /$(generateRandomStr path 32)"
        "IP_TYPE|check_ip_type"
    )

    for entry in "${vars[@]}"; do
        IFS='|' read -r key cmd <<< "$entry"
        ensure_var "$key" $cmd
    done

    log INFO "[阶段 1] 完成 hy2=${PORT_HYSTERIA2}(xray) tuic=${PORT_TUIC} anytls=${PORT_ANYTLS}"
}

# 阶段 2: ISP 代理测速与选路（ISP_TAG 已缓存则跳过）
# 依赖: speed_test (§9), _test_isp_node (§10), apply_isp_routing_logic (§10)
# 必须在 analyze_ai_routing_env 之前执行（ISP_TAG 是流媒体检测的前置条件）
run_speed_tests_if_needed() {
    log INFO "[阶段 2] 测速与选路..."

    if [[ -n "${ISP_TAG:-}" ]]; then
        log INFO "[阶段 2] ISP_TAG 已缓存 (${ISP_TAG})，跳过测速"
        # 重建 HAS_ISP_NODES 和 ISP_SPEEDS（build_client_and_server_configs 依赖）
        declare -gA ISP_SPEEDS
        local env_vars; env_vars=$(env | grep "_ISP_IP=" | cut -d= -f1) || true
        for var in $env_vars; do
            local prefix="${var%_IP}" ip="${!var}"
            local port_var="${prefix}_PORT"; local port="${!port_var:-}"
            [[ -z "$ip" || -z "$port" ]] && continue
            local tag="proxy-$(echo "${prefix}" | tr '[:upper:]_ ' '[:lower:]-')"
            ISP_SPEEDS["$tag"]="0"  # 无实际速度数据，设 0
        done
        if [[ ${#ISP_SPEEDS[@]} -gt 0 ]]; then
            export HAS_ISP_NODES="true"
            # 确保 ISP_TAG 对应的节点排在前面（设较大值）
            if [[ -n "${ISP_SPEEDS[${ISP_TAG}]+_}" ]]; then
                ISP_SPEEDS["${ISP_TAG}"]="999"
            fi
        fi
        return
    fi

    # ISP_TAG 需要重新评估：清除所有依赖 ISP_TAG 的服务路由缓存
    # 防止旧缓存值与新选路结果不一致（Bug #025）
    log INFO "[阶段 2] 清除服务路由缓存（与 ISP_TAG 同步刷新）..."
    _sed_i '/^export ISP_OUT=/d; /^export CHATGPT_OUT=/d; /^export NETFLIX_OUT=/d; /^export DISNEY_OUT=/d; /^export YOUTUBE_OUT=/d; /^export GEMINI_OUT=/d; /^export CLAUDE_OUT=/d; /^export SOCIAL_MEDIA_OUT=/d; /^export TIKTOK_OUT=/d' "${STATUS_FILE}" 2>/dev/null || true
    unset ISP_OUT CHATGPT_OUT NETFLIX_OUT DISNEY_OUT YOUTUBE_OUT GEMINI_OUT CLAUDE_OUT SOCIAL_MEDIA_OUT TIKTOK_OUT

    unset ISP_TAG TOP_ISP_TAG proxy_max_speed FASTEST_PROXY_TAG IS_8K_SMOOTH DIRECT_SPEED HAS_ISP_NODES
    export proxy_max_speed=0
    declare -gA ISP_SPEEDS

    log INFO "[阶段 2] 环境: IP_TYPE=${IP_TYPE:-未知} | 地区=${GEOIP_INFO%%|*} | DEFAULT_ISP=${DEFAULT_ISP:-未设置}"

    # 直连基准测速（不用于选路决策，仅用于：
    #   1. show_report 展示基准参考
    #   2. 无 ISP 代理时 IS_8K_SMOOTH 的判定依据（super 标签场景）
    export DIRECT_SPEED
    DIRECT_SPEED=$(speed_test "$_SPEED_TEST_URL" "Direct")
    show_report "${DIRECT_SPEED}" "Direct"
    log INFO "[阶段 2] 直连基准: ${DIRECT_SPEED} Mbps（不参与选路；无代理时用于 IS_8K_SMOOTH 判定）"

    # 遍历所有 *_ISP_IP 环境变量，依次测速（多采样均值 + 容差带，见 §9）
    local env_vars; env_vars=$(env | grep "_ISP_IP=" | cut -d= -f1) || true
    local _node_count=0; [[ -n "$env_vars" ]] && _node_count=$(echo "$env_vars" | wc -l | tr -d ' ')
    if [[ "$_node_count" -eq 0 ]]; then
        log WARN "[阶段 2] 未发现 ISP 节点（无 *_ISP_IP 环境变量），将回退直连"
    else
        export HAS_ISP_NODES="true"
        log INFO "[阶段 2] 发现 ISP 节点: ${_node_count} 个，开始逐节点测速（采样=${SPEED_SAMPLES}次）..."
    fi
    for var in $env_vars; do
        local prefix="${var%_IP}" ip="${!var}"
        local port_var="${prefix}_PORT" user_var="${prefix}_USER" pass_var="${prefix}_SECRET"
        local port="${!port_var:-}" user="${!user_var:-}" pass="${!pass_var:-}"
        [[ -z "$ip" || -z "$port" ]] && continue

        local tag="proxy-$(echo "${prefix}" | tr '[:upper:]_ ' '[:lower:]-')"
        _test_isp_node "$prefix" "$ip" "$port" "$user" "$pass" "$tag"
    done

    apply_isp_routing_logic
    log INFO "[阶段 2] 完成"
}

# 阶段 3: 流媒体/AI 可达性检测 + 加密密钥对生成
# 依赖: check_*_access (§11), ensure_key_pair (§6)
# 前置: run_speed_tests_if_needed（ISP_TAG 必须已确定）
analyze_ai_routing_env() {
    log INFO "[阶段 3] 流媒体/AI 可达性检测..."

    # 结果缓存在 STATUS_FILE；已缓存则跳过，避免重复网络探测
    local -a checks=(
        "CHATGPT_OUT|check_chatgpt_access"
        "NETFLIX_OUT|check_netflix_access"
        "DISNEY_OUT|check_disney_access"
        "YOUTUBE_OUT|check_youtube_access"
        "GEMINI_OUT|check_gemini_access"
        "CLAUDE_OUT|check_claude_access"
        "SOCIAL_MEDIA_OUT|check_social_media_access"
        "TIKTOK_OUT|check_tiktok_access"
        "ISP_OUT|get_isp_preferred_strategy"
    )

    for entry in "${checks[@]}"; do
        IFS='|' read -r key cmd <<< "$entry"
        if [[ -n "${!key:-}" ]]; then
            log DEBUG "[${key}] 命中缓存: ${!key}"; continue
        fi
        # 修复: 原版使用 eval "$cmd"；cmd 为函数名，直接调用即可
        local val; val=$($cmd)
        export "${key}=${val}"
        _sed_i "/^export ${key}=/d" "${STATUS_FILE}" 2>/dev/null || true
        echo "export ${key}='${val}'" >> "${STATUS_FILE}"
    done

    log INFO "[阶段 3] 生成加密密钥对..."
    ensure_key_pair "Reality"  "xray x25519"   "XRAY_REALITY_PRIVATE_KEY" "XRAY_REALITY_PUBLIC_KEY"
    ensure_key_pair "MLKEM768" "xray mlkem768"  "XRAY_MLKEM768_SEED"       "XRAY_MLKEM768_CLIENT"

    log_summary_box "IP_TYPE" "ISP_TAG" "IS_8K_SMOOTH" "ISP_OUT" \
                    "CHATGPT_OUT" "NETFLIX_OUT" "DISNEY_OUT" "YOUTUBE_OUT" \
                    "GEMINI_OUT"  "CLAUDE_OUT"  "TIKTOK_OUT" "SOCIAL_MEDIA_OUT"
    log INFO "[阶段 3] 完成"
}

# 阶段 4: 生成客户端 Clash/Surge 代理条目 + 服务端出站 JSON
# 依赖: process_single_isp (§10), ISP_TAG (§10 apply_isp_routing_logic)
build_client_and_server_configs() {
    log INFO "[阶段 4] 生成客户端/服务端配置片段..."

    export CUSTOM_OUTBOUNDS="" SB_CUSTOM_OUTBOUNDS="" SB_ISP_URLTEST="" \
           XRAY_OBSERVATORY_SECTION="" XRAY_BALANCERS_SECTION="" XRAY_SERVICE_RULES=""

    local env_vars; env_vars=$(env | grep "_ISP_IP=" | cut -d= -f1) || true

    # 构建所有 ISP 的服务端出站 JSON（按测速结果速度降序排列）
    if [[ -n "${HAS_ISP_NODES:-}" ]]; then
        # 按速度降序排列 ISP tags
        local sorted_tags
        sorted_tags=$(for tag in "${!ISP_SPEEDS[@]}"; do
            echo "${ISP_SPEEDS[$tag]} $tag"
        done | sort -t' ' -k1 -rn | awk '{print $2}')

        local xray_out="" sb_out=""
        for tag in $sorted_tags; do
            # 从环境变量逆向查找该 tag 对应的连接信息
            for var in $env_vars; do
                local prefix="${var%_IP}"
                local derived_tag="proxy-$(echo "${prefix}" | tr '[:upper:]_ ' '[:lower:]-')"
                if [[ "$derived_tag" == "$tag" ]]; then
                    local ip="${!var}"
                    local port_var="${prefix}_PORT" user_var="${prefix}_USER" pass_var="${prefix}_SECRET"
                    local port="${!port_var:-}" user="${!user_var:-}" pass="${!pass_var:-}"

                    # 保存最快 ISP 的连接信息（兼容旧引用）
                    if [[ "$tag" == "${FASTEST_PROXY_TAG:-}" ]]; then
                        export ISP_IP="$ip" ISP_PORT="$port" ISP_USER="$user" ISP_SECRET="$pass"
                    fi

                    # Xray SOCKS outbound
                    xray_out="${xray_out}{
  \"tag\": \"${tag}\",
  \"protocol\": \"socks\",
  \"settings\": {
    \"servers\": [
      {
        \"address\": \"${ip}\",
        \"port\": ${port},
        \"users\": [{\"user\": \"${user}\", \"pass\": \"${pass}\"}]
      }
    ]
  }
},
"
                    # Sing-box SOCKS outbound
                    sb_out="${sb_out}{
  \"type\": \"socks\",
  \"tag\": \"${tag}\",
  \"server\": \"${ip}\",
  \"server_port\": ${port},
  \"username\": \"${user}\",
  \"password\": \"${pass}\"
},
"
                    log INFO "[ISP] 注入出站: ${tag} (${ISP_SPEEDS[$tag]:-?} Mbps)"
                    break
                fi
            done
        done

        export CUSTOM_OUTBOUNDS="$xray_out"
        export SB_CUSTOM_OUTBOUNDS="$sb_out"
    fi

    # 生成 urltest / balancer / 动态服务路由规则
    build_sb_urltest
    build_xray_balancer
    build_xray_service_rules

    log INFO "[阶段 4] 完成"
}

# ==============================================================================
# §16 主入口
# ==============================================================================

main_init() {
    log INFO "────────────────────────────────────────────────"
    log INFO "  SB-Xray 启动初始化 (Startup Pipeline)"
    log INFO "────────────────────────────────────────────────"

    log INFO "[步骤 1]  初始化目录与文件"
    _init_dirs

    log INFO "[步骤 2]  解密远端密钥库"
    decryptSecretsEnv
    source "${SECRET_FILE}"

    log INFO "[步骤 3]  加载持久化状态"
    # 同时加载 ENV_FILE（UUID/端口等永久配置）和 STATUS_FILE（ISP_TAG/流媒体检测结果）
    source "${ENV_FILE}"
    source "${STATUS_FILE}"

    log INFO "[步骤 4]  基础环境变量初始化"
    analyze_base_env

    log INFO "[步骤 5]  ISP 测速与选路"
    run_speed_tests_if_needed

    log INFO "[步骤 6]  流媒体/AI 可达性检测"
    analyze_ai_routing_env

    log INFO "[步骤 7]  生成客户端/服务端配置片段"
    build_client_and_server_configs

    # 打印持久化变量摘要（DEBUG 级别）
    log DEBUG "── 持久化变量一览 ──"
    while IFS= read -r line; do log DEBUG "  ${line}"; done < "${ENV_FILE}"

    log INFO "[步骤 8]  TLS 证书申请/续签"
    issueCertificate "sb_xray_bundle" "${DOMAIN}:dns_ali|${CDNDOMAIN}:dns_cf"

    log INFO "[步骤 9]  生成 DH 参数"
    local dh_file="/etc/nginx/dhparam/dhparam.pem"
    if [ ! -f "$dh_file" ]; then
        mkdir -p "$(dirname "$dh_file")"
        openssl dhparam -dsaparam -out "$dh_file" 4096
        log INFO "[步骤 9]  DH 参数生成完成"
    else
        log DEBUG "[步骤 9]  DH 参数已存在，跳过"
    fi

    log INFO "[步骤 10] 更新 GeoIP/GeoSite 数据库"
    /scripts/geo_update.sh

    # 步骤 11: 渲染所有配置模板
    # 此步骤必须在所有变量（UUID/ISP/流媒体/密钥）就绪后执行
    # 产出: ${WORKDIR}/xray/ (priority=20), ${WORKDIR}/sing-box/ (priority=20),
    #       /etc/nginx/ (priority=25), ${WORKDIR}/dufs/ (priority=15),
    #       /etc/supervisor.d/daemon.ini (supervisord 读取)
    log INFO "[步骤 11] 渲染配置模板"
    createConfig
    generateProxyProvidersConfig

    # 步骤 12: 初始化 X-UI / S-UI 管理面板（daemon.ini priority=5，最先启动）
    log INFO "[步骤 12] 初始化 X-UI / S-UI"
    x-ui setting -username "${PUBLIC_USER}" -password "${PUBLIC_PASSWORD}" \
        -port "${XUI_LOCAL_PORT}" -webBasePath "${XUI_WEBBASEPATH}" >/dev/null
    sui setting -port "${SUI_PORT}" -subPort "${SUI_SUB_PORT}" \
        -path "/${SUI_WEBBASEPATH}" -subPath "/${SUI_SUB_PATH}" >/dev/null
    sui admin -password "${PUBLIC_PASSWORD}" -username "${PUBLIC_USER}" >/dev/null
    [ -f "${SUI_DB_FOLDER}/s-ui.db" ] && \
        sqlite3 "${SUI_DB_FOLDER}/s-ui.db" \
            "UPDATE settings SET value='https://${DOMAIN}/${SUI_SUB_PATH}/' WHERE key='subURI';"

    # 步骤 13: Nginx Basic Auth + Fail2ban（nginx priority=25，最后启动）
    log INFO "[步骤 13] 配置 Nginx Basic Auth 与 Fail2ban"
    local htpasswd_file="/etc/nginx/.htpasswd"
    if [ -n "${PUBLIC_USER:-}" ] && [ -n "${PUBLIC_PASSWORD:-}" ]; then
        local enc_pass; enc_pass=$(openssl passwd -apr1 "${PUBLIC_PASSWORD}")
        echo "${PUBLIC_USER}:${enc_pass}" > "${htpasswd_file}"
        chmod 644 "${htpasswd_file}"
        log INFO "[步骤 13] HTTP Basic Auth 已配置 (用户: ${PUBLIC_USER})"
    else
        log WARN "[步骤 13] PUBLIC_USER/PASSWORD 未设置，跳过 Basic Auth"
    fi
    fail2ban-client -x start >/dev/null 2>&1 || log WARN "[步骤 13] Fail2ban 启动失败"

    # 步骤 14: 配置 Cron 定时任务（daemon.ini priority=10）
    log INFO "[步骤 14] 配置 Cron 定时任务"
    local cron_file="/var/spool/cron/crontabs/root"
    touch "$cron_file"
    _sed_i '/geo_update.sh/d' "$cron_file"
    echo "0 3 * * * /scripts/geo_update.sh >> /var/log/geo_update.log 2>&1" >> "$cron_file"
    chmod 0600 "$cron_file"

    [[ ! -f "/usr/local/bin/show" ]] && ln -sf "/scripts/show" "/usr/local/bin/show"

    log INFO "── 配置总览 ──"
    /usr/local/bin/show

    log INFO "────────────────────────────────────────────────"
    log INFO "  ✅ 初始化完成，移交 Supervisord 接管"
    log INFO "────────────────────────────────────────────────"
}

# ==============================================================================
# 入口保护: 被 source 时（测试套件加载）跳过 exec，防止替换当前 shell
# ==============================================================================
[[ "${BASH_SOURCE[0]}" != "${0}" ]] && return 0

if [ "${1#-}" = 'supervisord' ] && [ "$(id -u)" = '0' ]; then
    main_init
    set -- "$@" -n -c /etc/supervisord.conf
fi

exec "$@"
