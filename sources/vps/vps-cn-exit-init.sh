#!/bin/sh
# vps-cn-exit-init.sh —— sb-xray VPS 侧回国（balance 双腿）一键初始化
#
# 在每台公网 VPS 上跑（标准 Linux + Docker）。把回国所需 env 写进 sb-xray 的 .env
# （docker-compose.yml 以 ${VAR} 引用），安装 Tailscale 并入 tailnet，装 VPS 侧
# keepalive，最后 docker compose up。配一次即可，拨号切换全在 OpenWrt 侧 cn-bridge。
#
# 供嵌进你已有的 VPS provisioning。通过环境变量传参：
#   SBXRAY_DIR      sb-xray 部署目录（默认 /root/sb-xray）
#   OPENWRT_TS_IP   家里 OpenWrt 的 Tailscale IP（必填 —— socks5 腿回国出口）
#   TS_AUTHKEY      Tailscale reusable auth key（首次装 tailscale 必填；已在网可省）
#   TS_AUTHKEY_FILE 改从文件读 authkey（TS_AUTHKEY 为空时生效，避免 key 进程表/历史泄露）
#   TS_HOSTNAME     本机在 tailnet 的设备名（默认取 hostname）
#   CN_EXIT_MODE    回国模式（默认 balance）
#   REVERSE_DOMAINS 经 bridge 出的内网域名，逗号分隔（可选，各节点建议统一）
#   VPS_DOMAIN      本节点对外域名（可选，写进 .env domain）
#   SHOUTRRR_URLS   事件总线告警 URL（可选）
#   COMPOSE_URL     docker-compose.yml 下载源（默认仓库 main 的 raw）
#   SKIP_COMPOSE_UPDATE  设 1 跳过 compose 同步（默认 0，会拉最新覆盖）
#   SKIP_PULL       设 1 跳过 docker compose pull（只 up -d，不升级镜像；默认 0）
#   SBX_CANARY_ROLE 本节点 watchtower 角色 canary|worker（默认 worker；指定一台金丝雀节点设 canary）
#   CANARY_URL      sbx-canary-check.sh 下载源（默认仓库 main 的 raw）
#   SKIP_CANARY_WIRING  设 1 跳过 watchtower 自检护栏安装（默认 0）
#   WD_TG_TOKEN     CN 出口反向探活 Telegram bot token（可选；与 WD_TG_CHAT 同时有值才装 watchdog）
#   WD_TG_CHAT      CN 出口反向探活 Telegram chat id（可选）
#   WATCHDOG_URL    cn-exit-watchdog.sh 下载源（默认仓库 main 的 raw）
#   SKIP_WATCHDOG_WIRING 设 1 跳过反向探活安装（默认 0）
#
# 退出码：自检全部通过 0；有硬失败（容器未起 / env 未生效 / Tailscale 未在网）非 0，
# 便于批量编排检测坏节点。ping / socks5 探测为软告警，不影响退出码。
#
# 兼容 POSIX sh。

set -e

log()  { printf '[vps-init] %s\n' "$*"; }
warn() { printf '[vps-init] WARN: %s\n' "$*" >&2; }
die()  { printf '[vps-init] ERROR: %s\n' "$*" >&2; exit 1; }

# with_timeout <秒> <cmd...>：有 timeout 就限时跑，避免 tailscale up 等命令挂死阻塞 provisioning
with_timeout() {
    _t=$1; shift
    if command -v timeout >/dev/null 2>&1; then timeout "$_t" "$@"; else "$@"; fi
}

# 可选：读取同目录 initial.env 作为节点唯一输入配置（Stage 1 也 source 同一文件）。
# 无此文件则行为与改前完全一致（命令行传参照常），向后兼容生产节点。
# source 后接各变量的 ${VAR:-default}：文件定义的项以文件为准，没写的项可用环境变量补。
_sbx_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" 2>/dev/null && pwd) || _sbx_dir=""
if [ -n "$_sbx_dir" ] && [ -f "$_sbx_dir/initial.env" ]; then
    log "读取同目录 initial.env"
    # shellcheck source=/dev/null
    . "$_sbx_dir/initial.env"
fi

SBXRAY_DIR="${SBXRAY_DIR:-/root/sb-xray}"
CN_EXIT_MODE="${CN_EXIT_MODE:-balance}"
TS_HOSTNAME="${TS_HOSTNAME:-$(hostname)}"
SBX_CANARY_ROLE="${SBX_CANARY_ROLE:-worker}"

# 支持从文件读 authkey（避免明文出现在远端进程表 / shell 历史）
if [ -z "$TS_AUTHKEY" ] && [ -n "$TS_AUTHKEY_FILE" ] && [ -f "$TS_AUTHKEY_FILE" ]; then
    TS_AUTHKEY=$(tr -d ' \t\r\n' < "$TS_AUTHKEY_FILE")
fi

# ── 校验 ──────────────────────────────────────────────────────────
[ -d "$SBXRAY_DIR" ] || die "未找到 sb-xray 目录: $SBXRAY_DIR（先部署 sb-xray 再跑本脚本）"
[ -f "$SBXRAY_DIR/docker-compose.yml" ] || die "未找到 $SBXRAY_DIR/docker-compose.yml"
[ -n "$OPENWRT_TS_IP" ] || die "必填 OPENWRT_TS_IP（家里 OpenWrt 的 Tailscale IP）"
case "$OPENWRT_TS_IP" in
    100.*) : ;;
    *) warn "OPENWRT_TS_IP=$OPENWRT_TS_IP 不像 Tailscale IP（应为 100.x 段）" ;;
esac
command -v docker >/dev/null 2>&1 || die "未找到 docker"

ENV_FILE="$SBXRAY_DIR/.env"
touch "$ENV_FILE"

# upsert_env <key> <value>：删旧行再追加（避免 sed 对含 :/@/& 的值转义出错）
upsert_env() {
    _k=$1; _v=$2
    grep -v "^${_k}=" "$ENV_FILE" > "$ENV_FILE.tmp" 2>/dev/null || true
    mv "$ENV_FILE.tmp" "$ENV_FILE"
    printf '%s=%s\n' "$_k" "$_v" >> "$ENV_FILE"
}

# ── 1. 写 .env 回国项 ─────────────────────────────────────────────
log "写入回国 env 到 $ENV_FILE"
upsert_env CN_EXIT_MODE "$CN_EXIT_MODE"
upsert_env ENABLE_REVERSE true
upsert_env ENABLE_SOCKS5_PROXY true
upsert_env tsip "$OPENWRT_TS_IP"           # docker-compose: CN_EXIT_SOCKS5_HOST=${tsip}
[ -n "$REVERSE_DOMAINS" ] && upsert_env REVERSE_DOMAINS "$REVERSE_DOMAINS"
[ -n "$VPS_DOMAIN" ]      && upsert_env domain "$VPS_DOMAIN"
[ -n "$SHOUTRRR_URLS" ]   && upsert_env shoutrrr_urls "$SHOUTRRR_URLS"
chmod 600 "$ENV_FILE"

# ── 2. 安装 Tailscale 并入网（socks5 腿命脉）──────────────────────
if ! command -v tailscale >/dev/null 2>&1; then
    [ -n "$TS_AUTHKEY" ] || die "未装 tailscale 且未提供 TS_AUTHKEY"
    log "安装 Tailscale..."
    curl -fsSL https://tailscale.com/install.sh | sh || die "Tailscale 安装失败"
fi
if [ -n "$TS_AUTHKEY" ]; then
    log "tailscale up（hostname=$TS_HOSTNAME）"
    # --accept-dns=false：VPS 不需要 MagicDNS，避免改写本机 DNS 影响容器
    # with_timeout：authkey 失效时 tailscale up 会退回交互式登录而挂死，限时兜底
    with_timeout 40 tailscale up --authkey="$TS_AUTHKEY" --hostname="$TS_HOSTNAME" --accept-dns=false \
        || warn "tailscale up 未成功（authkey 失效 / 超时？），将在自检反映为 Tailscale 未在网"
else
    log "未提供 TS_AUTHKEY，假定 tailscale 已在网，跳过 up"
fi

# ── 3. VPS 侧 keepalive（辅助；主力在 OpenWrt 侧）─────────────────
log "安装 VPS 侧 keepalive cron（ping OpenWrt $OPENWRT_TS_IP）"
_tsbin=$(command -v tailscale || echo /usr/bin/tailscale)
cat > /etc/cron.d/cn-exit-keepalive <<EOF
# sb-xray VPS 侧 Tailscale 链路保活（vps-cn-exit-init.sh 生成）
* * * * * root $_tsbin ping -c 1 --timeout 5s $OPENWRT_TS_IP >/dev/null 2>&1
EOF
chmod 644 /etc/cron.d/cn-exit-keepalive

# ── 3.5 watchtower 自动更新护栏（sbx-update 手动触发 + canary 自检定时器）──
# 设计：.superpowers/specs/2026-06-09-watchtower-auto-update-design.md §4.5/§4.8
# watchtower service 在 compose 里，凌晨自动更新 :latest；本段补两件节点侧护栏：
#   (a) sbx-update —— 立即 run-once 更新本台（幂等，灰度手动滚动用）
#   (b) sbx-canary-check 定时器 —— 更新后业务自检 + 中文告警（canary 错峰拦截）
if [ "${SKIP_CANARY_WIRING:-0}" = "1" ]; then
    log "跳过 watchtower 自检护栏安装（SKIP_CANARY_WIRING=1）"
else
    # (a) sbx-update：watchtower run-once，立即检查并更新本台镜像（digest 未变即 no-op）
    log "安装 sbx-update helper → /usr/local/bin/sbx-update"
    cat > /usr/local/bin/sbx-update <<'EOF'
#!/bin/sh
# sbx-update —— 立即检查并更新本台 sb-xray 镜像（watchtower run-once，幂等）
exec docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
    docker.io/nickfedor/watchtower:latest --run-once sb-xray "$@"
EOF
    chmod 755 /usr/local/bin/sbx-update

    # (b) 装/更新 sbx-canary-check.sh（更新后业务自检 + 中文告警，全部节点同构）
    CANARY_URL="${CANARY_URL:-https://raw.githubusercontent.com/currycan/sb-xray/main/sources/vps/sbx-canary-check.sh}"
    _canary="$SBXRAY_DIR/sbx-canary-check.sh"
    if curl -fsSL "$CANARY_URL" -o "$_canary.new" && [ -s "$_canary.new" ] && head -1 "$_canary.new" | grep -q '#!/bin/sh'; then
        mv "$_canary.new" "$_canary"; chmod 755 "$_canary"
        log "  sbx-canary-check.sh 已更新 ← $CANARY_URL"
    elif [ -f "$_canary" ]; then
        rm -f "$_canary.new"; warn "  canary-check 下载失败，保留现有 $_canary"
    else
        rm -f "$_canary.new"; warn "  canary-check 下载失败且本地缺失，自检护栏未装（手动放置后重跑本脚本）"
    fi

    # (c) 角色：canary（指定的金丝雀节点）让 watchtower 提前 1h（北京 03:00），自检 03:05；
    #     worker 走 compose 默认 04:00，自检 04:05。错峰留出 1h 人工叫停窗口。
    if [ "$SBX_CANARY_ROLE" = "canary" ]; then
        upsert_env WATCHTOWER_SCHEDULE "0 0 3 * * *"
        _check_min="5 3"
        log "  角色=canary：watchtower 03:00 先行，自检 03:05"
    else
        _check_min="5 4"
        log "  角色=worker：watchtower 04:00（compose 默认），自检 04:05"
    fi

    # (d) 装自检 cron（角色只决定运行时间与失败 runbook 文案）
    if [ -f "$_canary" ]; then
        cat > /etc/cron.d/sbx-canary-check <<EOF
# sb-xray 自动更新后业务自检（vps-cn-exit-init.sh 生成；角色=$SBX_CANARY_ROLE）
$_check_min * * * root SBX_CANARY_ROLE=$SBX_CANARY_ROLE SBX_DIGEST_STATE=$SBXRAY_DIR/.sbx-canary-last-digest $_canary >/dev/null 2>&1
EOF
        chmod 644 /etc/cron.d/sbx-canary-check
        log "  自检 cron 已装：/etc/cron.d/sbx-canary-check（$_check_min 时段）"
    fi
fi

# ── 3.6 CN 出口整机宕机反向探活（可选；WD_TG_TOKEN+WD_TG_CHAT 同时有值才装）──
# 设备侧监控（cn-bridge-monitor）跑在 CN 出口设备自身上，整机宕机时随之失联，
# 而 balance 探活只做静默 failover。本段在 VPS 侧装 cn-exit-watchdog.sh + cron
# 每分钟经 socks5 腿反向探活，连续失败发 TG 告警（机制见同目录 README）。
# 建议只在 1-2 台节点传 WD_* 启用；多台同时告警属预期（消息含 hostname）。
if [ "${SKIP_WATCHDOG_WIRING:-0}" = "1" ]; then
    log "跳过 CN 出口反向探活安装（SKIP_WATCHDOG_WIRING=1）"
elif [ -n "${WD_TG_TOKEN:-}" ] && [ -n "${WD_TG_CHAT:-}" ]; then
    WATCHDOG_URL="${WATCHDOG_URL:-https://raw.githubusercontent.com/currycan/sb-xray/main/sources/vps/cn-exit-watchdog.sh}"
    _wd="$SBXRAY_DIR/cn-exit-watchdog.sh"
    if curl -fsSL "$WATCHDOG_URL" -o "$_wd.new" && [ -s "$_wd.new" ] && head -1 "$_wd.new" | grep -q '#!/bin/sh'; then
        mv "$_wd.new" "$_wd"; chmod 755 "$_wd"
        log "cn-exit-watchdog.sh 已更新 ← $WATCHDOG_URL"
    elif [ -f "$_wd" ]; then
        rm -f "$_wd.new"; warn "watchdog 下载失败，保留现有 $_wd"
    else
        rm -f "$_wd.new"; warn "watchdog 下载失败且本地缺失，反向探活未装（手动放置后重跑本脚本）"
    fi
    if [ -f "$_wd" ]; then
        printf 'WD_TG_TOKEN=%s\nWD_TG_CHAT=%s\n' "$WD_TG_TOKEN" "$WD_TG_CHAT" > /etc/cn-exit-watchdog.conf
        chmod 600 /etc/cn-exit-watchdog.conf
        cat > /etc/cron.d/cn-exit-watchdog <<EOF
# sb-xray CN 出口整机宕机反向探活（vps-cn-exit-init.sh 生成）
* * * * * root $_wd >/dev/null 2>&1
EOF
        chmod 644 /etc/cron.d/cn-exit-watchdog
        # 清理早期手装的 user-crontab 条目（统一走 cron.d，避免双跑）
        if crontab -l 2>/dev/null | grep -q cn-exit-watchdog; then
            crontab -l 2>/dev/null | grep -v cn-exit-watchdog | crontab -
            log "已迁移旧 user-crontab 条目 → /etc/cron.d/cn-exit-watchdog"
        fi
        log "反向探活已装：/etc/cron.d/cn-exit-watchdog（通道验证可手跑 $_wd --test）"
    fi
else
    log "未传 WD_TG_TOKEN/WD_TG_CHAT，跳过 CN 出口反向探活（可选护栏）"
fi

# ── 4. 同步 docker-compose.yml（确保含最新回国 env 引用）────────────
# 旧部署的 compose 可能不含 ${CN_EXIT_MODE} / ${tsip} 等引用，导致上面写的
# .env 完全不生效（容器只拿到 Dockerfile 默认值）。默认从仓库拉最新 compose；
# 节点专属配置都在 .env，compose 是模板，覆盖安全。SKIP_COMPOSE_UPDATE=1 可跳过。
COMPOSE_URL="${COMPOSE_URL:-https://raw.githubusercontent.com/currycan/sb-xray/main/docker-compose.yml}"
if [ "${SKIP_COMPOSE_UPDATE:-0}" = "1" ]; then
    log "跳过 docker-compose.yml 更新（SKIP_COMPOSE_UPDATE=1）"
else
    log "同步 docker-compose.yml ← $COMPOSE_URL"
    _cf="$SBXRAY_DIR/docker-compose.yml"
    _tmp="$_cf.new"
    if curl -fsSL "$COMPOSE_URL" -o "$_tmp" && [ -s "$_tmp" ]; then
        if grep -q "CN_EXIT_MODE" "$_tmp"; then
            # 只在 .bak 不存在时备份，保住首次的原始 compose（重跑不被覆盖）
            [ -f "$_cf" ] && [ ! -f "$_cf.bak" ] && cp "$_cf" "$_cf.bak"
            mv "$_tmp" "$_cf"
            log "  docker-compose.yml 已更新（原始版本保留在 docker-compose.yml.bak）"
        else
            rm -f "$_tmp"; warn "  下载的 compose 不含 CN_EXIT_MODE 引用,疑似有误,保留原文件"
        fi
    else
        rm -f "$_tmp"; warn "  docker-compose.yml 下载失败,保留原文件（回国 env 可能不生效）"
    fi
fi

# ── 5. 拉起容器 ───────────────────────────────────────────────────
cd "$SBXRAY_DIR"
# SKIP_PULL=1：只 up -d 不升级镜像（想单独开回国、不动镜像版本时用）
if [ "${SKIP_PULL:-0}" = "1" ]; then
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

# ── 6. 自检 ───────────────────────────────────────────────────────
# 硬失败（容器/env/tailscale）计入 _fails → 影响退出码，供批量编排判好坏节点；
# ping / socks5 探测为软告警（打洞预热期可能暂时不通），不计退出码。
log "── 自检 ──"
_fails=0
sleep 5

if docker ps --filter name=sb-xray --format '{{.Status}}' | grep -qiE 'up|healthy'; then
    log "  [ OK ] sb-xray 容器运行中"
else
    warn "  [FAIL] sb-xray 容器未运行，检查 docker compose logs"; _fails=$((_fails+1))
fi

if docker exec sb-xray sh -c 'env | grep -q "CN_EXIT_MODE='"$CN_EXIT_MODE"'"' 2>/dev/null; then
    log "  [ OK ] 容器内 CN_EXIT_MODE=$CN_EXIT_MODE 生效"
else
    warn "  [FAIL] 容器内 CN_EXIT_MODE 未生效（compose 未含引用？跑 docker compose up -d --force-recreate）"; _fails=$((_fails+1))
fi

if tailscale status >/dev/null 2>&1 && ! tailscale status 2>/dev/null | grep -qiE 'Logged out|stopped'; then
    log "  [ OK ] Tailscale 在网"
    # ping 重试：打洞/DERP 预热期首次常超时，重试几次避免误报
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
    # socks5 腿端到端实测：经 OpenWrt SOCKS5 访问 geosite:cn 回显站，看是否走家宽出口
    if command -v curl >/dev/null 2>&1; then
        _sp="${CN_EXIT_SOCKS5_PORT:-7891}"
        _cnip=$(with_timeout 15 curl -x "socks5h://$OPENWRT_TS_IP:$_sp" -s -m 12 http://cip.cc 2>/dev/null \
                | grep -iE '^(IP|地址)' | head -1 | tr -s ' \t' ' ')
        if [ -n "$_cnip" ]; then
            log "  [ OK ] socks5 腿回国实测：$_cnip"
        else
            warn "  [warn] socks5 腿暂未探到回国出口（OpenClash :$_sp 未就绪 / 预热中，非硬失败）"
        fi
    fi
else
    warn "  [FAIL] Tailscale 未在网（authkey 失效？socks5 腿不可用）"; _fails=$((_fails+1))
fi

log "提示：若容器内 env 未更新，跑一次 'docker compose up -d --force-recreate'"
if [ "$_fails" -gt 0 ]; then
    die "自检 $_fails 项硬失败 —— 本节点未就绪，请按上面 [FAIL] 排查"
fi
log "=== 完成。回国出口由 OpenWrt 侧 cn-bridge 拨号控制（热备 r-tunnel + 本机 socks5 腿）==="
