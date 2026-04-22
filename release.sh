#!/bin/bash
set -e

# 颜色定义
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m' # 无颜色

# 辅助函数: 执行 CURL 请求
fetch_url() {
    local url=$1
    if [ -n "$GITHUB_TOKEN" ]; then
        curl -sSf -H "Authorization: token $GITHUB_TOKEN" "$url" 2>/dev/null || echo ""
    else
        curl -sSf "$url" 2>/dev/null || echo ""
    fi
}

# 辅助函数: 获取最新稳定版 Tag（与 build.sh get_latest_stable_tag 对齐，过滤 rc/beta/alpha）
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

echo -e "${BLUE}正在获取最新 Xray 稳定版,以保持版本一致性...${NC}"

# 获取 Xray 最新稳定版本 (与 build.sh 逻辑一致)
XRAY_TAG=$(get_latest_stable_tag "XTLS/Xray-core")

if [ -z "$XRAY_TAG" ] || [ "$XRAY_TAG" == "null" ]; then
    echo -e "${RED}无法获取 Xray 版本，取消发布。${NC}"
    exit 1
fi

# Xray_TAG 通常自带 'v' 前缀，Docker 镜像 Tag 清除了 'v'。
# 这里使用 'v' 前缀作为当前项目的 git tag，保持语义化版本。
# Xray 例如: v1.8.8 -> Docker image: 1.8.8 -> Git release: v1.8.8

CLEAN_VERSION=${XRAY_TAG#v}
RELEASE_TAG="v${CLEAN_VERSION}"

echo -e "${GREEN}获取到 Xray 版本: ${XRAY_TAG}${NC}"
echo -e "${BLUE}准备将此项目打上标签: ${RELEASE_TAG} (对应 Docker tag: ${CLEAN_VERSION})${NC}"

# 检查本地是否已经有此 tag
if git rev-parse "$RELEASE_TAG" >/dev/null 2>&1; then
    echo -e "${YELLOW}本地已存在标签 $RELEASE_TAG，将被跳过。${NC}"
else
    echo -e "创建本地 Git 标签 $RELEASE_TAG"
    git tag -a "$RELEASE_TAG" -m "Release $RELEASE_TAG (Sync with Xray $CLEAN_VERSION)"
fi

# 检查是否安装了 gh 命令行工具
if command -v gh &> /dev/null; then
    echo -e "${BLUE}检测到 GitHub CLI (gh)，正在检查远端 Release...${NC}"

    # 检查 Release 是否存在
    if gh release view "$RELEASE_TAG" >/dev/null 2>&1; then
         echo -e "${YELLOW}远端已存在 Release $RELEASE_TAG，无需创建。${NC}"
    else
         echo -e "${BLUE}正在推送标签并创建 GitHub Release...${NC}"
         git push origin "$RELEASE_TAG"

         gh release create "$RELEASE_TAG" \
            --title "Release $RELEASE_TAG" \
            --notes "同步 Xray 最新版本 $RELEASE_TAG。\n对应的 Docker 镜像 tag 为 \`${CLEAN_VERSION}\`。"

         echo -e "${GREEN}Release $RELEASE_TAG 创建成功！${NC}"
    fi
else
    echo -e "${YELLOW}未检测到 GitHub CLI (gh) 工具。${NC}"
    echo -e "${BLUE}正在推送标签到远端...${NC}"
    git push origin "$RELEASE_TAG"
    echo -e "${GREEN}已推送标签 $RELEASE_TAG，请前往 GitHub 网页端手动创建 Release。${NC}"
    echo -e "或者使用安装了 gh 的环境执行此脚本。"
fi
