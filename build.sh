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
check_version "Mihomo"          "$MIHOMO_TAG"                 "MIHOMO_VERSION"             "1.19.23"
check_version "Http-Meta"       "$HTTP_META_VERSION"          "HTTP_META_VERSION"          "1.1.0"
check_version "Sub-Store Front" "$SUB_STORE_FRONTEND_VERSION" "SUB_STORE_FRONTEND_VERSION" "2.16.55"
check_version "Sub-Store Back"  "$SUB_STORE_BACKEND_VERSION"  "SUB_STORE_BACKEND_VERSION"  "2.22.1"
check_version "s-ui"            "$SUI_TAG"                    "SUI_VERSION"                "1.4.1"
check_version "Dufs"            "$DUFS_TAG"                   "DUFS_VERSION"               "0.45.0"
check_version "Cloudflared"      "$CLOUDFLARED_VERSION"        "CLOUDFLARED_VERSION"        "2026.3.0"
check_version "3x-ui"           "$XUI_TAG"                    "XUI_VERSION"                "2.8.11"
check_version "Sing-box"        "$SING_BOX_TAG"               "SING_BOX_VERSION"           "1.13.8"
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

echo -e "${BLUE}开始构建 Docker 镜像...${NC}"
echo -e "Tags: currycan/sb-xray:${TAG_VERSION}  currycan/sb-xray:latest"

# shellcheck disable=SC2086
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  $BUILD_ARGS \
  --tag currycan/sb-xray:"${TAG_VERSION}" \
  --tag currycan/sb-xray:latest \
  --push .

echo -e "${GREEN}✓ 构建完成: currycan/sb-xray:${TAG_VERSION} + :latest${NC}"
