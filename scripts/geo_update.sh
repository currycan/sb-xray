#!/usr/bin/env bash

set -eou pipefail

# 日志文件
LOG_FILE="/var/log/geo_update.log"

# 日志函数
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

log "Starting GeoIP and GeoSite update..."

# 切换到存放 geo 文件的目录
cd /usr/local/bin/bin || { log "Failed to change directory to /usr/local/bin/bin"; exit 1; }

# 定义下载函数
download_file() {
    local url=$1
    local output=${2:-$(basename "$url")}
    log "Downloading $output..."
    curl -fsSL --retry 3 --retry-delay 2 -o "$output" "$url" &
}

# 并行下载文件
download_file "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geoip.dat"
download_file "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geosite.dat"
download_file "https://github.com/chocolate4u/Iran-v2ray-rules/releases/latest/download/geoip.dat" "geoip_IR.dat"
download_file "https://github.com/chocolate4u/Iran-v2ray-rules/releases/latest/download/geosite.dat" "geosite_IR.dat"
download_file "https://github.com/runetfreedom/russia-v2ray-rules-dat/releases/latest/download/geoip.dat" "geoip_RU.dat"
download_file "https://github.com/runetfreedom/russia-v2ray-rules-dat/releases/latest/download/geosite.dat" "geosite_RU.dat"

# 等待所有后台任务完成
wait

# 更新软链接 (Dockerfile 中已经做过，这里再次确保)
ln -sf /usr/local/bin/bin/*.dat /usr/local/bin/

log "Files downloaded successfully."

# 注：原本此处有 MPH 缓存重建逻辑（PR #5505），已作废。
# PR #5505 的 buildMphCache CLI 在 2026-04-13 被 PR #5814 的 geodata refactor revert
# 新方案运行时自动生效，无需重建。详见 docs/10-implementation-notes.md §M1-4

# 检查 supervisord 是否在运行（进程存活 + socket 是真 unix socket，过滤上次异常退出残留的 stale 文件）
if pgrep -x supervisord >/dev/null 2>&1 && [ -S "/var/run/supervisor.sock" ]; then
    log "Restarting ..."
    # supervisorctl restart all
    supervisorctl stop xray  || log "WARN: supervisorctl stop xray failed"
    # 清理 socket 文件，防止 bind error
    rm -f /dev/shm/uds*
    supervisorctl start xray || log "WARN: supervisorctl start xray failed"
    log "Restarting x-ui Xray..."
    netstat -tlnp 2>/dev/null | grep xray-linux | head -1 | awk '{print $7}' | awk -F/ '{print $1}' | xargs -r kill -9 2>/dev/null || true
else
    log "Supervisord not running, skipping service restart."
fi

log "Geo update completed successfully."
