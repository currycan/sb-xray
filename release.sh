#!/bin/bash
set -e

# 颜色定义
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m' # 无颜色

# 发布 tag 与镜像 tag 一致：YY.M.D-<short=7 sha>（与 daily-build.yml / build.sh 对齐）。
# 组件版本仍以 versions.json 为单一事实源，不再同步 Xray 上游版本号。
RELEASE_TAG="$(TZ=Asia/Shanghai date +%y.%-m.%-d)-$(git rev-parse --short=7 HEAD)"

echo -e "${BLUE}准备发布 tag: ${RELEASE_TAG}（对应 Docker 镜像 currycan/sb-xray:${RELEASE_TAG}）${NC}"

# 检查本地是否已经有此 tag
if git rev-parse "$RELEASE_TAG" >/dev/null 2>&1; then
    echo -e "${YELLOW}本地已存在标签 $RELEASE_TAG，将被跳过。${NC}"
else
    echo -e "创建本地 Git 标签 $RELEASE_TAG"
    git tag -a "$RELEASE_TAG" -m "Release $RELEASE_TAG"
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
            --notes "对应的 Docker 镜像 tag 为 \`${RELEASE_TAG}\`。"

         echo -e "${GREEN}Release $RELEASE_TAG 创建成功！${NC}"
    fi
else
    echo -e "${YELLOW}未检测到 GitHub CLI (gh) 工具。${NC}"
    echo -e "${BLUE}正在推送标签到远端...${NC}"
    git push origin "$RELEASE_TAG"
    echo -e "${GREEN}已推送标签 $RELEASE_TAG，请前往 GitHub 网页端手动创建 Release。${NC}"
    echo -e "或者使用安装了 gh 的环境执行此脚本。"
fi
