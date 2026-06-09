#!/bin/sh
# sbx-canary-check.sh —— sb-xray 自动更新后业务层自检 + 中文 Telegram 告警（L1 canary 护栏）
#
# 配合 watchtower（docker-compose 的 watchtower service）：watchtower 在 schedule 窗口
# 更新镜像后，本脚本由 cron/systemd-timer 在稍后跑一轮业务自检，经 shoutrrr-forwarder
# 推中文通知。watchtower 自带英文通知已关闭，所有 Telegram 报警统一走这里。
#
# 全 16 台同构。角色（SBX_CANARY_ROLE）只决定失败 runbook 文案：
#   canary  —— dc99-3，建议 03:05 跑；失败提示「叫停其余 15 台」（错峰拦截）
#   worker  —— 其余 15 台，建议 04:05 跑；失败提示「本台回滚」（个体故障可见）
#
# 通知策略（digest 落盘检测「是否刚更新」）：
#   自检失败                → 推中文告警（watchtower.canary.failed）+ 退出码 1
#   自检通过 且 检测到更新   → 推中文「已自动更新」通知（watchtower.canary.updated）
#   自检通过 且 无更新       → 静默（避免每天噪音）
#
# env：
#   SBX_CONTAINER       被检容器名（默认 sb-xray）
#   SBX_CANARY_ROLE     canary | worker（默认 worker）
#   SBX_FORWARDER       shoutrrr-forwarder 地址（默认 127.0.0.1:18085）
#   SBX_PROBE_URL       回国链路探测目标（默认与 compose CN_EXIT_PROBE_URL 同源）
#   SBX_DIGEST_STATE    digest 落盘文件（默认 /root/sb-xray/.sbx-canary-last-digest）
#   SBX_RETRIES         每项重试次数（默认 3）
#   SBX_RETRY_INTERVAL  重试间隔秒（默认 10）
#
# 兼容 POSIX sh。

set -e

CONTAINER="${SBX_CONTAINER:-sb-xray}"
ROLE="${SBX_CANARY_ROLE:-worker}"
FORWARDER="${SBX_FORWARDER:-127.0.0.1:18085}"
PROBE_URL="${SBX_PROBE_URL:-http://connect.rom.miui.com/generate_204}"
DIGEST_STATE="${SBX_DIGEST_STATE:-/root/sb-xray/.sbx-canary-last-digest}"
RETRIES="${SBX_RETRIES:-3}"
RETRY_INTERVAL="${SBX_RETRY_INTERVAL:-10}"

log()  { printf '[sbx-canary] %s\n' "$*"; }
warn() { printf '[sbx-canary] WARN: %s\n' "$*" >&2; }

# retry <描述> <cmd...>：重试 RETRIES 次、间隔 RETRY_INTERVAL，全失败返回 1。
# 防国内网络凌晨抖动等瞬时误报（尤其回国链路项）。
retry() {
    _desc=$1
    shift
    _i=1
    while [ "$_i" -le "$RETRIES" ]; do
        if "$@"; then return 0; fi
        warn "$_desc 第 $_i/$RETRIES 次失败"
        [ "$_i" -lt "$RETRIES" ] && sleep "$RETRY_INTERVAL"
        _i=$((_i + 1))
    done
    return 1
}

# ── 四项自检（返回 0 通过）─────────────────────────────────────────
# 1. 容器 Health = healthy（复用镜像内 HEALTHCHECK：xray 进程在跑）
check_health() {
    [ "$(docker inspect -f '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null)" = "healthy" ]
}

# 2. 443 tcp+udp 在 listen（对外唯一端口）
check_ports() {
    ss -tln 2>/dev/null | grep -q ':443 ' && ss -uln 2>/dev/null | grep -q ':443 '
}

# 3. 回国链路端到端：经容器内出站（走 xray 回国路由）探测国内目标，2xx/204 即通
check_cn_exit() {
    _code=$(docker exec "$CONTAINER" curl -s -o /dev/null -w '%{http_code}' \
        --max-time 8 "$PROBE_URL" 2>/dev/null)
    [ "$_code" = "204" ] || [ "$_code" = "200" ]
}

# 读当前容器镜像 RepoDigest（空=容器异常/拿不到，按失败处理）
read_digest() {
    _cid=$(docker inspect -f '{{.Image}}' "$CONTAINER" 2>/dev/null) || return 1
    docker inspect -f '{{if .RepoDigests}}{{index .RepoDigests 0}}{{end}}' "$_cid" 2>/dev/null
}

# ── 中文告警：POST 到 forwarder，未知事件 fallback 渲染成「中文 key: value」正文，
#    标题自动带容器侧 SHOUTRRR_TITLE_PREFIX（[sb-xray:<域名>]）。──
notify() {
    _event=$1
    _body=$2
    if curl -fsS -m 8 -X POST \
        -H "X-Event: $_event" \
        -H 'Content-Type: application/json' \
        --data "$_body" \
        "http://$FORWARDER/$_event" >/dev/null 2>&1; then
        log "已推送通知：$_event"
    else
        warn "通知推送失败（forwarder $FORWARDER 不可达？）：$_event"
    fi
}

# 失败 runbook 文案（按角色）
runbook_text() {
    if [ "$ROLE" = "canary" ]; then
        printf '立即叫停其余 15 台：本地 wrapper 遍历各节点 docker compose stop watchtower，确认坏镜像后再处置'
    else
        printf '回滚本台：docker compose down 后切回上一个 digest tag 重新 up，并核对回国链路'
    fi
}

# ── 主流程 ────────────────────────────────────────────────────────
CUR_DIGEST=$(read_digest || printf '')
PREV_DIGEST=$([ -f "$DIGEST_STATE" ] && cat "$DIGEST_STATE" 2>/dev/null || printf '')
UPDATED=0
# PREV 为空 = 首次运行（只落盘，不报「已更新」）
[ -n "$CUR_DIGEST" ] && [ -n "$PREV_DIGEST" ] && [ "$CUR_DIGEST" != "$PREV_DIGEST" ] && UPDATED=1

FAILS=""
add_fail() { FAILS="${FAILS}${FAILS:+、}$1"; }

retry '容器健康'   check_health  || add_fail '容器健康'
retry '443 端口'   check_ports   || add_fail '443端口监听'
retry '回国链路'   check_cn_exit || add_fail '回国链路端到端'
[ -n "$CUR_DIGEST" ] || add_fail '镜像digest读取'

# 落盘当前观测到的 digest（供下次比对「是否刚更新」）
[ -n "$CUR_DIGEST" ] && printf '%s' "$CUR_DIGEST" > "$DIGEST_STATE" 2>/dev/null || true

if [ -n "$FAILS" ]; then
    log "自检失败：$FAILS"
    BODY=$(printf '{"role":"%s","fails":"%s","image":"%s","runbook":"%s"}' \
        "$ROLE" "$FAILS" "${CUR_DIGEST:-未知}" "$(runbook_text)")
    notify 'watchtower.canary.failed' "$BODY"
    exit 1
fi

log "自检通过（4/4）"
if [ "$UPDATED" = 1 ]; then
    BODY=$(printf '{"role":"%s","old":"%s","new":"%s"}' \
        "$ROLE" "${PREV_DIGEST:-（首次记录）}" "$CUR_DIGEST")
    notify 'watchtower.canary.updated' "$BODY"
else
    log "无更新，静默"
fi
exit 0
