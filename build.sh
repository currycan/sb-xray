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

# 辅助函数: 获取最新 Tag
get_latest_tag() {
    local repo=$1
    local url="https://api.github.com/repos/$repo/tags"
    local response=$(fetch_url "$url")

    if [ -n "$response" ]; then
        echo "$response" | jq -r '.[0].name'
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
update_script_default() {
    local arg_name=$1
    local fetched_tag=$2
    local script
    script="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"

    # 跳过空或无效版本
    if [ -z "$fetched_tag" ] || [ "$fetched_tag" == "null" ]; then
        return
    fi

    local clean_version="${fetched_tag#v}"

    # 从脚本中读取当前默认版本
    local current_default
    current_default=$(grep "check_version" "$script" | grep "\"${arg_name}\"" | sed "s/.*\"${arg_name}\"[[:space:]]*\"\([^\"]*\)\".*/\1/")

    if [ "$clean_version" == "$current_default" ]; then
        return
    fi

    # 跨平台兼容更新
    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' "s|\(check_version.*\"${arg_name}\"[[:space:]]*\)\"[^\"]*\"|\1\"${clean_version}\"|" "$script"
    else
        sed -i "s|\(check_version.*\"${arg_name}\"[[:space:]]*\)\"[^\"]*\"|\1\"${clean_version}\"|" "$script"
    fi

    echo -e "  ${GREEN}↑ ${arg_name}: ${current_default} -> ${clean_version}${NC}"
    VERSIONS_UPDATED=true
}

# 解析参数：支持 "default"（离线模式）与 "--local"（本地单架构构建，不 push）
LOCAL_BUILD=false
for arg in "$@"; do
    case "$arg" in
        --local) LOCAL_BUILD=true ;;
    esac
done

# 检查是否使用默认版本模式
USE_DEFAULT_VERSIONS=false
XRAY_VERSION_FINAL=""
if [ "$1" == "default" ]; then
    USE_DEFAULT_VERSIONS=true
    echo -e "${YELLOW}使用默认版本模式，跳过 API 调用...${NC}"
fi

# 获取各组件版本
if [ "$USE_DEFAULT_VERSIONS" == "true" ]; then
    # 使用默认版本，不调用 API
    SHOUTRRR_TAG=""
    MIHOMO_TAG=""
    HTTP_META_VERSION=""
    SUB_STORE_FRONTEND_VERSION=""
    SUB_STORE_BACKEND_VERSION=""
    SUI_TAG=""
    DUFS_TAG=""
    CLOUDFLARED_VERSION=""
    XUI_TAG=""
    SING_BOX_TAG=""
    XRAY_TAG=""
else
    echo -e "${BLUE}开始获取最新版本信息...${NC}"

    # 获取各组件版本
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
XRAY_TAG=$(get_latest_tag "XTLS/Xray-core")
fi

# 处理版本号并构建 Docker 参数
BUILD_ARGS=""

# 检查版本函数
# 参数:
# $1 = 组件显示名称
# $2 =获取到的版本号 (可能带 v 前缀)
# $3 = Docker build-arg 变量名
# $4 = 默认版本号 (用于 fallback)
check_version() {
    local name=$1
    local version=$2
    local arg_name=$3
    local default_version=$4

    # 版本格式校验（与 .github/workflows/daily-build.yml 中 validate_version 保持一致）
    # 拒绝含 shell 元字符的 tag，避免 $BUILD_ARGS 展开时注入 docker buildx 参数
    _validate_semver() {
        [[ "$1" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?(-[a-zA-Z0-9.]+)?$ ]]
    }
    if [ -n "$version" ] && [ "$version" != "null" ]; then
        local stripped="${version#v}"
        if ! _validate_semver "$stripped"; then
            printf "%-25s ${RED}版本 %q 格式非法，拒绝构建（可能含注入字符）${NC}\n" "${name}:" "$version"
            exit 1
        fi
    fi

    if [ -z "$version" ] || [ "$version" == "null" ]; then
        if [ -n "$default_version" ]; then
            if [ "$USE_DEFAULT_VERSIONS" == "true" ]; then
                printf "%-25s ${GREEN}%s${NC}\n" "${name}:" "${default_version}"
            else
                printf "%-25s ${YELLOW}获取失败! 使用默认版本: %s${NC}\n" "${name}:" "${default_version}"
            fi
            BUILD_ARGS="${BUILD_ARGS} --build-arg ${arg_name}=${default_version}"
            # 版本获取失败时同样记录版本号用于镜像 Tag
            if [ "$name" == "Xray" ]; then
                XRAY_VERSION_FINAL=$default_version
            fi
        else
            printf "%-25s ${RED}获取失败! 停止构建${NC}\n" "${name}:"
            exit 1
        fi
    else
        # 去除 'v' 前缀
        clean_version=${version#v}
        printf "%-25s ${GREEN}%s${NC}\n" "${name}:" "${clean_version}"
        BUILD_ARGS="${BUILD_ARGS} --build-arg ${arg_name}=${clean_version}"
        if [ "$name" == "Xray" ]; then
            XRAY_VERSION_FINAL=$clean_version
        fi
    fi
}

check_version "Shoutrrr"        "$SHOUTRRR_TAG"               "SHOUTRRR_VERSION"           "0.8.0"
check_version "Mihomo"          "$MIHOMO_TAG"                 "MIHOMO_VERSION"             "1.19.24"
check_version "Http-Meta"       "$HTTP_META_VERSION"          "HTTP_META_VERSION"          "1.1.0"
check_version "Sub-Store Front" "$SUB_STORE_FRONTEND_VERSION" "SUB_STORE_FRONTEND_VERSION" "2.16.57"
check_version "Sub-Store Back"  "$SUB_STORE_BACKEND_VERSION"  "SUB_STORE_BACKEND_VERSION"  "2.22.5"
check_version "s-ui"            "$SUI_TAG"                    "SUI_VERSION"                "1.4.1"
check_version "Dufs"            "$DUFS_TAG"                   "DUFS_VERSION"               "0.45.0"
check_version "Cloudflared"      "$CLOUDFLARED_VERSION"        "CLOUDFLARED_VERSION"        "2026.3.0"
check_version "3x-ui"           "$XUI_TAG"                    "XUI_VERSION"                "2.9.0"
check_version "Sing-box"        "$SING_BOX_TAG"               "SING_BOX_VERSION"           "1.13.9"
check_version "Xray"            "$XRAY_TAG"                   "XRAY_VERSION"               "26.4.17"

# 当从 GitHub 获取版本后，自动更新脚本中的默认版本配置
if [ "$USE_DEFAULT_VERSIONS" != "true" ]; then
    echo -e "${BLUE}检查默认版本配置是否需要更新...${NC}"
    VERSIONS_UPDATED=false
    update_script_default "SHOUTRRR_VERSION"           "$SHOUTRRR_TAG"
    update_script_default "MIHOMO_VERSION"             "$MIHOMO_TAG"
    update_script_default "HTTP_META_VERSION"          "$HTTP_META_VERSION"
    update_script_default "SUB_STORE_FRONTEND_VERSION" "$SUB_STORE_FRONTEND_VERSION"
    update_script_default "SUB_STORE_BACKEND_VERSION"  "$SUB_STORE_BACKEND_VERSION"
    update_script_default "SUI_VERSION"                "$SUI_TAG"
    update_script_default "DUFS_VERSION"               "$DUFS_TAG"
    update_script_default "CLOUDFLARED_VERSION"        "$CLOUDFLARED_VERSION"
    update_script_default "XUI_VERSION"                "$XUI_TAG"
    update_script_default "SING_BOX_VERSION"           "$SING_BOX_TAG"
    update_script_default "XRAY_VERSION"               "$XRAY_TAG"
    if [ "$VERSIONS_UPDATED" == "true" ]; then
        echo -e "${GREEN}✓ build.sh 默认版本配置已同步更新${NC}"
    else
        echo -e "${BLUE}✓ 所有默认版本均为最新，无需更新${NC}"
    fi
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

if [ "$USE_DEFAULT_VERSIONS" == "true" ]; then
    # ---------- 默认模式：纯本地读取 versions.json，不触网 ----------
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

    # 任一缺失则提示用户先跑一次非 default 模式
    _missing=0
    for key in $DIGEST_KEYS; do
        if [ -z "$(get_cached_digest "$key")" ]; then _missing=1; break; fi
    done
    if [ $_missing -eq 1 ]; then
        echo -e "${RED}✗ versions.json 中缺少缓存 digest${NC}" >&2
        echo -e "${YELLOW}  先运行一次 \`./build.sh\`（不带 default）以从 GitHub API 获取并写回；或手动编辑 versions.json 的 .digests 字段${NC}" >&2
        exit 1
    fi
else
    # ---------- API 模式：从 GitHub API 获取（会写回 versions.json 缓存） ----------
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

# API 模式下把新获取的 digest 写回 versions.json，供后续 default 模式使用
if [ "$USE_DEFAULT_VERSIONS" != "true" ] && [ -f "$VERSIONS_JSON_PATH" ]; then
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
    echo -e "  ${GREEN}✓ digests 已写回 versions.json（下次 default 模式可直接使用）${NC}"
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
