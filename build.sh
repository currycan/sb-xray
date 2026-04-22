#!/bin/bash
set -e

# 颜色定义
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m' # 无颜色

# 辅助函数: 获取 GitHub API Token 头 (如果有 GITHUB_TOKEN 环境变量)
get_auth_header() {
    if [ -n "$GITHUB_TOKEN" ]; then
        echo "-H \"Authorization: token $GITHUB_TOKEN\""
    else
        echo ""
    fi
}

# 辅助函数: 执行 CURL 请求 (处理错误并返回空字符串)
fetch_url() {
    local url=$1
    local token_header=""
    if [ -n "$GITHUB_TOKEN" ]; then
        token_header="-H 'Authorization: token $GITHUB_TOKEN'"
    fi

    # 打印 URL 到 stderr 用于调试
    echo -e "${BLUE}[GET] ${url}${NC}" >&2

    # 执行请求，出错时返回空，不中止脚本 (|| echo "")
    # 使用 eval 处理带空格的 header 参数
    if [ -n "$GITHUB_TOKEN" ]; then
        curl -sSf -H "Authorization: token $GITHUB_TOKEN" "$url" 2>/dev/null || echo ""
    else
        curl -sSf "$url" 2>/dev/null || echo ""
    fi
}

# 辅助函数: 获取最新 Release Tag
get_latest_release() {
    local repo=$1
    local url="https://api.github.com/repos/$repo/releases/latest"
    local response=$(fetch_url "$url")

    if [ -n "$response" ]; then
        echo "$response" | jq -r '.tag_name'
    else
        echo ""
    fi
}

# 辅助函数: 获取 GitHub Release JSON（按 repo@tag 文件缓存）
# 相同 release 的多个 asset 只触发一次 API 调用，避免速率限额
# 使用文件缓存而非关联数组，保持 bash 3.2 兼容（macOS /bin/bash）
_RELEASE_CACHE_DIR="${TMPDIR:-/tmp}/sbxray-release-cache-$$"
mkdir -p "$_RELEASE_CACHE_DIR"
trap 'rm -rf "$_RELEASE_CACHE_DIR"' EXIT
get_release_json() {
    local repo=$1 tag=$2
    local key
    key=$(printf '%s@%s' "$repo" "$tag" | tr '/' '_')
    local cache_file="$_RELEASE_CACHE_DIR/$key.json"
    if [ ! -s "$cache_file" ]; then
        fetch_url "https://api.github.com/repos/$repo/releases/tags/$tag" > "$cache_file"
    fi
    cat "$cache_file"
}

# 辅助函数: 从 GitHub Release 资产列表中获取某文件的 sha256 digest
# 用法: get_asset_digest <repo> <tag> <asset_name>
# 返回纯 64 位 hex；失败返回空
get_asset_digest() {
    local repo=$1 tag=$2 asset=$3
    get_release_json "$repo" "$tag" | jq -r --arg n "$asset" '.assets[] | select(.name == $n) | .digest // empty' 2>/dev/null | sed -n 's|^sha256:||p'
}

# 辅助函数: 检查 GitHub API 速率限额；余额过低时提示设置 GITHUB_TOKEN
check_gh_rate_limit() {
    local need=$1  # 预计需要多少请求
    local rate_json remaining reset
    if [ -n "$GITHUB_TOKEN" ]; then
        rate_json=$(curl -sSf -H "Authorization: token $GITHUB_TOKEN" "https://api.github.com/rate_limit" 2>/dev/null)
    else
        rate_json=$(curl -sSf "https://api.github.com/rate_limit" 2>/dev/null)
    fi
    remaining=$(echo "$rate_json" | jq -r '.rate.remaining // 0' 2>/dev/null)
    reset=$(echo "$rate_json" | jq -r '.rate.reset // 0' 2>/dev/null)
    if [ -z "$remaining" ] || [ "$remaining" -lt "$need" ] 2>/dev/null; then
        local reset_human
        reset_human=$(date -r "$reset" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo "unknown")
        echo -e "${RED}✗ GitHub API 余额不足：剩 ${remaining}，需要 ${need}。重置时间：${reset_human}${NC}" >&2
        if [ -z "$GITHUB_TOKEN" ]; then
            echo -e "${YELLOW}  提示：export GITHUB_TOKEN=<token> 后上限从 60/h 提升到 5000/h${NC}" >&2
        fi
        exit 1
    fi
    echo -e "  ${BLUE}GitHub API 余额：${remaining} (需要 ≥${need})${NC}"
}

# 辅助函数: 获取最新 Stable Tag
get_latest_stable_tag() {
    local repo=$1
    local url="https://api.github.com/repos/$repo/tags?per_page=100"
    local response=$(fetch_url "$url")

    if [ -n "$response" ]; then
        echo "$response" | jq -r '[.[] | select(.name | test("rc|beta|alpha") | not)] | .[0].name'
    else
        echo ""
    fi
}

# 辅助函数: 将脚本中某组件的默认版本更新为最新获取值
# ============================================================
# 解析 CLI 模式
#
#   ./build.sh                 默认离线模式：从 versions.json 读取
#                              版本号 + digests，纯本地构建镜像，不触网。
#                              CI 每日把最新 versions + digests 写进
#                              versions.json，本地 git pull 即可与 CI
#                              产物一致。
#
#   ./build.sh refresh         刷新模式：调用 GitHub API 拉最新 versions
#                              + digests，写回 versions.json，然后走
#                              完整构建流程。等价于 CI 每日跑的动作。
#
#   ./build.sh --local         单架构 linux/amd64 + --load，不推送
#                              registry；可与两种模式组合。
#
#   ./build.sh default         旧的离线模式别名，保留向后兼容。
# ============================================================

LOCAL_BUILD=false
MODE=offline
for arg in "$@"; do
    case "$arg" in
        --local)          LOCAL_BUILD=true ;;
        refresh)          MODE=refresh ;;
        default|offline)  MODE=offline ;;
    esac
done

VERSIONS_JSON_PATH="$(cd "$(dirname "$0")" && pwd)/versions.json"

# 从 versions.json 读取缓存的 version（offline 模式用）
get_cached_version() {
    local key=$1
    [ -f "$VERSIONS_JSON_PATH" ] || { echo ""; return; }
    jq -r --arg k "$key" '.[$k] // empty' "$VERSIONS_JSON_PATH" 2>/dev/null
}

XRAY_VERSION_FINAL=""

# 获取各组件版本
if [ "$MODE" == "offline" ]; then
    echo -e "${BLUE}离线模式：从 versions.json 读取组件版本...${NC}"
    SHOUTRRR_TAG=$(get_cached_version shoutrrr)
    MIHOMO_TAG=$(get_cached_version mihomo)
    HTTP_META_VERSION=$(get_cached_version http_meta)
    SUB_STORE_FRONTEND_VERSION=$(get_cached_version sub_store_frontend)
    SUB_STORE_BACKEND_VERSION=$(get_cached_version sub_store_backend)
    SUI_TAG=$(get_cached_version s_ui)
    DUFS_TAG=$(get_cached_version dufs)
    CLOUDFLARED_VERSION=$(get_cached_version cloudflared)
    XUI_TAG=$(get_cached_version x_ui)
    SING_BOX_TAG=$(get_cached_version sing_box)
    XRAY_TAG=$(get_cached_version xray)
else
    echo -e "${BLUE}刷新模式：从 GitHub API 获取最新版本信息...${NC}"
    SHOUTRRR_TAG=$(get_latest_release "containrrr/shoutrrr")
    MIHOMO_TAG=$(get_latest_release "MetaCubeX/mihomo")
    HTTP_META_VERSION=$(get_latest_release "xream/http-meta")
    SUB_STORE_FRONTEND_VERSION=$(get_latest_release "sub-store-org/Sub-Store-Front-End")
    SUB_STORE_BACKEND_VERSION=$(get_latest_release "sub-store-org/Sub-Store")
    SUI_TAG=$(get_latest_release "alireza0/s-ui")
    DUFS_TAG=$(get_latest_stable_tag "sigoden/dufs")
    CLOUDFLARED_VERSION=$(get_latest_stable_tag "cloudflare/cloudflared")
    XUI_TAG=$(get_latest_stable_tag "MHSanaei/3x-ui")
    SING_BOX_TAG=$(get_latest_stable_tag "SagerNet/sing-box")
    XRAY_TAG=$(get_latest_stable_tag "XTLS/Xray-core")
fi

# 处理版本号并构建 Docker 参数
BUILD_ARGS=""

# 检查版本函数
# 参数:
#   $1 = 组件显示名称（日志用）
#   $2 = 版本号（可带 v 前缀）
#   $3 = Docker build-arg 变量名
# 版本为空时直接退出 — 上游要么是 versions.json 缺字段（离线模式下运
# 行 `./build.sh refresh` 刷新），要么是 GitHub API 调用失败（刷新模
# 式下网络问题），无论哪种都不应用任何 fallback 硬编码值。
check_version() {
    local name=$1
    local version=$2
    local arg_name=$3

    # 版本格式校验（与 .github/workflows/daily-build.yml 中 validate_version 一致）
    # 拒绝含 shell 元字符的 tag，避免 $BUILD_ARGS 展开时注入 docker buildx 参数
    _validate_semver() {
        [[ "$1" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?(-[a-zA-Z0-9.]+)?$ ]]
    }

    if [ -z "$version" ] || [ "$version" == "null" ]; then
        if [ "$MODE" == "offline" ]; then
            printf "%-25s ${RED}versions.json 中未找到该字段${NC}\n" "${name}:"
            echo -e "${YELLOW}  请运行 \`./build.sh refresh\` 从 GitHub API 拉取并写回 versions.json${NC}" >&2
        else
            printf "%-25s ${RED}GitHub API 获取失败${NC}\n" "${name}:"
        fi
        exit 1
    fi

    local stripped="${version#v}"
    if ! _validate_semver "$stripped"; then
        printf "%-25s ${RED}版本 %q 格式非法，拒绝构建（可能含注入字符）${NC}\n" "${name}:" "$version"
        exit 1
    fi

    printf "%-25s ${GREEN}%s${NC}\n" "${name}:" "${stripped}"
    BUILD_ARGS="${BUILD_ARGS} --build-arg ${arg_name}=${stripped}"
    if [ "$name" == "Xray" ]; then
        XRAY_VERSION_FINAL=$stripped
    fi
}

check_version "Shoutrrr"        "$SHOUTRRR_TAG"               "SHOUTRRR_VERSION"
check_version "Mihomo"          "$MIHOMO_TAG"                 "MIHOMO_VERSION"
check_version "Http-Meta"       "$HTTP_META_VERSION"          "HTTP_META_VERSION"
check_version "Sub-Store Front" "$SUB_STORE_FRONTEND_VERSION" "SUB_STORE_FRONTEND_VERSION"
check_version "Sub-Store Back"  "$SUB_STORE_BACKEND_VERSION"  "SUB_STORE_BACKEND_VERSION"
check_version "s-ui"            "$SUI_TAG"                    "SUI_VERSION"
check_version "Dufs"            "$DUFS_TAG"                   "DUFS_VERSION"
check_version "Cloudflared"     "$CLOUDFLARED_VERSION"        "CLOUDFLARED_VERSION"
check_version "3x-ui"           "$XUI_TAG"                    "XUI_VERSION"
check_version "Sing-box"        "$SING_BOX_TAG"               "SING_BOX_VERSION"
check_version "Xray"            "$XRAY_TAG"                   "XRAY_VERSION"

# 刷新模式：把新获取到的 versions 写回 versions.json（与 digests 同步更新）
if [ "$MODE" == "refresh" ]; then
    echo -e "${BLUE}刷新 versions.json（versions 段）...${NC}"
    _tmp_versions=$(mktemp)
    jq \
        --arg shoutrrr             "${SHOUTRRR_TAG#v}" \
        --arg mihomo               "${MIHOMO_TAG#v}" \
        --arg http_meta            "${HTTP_META_VERSION#v}" \
        --arg sub_store_frontend   "${SUB_STORE_FRONTEND_VERSION#v}" \
        --arg sub_store_backend    "${SUB_STORE_BACKEND_VERSION#v}" \
        --arg s_ui                 "${SUI_TAG#v}" \
        --arg dufs                 "${DUFS_TAG#v}" \
        --arg cloudflared          "${CLOUDFLARED_VERSION#v}" \
        --arg x_ui                 "${XUI_TAG#v}" \
        --arg sing_box             "${SING_BOX_TAG#v}" \
        --arg xray                 "${XRAY_TAG#v}" \
        '. + {
            shoutrrr: $shoutrrr,
            mihomo: $mihomo,
            http_meta: $http_meta,
            sub_store_frontend: $sub_store_frontend,
            sub_store_backend: $sub_store_backend,
            s_ui: $s_ui,
            dufs: $dufs,
            cloudflared: $cloudflared,
            x_ui: $x_ui,
            sing_box: $sing_box,
            xray: $xray
        }' "$VERSIONS_JSON_PATH" > "$_tmp_versions" && mv "$_tmp_versions" "$VERSIONS_JSON_PATH"
    echo -e "  ${GREEN}✓ versions 段已写回 versions.json${NC}"
fi

TAG_VERSION=$XRAY_VERSION_FINAL

# ============================================================
# 下载完整性：为每个不自带 checksum 文件的组件从 GitHub API 取 digest
# shoutrrr 和 xray 在 Dockerfile 内联使用上游 checksums/.dgst，不在此列
# ============================================================
echo -e "${BLUE}获取各组件发布文件的 SHA256...${NC}"

# 11 个 digest key 列表（便于校验/遍历）
DIGEST_KEYS="http_meta_bundle_sha256 http_meta_tpl_sha256 sub_store_backend_sha256 \
  mihomo_amd64_sha256 mihomo_arm64_sha256 dufs_amd64_sha256 dufs_arm64_sha256 \
  cloudflared_amd64_sha256 cloudflared_arm64_sha256 sing_box_amd64_sha256 sing_box_arm64_sha256"

VERSIONS_JSON_PATH="$(cd "$(dirname "$0")" && pwd)/versions.json"

# 从 versions.json 读取缓存的 digest（default 模式用）
# 返回值通过 stdout；字段不存在或为空时返回空字符串
get_cached_digest() {
    local key=$1
    [ -f "$VERSIONS_JSON_PATH" ] || { echo ""; return; }
    jq -r --arg k "$key" '.digests[$k] // empty' "$VERSIONS_JSON_PATH" 2>/dev/null
}

# 校验：任何一项为空即拒绝构建
_require_sha() {
    local name=$1 val=$2
    if [ -z "$val" ]; then
        echo -e "${RED}✗ ${name} 未设置${NC}" >&2
        exit 1
    fi
    printf "  %-32s ${GREEN}%s${NC}\n" "${name}:" "${val:0:12}…"
}

if [ "$MODE" == "offline" ]; then
    # ---------- 离线模式：纯本地读取 versions.json，不触网 ----------
    echo -e "  ${BLUE}从 ${VERSIONS_JSON_PATH#$PWD/} 读取缓存的 SHA256...${NC}"
    HTTP_META_BUNDLE_SHA=$(get_cached_digest http_meta_bundle_sha256)
    HTTP_META_TPL_SHA=$(get_cached_digest http_meta_tpl_sha256)
    SUB_STORE_BACKEND_SHA=$(get_cached_digest sub_store_backend_sha256)
    MIHOMO_AMD64_SHA=$(get_cached_digest mihomo_amd64_sha256)
    MIHOMO_ARM64_SHA=$(get_cached_digest mihomo_arm64_sha256)
    DUFS_AMD64_SHA=$(get_cached_digest dufs_amd64_sha256)
    DUFS_ARM64_SHA=$(get_cached_digest dufs_arm64_sha256)
    CLOUDFLARED_AMD64_SHA=$(get_cached_digest cloudflared_amd64_sha256)
    CLOUDFLARED_ARM64_SHA=$(get_cached_digest cloudflared_arm64_sha256)
    SING_BOX_AMD64_SHA=$(get_cached_digest sing_box_amd64_sha256)
    SING_BOX_ARM64_SHA=$(get_cached_digest sing_box_arm64_sha256)

    # 任一缺失则提示用户先跑 refresh 模式
    _missing=0
    for key in $DIGEST_KEYS; do
        if [ -z "$(get_cached_digest "$key")" ]; then _missing=1; break; fi
    done
    if [ $_missing -eq 1 ]; then
        echo -e "${RED}✗ versions.json 中缺少缓存 digest${NC}" >&2
        echo -e "${YELLOW}  请运行 \`./build.sh refresh\` 从 GitHub API 拉取并写回 digests${NC}" >&2
        exit 1
    fi
else
    # ---------- 刷新模式：从 GitHub API 获取 digests，写回 versions.json ----------
    check_gh_rate_limit 6

    _extract_arg() { echo "$BUILD_ARGS" | grep -oE "$1=[^ ]+" | cut -d= -f2- | tail -1; }
    MIHOMO_V=$(_extract_arg MIHOMO_VERSION)
    HTTP_META_V=$(_extract_arg HTTP_META_VERSION)
    SUB_STORE_BACKEND_V=$(_extract_arg SUB_STORE_BACKEND_VERSION)
    DUFS_V=$(_extract_arg DUFS_VERSION)
    CLOUDFLARED_V=$(_extract_arg CLOUDFLARED_VERSION)
    SING_BOX_V=$(_extract_arg SING_BOX_VERSION)

    HTTP_META_BUNDLE_SHA=$(get_asset_digest xream/http-meta "$HTTP_META_V" "http-meta.bundle.js")
    HTTP_META_TPL_SHA=$(get_asset_digest xream/http-meta "$HTTP_META_V" "tpl.yaml")
    SUB_STORE_BACKEND_SHA=$(get_asset_digest sub-store-org/Sub-Store "$SUB_STORE_BACKEND_V" "sub-store.bundle.js")
    MIHOMO_AMD64_SHA=$(get_asset_digest MetaCubeX/mihomo "v$MIHOMO_V" "mihomo-linux-amd64-v${MIHOMO_V}.gz")
    MIHOMO_ARM64_SHA=$(get_asset_digest MetaCubeX/mihomo "v$MIHOMO_V" "mihomo-linux-arm64-v${MIHOMO_V}.gz")
    DUFS_AMD64_SHA=$(get_asset_digest sigoden/dufs "v$DUFS_V" "dufs-v${DUFS_V}-x86_64-unknown-linux-musl.tar.gz")
    DUFS_ARM64_SHA=$(get_asset_digest sigoden/dufs "v$DUFS_V" "dufs-v${DUFS_V}-arm-unknown-linux-musleabihf.tar.gz")
    CLOUDFLARED_AMD64_SHA=$(get_asset_digest cloudflare/cloudflared "$CLOUDFLARED_V" "cloudflared-linux-amd64")
    CLOUDFLARED_ARM64_SHA=$(get_asset_digest cloudflare/cloudflared "$CLOUDFLARED_V" "cloudflared-linux-arm64")
    SING_BOX_AMD64_SHA=$(get_asset_digest SagerNet/sing-box "v$SING_BOX_V" "sing-box-${SING_BOX_V}-linux-amd64.tar.gz")
    SING_BOX_ARM64_SHA=$(get_asset_digest SagerNet/sing-box "v$SING_BOX_V" "sing-box-${SING_BOX_V}-linux-arm64.tar.gz")
fi

_require_sha "HTTP_META_BUNDLE_SHA256"  "$HTTP_META_BUNDLE_SHA"
_require_sha "HTTP_META_TPL_SHA256"     "$HTTP_META_TPL_SHA"
_require_sha "SUB_STORE_BACKEND_SHA256" "$SUB_STORE_BACKEND_SHA"
_require_sha "MIHOMO_AMD64_SHA256"      "$MIHOMO_AMD64_SHA"
_require_sha "MIHOMO_ARM64_SHA256"      "$MIHOMO_ARM64_SHA"
_require_sha "DUFS_AMD64_SHA256"        "$DUFS_AMD64_SHA"
_require_sha "DUFS_ARM64_SHA256"        "$DUFS_ARM64_SHA"
_require_sha "CLOUDFLARED_AMD64_SHA256" "$CLOUDFLARED_AMD64_SHA"
_require_sha "CLOUDFLARED_ARM64_SHA256" "$CLOUDFLARED_ARM64_SHA"
_require_sha "SING_BOX_AMD64_SHA256"    "$SING_BOX_AMD64_SHA"
_require_sha "SING_BOX_ARM64_SHA256"    "$SING_BOX_ARM64_SHA"

# 刷新模式下把新获取的 digest 写回 versions.json，供后续 offline 模式使用
if [ "$MODE" == "refresh" ] && [ -f "$VERSIONS_JSON_PATH" ]; then
    _tmp_versions=$(mktemp)
    # 顺序与 build.sh 中 check_version 调用保持一致，便于人工检查
    # 注意：不使用 -S（会按字母表排序破坏语义顺序）
    jq \
        --arg mihomo_amd64_sha256      "$MIHOMO_AMD64_SHA" \
        --arg mihomo_arm64_sha256      "$MIHOMO_ARM64_SHA" \
        --arg http_meta_bundle_sha256  "$HTTP_META_BUNDLE_SHA" \
        --arg http_meta_tpl_sha256     "$HTTP_META_TPL_SHA" \
        --arg sub_store_backend_sha256 "$SUB_STORE_BACKEND_SHA" \
        --arg dufs_amd64_sha256        "$DUFS_AMD64_SHA" \
        --arg dufs_arm64_sha256        "$DUFS_ARM64_SHA" \
        --arg cloudflared_amd64_sha256 "$CLOUDFLARED_AMD64_SHA" \
        --arg cloudflared_arm64_sha256 "$CLOUDFLARED_ARM64_SHA" \
        --arg sing_box_amd64_sha256    "$SING_BOX_AMD64_SHA" \
        --arg sing_box_arm64_sha256    "$SING_BOX_ARM64_SHA" \
        '.digests = {
            mihomo_amd64_sha256: $mihomo_amd64_sha256,
            mihomo_arm64_sha256: $mihomo_arm64_sha256,
            http_meta_bundle_sha256: $http_meta_bundle_sha256,
            http_meta_tpl_sha256: $http_meta_tpl_sha256,
            sub_store_backend_sha256: $sub_store_backend_sha256,
            dufs_amd64_sha256: $dufs_amd64_sha256,
            dufs_arm64_sha256: $dufs_arm64_sha256,
            cloudflared_amd64_sha256: $cloudflared_amd64_sha256,
            cloudflared_arm64_sha256: $cloudflared_arm64_sha256,
            sing_box_amd64_sha256: $sing_box_amd64_sha256,
            sing_box_arm64_sha256: $sing_box_arm64_sha256
        }' "$VERSIONS_JSON_PATH" > "$_tmp_versions" && mv "$_tmp_versions" "$VERSIONS_JSON_PATH"
    echo -e "  ${GREEN}✓ digests 段已写回 versions.json${NC}"
fi

BUILD_ARGS="${BUILD_ARGS} \
  --build-arg HTTP_META_BUNDLE_SHA256=${HTTP_META_BUNDLE_SHA} \
  --build-arg HTTP_META_TPL_SHA256=${HTTP_META_TPL_SHA} \
  --build-arg SUB_STORE_BACKEND_SHA256=${SUB_STORE_BACKEND_SHA} \
  --build-arg MIHOMO_AMD64_SHA256=${MIHOMO_AMD64_SHA} \
  --build-arg MIHOMO_ARM64_SHA256=${MIHOMO_ARM64_SHA} \
  --build-arg DUFS_AMD64_SHA256=${DUFS_AMD64_SHA} \
  --build-arg DUFS_ARM64_SHA256=${DUFS_ARM64_SHA} \
  --build-arg CLOUDFLARED_AMD64_SHA256=${CLOUDFLARED_AMD64_SHA} \
  --build-arg CLOUDFLARED_ARM64_SHA256=${CLOUDFLARED_ARM64_SHA} \
  --build-arg SING_BOX_AMD64_SHA256=${SING_BOX_AMD64_SHA} \
  --build-arg SING_BOX_ARM64_SHA256=${SING_BOX_ARM64_SHA}"

echo -e "${BLUE}开始构建 Docker 镜像...${NC}"
echo -e "Tags: currycan/sb-xray:${TAG_VERSION}  currycan/sb-xray:latest"

if [ "$LOCAL_BUILD" == "true" ]; then
    echo -e "${YELLOW}本地构建模式：单架构 linux/amd64 + --load，不 push 到 registry${NC}"
    # shellcheck disable=SC2086
    docker buildx build \
      --platform linux/amd64 \
      $BUILD_ARGS \
      --tag currycan/sb-xray:"${TAG_VERSION}-local" \
      --tag currycan/sb-xray:local \
      --load .
    echo -e "${GREEN}✓ 本地构建完成: currycan/sb-xray:${TAG_VERSION}-local + :local${NC}"
else
    # shellcheck disable=SC2086
    docker buildx build \
      --platform linux/amd64,linux/arm64 \
      $BUILD_ARGS \
      --tag currycan/sb-xray:"${TAG_VERSION}" \
      --tag currycan/sb-xray:latest \
      --push .
    echo -e "${GREEN}✓ 构建完成: currycan/sb-xray:${TAG_VERSION} + :latest${NC}"
fi
