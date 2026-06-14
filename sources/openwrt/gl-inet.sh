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
# 本脚本自更新源：指向 sb-xray 仓库（与 openwrt-init.sh / vps 脚本同一 raw 约定），
# 不用 HTTP_HOST（那是上游 ipk/主题素材源，仍需保留）。
SELF_UPDATE_URL="https://raw.githubusercontent.com/currycan/sb-xray/main/sources/openwrt/gl-inet.sh"
# 自定义软件源默认值（菜单12 直接回车时用）：中国大陆可达的清华 TUNA OpenWrt 镜像。
# 注：标准 OpenWrt 包，userspace 工具配合菜单①的 arch.conf 兼容可装；kmod 与 GL QSDK 内核 ABI 不同，勿装内核模块。
DEFAULT_CUSTOM_FEED="https://mirrors.tuna.tsinghua.edu.cn/openwrt/releases/23.05.5/packages/aarch64_cortex-a53/packages"

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
    local model hostname dev=""
    [ -n "${GLINET_DEVICE:-}" ] && dev="$GLINET_DEVICE"
    if [ -z "$dev" ]; then
        # GL.iNet 产品型号体现在 hostname（GL-BE6500 / GL-BE3600 / GL-MT3000）。
        # /tmp/sysinfo/model 在 MT 系列含型号数字，但 BE 系列是 Qualcomm 板名（IPQ5332…），
        # 故两者合并匹配：hostname 兜底 BE 系列的识别。
        model=$(cat /tmp/sysinfo/model 2>/dev/null)
        hostname=$(uci get system.@system[0].hostname 2>/dev/null)
        dev="$model $hostname"
    fi
    case "$dev" in
        *be3600*|*BE3600*|*3600*) dev=be3600 ;;
        *be6500*|*BE6500*|*6500*) dev=be6500 ;;
        *mt3000*|*MT3000*|*3000*) dev=mt3000 ;;
        *)                        dev=unknown ;;
    esac
    PROFILE="$dev"
    case "$PROFILE" in
        be3600) ARCH_CONF="64bit/arch.conf"; ISTORE_METHOD=wget;   QUICKSTART=full;   HAS_FAN_AUTOSET=0; HAS_DISTFEEDS=0; WAN_OPEN=0; HAS_OVERLAY=0; PROFILE_NAME="GL-iNet BE3600" ;;
        be6500) ARCH_CONF="64bit/arch.conf"; ISTORE_METHOD=wget;   QUICKSTART=none;   HAS_FAN_AUTOSET=0; HAS_DISTFEEDS=0; WAN_OPEN=0; HAS_OVERLAY=0; PROFILE_NAME="GL-iNet BE6500" ;;
        mt3000) ARCH_CONF="mtarch/arch.conf"; ISTORE_METHOD=isopkg; QUICKSTART=isopkg; HAS_FAN_AUTOSET=1; HAS_DISTFEEDS=1; WAN_OPEN=1; HAS_OVERLAY=1; PROFILE_NAME="GL-iNet MT-3000" ;;
        unknown) prompt_device_select ;;
    esac
}

# 取当前设备 LAN IP（动态，取不到回退 192.168.8.1）
lan_ip() {
    uci get network.lan.ipaddr 2>/dev/null || echo "192.168.8.1"
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
	# 幂等：已存在则不重复追加
	grep -q "option check_signature 1" "$opkg_conf" || echo "option check_signature 1" >>"$opkg_conf"
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
	echo "请输入风扇开始工作的温度(建议40-70之间的整数,直接回车默认48):"
	read temp
	[ -z "$temp" ] && temp=48

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
		green "AdGuardHome 已开启 访问 http://$(lan_ip):3000"
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
	echo "请输入自定义软件源的地址(直接回车用默认中国镜像 TUNA):"
	echo "  默认: $DEFAULT_CUSTOM_FEED"
	read feed_url
	[ -z "$feed_url" ] && feed_url="$DEFAULT_CUSTOM_FEED"
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
	NEW_DESCRIPTION="Openwrt like iStoreOS Style"
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
	NEW_DESCRIPTION="Openwrt like iStoreOS Style"
	CONTENT=$(cat $FILE_PATH)
	UPDATED_CONTENT=$(echo "$CONTENT" | sed "s/DISTRIB_DESCRIPTION='[^']*'/DISTRIB_DESCRIPTION='$NEW_DESCRIPTION'/")
	echo "$UPDATED_CONTENT" >$FILE_PATH

}

# 基础初始化（WAN 开放仅对 WAN_OPEN=1 的 profile）
setup_base_init() {
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
    green "安装完毕！请使用8080端口访问luci界面：http://$(lan_ip):8080"
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

do_luci_app_wireguard() {
	setup_software_source 0
	opkg install luci-app-wireguard
	opkg install luci-i18n-wireguard-zh-cn
	echo "请访问 http://"$(uci get network.lan.ipaddr)"/cgi-bin/luci/admin/status/wireguard  查看状态 "
	echo "也可以去接口中 查看是否增加了新的wireguard 协议的选项 "
}

update_luci_app_quickstart() {
	if [ -f "/bin/is-opkg" ]; then
		# 如果 /bin/is-opkg 存在，则执行 is-opkg update
		is-opkg update
		is-opkg install luci-i18n-quickstart-zh-cn --force-depends >/dev/null 2>&1
		opkg install iptables-mod-tproxy
		opkg install iptables-mod-socket
		opkg install iptables-mod-iprange
		green "正在更新到最新版iStoreOS首页风格 "
		TMPATH=/tmp/qstart
		mkdir -p ${TMPATH}
		app_aarch64='quickstart_0.11.13-r1_aarch64_generic.ipk'
		app_ui='luci-app-quickstart_0.12.4-r1_all.ipk'
		app_lng='luci-i18n-quickstart-zh-cn_25.090.31208-f5bf244_all.ipk'
		wget $HTTP_HOST/newquickstart/$app_aarch64 -O ${TMPATH}/$app_aarch64
		wget $HTTP_HOST/newquickstart/$app_ui -O ${TMPATH}/$app_ui
		wget $HTTP_HOST/newquickstart/$app_lng -O ${TMPATH}/$app_lng
		opkg install ${TMPATH}/*.ipk
		rm -rf ${TMPATH}
		hide_ui_elements
		yellow "恭喜您!现在你的路由器已经变成iStoreOS风格啦!"
		green "现在您可以访问8080端口 查看是否生效 http://$(lan_ip):8080"
		addr_hostname=$(uci get system.@system[0].hostname)
	else
		red "请先执行第一项 一键iStoreOS风格化"
	fi
}

#单独安装文件管理器
do_install_filemanager() {
	echo "为避免bug,安装文件管理器之前,需要先iStore商店"
	do_istore
	echo "接下来 尝试安装文件管理器......."
	is-opkg install 'app-meta-linkease'
	echo "重新登录web页面,然后您可以访问:  http://$(lan_ip)/cgi-bin/luci/admin/services/linkease/file/?path=/root"
}
#更新脚本
update_myself() {
	wget -O gl-inet.sh "$SELF_UPDATE_URL" && chmod +x gl-inet.sh
	echo "脚本已从 sb-xray 仓库更新并保存在当前目录 gl-inet.sh,现在将执行新脚本。"
	./gl-inet.sh
	exit 0
}

download_lib_quickstart() {
	# 目标目录
	REPO_URL="https://repo.istoreos.com/repo/aarch64_cortex-a53/nas/"
	DOWNLOAD_DIR="/tmp/ipk_downloads"

	# 创建下载目录
	mkdir -p "$DOWNLOAD_DIR"

	# 获取目录索引并筛选 quickstart ipk 链接
	wget -qO- "$REPO_URL" | grep -oE 'href="[^"]*quickstart[^"]*\.ipk"' |
		sed 's/href="//;s/"//' | while read -r FILE; do
		echo "📦 正在下载: $FILE"
		wget -q -P "$DOWNLOAD_DIR" "$REPO_URL$FILE"
	done

	echo "✅ 所有 quickstart 相关 IPK 文件已下载到: $DOWNLOAD_DIR"
}

download_luci_quickstart() {
	# 目标目录
	REPO_URL="https://repo.istoreos.com/repo/all/nas_luci/"
	DOWNLOAD_DIR="/tmp/ipk_downloads"

	# 创建下载目录
	mkdir -p "$DOWNLOAD_DIR"

	# 获取目录索引并筛选 quickstart ipk 链接
	wget -qO- "$REPO_URL" | grep -oE 'href="[^"]*quickstart[^"]*\.ipk"' |
		sed 's/href="//;s/"//' | while read -r FILE; do
		echo "📦 正在下载: $FILE"
		wget -q -P "$DOWNLOAD_DIR" "$REPO_URL$FILE"
	done

	echo "✅ 所有 quickstart 相关 IPK 文件已下载到: $DOWNLOAD_DIR"
}

# 首页和网络向导（BE3600 专用实现）
do_quickstart_be() {
	download_lib_quickstart
	download_luci_quickstart
	opkg install /tmp/ipk_downloads/*.ipk
	green "正在更新到最新版iStoreOS首页风格 "
	TMPATH=/tmp/qstart
	mkdir -p ${TMPATH}
	app_aarch64='quickstart_0.11.13-r1_aarch64_generic.ipk'
	app_ui='luci-app-quickstart_0.12.4-r1_all.ipk'
	app_lng='luci-i18n-quickstart-zh-cn_25.090.31208-f5bf244_all.ipk'
	wget $HTTP_HOST/newquickstart/$app_aarch64 -O ${TMPATH}/$app_aarch64
	wget $HTTP_HOST/newquickstart/$app_ui -O ${TMPATH}/$app_ui
	wget $HTTP_HOST/newquickstart/$app_lng -O ${TMPATH}/$app_lng
	opkg install ${TMPATH}/*.ipk
	rm -rf ${TMPATH}
	hide_ui_elements
	green "首页风格安装完毕！请使用8080端口访问luci界面：http://$(lan_ip):8080"
}

# 安装新首页风格
do_install_new_quickstart(){
	green "正在更新到最新版iStoreOS首页风格 "
	TMPATH=/tmp/qstart
	mkdir -p ${TMPATH}
	app_aarch64='quickstart_0.11.13-r1_aarch64_generic.ipk'
	app_ui='luci-app-quickstart_0.12.4-r1_all.ipk'
	app_lng='luci-i18n-quickstart-zh-cn_25.090.31208-f5bf244_all.ipk'
	wget $HTTP_HOST/newquickstart/$app_aarch64 -O ${TMPATH}/$app_aarch64
	wget $HTTP_HOST/newquickstart/$app_ui -O ${TMPATH}/$app_ui
	wget $HTTP_HOST/newquickstart/$app_lng -O ${TMPATH}/$app_lng
	opkg install ${TMPATH}/*.ipk
	rm -rf ${TMPATH}
	hide_ui_elements
	green "首页风格安装完毕！请使用8080端口访问luci界面：http://$(lan_ip):8080"
}

# quickstart 分发（一键流程用；按 profile）
do_quickstart() {
    case "$QUICKSTART" in
        full)   do_quickstart_be ;;
        isopkg) update_luci_app_quickstart ;;
        none)   yellow "本机型一键流程不含 quickstart（mdadm 不兼容）；如需请用菜单「手动安装/更新 quickstart」。" ;;
    esac
}

# ---- Overlay 换分区助手（搬运自 mt3000-overlay.sh，install_docker 除外）----

install_depends_apps() {
    cyan "正在安装必备工具...."
    opkg update >/dev/null 2>&1
    for pkg in lsblk fdisk; do
        if opkg list-installed | grep -qw "$pkg"; then
            cyan "$pkg 已安装。"
        else
            if opkg install "$pkg" >/dev/null 2>&1; then
                green "$pkg 安装成功。"
            else
                red "$pkg 安装失败。"
                exit 1
            fi
        fi
    done
}

# 卸载USB设备
unmount_usb_device() {
    for mount in $(mount | grep "$1" | awk '{print $3}'); do
        yellow "正在尝试卸载U盘挂载点：$mount"
        umount $mount || {
            red "警告：无法卸载挂载点 $mount。可能有文件正在被访问或权限不足。"
            exit 1
        }
        blueinfo "U盘挂载点 $mount 卸载成功。"
    done
}

create_and_format_partitions() {
    local device=$1
    # 使用fdisk -l获取设备的总容量（以字节为单位）并转换为GB
    local total_bytes=$(fdisk -l $device | grep "Disk $device:" | awk '{print $5}')
    local total_gb=$(echo "$total_bytes" | awk '{print int($1/(1024*1024*1024))}')

    if [ -n "$CUSTOM_OPKG_SIZE" ]; then
        part1_gb=$CUSTOM_OPKG_SIZE
        yellow "U盘总容量约为 $total_gb GB,您设置的自定义软件包大小为 ${part1_gb}GB。"
    else
        # 计算10%的大小，以GB为单位
        part1_gb=$((total_gb / 10))
        yellow "U盘总容量约为 $total_gb GB,第一分区大小设置为U盘容量的10% 即 ${part1_gb}GB。"
    fi
    green "计划将第一分区分配给软件包 其大小为 ${part1_gb}GB"
    cyan "没错～你没有看错,让我们任性的告别 容 量 焦 虑！"
    # 创建分区并分配空间
    {
        echo g             # 创建一个新的空DOS分区表
        echo n             # 添加一个新分区
        echo p             # 主分区
        echo 1             # 分区号1
        echo               # 第一个可用扇区（默认）
        echo +${part1_gb}G # 为第一个分区分配计算出的GB数
        echo n             # 添加第二个新分区
        echo p             # 主分区
        echo 2             # 分区号2
        echo               # 第一个可用扇区（默认，自动）
        echo               # 最后一个扇区（默认，使用剩余空间）
        echo w             # 写入并退出
    } | fdisk $device >/dev/null 2>&1

    # 给系统一点时间来识别新分区
    sleep 5

    # 格式化第一个分区为EXT4
    local new_partition1="${device}1"
    cyan "正在将 $new_partition1 格式化为EXT4文件系统..."
    mkfs.ext4 -F $new_partition1 >/dev/null 2>&1
    cyan "$new_partition1 已成功格式化为EXT4文件系统。"
    green "第2分区 ${device}2 暂不格式化,未来您可分配给docker使用"
}

# 换区到U盘
change_overlay_usb() {
    # 防护：已扩容过则确认（避免误操作重新抹盘）；一键初始化阶段1 此时未扩容，不会触发
    if overlay_is_expanded; then
        yellow "⚠️ 检测到 /overlay 已扩容到 U 盘（>1GB）。再次执行会重新抹盘并丢失 U 盘数据。"
        red "确定要重新扩容吗？(y/N)"
        read -r reconfirm
        [ "$reconfirm" = y ] || { yellow "已取消。"; return 1; }
    fi
    install_depends_apps
    blueinfo "现在开始查找USB设备分区 请稍后......"
    local USB_PARTITION=$(lsblk -dn -o NAME,RM,TYPE | awk '$2=="1" && $3=="disk" {print "/dev/"$1; exit}')
    if [ -z "$USB_PARTITION" ]; then
        red "未找到USB磁盘。"
        exit 1
    fi
    yellow "找到USB磁盘 $USB_PARTITION"
    # 清零磁盘开始部分以清除分区表和文件系统签名
    dd if=/dev/zero of=$USB_PARTITION bs=1M count=10
    sync
    # 卸载所有与该磁盘相关的挂载点
    unmount_usb_device "$USB_PARTITION"
    red "正在将U盘${USB_PARTITION}分为2个区 ..."
    create_and_format_partitions "$USB_PARTITION"

    # U盘分区的挂载点
    MOUNT_POINT="/mnt/usb_overlay"
    # 临时目录用于复制数据
    TMP_DIR="/tmp/overlay_backup"
    # 创建挂载点目录
    mkdir -p $MOUNT_POINT
    # 挂载U盘分区
    cyan "重新挂载第一分区 ${USB_PARTITION}1 到  $MOUNT_POINT"
    mount ${USB_PARTITION}1 $MOUNT_POINT >/dev/null 2>&1
    # 创建临时目录用于备份overlay数据
    mkdir -p $TMP_DIR
    # 复制当前overlay到临时目录
    cp -a /overlay/. $TMP_DIR
    # 将临时目录的数据复制到U盘
    blueinfo "正在拷贝 当前系统文件到U盘"
    cp -a $TMP_DIR/. $MOUNT_POINT
    # 更新fstab配置，以便在启动时自动挂载U盘为overlay
    blueinfo "正在更新启动时的配置文件"
    uci set fstab.overlay=mount
    uci set fstab.overlay.uuid="$(blkid -o value -s UUID ${USB_PARTITION}1)"
    uci set fstab.overlay.target="/overlay"
    uci commit fstab
    # 清理临时目录
    rm -rf $TMP_DIR
    cp /etc/config/fstab $MOUNT_POINT/fstab.bak
    cyan "overlay更换分区完成 重启验证是否成功."
    red "是否立即重启？(y/n)"
    read -r answer
    if [ "$answer" = "y" ] || [ -z "$answer" ]; then
        red "正在重启..."
        reboot
    else
        yellow "您选择了不重启"
    fi
}

# 重新绑定
rebind_usb_overlay() {
    cyan "正在重新绑定U盘设备...."
    if opkg list-installed | grep -qw "lsblk"; then
        echo
    else
        opkg update >/dev/null 2>&1
        if opkg install "lsblk" >/dev/null 2>&1; then
            echo
        else
            red "$pkg 安装失败。"
            exit 1
        fi
    fi
    local USB_DEVICE=$(lsblk -dn -o NAME,RM,TYPE | awk '$2=="1" && $3=="disk" {print "/dev/"$1; exit}')
    if [ -z "$USB_DEVICE" ]; then
        red "未找到USB磁盘。"
        exit 1
    fi
    uci set fstab.overlay=mount
    uci set fstab.overlay.uuid="$(blkid -o value -s UUID ${USB_DEVICE}1)"
    uci set fstab.overlay.target="/overlay"
    uci commit fstab
    green "重新绑定成功！ 重启后生效"
    red "正在重启..."
    reboot
}

#自定义软件包的大小
#默认为U盘容量的10%
custom_package_size() {
    while :; do
        echo "请输入想分配的软件包的大小(数字,单位:GB,直接回车默认5):"
        read size
        [ -z "$size" ] && size=5
        # 检查输入是否为数字
        if [[ $size =~ ^[0-9]+$ ]]; then
            CUSTOM_OPKG_SIZE=$size
            yellow "已设置软件包大小为:$CUSTOM_OPKG_SIZE GB"
            green "接下来,您可以执行第一项啦"
            break # 跳出循环
        else
            red "错误: 请输入一个有效的数字。"
        fi
    done
}

# Overlay 换分区助手子菜单（仅 MT-3000）
# 注：BE 系列 GL SDK4 固件的 preinit(80_mount_root) 写死 mount_ext4 "systemrw" /overlay、
# 不读 fstab 的 config mount 'overlay'，故 U 盘 extroot 扩容在 BE 上无效；菜单按 HAS_OVERLAY 门控。
overlay_menu() {
    while true; do
        clear
        echo "**********************************************************************"
        green "      Overlay 换分区助手 (U 盘扩容)"
        echo "**********************************************************************"
        echo
        cyan " 1. 一键更换 overlay 分区到 U 盘"
        cyan " 2. 自定义软件包大小(GB)"
        light_yellow " 3. 重新绑定 U 盘"
        echo
        echo " Q. 返回主菜单"
        echo
        read -p "请选择一个选项: " ochoice
        echo
        case "$ochoice" in
            1) change_overlay_usb ;;
            2) custom_package_size ;;
            3) rebind_usb_overlay ;;
            q|Q) return 0 ;;
            *) echo "无效选项，请重新选择。" ;;
        esac
        read -p "按 Enter 键继续..."
    done
}

# 一键 iStoreOS 风格化（按 profile 编排，复刻各设备现有顺序）
do_one_key_setup() {
    case "$PROFILE" in
        be3600)
            install_istore_os_style
            # 注：arch.conf 由下面 do_istore -> do_istore_wget 末尾的 arch_conf_apply 应用
            setup_base_init
            do_istore
            do_quickstart             # full
            ;;
        be6500)
            install_istore_os_style
            setup_base_init
            do_istore
            # 不跑 quickstart（mdadm 不兼容，复刻 B65 注释掉的行为）
            ;;
        mt3000)
            arch_conf_apply
            [ "$HAS_FAN_AUTOSET" = 1 ] && setup_cpu_fans
            recovery_opkg_settings
            do_istore                 # isopkg
            install_istore_os_style
            do_quickstart             # isopkg -> update_luci_app_quickstart
            setup_base_init
            ;;
    esac
}

# overlay 是否已扩到 U 盘（/overlay > 1GB 视为已扩）
overlay_is_expanded() {
    local kb
    kb=$(df /overlay 2>/dev/null | awk '/\/overlay/{print $2}')
    [ -n "$kb" ] && [ "$kb" -gt 1048576 ]
}
# 是否检测到可移除 U 盘（不依赖 lsblk）
# 全新 GL.iNet 固件默认不带 lsblk（它在 change_overlay_usb→install_depends_apps 才安装），
# 而 do_init_all 阶段1 在任何安装前就调用本函数；若沿用 lsblk，缺失时输出恒为空，
# 会把"已插 U 盘"误判为"无 U 盘"，导致 overlay 扩容阶段1 永远被跳过、选 0 与其他机型无异。
# 故改用内核自带的 sysfs removable 标志（loop/mtd/ubi 均为 0，U 盘 sda 为 1），零依赖。
init_has_usb() {
    for r in /sys/block/*/removable; do
        [ -e "$r" ] || continue
        [ "$(cat "$r" 2>/dev/null)" = 1 ] && return 0
    done
    return 1
}

# 一键初始化：跑除 6(AdGuard)/9(wireguard)/14(overlay→见两阶段)/15(更新本脚本) 外的全部，交互项用默认值。
# MT-3000 两阶段：阶段1 先扩容 overlay 到 U 盘并重启；重启后再点一次进阶段2 装其余（先扩容腾空间再装）。
# ✅ 已实测(MT-3000, GL 固件 OpenWrt 21.02 base)：overlay extroot 真生效——重启后 df /overlay 显示
#    /dev/sda1(U 盘) 挂为 /overlay 且 root overlayfs upperdir 指向之，与 BE 系列(preinit 写死 systemrw)不同。
#    用 /etc/.glinet_init_overlay_tried 标记：阶段1 重启后即便扩容没生效，也不会反复抹 U 盘，直接进阶段2。
do_init_all() {
    # —— 阶段1：仅 MT-3000、未扩容、未尝试过 → 先扩容 overlay 并自动重启 ——
    if [ "$HAS_OVERLAY" = 1 ] && ! overlay_is_expanded && [ ! -f /etc/.glinet_init_overlay_tried ]; then
        if init_has_usb; then
            yellow "===== [阶段1/2] MT-3000 先扩容 overlay 到 U 盘（默认5GB），完成后自动重启 ====="
            yellow "      ⚠️ 重启后请再点一次「0 一键初始化」完成其余安装。"
            yellow "      ⚠️ overlay 是否在本固件生效尚未验证；重启后用 df /overlay 确认是否 >1GB。"
            touch /etc/.glinet_init_overlay_tried 2>/dev/null
            CUSTOM_OPKG_SIZE=5
            change_overlay_usb </dev/null   # 内部自动重启，正常到不了下一行
            return 0
        fi
        red "未检测到 U 盘，跳过 overlay 扩容，直接安装其余（如需扩容请插 U 盘后跑菜单13）。"
    fi
    if [ "$HAS_OVERLAY" = 1 ] && [ -f /etc/.glinet_init_overlay_tried ] && ! overlay_is_expanded; then
        yellow "⚠️ 已尝试 overlay 扩容但 /overlay 仍在内部 flash——该固件可能不支持(GL SDK4 写死 systemrw)；继续装其余。"
    fi
    yellow "===== [阶段2] 一键初始化：装除 AdGuard/wireguard/overlay/更新脚本外的全部（交互项用默认值）====="
    green ">> 自定义软件源（菜单11·默认TUNA·最先执行）"; add_custom_feed </dev/null
    green ">> 一键 iStoreOS 风格化（菜单1）";            do_one_key_setup
    green ">> Argon 主题（菜单2）";                      do_install_argon_skin
    green ">> iStore 商店（菜单3）";                     do_istore
    green ">> 隐藏首页 UI（菜单4）";                     hide_ui_elements
    green ">> 风扇温度（菜单5·默认48℃）";              set_glfan_temp </dev/null
    green ">> UI 辅助插件（菜单7·自动继续）";           do_install_ui_helper </dev/null
    green ">> 高级卸载插件（菜单8）";                    advanced_uninstall </dev/null
    green ">> 文件管理器（菜单10）";                     do_install_filemanager
    green ">> quickstart 首页（菜单12）";                do_install_new_quickstart
    [ "$HAS_DISTFEEDS" = 1 ] && { green ">> 恢复原厂 OPKG 配置（菜单15）"; recovery_opkg_settings; }
    yellow "===== 一键初始化完成 ====="
}

# 渲染主菜单（按 profile 动态显示条件项）
render_menu() {
    echo "***********************************************************************"
    echo "*      GL-iNet 一键工具箱"
    echo "*      当前机型: $PROFILE_NAME"
    echo "*      支持: BE3600 / BE6500 / MT-3000   快捷键: g"
    echo "***********************************************************************"
    green "请确保固件版本在 $FIRMWARE_MIN_VERSION 以上"
    echo
    light_yellow " 0. 一键初始化（全自动·交互项用默认值·跳过 AdGuard/wireguard/overlay/更新脚本）"
    light_magenta " 1. 一键 iStoreOS 风格化"
    echo " 2. 安装 Argon 紫色主题"
    echo " 3. 单独安装 iStore 商店"
    echo " 4. 隐藏首页非必要 UI 元素"
    echo " 5. 设置风扇工作温度（交互式）"
    echo " 6. 启用/关闭 AdGuardHome"
    echo " 7. 安装个性化 UI 辅助插件"
    echo " 8. 安装高级卸载插件"
    echo " 9. 安装 luci-app-wireguard"
    echo "10. 安装文件管理器"
    echo "11. 设置/删除自定义软件源"
    echo "12. 手动安装/更新 quickstart 首页"
    if [ "$HAS_OVERLAY" = 1 ]; then
        cyan "13. Overlay 换分区助手（U 盘扩容，仅 MT-3000）"
    fi
    echo "14. 更新本脚本"
    if [ "$HAS_DISTFEEDS" = 1 ]; then
        echo "15. 恢复原厂 OPKG 配置 (distfeeds)"
    fi
    echo
    echo " R. 恢复出厂设置/重置路由器"
    echo " Q. 退出本程序"
    echo
}

# 自定义软件源子选择（12）
custom_feed_menu() {
    echo " a. 添加自定义软件源"
    echo " d. 删除自定义软件源"
    read -p "请选择 (a/d): " fc
    case "$fc" in
        a|A) add_custom_feed ;;
        d|D) remove_custom_feed ;;
        *) echo "无效选择" ;;
    esac
}

dispatch() {
    case "$1" in
        0)  do_init_all ;;
        1)  do_one_key_setup ;;
        2)  do_install_argon_skin ;;
        3)  do_istore ;;
        4)  hide_ui_elements ;;
        5)  set_glfan_temp ;;
        6)  toggle_adguardhome ;;
        7)  do_install_ui_helper ;;
        8)  advanced_uninstall ;;
        9)  do_luci_app_wireguard ;;
        10) do_install_filemanager ;;
        11) custom_feed_menu ;;
        12) do_install_new_quickstart ;;
        13) if [ "$HAS_OVERLAY" = 1 ]; then overlay_menu; else echo "无效选项，请重新选择。"; fi ;;
        14) update_myself ;;
        15) if [ "$HAS_DISTFEEDS" = 1 ]; then recovery_opkg_settings; else echo "无效选项，请重新选择。"; fi ;;
        r|R) recovery ;;
        q|Q) echo "退出"; exit 0 ;;
        *)  echo "无效选项，请重新选择。" ;;
    esac
}

main() {
    parse_args "$@"
    # 设置全局快捷命令 g
    cp -f "$0" /usr/bin/g 2>/dev/null && chmod +x /usr/bin/g 2>/dev/null
    detect_profile
    while true; do
        clear
        render_menu
        read -p "请选择一个选项: " choice
        dispatch "$choice"
        read -p "按 Enter 键继续..."
    done
}

# 可测性守卫：GLINET_LIB=1 source 时仅加载函数，不进菜单
[ -n "${GLINET_LIB:-}" ] || main "$@"
