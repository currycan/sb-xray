#!/usr/bin/env bash
# ==============================================================================
# test_smoke.sh — SB-Xray 持续验证基线
#
# 用途: 每个 Milestone 的 PR 都必须通过此脚本。测试项目容器能启动、关键服务
#       RUNNING、xray 配置语法正确、webhook 接收器监听、MPH 缓存就绪。
#
# 使用:
#       ./scripts/test_smoke.sh                     # 针对当前 compose 部署
#       SKIP_COMPOSE=1 ./scripts/test_smoke.sh      # 只做离线静态校验（适合 CI）
#       CONTAINER=sb-xray ./scripts/test_smoke.sh   # 指定容器名
#
# 退出码:
#       0 全部通过
#       1 至少一个检查失败
# ==============================================================================

set -u

RED=$'\033[1;31m'; GREEN=$'\033[1;32m'; YELLOW=$'\033[1;33m'; CYAN=$'\033[1;36m'; NC=$'\033[0m'
PASS=0; FAIL=0
CONTAINER="${CONTAINER:-sb-xray}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

ok()   { echo "${GREEN}✓${NC} $*"; PASS=$((PASS+1)); }
bad()  { echo "${RED}✗${NC} $*"; FAIL=$((FAIL+1)); }
info() { echo "${CYAN}ℹ${NC} $*"; }
warn() { echo "${YELLOW}⚠${NC} $*"; }

section() {
    echo
    echo "${CYAN}━━━ $* ━━━${NC}"
}

# ---- 静态检查（无需容器）---------------------------------------------------
section "静态检查"

# JSON 语法 (xray / sing-box 入站模板)
# 模板含 ${XXX_SECTION} / ${XRAY_UUID} 等 shell 占位符，不是纯 JSON
# 先把占位符替换为合法 JSON 值再用 jq 校验
json_template_check() {
    local f="$1"
    # 替换各类 shell 占位符为合法 JSON 值：
    #   `"key": ${VAR}` 或 `${VAR}` 独立出现 → null 或 "placeholder"
    #   `${FOO},` 这种 section-style 占位符 → "placeholder": null,
    # JSON 模板中段落占位符上下文不固定（object key 位置 / array element 位置），
    # 严格 JSON 解析不可靠。这里做 **括号 / 引号 平衡检查**（容忍 ${VAR} 嵌入），
    # 真实 JSON 有效性交给容器内 xray -test 来校验。
    python3 - "$f" <<'PY' 2>&1
import re, sys
p = sys.argv[1]
src = open(p, "r", encoding="utf-8").read()
# 剥离字符串字面量 (避免被里面的 { } 干扰)
stripped = re.sub(r'"(?:\\.|[^"\\])*"', '""', src, flags=re.DOTALL)
# 剥离 ${VAR} 占位符
stripped = re.sub(r'\$\{[A-Za-z0-9_]+\}', '', stripped)
# 平衡性检查
depth_brace = stripped.count('{') - stripped.count('}')
depth_bracket = stripped.count('[') - stripped.count(']')
if depth_brace != 0:
    print(f"brace imbalance: {depth_brace:+d}", file=sys.stderr); sys.exit(1)
if depth_bracket != 0:
    print(f"bracket imbalance: {depth_bracket:+d}", file=sys.stderr); sys.exit(1)
# 引号闭合（去掉内部字符串后不应残留 "）
if stripped.count('"') % 2 != 0:
    print("unclosed quote", file=sys.stderr); sys.exit(1)
sys.exit(0)
PY
}

if command -v jq >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1; then
    while IFS= read -r f; do
        if json_template_check "$f" >/dev/null 2>&1; then
            ok "JSON 语法: $(basename "$f")"
        else
            bad "JSON 语法错误: $f"
            json_template_check "$f" 2>&1 | head -3 | sed 's/^/    /'
        fi
    done < <(find "${REPO_ROOT}/templates/xray" "${REPO_ROOT}/templates/sing-box" -type f -name '*.json' 2>/dev/null)
else
    warn "jq/python3 未安装，跳过 JSON 语法校验"
fi

# Shell 脚本语法
if command -v bash >/dev/null 2>&1; then
    for f in "${REPO_ROOT}"/scripts/*.sh; do
        if bash -n "$f" 2>/dev/null; then
            ok "Shell 语法: $(basename "$f")"
        else
            bad "Shell 语法错误: $f"
        fi
    done
fi

# Python 脚本语法
if command -v python3 >/dev/null 2>&1; then
    for f in "${REPO_ROOT}"/scripts/*.py; do
        [ -f "$f" ] || continue
        if python3 -m py_compile "$f" 2>/dev/null; then
            ok "Python 语法: $(basename "$f")"
        else
            bad "Python 语法错误: $f"
        fi
    done
fi

# 关键模板存在性
for required in \
    "templates/xray/xr.json" \
    "templates/xray/01_reality_inbounds.json" \
    "templates/xray/02_xhttp_inbounds.json" \
    "templates/xray/02_xhttp_compat_inbounds.json" \
    "templates/xray/03_vmess_ws_inbounds.json" \
    "templates/sing-box/sb.json" \
    "templates/supervisord/daemon.ini" \
    "scripts/entrypoint.py" \
    "scripts/sb_xray/config_builder.py" \
    "scripts/shoutrrr-forwarder.py"; do
    if [ -f "${REPO_ROOT}/${required}" ]; then
        ok "存在: ${required}"
    else
        bad "缺失: ${required}"
    fi
done

# Python 包健康检查（Phase 0 起 entrypoint 开始 Python 化）
section "Python 包健康检查"

if [ -f "${REPO_ROOT}/pyproject.toml" ] && [ -d "${REPO_ROOT}/scripts/sb_xray" ]; then
    # 导入冒烟:sb_xray 应无副作用 import
    if PYTHONPATH="${REPO_ROOT}/scripts" python3 -c 'import sb_xray, sb_xray.routing; assert sb_xray.__version__' 2>/dev/null; then
        ok "Python: sb_xray 包可导入且暴露 __version__"
    else
        bad "Python: sb_xray 包导入失败"
        PYTHONPATH="${REPO_ROOT}/scripts" python3 -c 'import sb_xray' 2>&1 | head -3 | sed 's/^/    /'
    fi

    # pytest 单测(若 pytest 可用)——用 exit code 判断,不依赖输出格式
    if command -v pytest >/dev/null 2>&1; then
        pytest_output=$(cd "${REPO_ROOT}" && pytest --no-header 2>&1)
        pytest_rc=$?
        if [ "${pytest_rc}" -eq 0 ]; then
            ok "Python: pytest 通过"
        else
            bad "Python: pytest 失败 (exit ${pytest_rc})"
            echo "${pytest_output}" | tail -10 | sed 's/^/    /'
        fi
    else
        info "pytest 未安装，跳过单测(本地 pip install -e '.[dev]' 后可启用)"
    fi

    # ruff 静态检查(若 ruff 可用)
    if command -v ruff >/dev/null 2>&1; then
        if (cd "${REPO_ROOT}" && ruff check scripts/sb_xray tests 2>&1); then
            ok "Python: ruff check 通过"
        else
            bad "Python: ruff check 失败"
        fi
    else
        info "ruff 未安装，跳过静态检查"
    fi
else
    info "pyproject.toml / sb_xray 包尚未就绪，跳过 Python 检查"
fi

# M1 规约 grep 级别校验
section "M1 规约校验"

if grep -q '"trustedXForwardedFor"' "${REPO_ROOT}/templates/xray/02_xhttp_inbounds.json" && \
   grep -q '"trustedXForwardedFor"' "${REPO_ROOT}/templates/xray/02_xhttp_compat_inbounds.json" && \
   grep -q '"trustedXForwardedFor"' "${REPO_ROOT}/templates/xray/03_vmess_ws_inbounds.json"; then
    ok "M1-1: 3 个入站均含 trustedXForwardedFor"
else
    bad "M1-1: trustedXForwardedFor 缺失"
fi

if grep -q '"enableParallelQuery": true' "${REPO_ROOT}/templates/xray/xr.json"; then
    ok "M1-2: enableParallelQuery 已启用"
else
    bad "M1-2: enableParallelQuery 未启用"
fi

if grep -q '"serveStale": true' "${REPO_ROOT}/templates/xray/xr.json"; then
    ok "M1-2: serveStale 乐观缓存已启用"
else
    bad "M1-2: serveStale 未启用"
fi

if grep -q '"maskAddress": "/16+/64"' "${REPO_ROOT}/templates/xray/xr.json"; then
    ok "M1-3: log.maskAddress 已配置"
else
    bad "M1-3: log.maskAddress 未配置"
fi

# M1-4 已作废：PR #5505 的 buildMphCache 被 PR #5814 revert，新方案自动生效
# 改为验证 Python 下载器里包含 revert 注释，防止后续 contributor 再次尝试落地
if grep -q 'PR #5814' "${REPO_ROOT}/scripts/sb_xray/geo.py" 2>/dev/null \
    || grep -q 'PR #5814' "${REPO_ROOT}/docs/10-implementation-notes.md" 2>/dev/null; then
    ok "M1-4: buildMphCache 规划已正确回退（PR #5505 被 PR #5814 revert）"
else
    bad "M1-4: 未见 revert 说明注释"
fi

if grep -q 'program:shoutrrr-forwarder' "${REPO_ROOT}/templates/supervisord/daemon.ini"; then
    ok "M1-5: shoutrrr-forwarder supervisord program 已注册"
else
    bad "M1-5: shoutrrr-forwarder program 未注册"
fi

if [ "$(grep -c '"webhook"' "${REPO_ROOT}/templates/xray/xr.json")" -ge 4 ]; then
    ok "M1-6: 4 条 ban 规则均已接 webhook"
else
    bad "M1-6: webhook 规则数不足 4"
fi

if grep -q 'tls_ping_diagnose' "${REPO_ROOT}/scripts/sb_xray/display.py"; then
    ok "M1-7: tls ping 诊断命令已集成"
else
    bad "M1-7: tls ping 诊断命令未集成"
fi

# ---- M2 规约（2026-04 起 adv 轨已并入主轨，三轨→两轨）--------------------------
section "M2 规约校验"

# M2-2/M2-5 —— adv 能力已合并到主轨 02_xhttp（三轨→两轨）
if [ -f "${REPO_ROOT}/templates/xray/02_xhttp_inbounds.json" ] \
 && grep -q '"xPaddingQueryParam"' "${REPO_ROOT}/templates/xray/02_xhttp_inbounds.json" \
 && grep -q '"UplinkDataPlacement"' "${REPO_ROOT}/templates/xray/02_xhttp_inbounds.json" \
 && grep -q '"fragment"' "${REPO_ROOT}/templates/xray/02_xhttp_inbounds.json"; then
    ok "M2-2/M2-5: 02_xhttp 主轨已含 obfs 新字段 + Finalmask fragment（adv 已并入）"
else
    bad "M2-2/M2-5: 02_xhttp 主轨未含 adv 字段集"
fi

# M2-3 VMess-WS adv 已删除（2026-04-21 决策）：
# v2rayN 客户端不支持 vmess URL 的 fm= 字段，实测订阅里的 Vmess-Adv 节点握手失败；
# 同时为缓解小内存节点（内存不超过 512 MB）OOM，减少 xray worker 占用
# 未来 fm= 标准落地或客户端 UI 支持手动配置时，git show d2de076 找回
if [ ! -f "${REPO_ROOT}/templates/xray/03_vmess_ws_adv_inbounds.json" ] \
 && ! grep -q 'vmessws-adv' "${REPO_ROOT}/templates/nginx/http.conf"; then
    ok "M2-3: VMess-WS adv 已清理（模板/nginx 路由双删）"
else
    bad "M2-3: VMess-WS adv 残留"
fi

# M2-Adv-Retired 反向校验：adv 轨已并入主轨（2026-04）
if [ ! -f "${REPO_ROOT}/templates/xray/02_xhttp_adv_inbounds.json" ] \
 && ! grep -q 'v2rayn_adv\|xhttp_reality_adv' "${REPO_ROOT}/scripts/sb_xray/subscription.py" \
 && ! grep -q 'xhttp-adv' "${REPO_ROOT}/templates/nginx/http.conf"; then
    ok "M2-Adv-Retired: v2rayn-adv 三轨已退役，并入主轨（模板/nginx/subscription.py 三处清理完成）"
else
    bad "M2-Adv-Retired: adv 残留（模板/nginx/subscription.py 未彻底清理）"
fi

# ---- M3 规约 -----------------------------------------------------------------
section "M3 规约校验"

if grep -q 'XRAY_REVERSE_UUID' "${REPO_ROOT}/scripts/sb_xray/config_builder.py" \
 && grep -q 'ENABLE_REVERSE' "${REPO_ROOT}/scripts/sb_xray/config_builder.py"; then
    ok "M3-1/M3-4: ENABLE_REVERSE feature flag + XRAY_REVERSE_UUID 已接入 config_builder"
else
    bad "M3-1/M3-4: config_builder reverse 逻辑缺失"
fi

if grep -q 'ENABLE_REVERSE' "${REPO_ROOT}/Dockerfile"; then
    ok "M3-4: Dockerfile ENABLE_REVERSE=false 默认值已注册"
else
    bad "M3-4: Dockerfile 未注册 ENABLE_REVERSE"
fi

if grep -q 'reverse' "${REPO_ROOT}/scripts/sb_xray/config_builder.py" \
 && grep -q 'r-tunnel' "${REPO_ROOT}/scripts/sb_xray/config_builder.py"; then
    ok "M3-2: config_builder Python 注入 reverse client + routing 规则"
else
    bad "M3-2: config_builder reverse 注入片段缺失"
fi

if [ -f "${REPO_ROOT}/templates/reverse_bridge/client.json" ]; then
    ok "M3-3: 落地机 bridge 客户端模板已就位（独立目录，不会被 xray 主进程加载）"
else
    bad "M3-3: reverse_bridge/client.json 缺失"
fi

if [ -f "${REPO_ROOT}/docs/06-reverse-proxy-guide.md" ]; then
    ok "M3-5: docs/06-reverse-proxy-guide 指南已就位"
else
    bad "M3-5: docs/06-reverse-proxy-guide.md 缺失"
fi

# ---- M4 规约 -----------------------------------------------------------------
section "M4 规约校验"

# M4-1：xray hy2 模板就位 + sing-box hy2 已删（迁移）
if [ -f "${REPO_ROOT}/templates/xray/04_hy2_inbounds.json" ] \
   && [ ! -f "${REPO_ROOT}/templates/sing-box/01_hysteria2_inbounds.json" ]; then
    ok "M4-1: Hy2 已从 sing-box 迁移到 xray 原生入站"
else
    bad "M4-1: Hy2 迁移不完整（xray 04_hy2 缺失或 sing-box hy2 残留）"
fi

# M4-3/4/6：XHTTP-H3 / XICMP / XDNS 模板就位
missing_m4=()
for f in 02_xhttp_h3 05_xicmp_emergency 06_xdns_emergency; do
    [ -f "${REPO_ROOT}/templates/xray/${f}_inbounds.json" ] || missing_m4+=("$f")
done
if [ "${#missing_m4[@]}" -eq 0 ]; then
    ok "M4-3/4/6: XHTTP-H3 / XICMP / XDNS 入站模板全部就位"
else
    bad "M4-3/4/6: 缺少模板: ${missing_m4[*]}"
fi

# M4-接入：entrypoint 按 ENABLE_* flag 过滤渲染（Hy2 / XHTTP-H3 已永久启用，无开关；仅 emergency 通道有 flag）
if grep -q 'ENABLE_XICMP' "${REPO_ROOT}/scripts/sb_xray/config_builder.py" \
   && grep -q 'ENABLE_XDNS' "${REPO_ROOT}/scripts/sb_xray/config_builder.py"; then
    ok "M4-接入: config_builder feature-flag 过滤已接入（emergency 通道）"
else
    bad "M4-接入: config_builder 缺少 emergency feature-flag 接入"
fi

# M4-env：Dockerfile 注册默认值
if grep -q 'ENV PORT_XHTTP_H3=' "${REPO_ROOT}/Dockerfile" \
   && grep -q 'ENV PORT_XICMP_ID=' "${REPO_ROOT}/Dockerfile" \
   && grep -q 'ENV PORT_XDNS=' "${REPO_ROOT}/Dockerfile"; then
    ok "M4-env: Dockerfile 已注册 PORT_*/ENABLE_* 默认值"
else
    bad "M4-env: Dockerfile 缺少 M4 环境变量默认值"
fi

# M4-Hy2-permanent: ENABLE_HY2 开关已移除（Hy2 永久走 xray）
if ! grep -rq 'ENABLE_HY2' "${REPO_ROOT}/scripts/sb_xray" "${REPO_ROOT}/scripts/entrypoint.py" \
   && ! grep -q 'ENV ENABLE_HY2' "${REPO_ROOT}/Dockerfile"; then
    ok "M4-Hy2-permanent: ENABLE_HY2 开关已彻底移除，Hy2 永久由 xray 接管"
else
    bad "M4-Hy2-permanent: ENABLE_HY2 残留（应在 Dockerfile/scripts 全部清理）"
fi

# M4-H3-permanent: ENABLE_XHTTP_H3 开关已移除，H3 永久启用 + 进主轨
if ! grep -rq 'ENABLE_XHTTP_H3' "${REPO_ROOT}/scripts/sb_xray" "${REPO_ROOT}/scripts/entrypoint.py" \
   && ! grep -q 'ENV ENABLE_XHTTP_H3' "${REPO_ROOT}/Dockerfile"; then
    ok "M4-H3-permanent: ENABLE_XHTTP_H3 开关已彻底移除，H3 永久启用"
else
    bad "M4-H3-permanent: ENABLE_XHTTP_H3 残留（应在 Dockerfile/scripts 全部清理）"
fi

# M4-订阅：XHTTP-H3 进入 v2rayn 主轨 + v2rayn-adv 订阅（无条件）
if grep -q 'build_xhttp_h3_link' "${REPO_ROOT}/scripts/sb_xray/subscription.py" \
   && ! grep -q 'xhttp_h3_adv\|XHTTP_H3_ADV' "${REPO_ROOT}/scripts/sb_xray/subscription.py"; then
    ok "M4-订阅: subscription.py XHTTP-H3 已进 v2rayn 主轨（无 adv-only 条件）"
else
    bad "M4-订阅: subscription.py 的 XHTTP-H3 未移出 adv-only 条件或未进主轨"
fi

# ---- 容器运行时检查（可选）-------------------------------------------------
if [ "${SKIP_COMPOSE:-0}" = "1" ]; then
    info "SKIP_COMPOSE=1，跳过运行时检查"
else
    section "容器运行时检查"
    if ! command -v docker >/dev/null 2>&1; then
        warn "docker 不可用，跳过运行时检查"
    elif ! docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "${CONTAINER}"; then
        warn "容器 ${CONTAINER} 未运行，跳过运行时检查（可用 SKIP_COMPOSE=1 静默）"
    else
        # supervisord program 状态
        if docker exec "${CONTAINER}" supervisorctl status 2>/dev/null | grep -qE 'FATAL|EXITED'; then
            bad "supervisord 中存在 FATAL/EXITED program"
            docker exec "${CONTAINER}" supervisorctl status
        else
            ok "所有 supervisord program 正常"
        fi

        # xray -test
        if docker exec "${CONTAINER}" xray -test -confdir /sb-xray/xray/ >/dev/null 2>&1; then
            ok "xray 配置语法校验通过"
        else
            bad "xray -test 失败"
        fi

        # MPH 缓存：PR #5505 已被 PR #5814 revert，运行时自动优化，无需缓存文件
        info "MPH: 运行时自动优化（PR #5505 已 revert，无缓存文件）"

        # shoutrrr forwarder 健康探针
        if docker exec "${CONTAINER}" curl -fs http://127.0.0.1:18085/healthz >/dev/null 2>&1; then
            ok "shoutrrr-forwarder /healthz 响应"
        else
            bad "shoutrrr-forwarder /healthz 不响应"
        fi
    fi
fi

# ---- 汇总 -----------------------------------------------------------------
section "汇总"
echo "通过: ${GREEN}${PASS}${NC}  失败: ${RED}${FAIL}${NC}"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
