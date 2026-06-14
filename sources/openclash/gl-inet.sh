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

# 判断系统是否为iStoreOS
is_iStoreOS() {
	DISTRIB_ID=$(cat /etc/openwrt_release | grep "DISTRIB_ID" | cut -d "'" -f 2)
	# 检查DISTRIB_ID的值是否等于'iStoreOS'
	if [ "$DISTRIB_ID" = "iStoreOS" ]; then
		return 0 # true
	else
		return 1 # false
	fi
}

## 去除opkg签名
remove_check_signature_option() {
	local opkg_conf="/etc/opkg.conf"
	sed -i '/option check_signature/d' "$opkg_conf"
}

## 添加opkg签名
add_check_signature_option() {
	local opkg_conf="/etc/opkg.conf"
	echo "option check_signature 1" >>"$opkg_conf"
}

#设置第三方软件源
setup_software_source() {
	## 传入0和1 分别代表原始和第三方软件源
	if [ "$1" -eq 0 ]; then
		echo "# add your custom package feeds here" >/etc/opkg/customfeeds.conf
		##如果是iStoreOS系统,还原软件源之后，要添加签名
		if is_iStoreOS; then
			add_check_signature_option
		else
			echo
		fi
		# 还原软件源之后更新
		opkg update
	elif [ "$1" -eq 1 ]; then
		#传入1 代表设置第三方软件源 先要删掉签名
		remove_check_signature_option
		# 先删除再添加以免重复
		echo "# add your custom package feeds here" >/etc/opkg/customfeeds.conf
		echo "src/gz third_party_source $third_party_source" >>/etc/opkg/customfeeds.conf
		# 设置第三方源后要更新
		opkg update
	else
		echo "Invalid option. Please provide 0 or 1."
	fi
}

# 添加主机名映射(解决安卓原生TV首次连不上wifi的问题)
add_dhcp_domain() {
	local domain_name="time.android.com"
	local domain_ip="203.107.6.88"

	# 检查是否存在相同的域名记录
	existing_records=$(uci show dhcp | grep "dhcp.@domain\[[0-9]\+\].name='$domain_name'")
	if [ -z "$existing_records" ]; then
		# 添加新的域名记录
		uci add dhcp domain
		uci set "dhcp.@domain[-1].name=$domain_name"
		uci set "dhcp.@domain[-1].ip=$domain_ip"
		uci commit dhcp
	else
		echo
	fi
}

#添加出处信息
add_author_info() {
	uci set system.@system[0].description='wukongdaily'
	uci set system.@system[0].notes='文档说明:
    https://tvhelper.cpolar.cn/'
	uci commit system
}

##获取软路由型号信息
get_router_name() {
	model_info=$(cat /tmp/sysinfo/model)
	echo "$model_info"
}

get_router_hostname() {
	hostname=$(uci get system.@system[0].hostname)
	echo "$hostname 路由器"
}

# 安装体积非常小的文件传输软件 默认上传位置/tmp/upload/
do_install_filetransfer() {
	mkdir -p /tmp/luci-app-filetransfer/
	cd /tmp/luci-app-filetransfer/
	wget --user-agent="Mozilla/5.0" -O luci-app-filetransfer_all.ipk "$HTTP_HOST/luci-app-filetransfer/luci-app-filetransfer_all.ipk"
	wget --user-agent="Mozilla/5.0" -O luci-lib-fs_1.0-14_all.ipk "$HTTP_HOST/luci-app-filetransfer/luci-lib-fs_1.0-14_all.ipk"
	opkg install *.ipk --force-depends
}

recovery() {
	echo "⚠️ 警告：此操作将恢复出厂设置，所有配置将被清除！"
	echo "⚠️ 请确保已备份必要数据。"
	read -p "是否确定执行恢复出厂设置？(yes/[no]): " confirm

	if [ "$confirm" = "yes" ]; then
		echo "正在执行恢复出厂设置..."
		# 安静执行 firstboot，不显示其内部的提示信息
		firstboot -y >/dev/null 2>&1
		echo "操作完成，正在重启设备..."
		reboot
	else
		echo "操作已取消。"
	fi
}

# 防止误操作 隐藏首页无用的元素
hide_ui_elements() {

    TARGET="/www/luci-static/quickstart/style.css"
    MARKER="/* hide custom luci elements */"

    # 如果没有追加过，就添加
    if ! grep -q "$MARKER" "$TARGET"; then
        cat <<EOF >>"$TARGET"

$MARKER
/* 隐藏首页格式化按钮 */
.value-data button {
  display: none !important;
}

/* 隐藏网络页的第 3 个 item */
#main > div > div.network-container.align-c > div > div > div:nth-child(3) {
  display: none !important;
}

/* 隐藏网络页的第 5 个 item */
#main > div > div.network-container.align-c > div > div > div:nth-child(5) {
  display: none !important;
}

/* 隐藏 feature-card.pink */
#main > div > div.card-container > div.feature-card.pink {
  display: none !important;
}

EOF
        echo "✅ 自定义元素已隐藏"
    else
        echo "⚠️ 无需重复操作"
    fi
}

#自定义风扇开始工作的温度
set_glfan_temp() {

	is_integer() {
		if [[ $1 =~ ^[0-9]+$ ]]; then
			return 0 # 是整数
		else
			return 1 # 不是整数
		fi
	}
	echo "兼容带风扇机型的GL-iNet路由器"
	echo "请输入风扇开始工作的温度(建议40-70之间的整数):"
	read temp

	if is_integer "$temp"; then
		uci set glfan.@globals[0].temperature="$temp"
		uci set glfan.@globals[0].warn_temperature="$temp"
		uci set glfan.@globals[0].integration=4
		uci set glfan.@globals[0].differential=20
		uci commit glfan
		/etc/init.d/gl_fan restart
		echo "设置成功！稍等片刻,请查看风扇转动情况"
	else
		echo "错误: 请输入整数."
	fi
}

toggle_adguardhome() {
	status=$(uci get adguardhome.config.enabled)

	if [ "$status" -eq 1 ]; then
		echo "Disabling AdGuardHome..."
		uci set adguardhome.config.enabled='0' >/dev/null 2>&1
		uci commit adguardhome >/dev/null 2>&1
		/etc/init.d/adguardhome disable >/dev/null 2>&1
		/etc/init.d/adguardhome stop >/dev/null 2>&1
		green "AdGuardHome 已关闭"
	else
		echo "Enabling AdGuardHome..."
		uci set adguardhome.config.enabled='1' >/dev/null 2>&1
		uci commit adguardhome >/dev/null 2>&1
		/etc/init.d/adguardhome enable >/dev/null 2>&1
		/etc/init.d/adguardhome start >/dev/null 2>&1
		green "AdGuardHome 已开启 访问 http://192.168.8.1:3000"
	fi
}

#高级卸载
advanced_uninstall(){
	echo "📥 正在下载 高级卸载插件..."
	wget -O /tmp/advanced_uninstall.run $HTTP_HOST/luci-app-uninstall.run && chmod +x /tmp/advanced_uninstall.run
	sh /tmp/advanced_uninstall.run
}

add_custom_feed() {
	# 先清空配置
	echo "# add your custom package feeds here" >/etc/opkg/customfeeds.conf
	# Prompt the user to enter the feed URL
	echo "请输入自定义软件源的地址(通常是https开头 aarch64_cortex-a53 结尾):"
	read feed_url
	if [ -n "$feed_url" ]; then
		echo "src/gz custom_feed $feed_url" >>/etc/opkg/customfeeds.conf
		opkg update
		if [ $? -eq 0 ]; then
			echo "已添加并更新列表."
		else
			echo "已添加但更新失败,请检查网络或重试."
		fi
	else
		echo "Error: Feed URL not provided. No changes were made."
	fi
}

remove_custom_feed() {
	echo "# add your custom package feeds here" >/etc/opkg/customfeeds.conf
	opkg update
	if [ $? -eq 0 ]; then
		echo "已删除并更新列表."
	else
		echo "已删除了自定义软件源但更新失败,请检查网络或重试."
	fi
}

# 检查是否安装了 whiptail
check_whiptail_installed() {
	if [ -e /usr/bin/whiptail ]; then
		return 0
	else
		return 1
	fi
}

#定义一个通用的Dialog
show_whiptail_dialog() {
	#判断是否具备whiptail dialog组件
	if check_whiptail_installed; then
		echo "whiptail has installed"
	else
		echo "# add your custom package feeds here" >/etc/opkg/customfeeds.conf
		opkg update
		opkg install whiptail
	fi
	local title="$1"
	local message="$2"
	local function_definition="$3"
	whiptail --title "$title" --yesno "$message" 15 60 --yes-button "是" --no-button "否"
	if [ $? -eq 0 ]; then
		eval "$function_definition"
	else
		echo "退出"
		exit 0
	fi
}

# 执行重启操作
do_reboot() {
	reboot
}

#提示用户要重启
show_reboot_tips() {
	reboot_code='do_reboot'
	show_whiptail_dialog "重启提醒" "           $(get_router_hostname)\n           一键风格化运行完成.\n           为了更好的清理临时缓存,\n           您是否要重启路由器?" "$reboot_code"
}

do_install_depends_ipk() {
	wget --user-agent="Mozilla/5.0" -O "/tmp/luci-lua-runtime_all.ipk" "$HTTP_HOST/theme/luci-lua-runtime_all.ipk"
	wget --user-agent="Mozilla/5.0" -O "/tmp/libopenssl3.ipk" "$HTTP_HOST/theme/libopenssl3.ipk"
	wget --user-agent="Mozilla/5.0" -O "/tmp/luci-compat.ipk" "$HTTP_HOST/theme/luci-compat.ipk"
	opkg install "/tmp/luci-lua-runtime_all.ipk"
	opkg install "/tmp/libopenssl3.ipk"
	opkg install "/tmp/luci-compat.ipk"
}

# 单独安装 argon 主题（合并超集：含登录按钮中文化 sed 修复）
do_install_argon_skin() {
    echo "正在尝试安装argon主题......."
    do_install_depends_ipk
    opkg update
    opkg install luci-lib-ipkg
    wget --user-agent="Mozilla/5.0" -O "/tmp/luci-theme-argon.ipk" "$HTTP_HOST/theme/luci-theme-argon-master_2.2.9.4_all.ipk"
    wget --user-agent="Mozilla/5.0" -O "/tmp/luci-app-argon-config.ipk" "$HTTP_HOST/theme/luci-app-argon-config_0.9_all.ipk"
    wget --user-agent="Mozilla/5.0" -O "/tmp/luci-i18n-argon-config-zh-cn.ipk" "$HTTP_HOST/theme/luci-i18n-argon-config-zh-cn.ipk"
    cd /tmp/
    opkg install luci-theme-argon.ipk luci-app-argon-config.ipk luci-i18n-argon-config-zh-cn.ipk
    if [ $? -eq 0 ]; then
        echo "argon主题 安装成功"
        uci set luci.main.mediaurlbase='/luci-static/argon'
        uci set luci.main.lang='zh_cn'
        uci commit
        sed -i 's/value="<%:Login%>"/value="登录"/' /usr/lib/lua/luci/view/themes/argon/sysauth.htm
        echo "重新登录web页面后, 查看新主题 "
    else
        echo "argon主题 安装失败! 建议再执行一次!再给我一个机会!事不过三!"
    fi
}

# 安装 [官方辅助UI] 插件 by 论坛 iBelieve
do_install_ui_helper() {
    echo "⚠️ 请您确保当前固件版本大于 $FIRMWARE_MIN_VERSION，若低于此版本建议先升级。"
    read -p "👉 如果您已确认，请按 [回车] 继续；否则按 Ctrl+C 或输入任意内容后回车退出：" user_input
    if [ -n "$user_input" ]; then
        echo "🚫 用户取消安装。"
        return 1
    fi
    local ipk_file="/tmp/glinjector_3.0.5-6_all.ipk"
    local sha_file="${ipk_file}.sha256"
    echo "📥 正在下载 IPK 及 SHA256 校验文件..."
    wget -O "$sha_file" "$HTTP_HOST/ui/glinjector_3.0.5-6_all.ipk.sha256" || { echo "❌ 下载 SHA256 文件失败"; return 1; }
    wget --user-agent="Mozilla/5.0" -O "$ipk_file" "$HTTP_HOST/ui/glinjector_3.0.5-6_all.ipk" || { echo "❌ 下载 IPK 文件失败"; return 1; }
    echo "🔐 正在进行 SHA256 校验..."
    cd "$(dirname "$ipk_file")"
    sha256sum -c "$sha_file" || { echo "❌ 校验失败：文件已损坏或未完整下载"; rm -f "$ipk_file"; return 1; }
    echo "✅ 校验通过，开始安装..."
    opkg update
    opkg install "$ipk_file"
}

# 应用 arch.conf（按 profile 选源）
arch_conf_apply() {
    if ! wget -O /etc/opkg/arch.conf "$HTTP_HOST/$ARCH_CONF"; then
        red "下载 arch.conf 失败，脚本终止。"
        exit 1
    fi
}

# iStore 安装（按 profile 分支）
do_istore() {
    if [ "$ISTORE_METHOD" = isopkg ]; then
        do_istore_isopkg
    else
        do_istore_wget
    fi
}

do_istore_wget() {
	echo "do_istore 64bit ==================>"
	opkg update
	# 定义目标 URL 和本地目录
	URL="https://repo.istoreos.com/repo/all/store/"
	DIR="/tmp/ipk_store"

	# 创建目录
	mkdir -p "$DIR"
	cd "$DIR" || exit 1

	for ipk in $(wget -qO- "$URL" | grep -oE 'href="[^"]+\.ipk"' | cut -d'"' -f2); do
		echo "下载 $ipk"
		wget -q "${URL}${ipk}"
	done

	# 安装所有下载的 .ipk 包
	opkg install ./*.ipk

	#调整a53架构优先级
	arch_conf_apply

}

do_istore_isopkg() {
	echo "do_istore method==================>"
	ISTORE_REPO=https://istore.linkease.com/repo/all/store
	FCURL="curl --fail --show-error"

	curl -V >/dev/null 2>&1 || {
		echo "prereq: install curl"
		opkg info curl | grep -Fqm1 curl || opkg update
		opkg install curl
	}

	IPK=$($FCURL "$ISTORE_REPO/Packages.gz" | zcat | grep -m1 '^Filename: luci-app-store.*\.ipk$' | sed -n -e 's/^Filename: \(.\+\)$/\1/p')

	[ -n "$IPK" ] || exit 1

	$FCURL "$ISTORE_REPO/$IPK" | tar -xzO ./data.tar.gz | tar -xzO ./bin/is-opkg >/tmp/is-opkg

	[ -s "/tmp/is-opkg" ] || exit 1

	chmod 755 /tmp/is-opkg
	/tmp/is-opkg update
	# /tmp/is-opkg install taskd
	/tmp/is-opkg opkg install --force-reinstall luci-lib-taskd luci-lib-xterm
	/tmp/is-opkg opkg install --force-reinstall luci-app-store || exit $?
	[ -s "/etc/init.d/tasks" ] || /tmp/is-opkg opkg install --force-reinstall taskd
	[ -s "/usr/lib/lua/luci/cbi.lua" ] || /tmp/is-opkg opkg install luci-compat >/dev/null 2>&1

}

# iStore 风格化（按 profile 分支）
install_istore_os_style() {
    if [ "$ISTORE_METHOD" = isopkg ]; then
        install_istore_os_style_mt
    else
        install_istore_os_style_be
    fi
}

install_istore_os_style_be() {
	##设置Argon 紫色主题
	do_install_argon_skin
	#增加终端
	opkg install luci-i18n-ttyd-zh-cn
	#默认安装必备工具SFTP 方便下载文件 比如finalshell等工具可以直接浏览路由器文件
	opkg install openssh-sftp-server
	#默认使用体积很小的文件传输：系统——文件传输
	do_install_filetransfer
	FILE_PATH="/etc/openwrt_release"
	NEW_DESCRIPTION="Openwrt like iStoreOS Style by wukongdaily"
	CONTENT=$(cat $FILE_PATH)
	UPDATED_CONTENT=$(echo "$CONTENT" | sed "s/DISTRIB_DESCRIPTION='[^']*'/DISTRIB_DESCRIPTION='$NEW_DESCRIPTION'/")
	echo "$UPDATED_CONTENT" >$FILE_PATH
}

install_istore_os_style_mt() {
	##设置Argon 紫色主题
	do_install_argon_skin
	#增加首页终端图标
	opkg install ttyd
	#默认使用体积很小的文件传输：系统——文件传输
	do_install_filetransfer
	#默认安装必备工具SFTP 方便下载文件 比如finalshell等工具可以直接浏览路由器文件
	is-opkg install app-meta-sftp
	is-opkg install 'app-meta-ddnsto'
	# 安装磁盘管理
	is-opkg install 'app-meta-diskman'
	FILE_PATH="/etc/openwrt_release"
	NEW_DESCRIPTION="Openwrt like iStoreOS Style by wukongdaily"
	CONTENT=$(cat $FILE_PATH)
	UPDATED_CONTENT=$(echo "$CONTENT" | sed "s/DISTRIB_DESCRIPTION='[^']*'/DISTRIB_DESCRIPTION='$NEW_DESCRIPTION'/")
	echo "$UPDATED_CONTENT" >$FILE_PATH

}

# 基础初始化（WAN 开放仅对 WAN_OPEN=1 的 profile）
setup_base_init() {
    add_author_info
    add_dhcp_domain
    uci set system.@system[0].zonename='Asia/Shanghai'
    uci set system.@system[0].timezone='CST-8'
    uci commit system
    /etc/init.d/system reload
    if [ "$WAN_OPEN" = 1 ]; then
        # 打开防火墙 wan input，方便主路由访问
        uci set firewall.@zone[1].input='ACCEPT'
        uci commit firewall
    fi
    green "安装完毕！请使用8080端口访问luci界面：http://192.168.8.1:8080"
    green "作者更多动态务必收藏：https://tvhelper.cpolar.cn/"
}

#设置风扇工作温度
setup_cpu_fans() {
	#设定温度阀值,cpu高于48度,则风扇开始工作
	uci set glfan.@globals[0].temperature=50
	uci set glfan.@globals[0].warn_temperature=50
	uci set glfan.@globals[0].integration=4
	uci set glfan.@globals[0].differential=20
	uci commit glfan
	/etc/init.d/gl_fan restart
}

# 恢复原厂 OPKG 配置（仅 MT-3000）
recovery_opkg_settings() {
    echo "# add your custom package feeds here" >/etc/opkg/customfeeds.conf
    local router_name
    router_name=$(get_router_name)
    case "$router_name" in
    *3000*)
        echo "Router name contains '3000'."
        wget -O /etc/opkg/distfeeds.conf "$HTTP_HOST/mt-3000/distfeeds.conf"
        ;;
    *)
        echo "当前机型无需恢复 distfeeds。"
        ;;
    esac
}

main() {
    :  # 占位，Task 8 实现
}

# 可测性守卫：GLINET_LIB=1 source 时仅加载函数，不进菜单
[ -n "${GLINET_LIB:-}" ] || main "$@"
