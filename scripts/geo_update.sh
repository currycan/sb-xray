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

# 检查 supervisord 是否在运行
if [ -e "/var/run/supervisor.sock" ]; then
    log "Restarting ..."
    # supervisorctl restart all
    supervisorctl stop xray
    # 清理 socket 文件，防止 bind error
    rm -f /dev/shm/uds*
    supervisorctl start xray
    log "Restarting x-ui Xray..."
    netstat -tlnp | grep xray-linux | head -1 | awk '{print $7}' | awk -F/ '{print $1}' | xargs kill -9
else
    log "Supervisord not running, skipping service restart."
fi

log "Geo update completed successfully."
