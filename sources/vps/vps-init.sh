#!/usr/bin/env bash
set -euo pipefail

# vps-init.sh —— sb-xray VPS Stage 1 基础初始化
#
# 在全新 Debian/Ubuntu VPS 上运行，完成基础系统调优、sudo 用户、SSH 加固、
# Docker 官方仓库安装，以及首次 sb-xray 目录与 compose 模板落盘。
# 这是 Stage 1：目标是把机器准备到可继续执行同目录 Stage 2
# `vps-cn-exit-init.sh` 的状态。
#
# 配置从脚本同目录的 initial.env 读取（若存在）——这是整套 vps/ 脚本共用的
# 「节点唯一输入配置」，Stage 2（vps-cn-exit-init.sh）也 source 同一文件。
# 语义：文件定义的项以文件为准，文件没写的项可用命令行环境变量补。
# 注意区分：initial.env 是「输入」；sb-xray 的 .env / 各 cron 的 conf 是脚本「生成产物」，不手改。
# 关键变量：
#   SBXRAY_DIR              sb-xray 目录（默认 /root/sb-xray）
#   SBX_USER                要创建的 sudo 用户（默认 sbx）
#   SBX_USER_PASSWORD       sudo 用户密码（可空，空则不设）
#   ROOT_PASSWORD           root 密码（可空，空则不改）
#   SSH_PORT                SSH 端口（默认 38666）
#   SSH_PUBKEY              内联公钥（含空格须加引号；推荐改用 SSH_PUBKEY_FILE）
#   SSH_PUBKEY_FILE         公钥文件路径，读取其中全部有效公钥行（支持多公钥）
#   TIMEZONE                时区（默认 Asia/Shanghai）
#   SBX_DOMAIN(空=hostname,须.com) / SBX_CDN_DOMAIN(空=hostname .com→.top) / SBX_CODE
#   SBX_COMPOSE_URL         sb-xray docker-compose.yml 下载地址
#   INSTALL_SSRPOLIPO / SSRPOLIPO_COMPOSE_URL
#   BASHRC_URL / VIMRC_URL  可选 .bashrc/.vimrc 下载地址（空则跳过）
#   INSTALL_TCP_BRUTAL / TCP_BRUTAL_URL  可选 tcp-brutal DKMS 模块（默认关）
#
# 重要警告：
#   本脚本会关闭 SSH 密码登录，仅保留公钥登录。
#   必须先确保 SSH_PUBKEY / SSH_PUBKEY_FILE 可用且能成功写入 authorized_keys，
#   否则配置 SSH 后会把自己锁在机器外。
#
# 退出码：
#   0  初始化完成，可继续运行 Stage 2。
#   1  任一步骤失败，脚本停止，需修复后重跑。
#
# Stage 1 -> Stage 2 交接条件：
#   1. docker 已安装并可用
#   2. $SBXRAY_DIR/docker-compose.yml 已存在
#   3. $SBXRAY_DIR/.env 已写入 domain / cdndomain / code
#   4. 可继续运行同目录 vps-cn-exit-init.sh

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
    if ! command -v curl >/dev/null 2>&1; then
        log "安装基础依赖 ca-certificates/curl"
        export DEBIAN_FRONTEND=noninteractive
        apt-get update
        apt-get install -y ca-certificates curl linux-headers-generic
    fi
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
    local base_config include_line tmp_file drop_in sshd_bin

    base_config="/etc/ssh/sshd_config"
    include_line='Include /etc/ssh/sshd_config.d/*.conf'
    drop_in="/etc/ssh/sshd_config.d/99-sbx.conf"

    if [ ! -f "$base_config" ]; then
        die "未找到 SSH 主配置: $base_config"
    fi

    if ! grep -Fxq "$include_line" "$base_config"; then
        tmp_file="$(mktemp)"
        {
            cat "$base_config"
            printf '%s\n' "$include_line"
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
    # 这是 DKMS 模块（针对当前内核编译），不替换内核、不改 GRUB、不需重启换核——
    # 比换内核安全得多。默认开启（sb-xray 必需）；如需关闭在 initial.env 设 INSTALL_TCP_BRUTAL=0。
    # 先下载校验再执行，不裸 curl|bash；失败仅告警，不影响其余初始化。
    local tmp_file
    if [ "$INSTALL_TCP_BRUTAL" != "1" ]; then
        return
    fi

    log "安装 tcp-brutal DKMS 模块 ← $TCP_BRUTAL_URL"
    tmp_file="$(mktemp)"
    if curl -fsSL "$TCP_BRUTAL_URL" -o "$tmp_file" && [ -s "$tmp_file" ]; then
        bash "$tmp_file" || warn "tcp-brutal 安装失败（内核头/编译环境？），跳过"
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
    log "Stage 1 完成,回国节点接着跑 vps-cn-exit-init.sh"
}

main "$@"
