#!/bin/sh
# cn-exit-watchdog.sh —— VPS 侧 CN 出口反向探活（设备整机宕机告警的兜底）
#
# 背景：设备侧 cn-bridge-monitor 跑在 CN 出口设备自身上——设备整机宕机时监控
# 随之失联，而 VPS 侧 balance 探活只做静默 failover，不发告警。本脚本部署在
# 任意 VPS 上 cron 周期执行，经 socks5 腿实测回国可用性，连续 WD_THRESHOLD
# 次失败发 TG 告警，恢复时发解除；状态文件去重，告警期内不重复刷屏。
#
# 配置（/etc/cn-exit-watchdog.conf 或环境变量，后者优先）：
#   WD_SOCKS5_HOST   CN 出口 socks5 地址（默认取 OPENWRT_TS_IP env，由 cron 注入）
#   WD_SOCKS5_PORT   socks5 端口（默认 7891）
#   WD_PROBE_URL     探活 URL（默认 http://connect.rom.miui.com/generate_204）
#   WD_THRESHOLD     连续失败阈值（默认 3）
#   WD_TG_TOKEN      Telegram bot token（必填，告警通道）
#   WD_TG_CHAT       Telegram chat id（必填）
#   WD_STATE         状态文件（默认 /var/tmp/cn-exit-watchdog.state）
#   WD_TAG           消息前缀标签（默认空；演练时可设 "[演练]"）
#
# 用法（vps-init.sh 已装到 /usr/local/bin，作命令直接调用）：
#   cn-exit-watchdog          # 单次探活（cron 每分钟调一次）
#   cn-exit-watchdog --test   # 发一条 TG 测试消息验证告警通道
#
# 安装（vps-init.sh 在回国节点自动完成，幂等；手动同效）：
#   装本脚本到 /usr/local/bin/cn-exit-watchdog && chmod 755
#   写 /etc/cn-exit-watchdog.conf（含 WD_TG_TOKEN/WD_TG_CHAT，chmod 600）
#   cron 加：* * * * * root OPENWRT_TS_IP=<100.x.x.x> /usr/local/bin/cn-exit-watchdog >/dev/null 2>&1

[ -f /etc/cn-exit-watchdog.conf ] && . /etc/cn-exit-watchdog.conf

WD_SOCKS5_HOST="${WD_SOCKS5_HOST:-${OPENWRT_TS_IP:-}}"
WD_SOCKS5_PORT="${WD_SOCKS5_PORT:-7891}"
WD_PROBE_URL="${WD_PROBE_URL:-http://connect.rom.miui.com/generate_204}"
WD_THRESHOLD="${WD_THRESHOLD:-3}"
WD_STATE="${WD_STATE:-/var/tmp/cn-exit-watchdog.state}"
WD_TAG="${WD_TAG:-}"

self="$(hostname)"

tg_send() {
    [ -n "$WD_TG_TOKEN" ] && [ -n "$WD_TG_CHAT" ] || return 1
    curl -s -m 10 -o /dev/null \
        --data-urlencode "chat_id=${WD_TG_CHAT}" \
        --data-urlencode "text=${WD_TAG}$1" \
        "https://api.telegram.org/bot${WD_TG_TOKEN}/sendMessage"
}

if [ "$1" = "--test" ]; then
    tg_send "🔧 cn-exit-watchdog @ ${self}: 告警通道测试 OK（目标 ${WD_SOCKS5_HOST}:${WD_SOCKS5_PORT}）" \
        && echo "test message sent" || { echo "test send FAILED"; exit 1; }
    exit 0
fi

[ -n "$WD_SOCKS5_HOST" ] || { echo "WD_SOCKS5_HOST 未配置且 .env 无 tsip"; exit 1; }

# 并发去重：cron 每分钟一调，curl 卡 -m 8 可能令两进程并发改写 state。
# 非阻塞自锁——已持锁说明上一轮还在跑，本轮直接退（探活下一分钟补上）。
# 缺 flock（极简镜像）则优雅降级裸跑，绝不因缺工具中断告警链路。
WD_LOCK="${WD_LOCK:-${WD_STATE}.lock}"
if command -v flock >/dev/null 2>&1; then
    exec 9>"$WD_LOCK" 2>/dev/null || true
    flock -n 9 || { echo "上一轮探活仍在运行，跳过本轮"; exit 0; }
fi

# 原子状态写：tmp 同目录落盘后 mv 覆盖，杜绝并发读到截断半行。
_state_write() {
    _tmp="${WD_STATE}.$$"
    printf '%s %s\n' "$1" "$2" > "$_tmp" && mv -f "$_tmp" "$WD_STATE"
}

# state 文件两个字段：连续失败计数 / 是否已告警（0|1）
fails=0; alerted=0
[ -f "$WD_STATE" ] && { read -r fails alerted < "$WD_STATE" 2>/dev/null || true; }
case "$fails" in ''|*[!0-9]*) fails=0;; esac
case "$alerted" in ''|*[!0-9]*) alerted=0;; esac

code=$(curl -s -m 8 --socks5-hostname "${WD_SOCKS5_HOST}:${WD_SOCKS5_PORT}" \
    -o /dev/null -w '%{http_code}' "$WD_PROBE_URL" 2>/dev/null)

if [ "$code" = "204" ] || [ "$code" = "200" ]; then
    if [ "$alerted" = "1" ]; then
        tg_send "✅ CN出口恢复 @ ${self}: ${WD_SOCKS5_HOST}:${WD_SOCKS5_PORT} 探活恢复（HTTP ${code}）"
    fi
    _state_write 0 0
    exit 0
fi

fails=$((fails + 1))
if [ "$fails" -ge "$WD_THRESHOLD" ] && [ "$alerted" = "0" ]; then
    tg_send "❌ CN出口疑似宕机 @ ${self}: 经 ${WD_SOCKS5_HOST}:${WD_SOCKS5_PORT} 探活连续 ${fails} 次失败（最后码 ${code:-超时}）。设备侧监控可能已随设备失联，请检查 CN 出口设备电源/网络。"
    alerted=1
fi
_state_write "$fails" "$alerted"
exit 0
