#!/bin/sh
# gl-inet.sh — 统一 GL.iNet 一键工具箱 (BE3600 / BE6500 / MT-3000)
# 由 be3600.sh / be6500.sh / mt3000.sh / mt3000-overlay.sh 合并而来

# ---- 颜色输出（合并超集）----
red() { echo -e "\033[31m\033[01m$1\033[0m"; }
green() { echo -e "\033[32m\033[01m$1\033[0m"; }
greeninfo() { echo -e "\033[32m\033[01m[INFO] $1\033[0m"; }
blueinfo() { echo -e "\033[32m\033[01m$1\033[0m"; }
yellow() { echo -e "\033[33m\033[01m$1\033[0m"; }
blue() { echo -e "\033[34m\033[01m$1\033[0m"; }
light_magenta() { echo -e "\033[95m\033[01m$1\033[0m"; }
light_yellow() { echo -e "\033[93m\033[01m$1\033[0m"; }
purple() { echo -e "\033[38;5;141m$1\033[0m"; }
cyan() { echo -e "\033[38;2;0;255;255m$1\033[0m"; }

# ---- 全局 ----
third_party_source="https://istore.linkease.com/repo/all/nas_luci"
HTTP_HOST="https://cafe.cpolar.cn/wkdaily/gl/raw/branch/main"
FIRMWARE_MIN_VERSION="4.7.2"

# ====================================================================
# 函数定义区（后续 Task 往此处追加）
# ====================================================================

main() {
    :  # 占位，Task 8 实现
}

# 可测性守卫：GLINET_LIB=1 source 时仅加载函数，不进菜单
[ -n "${GLINET_LIB:-}" ] || main "$@"
