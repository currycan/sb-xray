#!/usr/bin/env bash
set -euo pipefail

# vps-init.sh —— sb-xray VPS 一键初始化（单脚本：置备 + 启动）
#
# 在全新 Debian/Ubuntu VPS 上运行，一次跑完全部「第一次安装」工作：系统调优 + BBR、
# sudo 用户、SSH 加固（仅公钥）、装 Docker（官方源）、写 sb-xray 运行 .env + 落 compose、
# 启动容器；回国（CN-exit）节点另装 Tailscale 入网 / keepalive / 自检护栏 / 反向探活 / 自检。
#
# 两个目录、两个工作流（解耦）：
#   ~/initial  （本脚本 + initial.env）—— 初始化，跑本脚本
#   ~/sb-xray  （.env + docker-compose.yml + 运行时脚本）—— 运行容器
# initial.env 是唯一人填输入（脚本同目录读取）；~/sb-xray/.env 由本脚本生成，只给容器用。
#
# 回国节点开关：给 OPENWRT_TS_IP（家里 OpenWrt 的 Tailscale IP）即判本机为 CN-exit 节点，
# 自动写全回国容器 env + 装 Tailscale/护栏 + 自检；不给则是通用节点，跑完即启动容器。
#
# 关键变量（全集见 initial.env.example）：
#   SBXRAY_DIR              sb-xray 运行目录（默认 ~/sb-xray，root 即 /root/sb-xray）
#   SBX_USER / SBX_USER_PASSWORD / ROOT_PASSWORD / SSH_PORT(默认 38666)
#   SSH_PUBKEY / SSH_PUBKEY_FILE（仅公钥登录必需）/ TIMEZONE
#   SBX_DOMAIN(空=hostname,须.com) / SBX_CDN_DOMAIN / SBX_CODE / SBX_COMPOSE_URL
#   INSTALL_SSRPOLIPO / SSRPOLIPO_COMPOSE_URL / BASHRC_URL / VIMRC_URL
#   INSTALL_TCP_BRUTAL / TCP_BRUTAL_URL  tcp-brutal DKMS（默认开；仅诊断位 IS_BRUTAL，Hy2 用 bbr）
#   REDEPLOY_URL            运行时更新脚本下载源（装到 /usr/local/bin 作 sbx-redeploy 命令）
#   回国（给 OPENWRT_TS_IP 触发）：
#     TS_AUTHKEY / TS_AUTHKEY_FILE / TS_HOSTNAME      Tailscale 入网（从 initial.env 读，不入 .env）
#     CN_EXIT_MODE / SBX_CANARY_ROLE(canary|worker) / REVERSE_DOMAINS / VPS_DOMAIN / SHOUTRRR_URLS
#     CANARY_URL / WATCHDOG_URL / WD_TG_TOKEN / WD_TG_CHAT
#     SKIP_PULL / SKIP_CANARY_WIRING / SKIP_WATCHDOG_WIRING
#
# 重要警告：本脚本关闭 SSH 密码登录、仅保留公钥。必须先确保 SSH_PUBKEY / SSH_PUBKEY_FILE
#   可用且能写入 authorized_keys，否则配置 SSH 后会把自己锁在机外；建议保留一个已连接会话，
#   直到用新端口 + 公钥验证能登录。
#
# 退出码：0 全部完成（回国节点自检全过）；1 任一步骤 / 自检硬失败，需修复后重跑（幂等）。
#
# 运行时更新容器：跑 sbx-redeploy 命令（重拉 compose + 重建容器 + 清理）。

log() {
    printf '[sbx-init] %s\n' "$*"
}

warn() {
    printf '[sbx-init] WARN: %s\n' "$*" >&2
}

die() {
    printf '[sbx-init] %s\n' "$*" >&2
    exit 1
}

# with_timeout <秒> <cmd...>：有 timeout 就限时跑，避免 tailscale up 等命令挂死阻塞
with_timeout() {
    local _t=$1; shift
    if command -v timeout >/dev/null 2>&1; then timeout "$_t" "$@"; else "$@"; fi
}

usage() {
    cat <<'USAGE'
用法: sudo ./vps-init.sh [-h|--help]

sb-xray VPS 一键初始化（Debian/Ubuntu），单脚本跑完置备 + 启动。幂等可重跑：
系统调优 + BBR、建 sudo 用户、SSH 加固（仅公钥）、装 Docker（官方源）、写 sb-xray
运行 .env + 落 compose、启动容器；回国节点另装 Tailscale/keepalive/护栏/反向探活并自检。

约定：本脚本 + initial.env 放 ~/initial（init 工作流）；容器跑在 ~/sb-xray（运行工作流）。
配置来源：同目录 initial.env（存在则 source；文件为准，缺项可用环境变量补）。
  模板见 initial.env.example；真实 initial.env 含凭据、不入库。

主要变量（全集见 initial.env.example 与本脚本头部注释）：
  系统/SSH  SBX_USER / SBX_USER_PASSWORD / ROOT_PASSWORD / SSH_PORT(默认 38666)
            SSH_PUBKEY / SSH_PUBKEY_FILE（仅公钥登录必需）/ TIMEZONE
  sb-xray   SBX_DOMAIN(空=hostname,须 .com FQDN) / SBX_CDN_DOMAIN / SBX_CODE
            SBX_COMPOSE_URL / INSTALL_SSRPOLIPO / INSTALL_TCP_BRUTAL(默认开)
  CN-exit   给 OPENWRT_TS_IP 即判本机为回国节点，自动写全回国容器 env + 装 Tailscale/护栏：
            TS_AUTHKEY / TS_AUTHKEY_FILE / TS_HOSTNAME（入网，从 initial.env 读，不入 .env）
            CN_EXIT_MODE / SBX_CANARY_ROLE(canary|worker) / REVERSE_DOMAINS / VPS_DOMAIN
            / SHOUTRRR_URLS / WD_TG_TOKEN / WD_TG_CHAT / SKIP_PULL / SKIP_*_WIRING

运行时更新容器：跑 sbx-redeploy 命令（本脚本已装到 /usr/local/bin）。

⚠️ 本脚本关闭 SSH 密码登录、仅留公钥。务必先确保 SSH_PUBKEY / SSH_PUBKEY_FILE 可用，
   否则会把自己锁在机外；建议保留一个已连接会话直到用新端口 + 公钥验证能登录。
USAGE
}

is_valid_public_key_line() {
    local key_line key_type key_body rest

    key_line="$1"
    read -r key_type key_body rest <<< "$key_line"
    case "$key_type" in
        ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256|ecdsa-sha2-nistp384|ecdsa-sha2-nistp521|sk-ssh-ed25519@openssh.com|sk-ecdsa-sha2-nistp256@openssh.com)
            ;;
        *)
            return 1
            ;;
    esac
    [ -n "${key_body:-}" ]
}

authorized_keys_has_valid_key() {
    # 用 grep 而非多行 awk：Debian/Ubuntu 默认 mawk 不允许 '(' 后换行续行，
    # 多行 awk 条件会在 mawk 下语法报错。grep -E 与 awk 实现无关，稳。
    grep -Eq \
        '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256|ecdsa-sha2-nistp384|ecdsa-sha2-nistp521|sk-ssh-ed25519@openssh\.com|sk-ecdsa-sha2-nistp256@openssh\.com)[[:space:]]+[A-Za-z0-9+/]' \
        "$1"
}

collect_pubkeys() {
    # 汇总所有有效公钥行（内联 SSH_PUBKEY + SSH_PUBKEY_FILE 的每一行），逐行过滤、去重。
    # 支持多公钥：多台管理机 / 密钥轮换都靠这里把整份 .pub 或 authorized_keys 文件装上。
    {
        if [ -n "$SSH_PUBKEY" ]; then
            printf '%s\n' "$SSH_PUBKEY"
        fi
        if [ -n "$SSH_PUBKEY_FILE" ] && [ -f "$SSH_PUBKEY_FILE" ]; then
            sed 's/\r$//' "$SSH_PUBKEY_FILE"
        fi
    } | while IFS= read -r line; do
        if is_valid_public_key_line "$line"; then
            printf '%s\n' "$line"
        fi
    done | awk 'NF && !seen[$0]++ { print }'
}

install_keys_to() {
    local file_path owner_name group_name keys tmp_file

    file_path="$1"
    owner_name="$2"
    group_name="$3"
    keys="$4"
    tmp_file="$(mktemp)"

    # 合并现有 + 新公钥后去重：可重复运行不产生重复行（幂等的累加）。
    {
        if [ -f "$file_path" ]; then
            cat "$file_path"
        fi
        printf '%s\n' "$keys"
    } | awk 'NF && !seen[$0]++ { print }' > "$tmp_file"

    install -m 600 -o "$owner_name" -g "$group_name" "$tmp_file" "$file_path"
    rm -f "$tmp_file"

    if ! authorized_keys_has_valid_key "$file_path"; then
        die "authorized_keys 未包含有效公钥: $file_path"
    fi
}

require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        die "必须以 root 运行"
    fi
}

load_config() {
    local script_dir config_file

    script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
    config_file="$script_dir/initial.env"
    if [ -f "$config_file" ]; then
        # shellcheck source=/dev/null
        source "$config_file"
    fi

    SBXRAY_DIR="${SBXRAY_DIR:-/root/sb-xray}"
    # 运维脚本集中目录：sbx-redeploy / sbx-canary-check / cn-exit-watchdog 与既有
    # sbx-update 同住此处,装成一等命令（去 .sh 后缀）。~/sb-xray 只留 compose 项目。
    SBX_BIN_DIR="${SBX_BIN_DIR:-/usr/local/bin}"
    SBX_USER="${SBX_USER:-sbx}"
    SBX_USER_PASSWORD="${SBX_USER_PASSWORD:-}"
    ROOT_PASSWORD="${ROOT_PASSWORD:-}"
    SSH_PORT="${SSH_PORT:-38666}"
    SSH_PUBKEY="${SSH_PUBKEY:-}"
    SSH_PUBKEY_FILE="${SSH_PUBKEY_FILE:-}"
    TIMEZONE="${TIMEZONE:-Asia/Shanghai}"
    SBX_DOMAIN="${SBX_DOMAIN:-}"
    SBX_CDN_DOMAIN="${SBX_CDN_DOMAIN:-}"
    SBX_CODE="${SBX_CODE:-}"
    SBX_COMPOSE_URL="${SBX_COMPOSE_URL:-https://raw.githubusercontent.com/currycan/sb-xray/main/docker-compose.yml}"
    INSTALL_SSRPOLIPO="${INSTALL_SSRPOLIPO:-1}"
    SSRPOLIPO_COMPOSE_URL="${SSRPOLIPO_COMPOSE_URL:-}"
    BASHRC_URL="${BASHRC_URL:-}"
    VIMRC_URL="${VIMRC_URL:-}"
    INSTALL_TCP_BRUTAL="${INSTALL_TCP_BRUTAL:-1}"
    TCP_BRUTAL_URL="${TCP_BRUTAL_URL:-https://tcp.hy2.sh/}"
    # CN-exit 回国项：在启动容器前一次写全容器 .env，消除「.env 半完整时容器先 up
    # → 空 tsip 降级 off → watchtower 固化」的窗口。仅在本节点是 CN-exit 节点
    # （OPENWRT_TS_IP 非空）时写入，通用节点跳过。
    OPENWRT_TS_IP="${OPENWRT_TS_IP:-}"
    CN_EXIT_MODE="${CN_EXIT_MODE:-balance}"
    SBX_CANARY_ROLE="${SBX_CANARY_ROLE:-worker}"
    REVERSE_DOMAINS="${REVERSE_DOMAINS:-}"
    SHOUTRRR_URLS="${SHOUTRRR_URLS:-}"
    VPS_DOMAIN="${VPS_DOMAIN:-}"
    # 回国（CN-exit）入网 + 护栏参数（给 OPENWRT_TS_IP 即触发；见 setup_cn_exit）。
    # 这些是 init 侧运行时参数，从 initial.env 读，不写进容器 .env。
    TS_AUTHKEY="${TS_AUTHKEY:-}"
    TS_AUTHKEY_FILE="${TS_AUTHKEY_FILE:-}"
    TS_HOSTNAME="${TS_HOSTNAME:-}"
    CANARY_URL="${CANARY_URL:-https://raw.githubusercontent.com/currycan/sb-xray/main/sources/vps/sbx-canary-check.sh}"
    WATCHDOG_URL="${WATCHDOG_URL:-https://raw.githubusercontent.com/currycan/sb-xray/main/sources/vps/cn-exit-watchdog.sh}"
    WD_TG_TOKEN="${WD_TG_TOKEN:-}"
    WD_TG_CHAT="${WD_TG_CHAT:-}"
    SKIP_CANARY_WIRING="${SKIP_CANARY_WIRING:-0}"
    SKIP_WATCHDOG_WIRING="${SKIP_WATCHDOG_WIRING:-0}"
    SKIP_PULL="${SKIP_PULL:-0}"
    # 运行时更新脚本的下载源（装到 $SBX_BIN_DIR 作 sbx-redeploy 命令，供日后更新容器）。
    REDEPLOY_URL="${REDEPLOY_URL:-https://raw.githubusercontent.com/currycan/sb-xray/main/sources/vps/sbx-redeploy.sh}"
    ENV_FILE="${SBXRAY_DIR}/.env"

}

detect_os() {
    if [ ! -f /etc/os-release ]; then
        die "缺少 /etc/os-release，无法识别系统"
    fi

    # shellcheck source=/dev/null
    source /etc/os-release
    case "${ID:-}" in
        debian|ubuntu)
            OS_ID="$ID"
            OS_CODENAME="${VERSION_CODENAME:-}"
            export OS_ID OS_CODENAME
            ;;
        *)
            die "仅支持 Debian/Ubuntu，当前系统: ${ID:-unknown}"
            ;;
    esac

    if [ -z "$OS_CODENAME" ]; then
        die "未检测到 VERSION_CODENAME，无法配置 Docker 官方仓库"
    fi
}

ensure_prereqs() {
    # 保证后续所有下载步骤可用 curl（install_docker 跳过时也不至于缺 curl）。
    # 基础工具集 + 排障常用：ca-certificates/curl 是后续所有下载步骤的前提；
    # 其余为日常运维/排障常备（vim/jq/tree/net-tools/telnet/lsof/python3 等）。
    # apt-get install 幂等：已装即跳过。linux-headers-generic 供 DKMS（tcp-brutal）。
    log "安装基础工具集（vim/curl/jq/tree/net-tools/python3/git 等）"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y \
        vim ca-certificates curl lsof bash-completion tree net-tools telnet \
        python3 python3-pip psmisc git jq linux-headers-generic
}

set_timezone() {
    local current_timezone

    if ! command -v timedatectl >/dev/null 2>&1; then
        warn "timedatectl 不可用，跳过时区设置"
        return
    fi

    current_timezone="$(timedatectl show --property=Timezone --value 2>/dev/null || true)"
    if [ "$current_timezone" != "$TIMEZONE" ]; then
        log "设置时区"
        timedatectl set-timezone "$TIMEZONE" >/dev/null 2>&1 || true
    fi
}

tune_sysctl() {
    log "写入 sysctl 优化"
    install -d -m 755 /etc/sysctl.d
    cat > /etc/sysctl.d/99-sbx.conf <<'EOF'
# sb-xray VPS 基础网络优化 + BBR（vps-init.sh 生成，每次全量重写）
fs.file-max = 1000000
fs.inotify.max_user_instances = 8192
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
net.ipv4.tcp_syncookies = 1
net.ipv4.tcp_fin_timeout = 30
net.ipv4.tcp_tw_reuse = 1
net.ipv4.ip_local_port_range = 1024 65000
net.ipv4.tcp_max_syn_backlog = 16384
net.ipv4.tcp_max_tw_buckets = 6000
net.ipv4.route.gc_timeout = 100
net.ipv4.tcp_syn_retries = 1
net.ipv4.tcp_synack_retries = 1
net.core.somaxconn = 32768
net.core.netdev_max_backlog = 32768
net.ipv4.tcp_timestamps = 1
net.ipv4.tcp_max_orphans = 32768
EOF
    chmod 644 /etc/sysctl.d/99-sbx.conf
    # best-effort：OpenVZ/LXC 等容器化 VPS 上部分 key 不可写会返回非 0，
    # 不应因此让整个初始化在此硬死（drop-in 已落盘，重启后由内核加载）。
    sysctl --system >/dev/null 2>&1 || warn "部分 sysctl 未即时生效（内核/虚拟化限制），已写入 drop-in"
}

verify_bbr() {
    # 检查 BBR 是否真生效，报告当前「模式」（拥塞控制算法 + 队列算法）。
    # 仅信息性：写了 drop-in 不等于内核已加载 bbr（不支持/需重启时不生效）。不 die。
    local cc qdisc avail
    cc="$(sysctl -n net.ipv4.tcp_congestion_control 2>/dev/null || echo unknown)"
    qdisc="$(sysctl -n net.core.default_qdisc 2>/dev/null || echo unknown)"
    avail="$(sysctl -n net.ipv4.tcp_available_congestion_control 2>/dev/null || echo '')"

    if [ "$cc" = "bbr" ]; then
        log "BBR 已开启：拥塞控制=$cc 队列=$qdisc"
    else
        warn "BBR 未生效：当前拥塞控制=$cc 队列=$qdisc（可用：${avail:-未知}）。内核可能不支持 bbr 或需重启加载。"
    fi
}

tune_limits() {
    log "写入 limits 配置"
    install -d -m 755 /etc/security/limits.d
    cat > /etc/security/limits.d/99-sbx.conf <<'EOF'
*       soft    nofile  1000000
*       hard    nofile  1000000
root    soft    nofile  1000000
root    hard    nofile  1000000
EOF
    chmod 644 /etc/security/limits.d/99-sbx.conf
}

tune_profile() {
    log "写入 profile 配置"
    install -d -m 755 /etc/profile.d
    cat > /etc/profile.d/99-sbx.sh <<'EOF'
ulimit -SHn 1000000 2>/dev/null || true
export PS1='\u@\h:\w\$ '
alias show='docker exec -it sb-xray show'
alias logs='docker logs -f sb-xray'
alias restart='docker restart sb-xray'
EOF
    chmod 644 /etc/profile.d/99-sbx.sh
}

ensure_user() {
    log "确保 sudo 用户存在"
    if ! getent group "$SBX_USER" >/dev/null 2>&1; then
        groupadd "$SBX_USER"
    fi
    if ! id -u "$SBX_USER" >/dev/null 2>&1; then
        useradd -m -g "$SBX_USER" -s /bin/bash "$SBX_USER"
    fi

    if [ -n "$SBX_USER_PASSWORD" ]; then
        log "设置 sudo 用户密码（隐藏）"
        printf '%s:%s\n' "$SBX_USER" "$SBX_USER_PASSWORD" | chpasswd
    fi
    if [ -n "$ROOT_PASSWORD" ]; then
        log "设置 root 密码（隐藏）"
        printf 'root:%s\n' "$ROOT_PASSWORD" | chpasswd
    fi
}

install_ssh_key() {
    local passwd_entry user_home valid_keys key_count

    valid_keys="$(collect_pubkeys)"
    if [ -z "$valid_keys" ]; then
        die "未提供有效公钥（SSH_PUBKEY / SSH_PUBKEY_FILE 均无有效项），拒绝启用仅公钥登录"
    fi

    passwd_entry="$(getent passwd "$SBX_USER" || true)"
    if [ -z "$passwd_entry" ]; then
        die "未找到用户信息: $SBX_USER"
    fi
    user_home="$(printf '%s\n' "$passwd_entry" | cut -d: -f6)"
    if [ -z "$user_home" ]; then
        die "未解析到用户家目录: $SBX_USER"
    fi

    key_count="$(printf '%s\n' "$valid_keys" | grep -c .)"
    log "安装 SSH 公钥（$key_count 个）"
    install -d -m 700 -o "$SBX_USER" -g "$SBX_USER" "$user_home/.ssh"
    install -d -m 700 -o root -g root /root/.ssh

    install_keys_to "$user_home/.ssh/authorized_keys" "$SBX_USER" "$SBX_USER" "$valid_keys"
    install_keys_to "/root/.ssh/authorized_keys" root root "$valid_keys"
}

configure_ssh() {
    local base_config include_line tmp_file drop_in sshd_bin effective_pw

    base_config="/etc/ssh/sshd_config"
    include_line='Include /etc/ssh/sshd_config.d/*.conf'
    drop_in="/etc/ssh/sshd_config.d/99-sbx.conf"

    if [ ! -f "$base_config" ]; then
        die "未找到 SSH 主配置: $base_config"
    fi

    # 缺 Include 时【前置】插入（不是追加到尾）：sshd 多数指令首次匹配生效，
    # Include 放在最前才能让 drop-in 的 PasswordAuthentication no 等压过 base 里
    # 可能存在的早出现的 PasswordAuthentication yes（追加到尾会被 base 抢先生效）。
    if ! grep -Fxq "$include_line" "$base_config"; then
        tmp_file="$(mktemp)"
        {
            printf '%s\n' "$include_line"
            cat "$base_config"
        } > "$tmp_file"
        cat "$tmp_file" > "$base_config"
        rm -f "$tmp_file"
    fi

    install -d -m 755 /etc/ssh/sshd_config.d
    cat > "$drop_in" <<EOF
Port $SSH_PORT
PubkeyAuthentication yes
PasswordAuthentication no
PermitRootLogin prohibit-password
ClientAliveInterval 60
ClientAliveCountMax 86400
EOF
    chmod 644 "$drop_in"

    sshd_bin="$(command -v sshd || true)"
    if [ -z "$sshd_bin" ] && [ -x /usr/sbin/sshd ]; then
        sshd_bin=/usr/sbin/sshd
    fi
    if [ -z "$sshd_bin" ]; then
        rm -f "$drop_in"
        die "未找到 sshd，无法校验配置"
    fi

    if ! "$sshd_bin" -t; then
        rm -f "$drop_in"
        die "sshd 配置校验失败，已删除 $drop_in"
    fi

    # 断言密码登录确已关闭：sshd -T 导出生效配置，若 passwordauthentication 仍为 yes，
    # 说明 base 配置抢先生效（Include 前置未奏效或被其他 drop-in 覆盖）——回滚后中止，
    # 避免「以为加固了、其实密码登录还开着」的静默削弱。sshd -T 跑不起来（如无 host
    # key）则拿不到值，跳过断言（语法已由 -t 校验），不阻断 keepalive。
    effective_pw="$("$sshd_bin" -T 2>/dev/null | awk '$1=="passwordauthentication"{print $2}')"
    if [ "$effective_pw" = "yes" ]; then
        rm -f "$drop_in"
        die "密码登录仍为开启（base $base_config 抢先生效）——已回滚 $drop_in，请检查主配置中靠前的 PasswordAuthentication"
    fi

    systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
}

configure_sudoers() {
    local sudoers_file visudo_bin

    sudoers_file="/etc/sudoers.d/sbx-$SBX_USER"
    install -d -m 755 /etc/sudoers.d
    cat > "$sudoers_file" <<EOF
%$SBX_USER ALL=(ALL:ALL) NOPASSWD: ALL
EOF
    chmod 0440 "$sudoers_file"

    visudo_bin="$(command -v visudo || true)"
    if [ -z "$visudo_bin" ] && [ -x /usr/sbin/visudo ]; then
        visudo_bin=/usr/sbin/visudo
    fi
    if [ -z "$visudo_bin" ]; then
        rm -f "$sudoers_file"
        die "未找到 visudo，无法校验 sudoers"
    fi

    if ! "$visudo_bin" -cf "$sudoers_file" >/dev/null; then
        rm -f "$sudoers_file"
        die "sudoers 校验失败，已删除 $sudoers_file"
    fi
}

install_docker() {
    if command -v docker >/dev/null 2>&1; then
        log "docker 已安装，跳过"
        return
    fi

    log "安装 Docker 官方仓库版本"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y ca-certificates curl
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL "https://download.docker.com/linux/$OS_ID/gpg" -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$OS_ID $OS_CODENAME stable" > /etc/apt/sources.list.d/docker.list
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable --now docker >/dev/null 2>&1
}

upsert_env() {
    _k=$1; _v=$2
    grep -v "^${_k}=" "$ENV_FILE" > "$ENV_FILE.tmp" 2>/dev/null || true
    mv "$ENV_FILE.tmp" "$ENV_FILE"
    printf '%s=%s\n' "$_k" "$_v" >> "$ENV_FILE"
}

resolve_domains() {
    # 域名按节点从 hostname 派生（要求 hostname 是 .com FQDN）：
    #   SBX_DOMAIN     = hostname（不含 .com → die，提示 hostname 未正确配置）
    #   SBX_CDN_DOMAIN = hostname 把 .com 换成 .top
    # 二者若在 initial.env 显式给值则以显式值为准（跳过派生）。
    local hn
    hn="$(hostname)"

    if [ -z "$SBX_DOMAIN" ]; then
        case "$hn" in
            *.com*) SBX_DOMAIN="$hn" ;;
            *) die "hostname '$hn' 不含 .com —— hostname 未正确配置（应为 .com FQDN，如 dc99.example.com）" ;;
        esac
    fi

    if [ -z "$SBX_CDN_DOMAIN" ]; then
        case "$hn" in
            *.com*) SBX_CDN_DOMAIN="${hn/.com/.top}" ;;
            *) die "hostname '$hn' 不含 .com —— 无法派生 cdndomain（请正确配置 .com hostname，或显式设 SBX_CDN_DOMAIN）" ;;
        esac
    fi

    log "域名：domain=$SBX_DOMAIN cdndomain=$SBX_CDN_DOMAIN"
}

deploy_sbx() {
    local compose_file

    log "准备 sb-xray 目录"
    mkdir -p "$SBXRAY_DIR"
    touch "$ENV_FILE"

    upsert_env domain "$SBX_DOMAIN"
    upsert_env cdndomain "$SBX_CDN_DOMAIN"
    upsert_env code "$SBX_CODE"

    # CN-exit 节点：初始化即写全回国 env（OPENWRT_TS_IP 是 CN-exit 节点的标志输入）。
    # 非 CN-exit 节点（OPENWRT_TS_IP 空）跳过，compose 默认值生效，保持通用。
    if [ -n "$OPENWRT_TS_IP" ]; then
        log "CN-exit 节点：写全回国 env（mode=$CN_EXIT_MODE role=$SBX_CANARY_ROLE）"
        upsert_env CN_EXIT_MODE "$CN_EXIT_MODE"
        upsert_env ENABLE_REVERSE true
        upsert_env ENABLE_SOCKS5_PROXY true
        upsert_env tsip "$OPENWRT_TS_IP"          # docker-compose: CN_EXIT_SOCKS5_HOST=${tsip}
        # 注：.env 只放容器变量；Tailscale 入网参数（TS_AUTHKEY/TS_HOSTNAME）属 init 侧，
        # 由 setup_cn_exit 直接从 initial.env 读，不写进容器 .env。
        [ -n "$REVERSE_DOMAINS" ] && upsert_env REVERSE_DOMAINS "$REVERSE_DOMAINS"
        [ -n "$SHOUTRRR_URLS" ]   && upsert_env shoutrrr_urls "$SHOUTRRR_URLS"
        [ -n "$VPS_DOMAIN" ]      && upsert_env domain "$VPS_DOMAIN"   # 覆盖 hostname 派生值（可选）
        # 角色派生 WATCHTOWER_SCHEDULE：canary 提前 1h（北京 03:00）留人工叫停窗口；
        # worker 不写，走 compose 默认 04:00。错峰逻辑见 setup_cn_exit 自检 cron。
        if [ "$SBX_CANARY_ROLE" = "canary" ]; then
            upsert_env WATCHTOWER_SCHEDULE "0 0 3 * * *"
        else
            # 幂等：worker 显式删除可能残留的旧行，避免 canary→worker 改角色后固化 03:00。
            grep -v '^WATCHTOWER_SCHEDULE=' "$ENV_FILE" > "$ENV_FILE.tmp" 2>/dev/null && mv "$ENV_FILE.tmp" "$ENV_FILE" || true
        fi
    fi
    chmod 600 "$ENV_FILE"

    compose_file="$SBXRAY_DIR/docker-compose.yml"
    if [ ! -f "$compose_file" ]; then
        log "下载 sb-xray docker-compose.yml"
        curl -fsSL "$SBX_COMPOSE_URL" -o "$compose_file"
        if [ ! -s "$compose_file" ]; then
            rm -f "$compose_file"
            die "下载 docker-compose.yml 失败"
        fi
    fi
}

install_redeploy_helper() {
    # 把运行时更新脚本装成 $SBX_BIN_DIR/sbx-redeploy 命令（所有节点；日后更新容器用）。
    # 下载→校验 shebang→替换；失败仅 warn（有本地副本则保留）。
    local f="$SBX_BIN_DIR/sbx-redeploy"
    if curl -fsSL "$REDEPLOY_URL" -o "$f.new" && [ -s "$f.new" ] && head -1 "$f.new" | grep -q '#!'; then
        mv "$f.new" "$f"; chmod 755 "$f"
        log "运行时更新脚本已就位 → $f"
        rm -f "$SBXRAY_DIR/sbx-redeploy.sh"  # 迁移：清掉旧位置残留副本（旧节点重跑即净）
    elif [ -f "$f" ]; then
        rm -f "$f.new"; warn "sbx-redeploy 下载失败,保留现有 $f"
    else
        rm -f "$f.new"; warn "sbx-redeploy 下载失败且本地缺失($REDEPLOY_URL)"
    fi
}

setup_cn_exit() {
    # 回国（CN-exit）节点专属（由 main 在 OPENWRT_TS_IP 非空时调用）：装 Tailscale
    # 入网 + keepalive cron + watchtower 自检护栏 + 反向探活。参数来自 initial.env。
    local _tsbin _canary _wd _check_min

    # authkey 可从文件读（避免明文进进程表/历史）
    if [ -z "$TS_AUTHKEY" ] && [ -n "$TS_AUTHKEY_FILE" ] && [ -f "$TS_AUTHKEY_FILE" ]; then
        TS_AUTHKEY=$(tr -d ' \t\r\n' < "$TS_AUTHKEY_FILE")
    fi
    TS_HOSTNAME="${TS_HOSTNAME:-$(hostname)}"
    case "$OPENWRT_TS_IP" in
        100.*) : ;;
        *) warn "OPENWRT_TS_IP=$OPENWRT_TS_IP 不像 Tailscale IP（应为 100.x 段）" ;;
    esac

    # 1. 安装 Tailscale 并入网（socks5 腿命脉）
    if ! command -v tailscale >/dev/null 2>&1; then
        [ -n "$TS_AUTHKEY" ] || die "未装 tailscale 且无 TS_AUTHKEY —— 在 initial.env 配 TS_AUTHKEY 后重跑（或 inline 传入）"
        log "安装 Tailscale..."
        curl -fsSL https://tailscale.com/install.sh | sh || die "Tailscale 安装失败"
    fi
    if [ -n "$TS_AUTHKEY" ]; then
        log "tailscale up（hostname=$TS_HOSTNAME）"
        # --accept-dns=false：VPS 不需要 MagicDNS，避免改写本机 DNS 影响容器
        # with_timeout：authkey 失效时 tailscale up 会退回交互式登录而挂死，限时兜底
        with_timeout 40 tailscale up --authkey="$TS_AUTHKEY" --hostname="$TS_HOSTNAME" --accept-dns=false \
            || warn "tailscale up 未成功（authkey 失效 / 超时？），自检会反映为 Tailscale 未在网"
    else
        log "未提供 TS_AUTHKEY，假定 tailscale 已在网，跳过 up"
    fi

    # 2. VPS 侧 keepalive（辅助；主力在 OpenWrt 侧）
    log "安装 keepalive cron（ping OpenWrt $OPENWRT_TS_IP）"
    _tsbin=$(command -v tailscale || echo /usr/bin/tailscale)
    cat > /etc/cron.d/cn-exit-keepalive <<EOF
# sb-xray VPS 侧 Tailscale 链路保活（vps-init.sh 生成）
* * * * * root $_tsbin ping -c 1 --timeout 5s $OPENWRT_TS_IP >/dev/null 2>&1
EOF
    chmod 644 /etc/cron.d/cn-exit-keepalive

    # 3. watchtower 自检护栏：sbx-update（手动 run-once）+ canary 自检定时器
    if [ "$SKIP_CANARY_WIRING" = "1" ]; then
        log "跳过 watchtower 自检护栏（SKIP_CANARY_WIRING=1）"
    else
        log "安装 sbx-update helper → /usr/local/bin/sbx-update"
        cat > /usr/local/bin/sbx-update <<'EOF'
#!/bin/sh
# sbx-update —— 立即检查并更新本台 sb-xray 镜像（watchtower run-once，幂等）
exec docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
    docker.io/nickfedor/watchtower:latest --run-once sb-xray "$@"
EOF
        chmod 755 /usr/local/bin/sbx-update

        _canary="$SBX_BIN_DIR/sbx-canary-check"
        if curl -fsSL "$CANARY_URL" -o "$_canary.new" && [ -s "$_canary.new" ] && head -1 "$_canary.new" | grep -q '#!/bin/sh'; then
            mv "$_canary.new" "$_canary"; chmod 755 "$_canary"
            log "  sbx-canary-check 已就位 ← $CANARY_URL"
            rm -f "$SBXRAY_DIR/sbx-canary-check.sh"  # 迁移：清旧位置残留副本
        elif [ -f "$_canary" ]; then
            rm -f "$_canary.new"; warn "  canary-check 下载失败，保留现有 $_canary"
        else
            rm -f "$_canary.new"; warn "  canary-check 下载失败且本地缺失，自检护栏未装（手动放置后重跑）"
        fi

        # 角色只决定自检 cron 运行时段（WATCHTOWER_SCHEDULE 已由 deploy_sbx 按角色写 .env）
        if [ "$SBX_CANARY_ROLE" = "canary" ]; then
            _check_min="5 3"; log "  角色=canary：watchtower 03:00 先行，自检 03:05"
        else
            _check_min="5 4"; log "  角色=worker：watchtower 04:00（compose 默认），自检 04:05"
        fi
        if [ -f "$_canary" ]; then
            cat > /etc/cron.d/sbx-canary-check <<EOF
# sb-xray 自动更新后业务自检（vps-init.sh 生成；角色=$SBX_CANARY_ROLE）
$_check_min * * * root SBXRAY_DIR=$SBXRAY_DIR SBX_CANARY_ROLE=$SBX_CANARY_ROLE SBX_DIGEST_STATE=$SBXRAY_DIR/.sbx-canary-last-digest $_canary >/dev/null 2>&1
EOF
            chmod 644 /etc/cron.d/sbx-canary-check
            log "  自检 cron 已装：/etc/cron.d/sbx-canary-check（$_check_min 时段）"
        fi
    fi

    # 4. CN 出口整机宕机反向探活（WD_TG_TOKEN+WD_TG_CHAT 同时有值才装）
    if [ "$SKIP_WATCHDOG_WIRING" = "1" ]; then
        log "跳过 CN 出口反向探活（SKIP_WATCHDOG_WIRING=1）"
    elif [ -n "$WD_TG_TOKEN" ] && [ -n "$WD_TG_CHAT" ]; then
        _wd="$SBX_BIN_DIR/cn-exit-watchdog"
        if curl -fsSL "$WATCHDOG_URL" -o "$_wd.new" && [ -s "$_wd.new" ] && head -1 "$_wd.new" | grep -q '#!/bin/sh'; then
            mv "$_wd.new" "$_wd"; chmod 755 "$_wd"
            log "cn-exit-watchdog 已就位 ← $WATCHDOG_URL"
            rm -f "$SBXRAY_DIR/cn-exit-watchdog.sh"  # 迁移：清旧位置残留副本
        elif [ -f "$_wd" ]; then
            rm -f "$_wd.new"; warn "watchdog 下载失败，保留现有 $_wd"
        else
            rm -f "$_wd.new"; warn "watchdog 下载失败且本地缺失，反向探活未装（手动放置后重跑）"
        fi
        if [ -f "$_wd" ]; then
            printf 'WD_TG_TOKEN=%s\nWD_TG_CHAT=%s\n' "$WD_TG_TOKEN" "$WD_TG_CHAT" > /etc/cn-exit-watchdog.conf
            chmod 600 /etc/cn-exit-watchdog.conf
            cat > /etc/cron.d/cn-exit-watchdog <<EOF
# sb-xray CN 出口整机宕机反向探活（vps-init.sh 生成）
* * * * * root OPENWRT_TS_IP=$OPENWRT_TS_IP $_wd >/dev/null 2>&1
EOF
            chmod 644 /etc/cron.d/cn-exit-watchdog
            # 清理早期手装的 user-crontab 条目（统一走 cron.d，避免双跑）
            if crontab -l 2>/dev/null | grep -q cn-exit-watchdog; then
                crontab -l 2>/dev/null | grep -v cn-exit-watchdog | crontab -
                log "已迁移旧 user-crontab 条目 → /etc/cron.d/cn-exit-watchdog"
            fi
            log "反向探活已装：/etc/cron.d/cn-exit-watchdog（手跑 $_wd --test 验证通道）"
        fi
    else
        log "未传 WD_TG_TOKEN/WD_TG_CHAT，跳过 CN 出口反向探活（可选护栏）"
    fi
}

start_sbx() {
    # 所有节点：拉运行时更新脚本 + 启动容器。
    install_redeploy_helper
    cd "$SBXRAY_DIR"
    if [ "$SKIP_PULL" = "1" ]; then
        log "docker compose up -d（SKIP_PULL=1，不升级镜像）"
        if docker compose version >/dev/null 2>&1; then docker compose up -d; else docker-compose up -d; fi
    else
        log "docker compose pull + up -d（顺带升级到最新镜像）"
        if docker compose version >/dev/null 2>&1; then
            docker compose pull && docker compose up -d
        else
            docker-compose pull && docker-compose up -d
        fi
    fi
}

self_check() {
    # 回国节点自检：容器/env/tailscale 硬失败计入 _fails → die；ping/socks5 软告警。
    local _fails=0 _pinged _i _sp _cnip
    log "── 自检 ──"
    sleep 5

    if docker ps --filter name=sb-xray --format '{{.Status}}' | grep -qiE 'up|healthy'; then
        log "  [ OK ] sb-xray 容器运行中"
    else
        warn "  [FAIL] sb-xray 容器未运行，检查 docker compose logs"; _fails=$((_fails+1))
    fi

    if docker exec sb-xray sh -c 'env | grep -q "CN_EXIT_MODE='"$CN_EXIT_MODE"'"' 2>/dev/null; then
        log "  [ OK ] 容器内 CN_EXIT_MODE=$CN_EXIT_MODE 生效"
    else
        warn "  [FAIL] 容器内 CN_EXIT_MODE 未生效（compose 未含引用？docker compose up -d --force-recreate）"; _fails=$((_fails+1))
    fi

    if tailscale status >/dev/null 2>&1 && ! tailscale status 2>/dev/null | grep -qiE 'Logged out|stopped'; then
        log "  [ OK ] Tailscale 在网"
        _pinged=0; _i=1
        while [ "$_i" -le 3 ]; do
            if tailscale ping -c 1 --timeout 6s "$OPENWRT_TS_IP" >/dev/null 2>&1; then _pinged=1; break; fi
            _i=$((_i+1)); sleep 3
        done
        if [ "$_pinged" = 1 ]; then
            log "  [ OK ] 到 OpenWrt $OPENWRT_TS_IP 的 Tailscale 链路通"
        else
            warn "  [warn] 暂时 ping 不通 OpenWrt（打洞/DERP 预热中，keepalive 会自愈，非硬失败）"
        fi
        if command -v curl >/dev/null 2>&1; then
            _sp="${CN_EXIT_SOCKS5_PORT:-7891}"
            _cnip=$(with_timeout 15 curl -x "socks5h://$OPENWRT_TS_IP:$_sp" -s -m 12 http://cip.cc 2>/dev/null \
                    | grep -iE '^(IP|地址)' | head -1 | tr -s ' \t' ' ') || _cnip=""
            if [ -n "$_cnip" ]; then
                log "  [ OK ] socks5 腿回国实测：$_cnip"
            else
                warn "  [warn] socks5 腿暂未探到回国出口（OpenClash :$_sp 未就绪/预热中，非硬失败）"
            fi
        fi
    else
        warn "  [FAIL] Tailscale 未在网（authkey 失效？socks5 腿不可用）"; _fails=$((_fails+1))
    fi

    if [ "$_fails" -gt 0 ]; then
        die "自检 $_fails 项硬失败 —— 本节点未就绪，请按上面 [FAIL] 排查"
    fi
}

setup_dotfiles() {
    # 可选：从 env 给定 URL 拉取 .bashrc/.vimrc 到 root 与 sudo 用户家目录。
    # URL 留空则跳过——不在入库脚本里硬编码任何个人 repo（保持可移植，符合项目约定）。
    # 真实 URL 写进 gitignored 的 initial.env。curl -o 覆盖即幂等。
    local user_home
    if [ -z "$BASHRC_URL" ] && [ -z "$VIMRC_URL" ]; then
        return
    fi
    user_home="$(getent passwd "$SBX_USER" | cut -d: -f6 || true)"

    if [ -n "$BASHRC_URL" ]; then
        log "拉取 .bashrc"
        curl -fsSL "$BASHRC_URL" -o /root/.bashrc
        if [ -n "$user_home" ]; then
            curl -fsSL "$BASHRC_URL" -o "$user_home/.bashrc"
            chown "$SBX_USER:$SBX_USER" "$user_home/.bashrc"
        fi
    fi
    if [ -n "$VIMRC_URL" ]; then
        log "拉取 .vimrc"
        curl -fsSL "$VIMRC_URL" -o /root/.vimrc
        if [ -n "$user_home" ]; then
            curl -fsSL "$VIMRC_URL" -o "$user_home/.vimrc"
            chown "$SBX_USER:$SBX_USER" "$user_home/.vimrc"
        fi
    fi
}

deploy_ssrpolipo() {
    local compose_file

    if [ "$INSTALL_SSRPOLIPO" != "1" ]; then
        return
    fi
    if [ -z "$SSRPOLIPO_COMPOSE_URL" ]; then
        warn "INSTALL_SSRPOLIPO=1 但未提供 SSRPOLIPO_COMPOSE_URL，跳过"
        return
    fi

    log "准备 ssrpolipo 目录"
    mkdir -p /root/ssrpolipo
    compose_file="/root/ssrpolipo/docker-compose.yml"
    if [ ! -f "$compose_file" ]; then
        curl -fsSL "$SSRPOLIPO_COMPOSE_URL" -o "$compose_file"
        if [ ! -s "$compose_file" ]; then
            rm -f "$compose_file"
            die "下载 ssrpolipo compose 失败"
        fi
    fi
}

install_tcp_brutal() {
    # 可选：安装 tcp-brutal DKMS 内核模块（Hysteria 2 brutal 拥塞控制，apernet 官方）。
    # DKMS 模块（针对当前内核编译），不替换内核、不改 GRUB、不需重启换核。
    # 注意：本模块仅令诊断位 IS_BRUTAL=true；Hy2 实际用 bbr，不装【不影响代理功能】。
    # 默认开启；如需关闭在 initial.env 设 INSTALL_TCP_BRUTAL=0。
    # 先下载再执行（不裸 curl|bash）；失败仅告警，不影响其余初始化。
    local tmp_file
    if [ "$INSTALL_TCP_BRUTAL" != "1" ]; then
        return
    fi

    # DKMS 编译链：tcp.hy2.sh 会装内核头但【不装编译器】，且 Debian 上 linux-headers
    # 只拉 gcc-<ver> 不建 /usr/bin/gcc 链接，故须显式 build-essential。匹配【运行内核】
    # 的头也一并装（运行内核被仓库淘汰时此步失败→DKMS 本就无法编译，best-effort 不中止）。
    log "准备 tcp-brutal 编译依赖（build-essential + 运行内核头）"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update >/dev/null 2>&1 || true
    apt-get install -y build-essential || warn "build-essential 未装全，DKMS 可能无法编译"
    apt-get install -y "linux-headers-$(uname -r)" \
        || warn "运行内核头 linux-headers-$(uname -r) 不可用（运行内核或已被仓库淘汰；需先升级内核+重启再重试）"

    log "安装 tcp-brutal DKMS 模块 ← $TCP_BRUTAL_URL"
    tmp_file="$(mktemp)"
    if curl -fsSL "$TCP_BRUTAL_URL" -o "$tmp_file" && [ -s "$tmp_file" ]; then
        # tcp.hy2.sh 带 set -e，顶部 `tred=$(tput setaf 1)` 在【无 TERM 的非交互环境】
        # （cron / 管道 / 远程批量）下会因 tput 返非 0 而提前 abort（RC=1 且无报错）。
        # 给 TERM 兜底让其正常跑到 DKMS 构建。
        TERM="${TERM:-xterm}" bash "$tmp_file" || warn "tcp-brutal 安装失败（内核头/编译环境？），跳过"
    else
        warn "tcp-brutal 脚本下载失败（$TCP_BRUTAL_URL），跳过"
    fi
    rm -f "$tmp_file"

    if command -v dkms >/dev/null 2>&1; then
        log "DKMS 状态："
        dkms status || true
    fi
}

main() {
    case "${1:-}" in
        -h|--help) usage; exit 0 ;;
        "") : ;;
        *) die "未知参数: $1（-h|--help 查看用法）" ;;
    esac
    require_root
    load_config
    detect_os
    resolve_domains
    ensure_prereqs
    set_timezone
    tune_sysctl
    verify_bbr
    tune_limits
    tune_profile
    ensure_user
    install_ssh_key
    configure_ssh
    configure_sudoers
    setup_dotfiles
    install_docker
    deploy_sbx
    deploy_ssrpolipo
    install_tcp_brutal
    # 回国（CN-exit）节点专属：Tailscale 入网 + keepalive + 自检护栏 + 反向探活。
    if [ -n "$OPENWRT_TS_IP" ]; then setup_cn_exit; fi
    # 所有节点：拉运行时更新脚本 + 启动容器。
    start_sbx
    # 回国节点：拉起后自检（硬失败 die）。
    if [ -n "$OPENWRT_TS_IP" ]; then self_check; fi
    log "=== 初始化完成，容器已启动。运行时更新容器跑：sbx-redeploy ==="
    if [ -n "$OPENWRT_TS_IP" ]; then
        log "回国出口由 OpenWrt 侧 cn-bridge 拨号控制（热备 r-tunnel + 本机 socks5 腿）"
    fi
}

main "$@"
