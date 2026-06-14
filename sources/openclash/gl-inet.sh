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

# 解析命令行参数（支持 --device 覆盖机型）
parse_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --device) GLINET_DEVICE="$2"; shift 2 ;;
            *) shift ;;
        esac
    done
}

# unknown 机型时让用户手选
prompt_device_select() {
    red "无法自动识别机型（/tmp/sysinfo/model 未匹配）。"
    echo "请手动选择您的设备："
    echo " 1. GL-iNet BE3600"
    echo " 2. GL-iNet BE6500"
    echo " 3. GL-iNet MT-3000"
    read -p "输入 1/2/3: " sel
    case "$sel" in
        1) GLINET_DEVICE=be3600 ;;
        2) GLINET_DEVICE=be6500 ;;
        3) GLINET_DEVICE=mt3000 ;;
        *) red "无效选择，退出。"; exit 1 ;;
    esac
    detect_profile
}

# 识别机型并设定 profile 表
detect_profile() {
    local model dev=""
    [ -n "${GLINET_DEVICE:-}" ] && dev="$GLINET_DEVICE"
    [ -z "$dev" ] && model=$(cat /tmp/sysinfo/model 2>/dev/null)
    case "${dev:-$model}" in
        *be3600*|*BE3600*|*3600*) dev=be3600 ;;
        *be6500*|*BE6500*|*6500*) dev=be6500 ;;
        *3000*)                   dev=mt3000 ;;
        *)                        dev=unknown ;;
    esac
    PROFILE="$dev"
    case "$PROFILE" in
        be3600) ARCH_CONF="64bit/arch.conf"; ISTORE_METHOD=wget;   QUICKSTART=full;   HAS_FAN_AUTOSET=0; HAS_DISTFEEDS=0; WAN_OPEN=0; PROFILE_NAME="GL-iNet BE3600" ;;
        be6500) ARCH_CONF="64bit/arch.conf"; ISTORE_METHOD=wget;   QUICKSTART=none;   HAS_FAN_AUTOSET=0; HAS_DISTFEEDS=0; WAN_OPEN=0; PROFILE_NAME="GL-iNet BE6500" ;;
        mt3000) ARCH_CONF="mtarch/arch.conf"; ISTORE_METHOD=isopkg; QUICKSTART=isopkg; HAS_FAN_AUTOSET=1; HAS_DISTFEEDS=1; WAN_OPEN=1; PROFILE_NAME="GL-iNet MT-3000" ;;
        unknown) prompt_device_select ;;
    esac
}

main() {
    :  # 占位，Task 8 实现
}

# 可测性守卫：GLINET_LIB=1 source 时仅加载函数，不进菜单
[ -n "${GLINET_LIB:-}" ] || main "$@"
