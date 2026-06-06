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
#   TS_HOSTNAME     本机在 tailnet 的设备名（默认取 hostname）
#   CN_EXIT_MODE    回国模式（默认 balance）
#   REVERSE_DOMAINS 经 bridge 出的内网域名，逗号分隔（可选，16 台建议统一）
#   VPS_DOMAIN      本节点对外域名（可选，写进 .env domain）
#   SHOUTRRR_URLS   事件总线告警 URL（可选）
#   COMPOSE_URL     docker-compose.yml 下载源（默认仓库 main 的 raw）
#   SKIP_COMPOSE_UPDATE  设 1 跳过 compose 同步（默认 0，会拉最新覆盖）
#
# 兼容 POSIX sh。

set -e

log()  { printf '[vps-init] %s\n' "$*"; }
warn() { printf '[vps-init] WARN: %s\n' "$*" >&2; }
die()  { printf '[vps-init] ERROR: %s\n' "$*" >&2; exit 1; }

SBXRAY_DIR="${SBXRAY_DIR:-/root/sb-xray}"
CN_EXIT_MODE="${CN_EXIT_MODE:-balance}"
TS_HOSTNAME="${TS_HOSTNAME:-$(hostname)}"

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
    tailscale up --authkey="$TS_AUTHKEY" --hostname="$TS_HOSTNAME" --accept-dns=false \
        || warn "tailscale up 未成功，请手动检查（可能 authkey 失效）"
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
            [ -f "$_cf" ] && cp "$_cf" "$_cf.bak"
            mv "$_tmp" "$_cf"
            log "  docker-compose.yml 已更新（原文件备份为 docker-compose.yml.bak）"
        else
            rm -f "$_tmp"; warn "  下载的 compose 不含 CN_EXIT_MODE 引用,疑似有误,保留原文件"
        fi
    else
        rm -f "$_tmp"; warn "  docker-compose.yml 下载失败,保留原文件（回国 env 可能不生效）"
    fi
fi

# ── 5. 拉起容器 ───────────────────────────────────────────────────
log "docker compose pull + up -d（顺带升级到最新镜像）"
cd "$SBXRAY_DIR"
if docker compose version >/dev/null 2>&1; then
    docker compose pull && docker compose up -d
else
    docker-compose pull && docker-compose up -d
fi

# ── 6. 自检 ───────────────────────────────────────────────────────
log "── 自检 ──"
sleep 5
if docker ps --filter name=sb-xray --format '{{.Status}}' | grep -qiE 'up|healthy'; then
    log "  [ OK ] sb-xray 容器运行中"
else
    warn "  [FAIL] sb-xray 容器未运行，检查 docker compose logs"
fi
if docker exec sb-xray sh -c 'env | grep -q "CN_EXIT_MODE='"$CN_EXIT_MODE"'"' 2>/dev/null; then
    log "  [ OK ] 容器内 CN_EXIT_MODE=$CN_EXIT_MODE 生效"
else
    warn "  [FAIL] 容器内 CN_EXIT_MODE 未生效（可能需 docker compose up --force-recreate）"
fi
if tailscale status >/dev/null 2>&1 && ! tailscale status 2>/dev/null | grep -qiE 'Logged out|stopped'; then
    log "  [ OK ] Tailscale 在网"
    tailscale ping -c 1 --timeout 5s "$OPENWRT_TS_IP" >/dev/null 2>&1 \
        && log "  [ OK ] 到 OpenWrt $OPENWRT_TS_IP 的 Tailscale 链路通" \
        || warn "  [FAIL] 暂时 ping 不通 OpenWrt（链路可能需打洞，稍后由 keepalive 自愈）"
else
    warn "  [FAIL] Tailscale 未在网"
fi

log "=== 完成。回国出口由 OpenWrt 侧 cn-bridge 拨号控制（热备 r-tunnel + 本机 socks5 腿）==="
log "提示：若容器内 env 未更新，跑一次 'docker compose up -d --force-recreate'"
