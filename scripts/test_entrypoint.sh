#!/usr/bin/env bash
# ==============================================================================
# test_entrypoint.sh — entrypoint.sh 单元测试套件（红绿测试）
#
# 用法: bash scripts/test_entrypoint.sh
# 说明: 通过 source 加载被测脚本，逐组验证核心函数行为。
#       重构前运行 → 验证 bug 存在（红）
#       重构后运行 → 全部通过（绿）
# ==============================================================================

set -uo pipefail

PASS=0; FAIL=0
_TEST_TMPDIR=$(mktemp -d)
trap 'rm -rf "$_TEST_TMPDIR"' EXIT

# ==============================================================================
# 断言工具
# ==============================================================================
assert_eq() {
    local desc=$1 expected=$2 actual=$3
    if [[ "$actual" == "$expected" ]]; then
        echo "  ✓ ${desc}"; (( PASS++ )) || true
    else
        echo "  ✗ ${desc}"
        echo "    期望: '${expected}'"
        echo "    实际: '${actual}'"
        (( FAIL++ )) || true
    fi
}

assert_match() {
    local desc=$1 pattern=$2 actual=$3
    if [[ "$actual" =~ $pattern ]]; then
        echo "  ✓ ${desc}"; (( PASS++ )) || true
    else
        echo "  ✗ ${desc}"
        echo "    期望匹配: '${pattern}'"
        echo "    实际:     '${actual}'"
        (( FAIL++ )) || true
    fi
}

assert_not_empty() {
    local desc=$1 actual=$2
    if [[ -n "$actual" ]]; then
        echo "  ✓ ${desc}"; (( PASS++ )) || true
    else
        echo "  ✗ ${desc} (结果为空)"; (( FAIL++ )) || true
    fi
}

# ==============================================================================
# Mock 外部命令（防止真实网络调用与系统依赖）
# ==============================================================================
curl()    { :; }                                                   # 默认空实现，各测试组按需覆盖
xray()    {
    case "${1:-}" in
        uuid)     echo "mock-uuid-$(date +%s)" ;;
        x25519)   printf "Private key: mock-priv-key\nPublic key: mock-pub-key\n" ;;
        mlkem768) printf "Seed: mock-seed\nClient: mock-client\n" ;;
    esac
}
openssl() { echo "mock-openssl-output"; }
jq()      { cat; }

# ==============================================================================
# 加载被测脚本（source 模式：BASH_SOURCE 保护跳过 exec）
# ==============================================================================
ENV_FILE="${_TEST_TMPDIR}/sb-xray.env"
SECRET_FILE="${_TEST_TMPDIR}/secret"
STATUS_FILE="${_TEST_TMPDIR}/status.env"
touch "$ENV_FILE" "$STATUS_FILE"

# shellcheck source=scripts/entrypoint.sh
source "$(dirname "$0")/entrypoint.sh"

# ==============================================================================
# T1  http_probe — 无 eval，参数数组方式调用
# ==============================================================================
echo ""
echo "▶ [T1] http_probe"

curl() { echo "HTTP/2 200"; }
assert_eq "T1-1: 返回 HTTP 200" "200" "$(http_probe "https://example.com")"

curl() { echo "HTTP/1.1 404 Not Found"; }
assert_eq "T1-2: 返回 HTTP 404" "404" "$(http_probe "https://example.com")"

curl() { :; }
assert_eq "T1-3: 超时返回 Timeout" "Timeout" "$(http_probe "https://example.com")"

curl() { echo "HTTP/1.1 200 OK"; }
assert_eq "T1-4: follow_redirect=true 返回 200" "200" "$(http_probe "https://example.com" "true")"

# ==============================================================================
# T2  ensure_var — 三种分支（含修复验证）
# ==============================================================================
echo ""
echo "▶ [T2] ensure_var"

# T2-1: 变量未在 env 也不在文件 → 执行命令并写入文件
unset TEST_VAR_A
> "$ENV_FILE"
ensure_var TEST_VAR_A echo "hello"
assert_eq     "T2-1: 命令执行后变量已 export"     "hello" "${TEST_VAR_A:-}"
assert_match  "T2-1: 变量已写入 ENV_FILE"          "TEST_VAR_A" "$(cat "$ENV_FILE")"

# T2-2: 变量已在文件中但未在当前 shell → 应从文件加载（核心 bug 修复验证）
#        原代码仅 grep 检查文件，不重新 export，导致变量仍为空
unset TEST_VAR_B
echo "export TEST_VAR_B='from_file'" >> "$ENV_FILE"
ensure_var TEST_VAR_B echo "should_not_run"
assert_eq "T2-2: 从文件加载（不重复执行命令）" "from_file" "${TEST_VAR_B:-}"

# T2-3: 变量已在当前 shell → 直接跳过
export TEST_VAR_C="already_set"
ensure_var TEST_VAR_C echo "should_not_override"
assert_eq "T2-3: 已在 env 中则跳过" "already_set" "${TEST_VAR_C:-}"

# T2-4: --no-persist 不写文件
unset TEST_VAR_D
> "$ENV_FILE"
ensure_var TEST_VAR_D --no-persist echo "mem_only"
assert_eq    "T2-4: --no-persist 变量已 export"   "mem_only" "${TEST_VAR_D:-}"
assert_eq    "T2-4: --no-persist 不写入文件"        "" "$(grep 'TEST_VAR_D' "$ENV_FILE" || true)"

# ==============================================================================
# T3  generateRandomStr
# ==============================================================================
echo ""
echo "▶ [T3] generateRandomStr"

assert_match "T3-1: port 在 32000-38000" "^3[2-7][0-9]{3}$|^38000$" "$(generateRandomStr port)"

xray() { echo "mock-uuid-1234"; }
assert_match "T3-2: uuid 非空" "." "$(generateRandomStr uuid)"

pw=$(generateRandomStr password 16)
assert_eq    "T3-3: password 长度 16"              "16" "${#pw}"
assert_match "T3-4: password 含字母数字"            "^[A-Za-z0-9]+$" "$pw"

pt=$(generateRandomStr path 32)
assert_eq    "T3-5: path 长度 32"                  "32" "${#pt}"
assert_match "T3-6: path 仅含小写字母和数字"        "^[a-z0-9]+$" "$pt"

# ==============================================================================
# T4  apply_isp_routing_logic — 各选路分支
# ==============================================================================
echo ""
echo "▶ [T4] apply_isp_routing_logic"

_reset_routing() {
    unset ISP_TAG IS_8K_SMOOTH FASTEST_PROXY_TAG proxy_max_speed DIRECT_SPEED \
          DEFAULT_ISP GEOIP_INFO IP_TYPE 2>/dev/null || true
    > "$ENV_FILE"
    > "$STATUS_FILE"
}

# T4-1: DEFAULT_ISP 手动覆盖
_reset_routing
export DEFAULT_ISP="MYISP_ISP" DIRECT_SPEED=30 proxy_max_speed=0
apply_isp_routing_logic "proxy-fallback"
assert_eq "T4-1: DEFAULT_ISP 强制覆盖" "proxy-myisp" "${ISP_TAG:-}"

# T4-2: 受限地区 + first_tag 存在 → 使用 first_tag
_reset_routing
export GEOIP_INFO="中国|1.2.3.4" IP_TYPE="hosting" DIRECT_SPEED=50 proxy_max_speed=0
apply_isp_routing_logic "proxy-first"
assert_eq "T4-2: 受限地区使用 first_tag" "proxy-first" "${ISP_TAG:-}"

# T4-3: 非住宅 IP + 有最优代理 → 使用最优代理
_reset_routing
export GEOIP_INFO="US|1.2.3.4" IP_TYPE="hosting" FASTEST_PROXY_TAG="proxy-best" \
       proxy_max_speed=80 DIRECT_SPEED=30
apply_isp_routing_logic ""   # explicit empty argument
assert_eq "T4-3: 非住宅 IP 使用最优代理" "proxy-best" "${ISP_TAG:-}"

# T4-4: 住宅 IP + 直连够快 → direct
_reset_routing
export GEOIP_INFO="SG|1.2.3.4" IP_TYPE="isp" DIRECT_SPEED=80 proxy_max_speed=0
apply_isp_routing_logic ""   # explicit empty argument
assert_eq "T4-4: 住宅 IP 直连" "direct" "${ISP_TAG:-}"

# T4-5: 有 ISP 代理时始终使用（不与直连竞速，即使直连更慢也用代理）
_reset_routing
export GEOIP_INFO="SG|1.2.3.4" IP_TYPE="isp" FASTEST_PROXY_TAG="proxy-fast" \
       proxy_max_speed=100 DIRECT_SPEED=30
apply_isp_routing_logic ""   # explicit empty argument
assert_eq "T4-5: 有 ISP 代理时始终使用代理（不与直连竞速）" "proxy-fast" "${ISP_TAG:-}"

# T4-7: 住宅 IP + 有 ISP 代理 → 依然使用代理（解锁用途，非速度竞争）
_reset_routing
export GEOIP_INFO="SG|1.2.3.4" IP_TYPE="isp" FASTEST_PROXY_TAG="proxy-kr" \
       proxy_max_speed=80 DIRECT_SPEED=200
apply_isp_routing_logic ""   # explicit empty argument
assert_eq "T4-7: 住宅 IP 有代理也用代理，不因直连更快而走直连" "proxy-kr" "${ISP_TAG:-}"

# T4-6: 原 L754 死代码修复验证
#        原条件: `ISP_TAG != "direct" && -z ISP_TAG && first_tag` — 但 if-else 已保证 ISP_TAG 非空
#        修复后: `if [[ -z ISP_TAG && -n first_tag ]]` 永远不应在 else 之后命中
#        此处直接调用 apply_isp_routing_logic 并检查 ISP_TAG 被正确设为 "direct"（无 first_tag 情况）
_reset_routing
export GEOIP_INFO="SG|1.2.3.4" IP_TYPE="isp" DIRECT_SPEED=50 proxy_max_speed=0
apply_isp_routing_logic ""   # explicit empty argument
assert_eq "T4-6: 无代理无 first_tag → direct" "direct" "${ISP_TAG:-}"

# ==============================================================================
# T5  IS_8K_SMOOTH 计算
# ==============================================================================
echo ""
echo "▶ [T5] IS_8K_SMOOTH"

# T5-1: 住宅 IP + 直连 >100 → true
_reset_routing
export IP_TYPE="isp" DIRECT_SPEED=120 proxy_max_speed=0
apply_isp_routing_logic ""   # explicit empty argument
assert_eq "T5-1: 住宅 IP 直连 >100 → smooth=true" "true" "${IS_8K_SMOOTH:-}"

# T5-2: 机房 IP + 代理 >100 → true
_reset_routing
export GEOIP_INFO="SG|1.2.3.4" IP_TYPE="hosting" FASTEST_PROXY_TAG="proxy-x" \
       proxy_max_speed=150 DIRECT_SPEED=20
apply_isp_routing_logic ""   # explicit empty argument
assert_eq "T5-2: 机房 IP 代理 >100 → smooth=true" "true" "${IS_8K_SMOOTH:-}"

# T5-3: 直连 <100 且无代理 → false
_reset_routing
export IP_TYPE="isp" DIRECT_SPEED=30 proxy_max_speed=0
apply_isp_routing_logic ""   # explicit empty argument
assert_eq "T5-3: 直连 <100 → smooth=false" "false" "${IS_8K_SMOOTH:-}"

# ==============================================================================
# T6  speed_test — 无全局 CurlARG 污染
# ==============================================================================
echo ""
echo "▶ [T6] speed_test"

# Mock curl 返回模拟字节速率（bytes/sec），3145728 ≈ 25 Mbps
curl() { echo "3145728"; }

result=$(speed_test "https://example.com/__down" "TestDirect")
assert_not_empty "T6-1: 直连测速返回非空" "$result"
assert_match     "T6-2: 直连测速返回数值"  "^[0-9]+\.[0-9]+$" "$result"

# 代理测速：验证代理参数被正确传递，不改变全局状态
result_proxy=$(speed_test "https://example.com/__down" "TestProxy" "socks5h://1.2.3.4:1080" "user:pass")
assert_not_empty "T6-3: 代理测速返回非空" "$result_proxy"
assert_match     "T6-4: 代理测速返回数值" "^[0-9]+\.[0-9]+$" "$result_proxy"

# 验证两次调用结果相同（无全局 CurlARG 污染）
result2=$(speed_test "https://example.com/__down" "TestDirect2")
assert_eq "T6-5: 代理测速后直连结果不变（无全局污染）" "$result" "$result2"

# ==============================================================================
# T7  ensure_key_pair
# ==============================================================================
echo ""
echo "▶ [T7] ensure_key_pair"

unset TEST_KEY1 TEST_KEY2
> "$ENV_FILE"
xray() { printf "Private key: mock-priv-key\nPublic key: mock-pub-key\n"; }

ensure_key_pair "TestAlgo" "xray x25519" "TEST_KEY1" "TEST_KEY2"
assert_eq "T7-1: KEY1 已 export"    "mock-priv-key" "${TEST_KEY1:-}"
assert_eq "T7-2: KEY2 已 export"    "mock-pub-key"  "${TEST_KEY2:-}"
assert_match "T7-3: KEY1 写入文件"  "TEST_KEY1"     "$(cat "$ENV_FILE")"

# 再次调用不应重新生成：从文件加载
unset TEST_KEY1 TEST_KEY2
_xray_call_count=0
xray() { (( _xray_call_count++ )) || true; printf "Private key: NEW-priv\nPublic key: NEW-pub\n"; }
ensure_key_pair "TestAlgo" "xray x25519" "TEST_KEY1" "TEST_KEY2"
assert_eq "T7-4: 已存在时从文件加载" "mock-priv-key" "${TEST_KEY1:-}"
assert_eq "T7-5: 已存在时不调用生成命令" "0" "${_xray_call_count}"

# ==============================================================================
# T8  _is_restricted_region
# ==============================================================================
echo ""
echo "▶ [T8] _is_restricted_region"

export GEOIP_INFO="中国|1.2.3.4"
_is_restricted_region && assert_eq "T8-1: 中国 → 受限" "0" "0" || assert_eq "T8-1: 中国 → 受限" "受限" "未受限"

export GEOIP_INFO="US|1.2.3.4"
_is_restricted_region && assert_eq "T8-2: 美国 → 未受限" "未受限" "受限" || assert_eq "T8-2: 美国 → 未受限" "0" "0"

export GEOIP_INFO="香港|1.2.3.4"
_is_restricted_region && assert_eq "T8-3: 香港 → 受限" "0" "0" || assert_eq "T8-3: 香港 → 受限" "受限" "未受限"

# ==============================================================================
# T9  speed_test — 多采样平均 & 部分失败容错
# ==============================================================================
echo ""
echo "▶ [T9] speed_test 多采样平均"

# T9-1: 3 次采样均成功 → 返回均值
# 3145728 bytes/sec × 8 / 1024 / 1024 = 24.00 Mbps
curl() { echo "3145728"; }
result9a=$(speed_test "https://example.com/__down" "T9-AllValid")
assert_eq "T9-1: 全部采样成功 → 均值 24.00 Mbps" "24.00" "$result9a"

# T9-2: 3 次采样中 2 次失败（返回 0），1 次成功 → 取成功样本均值
_t9_curl_n=0
curl() {
    (( _t9_curl_n++ )) || true
    [[ "$_t9_curl_n" -eq 1 ]] && echo "3145728" || echo "0"
}
result9b=$(speed_test "https://example.com/__down" "T9-TwoFail")
assert_eq "T9-2: 1次成功 2次失败 → 仍返回 24.00 Mbps（非 0）" "24.00" "$result9b"

# T9-3: 全部采样失败 → 返回 "0.00"
curl() { echo "0"; }
result9c=$(speed_test "https://example.com/__down" "T9-AllFail")
assert_eq "T9-3: 全部失败 → 0.00" "0.00" "$result9c"

# T9-4: 极小正数（如 100 bytes/sec）→ 应视为失败
# curl 返回 100 bytes/sec → kbps≈0, mbps≈0.00，但 raw > 0 为真
# 修复前: 被计为"有效样本"，日志显示 "3/3 有效样本，均值 0.00 Mbps"（矛盾）
# 修复后: 低于阈值的样本不计入有效 → 日志显示"全部采样失败"
curl() { echo "100"; }
result9d=$(speed_test "https://example.com/__down" "T9-TinySpeed" 2>/dev/null)
assert_eq "T9-4: 极小速度（100 B/s）→ 应返回 0.00" "0.00" "$result9d"
# 验证诊断日志存在（不依赖具体措辞）
_t9d_log=$(speed_test "https://example.com/__down" "T9-TinySpeed-log" 2>&1 >/dev/null)
[[ -n "$_t9d_log" ]] && _t9d_has_log="yes" || _t9d_has_log="no"
assert_eq "T9-4b: 极小速度 → 有诊断日志输出" "yes" "$_t9d_has_log"

curl() { :; }   # 恢复默认

# ==============================================================================
# T10  _test_isp_node — 容差阈值（tolerance band）
# ==============================================================================
echo ""
echo "▶ [T10] _test_isp_node 容差阈值"

proxy_max_speed=0
unset FASTEST_PROXY_TAG

# T10-1: 首个代理（阈值 = 0 × 1.15 = 0）→ 任何速度均成为最优
speed_test() { echo "50.00"; }
_test_isp_node "T10-Node1" "1.2.3.4" 1080 "user" "pass" "proxy-node1"
assert_eq "T10-1: 首个代理成为最优" "proxy-node1" "${FASTEST_PROXY_TAG:-}"
assert_eq "T10-1: proxy_max_speed 更新为 50.00" "50.00" "${proxy_max_speed:-}"

# T10-2: 第二个代理速度在容差内（50 × 1.15 = 57.5，55 < 57.5）→ 不替换
speed_test() { echo "55.00"; }
_test_isp_node "T10-Node2" "1.2.3.4" 1081 "user" "pass" "proxy-node2"
assert_eq "T10-2: 容差内（55 < 57.5）→ 不替换最优" "proxy-node1" "${FASTEST_PROXY_TAG:-}"
assert_eq "T10-2: proxy_max_speed 不变" "50.00" "${proxy_max_speed:-}"

# T10-3: 第三个代理明显更快（70 > 57.5）→ 替换最优
speed_test() { echo "70.00"; }
_test_isp_node "T10-Node3" "1.2.3.4" 1082 "user" "pass" "proxy-node3"
assert_eq "T10-3: 超过容差阈值（70 > 57.5）→ 替换最优" "proxy-node3" "${FASTEST_PROXY_TAG:-}"
assert_eq "T10-3: proxy_max_speed 更新为 70.00" "70.00" "${proxy_max_speed:-}"

# ==============================================================================
# T11  run_speed_tests_if_needed — ISP_TAG 重新评估时清除服务路由旧缓存
# ==============================================================================
echo ""
echo "▶ [T11] ISP_TAG 重新评估时服务路由缓存联动清除"

# 构造旧缓存场景：ISP_TAG 未缓存（空），但 *_OUT 有上次遗留的旧代理值
unset ISP_TAG
export CHATGPT_OUT="proxy-stale" ISP_OUT="proxy-stale" NETFLIX_OUT="proxy-stale" \
       DISNEY_OUT="proxy-stale" YOUTUBE_OUT="proxy-stale" GEMINI_OUT="proxy-stale" \
       CLAUDE_OUT="proxy-stale" SOCIAL_MEDIA_OUT="proxy-stale" TIKTOK_OUT="proxy-stale"
> "$ENV_FILE"
# STATUS_FILE 也写入旧值，验证 _sed_i 能正确清除文件内容
cat > "$STATUS_FILE" <<'EOF'
export CHATGPT_OUT='proxy-stale'
export ISP_OUT='proxy-stale'
export NETFLIX_OUT='proxy-stale'
export DISNEY_OUT='proxy-stale'
export YOUTUBE_OUT='proxy-stale'
export GEMINI_OUT='proxy-stale'
export CLAUDE_OUT='proxy-stale'
export SOCIAL_MEDIA_OUT='proxy-stale'
export TIKTOK_OUT='proxy-stale'
EOF

# mock: 速度测试直接返回定值；选路设置新 ISP_TAG
speed_test()             { echo "50.00"; }
show_report()            { :; }
apply_isp_routing_logic() {
    export ISP_TAG="proxy-new-isp"
    export IS_8K_SMOOTH="false"
    echo "export ISP_TAG='proxy-new-isp'"    >> "$STATUS_FILE"
    echo "export IS_8K_SMOOTH='false'"       >> "$STATUS_FILE"
}

run_speed_tests_if_needed

assert_eq "T11-1: 旧 CHATGPT_OUT 缓存已清除" "" "${CHATGPT_OUT:-}"
assert_eq "T11-2: 旧 ISP_OUT 缓存已清除"     "" "${ISP_OUT:-}"
assert_eq "T11-3: 旧 NETFLIX_OUT 缓存已清除" "" "${NETFLIX_OUT:-}"
assert_eq "T11-3b: 旧 DISNEY_OUT 缓存已清除"       "" "${DISNEY_OUT:-}"
assert_eq "T11-3c: 旧 YOUTUBE_OUT 缓存已清除"      "" "${YOUTUBE_OUT:-}"
assert_eq "T11-3d: 旧 GEMINI_OUT 缓存已清除"       "" "${GEMINI_OUT:-}"
assert_eq "T11-3e: 旧 CLAUDE_OUT 缓存已清除"       "" "${CLAUDE_OUT:-}"
assert_eq "T11-3f: 旧 SOCIAL_MEDIA_OUT 缓存已清除" "" "${SOCIAL_MEDIA_OUT:-}"
assert_eq "T11-3g: 旧 TIKTOK_OUT 缓存已清除"       "" "${TIKTOK_OUT:-}"
assert_eq "T11-4: 新 ISP_TAG 正确设置"       "proxy-new-isp" "${ISP_TAG:-}"
# 验证 STATUS_FILE 中旧 *_OUT 行已被 _sed_i 删除
assert_eq "T11-5: STATUS_FILE 无残留 CHATGPT_OUT" "" "$(grep '^export CHATGPT_OUT=' "$STATUS_FILE" || true)"
assert_eq "T11-6: STATUS_FILE 无残留 ISP_OUT"     "" "$(grep '^export ISP_OUT=' "$STATUS_FILE" || true)"
assert_eq "T11-5-netflix: STATUS_FILE 无残留 NETFLIX_OUT" "" "$(grep '^export NETFLIX_OUT=' "$STATUS_FILE" || true)"
assert_eq "T11-5b: STATUS_FILE 无残留 DISNEY_OUT"       "" "$(grep '^export DISNEY_OUT=' "$STATUS_FILE" || true)"
assert_eq "T11-5c: STATUS_FILE 无残留 YOUTUBE_OUT"      "" "$(grep '^export YOUTUBE_OUT=' "$STATUS_FILE" || true)"
assert_eq "T11-5d: STATUS_FILE 无残留 GEMINI_OUT"       "" "$(grep '^export GEMINI_OUT=' "$STATUS_FILE" || true)"
assert_eq "T11-5e: STATUS_FILE 无残留 CLAUDE_OUT"       "" "$(grep '^export CLAUDE_OUT=' "$STATUS_FILE" || true)"
assert_eq "T11-5f: STATUS_FILE 无残留 SOCIAL_MEDIA_OUT" "" "$(grep '^export SOCIAL_MEDIA_OUT=' "$STATUS_FILE" || true)"
assert_eq "T11-5g: STATUS_FILE 无残留 TIKTOK_OUT"       "" "$(grep '^export TIKTOK_OUT=' "$STATUS_FILE" || true)"

# ==============================================================================
# 汇总
# ==============================================================================
echo ""
echo "════════════════════════════════════════"
echo "  测试结果:  ✓ ${PASS} 通过   ✗ ${FAIL} 失败"
echo "════════════════════════════════════════"
echo ""
(( FAIL == 0 ))
