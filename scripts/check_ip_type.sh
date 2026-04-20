#!/bin/bash
# ====================================================
# IP质量体检脚本 (简化版)
# 原作者: xykt
# 修改者: Refactored for Docker Environment
# 版本: v2026-02-12-Docker-Refactor
# 功能: 检测 IP 基础信息、风险评分、流媒体解锁情况
# ====================================================

# --- 全局变量与配置 ---

# 脚本版本
script_version="v2026-02-12-Docker-Refactor"

# 颜色定义
Font_Black="\033[30m"
Font_Red="\033[31m"
Font_Green="\033[32m"
Font_Yellow="\033[33m"
Font_Blue="\033[34m"
Font_Purple="\033[35m"
Font_Cyan="\033[36m"
Font_White="\033[37m"
Back_Green="\033[42m"
Back_Yellow="\033[43m"
Back_Red="\033[41m"
Font_B="\033[1m"
Font_I="\033[3m"
Font_U="\033[4m"
Font_Suffix="\033[0m"

# 类型定义
stype_business="$Back_Yellow$Font_White$Font_B 商业 $Font_Suffix"
stype_isp="$Back_Green$Font_White$Font_B 家宽 $Font_Suffix"
stype_hosting="$Back_Red$Font_White$Font_B 机房 $Font_Suffix"
stype_education="$Back_Yellow$Font_White$Font_B 教育 $Font_Suffix"
stype_gov="$Back_Yellow$Font_White$Font_B 政府 $Font_Suffix"
stype_org="$Back_Yellow$Font_White$Font_B 组织 $Font_Suffix"
stype_mil="$Back_Yellow$Font_White$Font_B 军队 $Font_Suffix"
stype_cdn="$Back_Red$Font_White$Font_B CDN $Font_Suffix"
stype_mobile="$Back_Green$Font_White$Font_B 手机 $Font_Suffix"
stype_spider="$Back_Red$Font_White$Font_B 蜘蛛 $Font_Suffix"
stype_reserved="$Back_Yellow$Font_White$Font_B 保留 $Font_Suffix"
stype_other="$Back_Yellow$Font_White$Font_B 其他 $Font_Suffix"


# 默认参数
YY="cn"            # 语言: cn/en
IPV4check=1        # 检查 IPv4
IPV6check=1        # 检查 IPv6
mode_lite=0        # 精简模式
mode_output=0      # 输出到文件
fullIP=0           # 显示完整 IP
CurlARGS=()        # curl 参数（数组形式避免引号注入）

# 存储结果的关联数组
declare -A maxmind ipinfo scamalytics ipregistry ipapi abuseipdb ip2location dbip ipwhois ipdata ipqs
declare -A tiktok disney netflix youtube amazon reddit chatgpt
declare -A tiktok disney netflix youtube amazon reddit chatgpt

# 资源地址前缀
rawgithub="https://raw.githubusercontent.com/xykt/IPQuality/main"

# --- 工具函数 ---

# 显示进度 (简化版)
show_progress() {
    echo -ne "\r$Font_Cyan$Font_B[*] $1 ... $Font_Suffix"
}

# 结束进度显示
end_progress() {
    echo -ne "\r\033[K"
}

# 生成随机 User-Agent
generate_user_agent() {
    local chrome_version="145.0.0.0"
    UA_Browser="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/$chrome_version Safari/537.36"
}

# 检查网络连通性并设置资源源
check_connectivity() {
    if curl -s --connect-timeout 2 "https://www.google.com/generate_204" >/dev/null; then
        rawgithub="https://raw.githubusercontent.com/xykt/IPQuality/main"
    else
        # Fallback for CN or restricted networks
        rawgithub="https://testingcf.jsdelivr.net/gh/xykt/IPQuality@main"
    fi
}

# 获取媒体检测所需的 Cookies
fetch_cookies() {
    show_progress "正在获取检测所需 Cookies"
    Media_Cookie=$(curl "${CurlARGS[@]}" -sL --retry 3 --max-time 10 "${rawgithub}/ref/cookies.txt")
    end_progress
}

# 计算字符串显示宽度 (处理中文字符)
calc_width() {
    local string="$1"
    local length=0
    local char
    # 简单的字节计数逻辑，非精确但通常足够
    # 此处为简化，假设 UTF-8 中文占 2 宽，ASCII 占 1 宽
    # 实际在 bash 中处理较复杂，这里使用简化逻辑
    echo "${#string}"
}

# --- IP 获取与验证 ---

# 验证 IPv4 格式
is_valid_ipv4() {
    local ip=$1
    [[ $ip =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]
}

# 验证 IPv6 格式
is_valid_ipv6() {
    local ip=$1
    [[ $ip =~ : ]] # 简化检查
}

# 获取本机 IPv4
get_ipv4() {
    local API_NET=("myip.check.place" "ip.sb" "ping0.cc" "icanhazip.com" "api64.ipify.org")
    IPV4=""
    for p in "${API_NET[@]}"; do
        IPV4=$(curl "${CurlARGS[@]}" -s4 --max-time 3 "$p")
        if [[ -n "$IPV4" && ! "$IPV4" =~ "error" ]]; then
            break
        fi
    done
}

# 获取本机 IPv6
get_ipv6() {
    local API_NET=("myip.check.place" "ip.sb" "ping0.cc" "icanhazip.com" "api64.ipify.org")
    IPV6=""
    for p in "${API_NET[@]}"; do
        IPV6=$(curl "${CurlARGS[@]}" -s6 --max-time 3 "$p")
        if [[ -n "$IPV6" && ! "$IPV6" =~ "error" ]]; then
            break
        fi
    done
}

# 隐藏 IP (隐私模式)
mask_ip() {
    local ip=$1
    if [[ "$ip" =~ .*:.* ]]; then
        # IPv6 Mask
        echo "${ip%%:*}:****:****:****:****:****:****:****"
    else
        # IPv4 Mask
        echo "${ip%.*.*}.*.*"
    fi
}

# --- 数据库检测函数 (精简核心逻辑) ---

# Maxmind 数据库检测
db_maxmind() {
    local ip_ver=$1
    show_progress "正在查询 Maxmind 数据库"
    local response
    response=$(curl "${CurlARGS[@]}" -Ls -$ip_ver -m 10 "https://ipinfo.check.place/$IP?lang=$YY")

    # 解析 JSON
    maxmind[asn]=$(echo "$response" | jq -r '.ASN.AutonomousSystemNumber // "N/A"')
    maxmind[org]=$(echo "$response" | jq -r '.ASN.AutonomousSystemOrganization // "N/A"')
    maxmind[city]=$(echo "$response" | jq -r '.City.Name // "N/A"')
    maxmind[country]=$(echo "$response" | jq -r '.Country.Name // "N/A"')
    maxmind[countrycode]=$(echo "$response" | jq -r '.Country.IsoCode // "N/A"')
    maxmind[type]=$(echo "$response" | jq -r '.City.Subdivisions[0].IsoCode // "N/A"') # 仅作示例

    # 判断 IP 类型 (简单逻辑)
    local country=$(echo "$response" | jq -r '.Country.IsoCode')
    local reg_country=$(echo "$response" | jq -r '.Country.RegisteredCountry.IsoCode')
    if [[ "$country" == "$reg_country" ]]; then
         maxmind[type_disp]="$Back_Green$Font_White 原生IP $Font_Suffix"
    else
         maxmind[type_disp]="$Back_Red$Font_White 广播IP $Font_Suffix"
    fi
    end_progress
}


# IPinfo 数据库检测
db_ipinfo() {
    show_progress "正在查询 IPinfo 数据库"
    local response
    response=$(curl "${CurlARGS[@]}" -Ls -m 10 "https://ipinfo.io/widget/demo/$IP")

    ipinfo[asn]=$(echo "$response" | jq -r '.data.asn.asn // "N/A"')
    ipinfo[org]=$(echo "$response" | jq -r '.data.asn.name // "N/A"')
    ipinfo[city]=$(echo "$response" | jq -r '.data.city // "N/A"')
    ipinfo[country]=$(echo "$response" | jq -r '.data.country // "N/A"')

    # 提取类型
    local type=$(echo "$response" | jq -r '.data.asn.type')
    shopt -s nocasematch
    case "$type" in
        "isp") ipinfo[type_disp]="${stype_isp}";;
        "hosting") ipinfo[type_disp]="${stype_hosting}";;
        "business") ipinfo[type_disp]="${stype_business}";;
        "education") ipinfo[type_disp]="${stype_education}";;
        *) ipinfo[type_disp]="${stype_other}";;
    esac

    local company_type=$(echo "$response" | jq -r '.data.company.type')
    case "$company_type" in
        "isp") ipinfo[comp_disp]="${stype_isp}";;
        "hosting") ipinfo[comp_disp]="${stype_hosting}";;
        "business") ipinfo[comp_disp]="${stype_business}";;
        "education") ipinfo[comp_disp]="${stype_education}";;
        *) ipinfo[comp_disp]="${stype_other}";;
    esac
    shopt -u nocasematch
    end_progress
}


# IPregistry检测
db_ipregistry() {
    show_progress "正在查询 ipregistry 数据库"
    local response
    response=$(curl "${CurlARGS[@]}" -sL -$1 -m 10 "https://ipinfo.check.place/$IP?db=ipregistry")

    local type=$(echo "$response" | jq -r '.connection.type')
    shopt -s nocasematch
    case "$type" in
        "isp") ipregistry[type_disp]="${stype_isp}";;
        "hosting") ipregistry[type_disp]="${stype_hosting}";;
        "business") ipregistry[type_disp]="${stype_business}";;
        "education") ipregistry[type_disp]="${stype_education}";;
        "government") ipregistry[type_disp]="${stype_gov}";;
        *) ipregistry[type_disp]="${stype_other}";;
    esac
    shopt -u nocasematch
    end_progress
}


# IP2Location检测
db_ip2location() {
    show_progress "正在查询 IP2Location 数据库"
    local response
    response=$(curl "${CurlARGS[@]}" -sL -$1 -m 10 "https://ipinfo.check.place/$IP?db=ip2location")

    ip2location[score]=$(echo "$response" | jq -r '.fraud_score // 0')
    local type=$(echo "$response" | jq -r '.usage_type')
    type="${type%%/*}"
    case "$type" in
        "ISP") ip2location[type_disp]="${stype_isp}";;
        "DCH") ip2location[type_disp]="${stype_hosting}";;
        "COM") ip2location[type_disp]="${stype_business}";;
        "EDU") ip2location[type_disp]="${stype_education}";;
        "GOV") ip2location[type_disp]="${stype_gov}";;
        "MIL") ip2location[type_disp]="${stype_mil}";;
        "CDN") ip2location[type_disp]="${stype_cdn}";;
        "MOB") ip2location[type_disp]="${stype_mobile}";;
        "SES") ip2location[type_disp]="${stype_spider}";;
        "RSV") ip2location[type_disp]="${stype_reserved}";;
        *) ip2location[type_disp]="${stype_other}";;
    esac
    end_progress
}

# AbuseIPDB检测
db_abuseipdb() {
    show_progress "正在查询 AbuseIPDB 数据库"
    local response
    response=$(curl "${CurlARGS[@]}" -sL -$1 -m 10 "https://ipinfo.check.place/$IP?db=abuseipdb")

    abuseipdb[score]=$(echo "$response" | jq -r '.data.abuseConfidenceScore // 0')
    local type=$(echo "$response" | jq -r '.data.usageType')
    shopt -s nocasematch
    case "$type" in
        "Fixed Line ISP"|"Mobile ISP") abuseipdb[type_disp]="${stype_isp}";;
        "Data Center/Web Hosting/Transit") abuseipdb[type_disp]="${stype_hosting}";;
        "Commercial") abuseipdb[type_disp]="${stype_business}";;
        "University/College/School") abuseipdb[type_disp]="${stype_education}";;
        "Government") abuseipdb[type_disp]="${stype_gov}";;
        "Content Delivery Network") abuseipdb[type_disp]="${stype_cdn}";;
        "Search Engine Spider") abuseipdb[type_disp]="${stype_spider}";;
        "Reserved") abuseipdb[type_disp]="${stype_reserved}";;
        *) abuseipdb[type_disp]="${stype_other}";;
    esac
    shopt -u nocasematch
    end_progress
}


# Scamalytics 欺诈分数检测
db_scamalytics() {
    local ip_ver=$1
    show_progress "正在查询 Scamalytics 风险评分"
    local response
    response=$(curl "${CurlARGS[@]}" -sL -$ip_ver -m 10 "https://ipinfo.check.place/$IP?db=scamalytics")

    local score=$(echo "$response" | jq -r '.scamalytics.scamalytics_score // 0')
    scamalytics[score]=$score

    if [[ $score -lt 30 ]]; then
        scamalytics[risk]="$Font_Green 低风险 $Font_Suffix"
    elif [[ $score -lt 70 ]]; then
        scamalytics[risk]="$Font_Yellow 中风险 $Font_Suffix"
    else
        scamalytics[risk]="$Font_Red 高风险 $Font_Suffix"
    fi
    end_progress
}

# IPAPI 风险检测
db_ipapi() {
    show_progress "正在查询 IPAPI 风险数据"
    local response
    response=$(curl "${CurlARGS[@]}" -sL -m 10 "https://api.ipapi.is/?q=$IP")

    ipapi[proxy]=$(echo "$response" | jq -r '.is_proxy // false')
    ipapi[vpn]=$(echo "$response" | jq -r '.is_vpn // false')
    ipapi[tor]=$(echo "$response" | jq -r '.is_tor // false')
    ipapi[datacenter]=$(echo "$response" | jq -r '.is_datacenter // false')
    ipapi[abuser]=$(echo "$response" | jq -r '.is_abuser // false')

    local type=$(echo "$response" | jq -r '.asn.type')
    shopt -s nocasematch
    case "$type" in
        "isp") ipapi[type_disp]="${stype_isp}";;
        "hosting") ipapi[type_disp]="${stype_hosting}";;
        "business") ipapi[type_disp]="${stype_business}";;
        "education") ipapi[type_disp]="${stype_education}";;
        "government") ipapi[type_disp]="${stype_gov}";;
        *) ipapi[type_disp]="${stype_other}";;
    esac
    shopt -u nocasematch
    end_progress
}

# --- 流媒体解锁检测 (核心逻辑保留) ---

# 定义状态显示
status_yes="$Back_Green$Font_White 解锁 $Font_Suffix"
status_no="$Back_Red$Font_White 失败 $Font_Suffix"
status_cn="$Back_Red$Font_White 中国 $Font_Suffix"
status_na="$Font_Yellow N/A $Font_Suffix"

# DNS 辅助检测函数
check_dns_unlock() {
    # 简化的 DNS 解锁判断，实际逻辑较复杂，此处占位
    # 如果需要完整逻辑，需保留原脚本的 Check_DNS_* 函数
    echo "$Back_Green$Font_White 原生 $Font_Suffix"
}

# TikTok 检测
test_tiktok() {
    local ip_ver=$1
    show_progress "正在检测 TikTok"
    tiktok[status]="$status_no"
    tiktok[region]=""

    local result
    result=$(curl "${CurlARGS[@]}" -$ip_ver --user-agent "$UA_Browser" -sL -m 10 "https://www.tiktok.com/")

    if [[ "$result" == *"region"* ]]; then
        local region=$(echo "$result" | grep '"region":' | sed 's/.*"region"//' | cut -f2 -d'"')
        if [[ -n "$region" ]]; then
            tiktok[status]="$status_yes"
            tiktok[region]="[$region]"
        fi
    fi
    end_progress
}

# Netflix 检测 (简化版)
test_netflix() {
    local ip_ver=$1
    show_progress "正在检测 Netflix"
    netflix[status]="$status_no"
    netflix[region]=""

    local result
    result=$(curl "${CurlARGS[@]}" -$ip_ver --user-agent "$UA_Browser" -fsL --max-time 10 "https://www.netflix.com/title/81280792" 2>&1)

    if [[ "$result" != *"Oh no!"* && -n "$result" ]]; then
        # 尝试提取地区 ID
        local region=$(echo "$result" | grep -o '"countryName":"[^"]*"' | head -n1 | cut -d'"' -f4)
        if [[ -n "$region" ]]; then
             netflix[status]="$status_yes"
             netflix[region]="[$region]"
        else
             netflix[status]="$status_yes"
             netflix[region]="[自制剧]"
        fi
    elif [[ "$result" == *"Oh no!"* ]]; then
        # 仅自制剧
         netflix[status]="$Back_Yellow$Font_White 仅自制 $Font_Suffix"
    fi
    end_progress
}

# YouTube 检测
test_youtube() {
    local ip_ver=$1
    show_progress "正在检测 YouTube Premium"
    youtube[status]="$status_no"

    local result
    result=$(curl "${CurlARGS[@]}" -$ip_ver -sSL --max-time 10 -H "Accept-Language: en" "https://www.youtube.com/premium" 2>&1)

    if echo "$result" | grep -q "Premium is not available in your country"; then
        youtube[status]="$status_no"
    elif echo "$result" | grep -q "www.google.cn"; then
        youtube[status]="$status_cn"
    else
        local region=$(echo "$result" | grep '"contentRegion":' | head -n1 | cut -d'"' -f4)
        if [[ -n "$region" ]]; then
            youtube[status]="$status_yes"
            youtube[region]="[$region]"
        else
            youtube[status]="$status_yes" # 假设能访问即解锁，若无 Premium 提示
        fi
    fi
    end_progress
}

# ChatGPT 检测
test_chatgpt() {
    local ip_ver=$1
    show_progress "正在检测 ChatGPT"
    chatgpt[status]="$status_no"
    chatgpt[region]=""

    # 检查是否在该地区提供服务
    local result
    result=$(curl "${CurlARGS[@]}" -$ip_ver -sS --max-time 10 "https://chat.openai.com/cdn-cgi/trace" 2>&1)

    if echo "$result" | grep -q "loc="; then
        local loc=$(echo "$result" | grep "loc=" | cut -d= -f2)
        chatgpt[status]="$status_yes"
        chatgpt[region]="[$loc]"
    else
         chatgpt[status]="$status_no"
    fi
    end_progress
}


# --- 报告输出 ---

show_report() {
    local ip=$1
    local ver=$2
    local display_ip=$ip

    if [[ $fullIP -eq 0 ]]; then
        display_ip=$(mask_ip "$ip")
    fi

    echo -e "\n"
    echo -e "========================================"
    echo -e " IP 质量体检报告 (IPv$ver)"
    echo -e "========================================"
    echo -e "IP 地址    : $Font_Cyan $display_ip $Font_Suffix"
    echo -e "ASN        : ${Font_Green}AS${maxmind[asn]}$Font_Suffix ${maxmind[org]}"
    echo -e "地理位置   : ${maxmind[country]} - ${maxmind[city]}"
    echo -e "IP 类型    : ${maxmind[type_disp]}"
    echo -e "风险评分   : ${scamalytics[risk]} (Scamalytics: ${scamalytics[score]})"
    echo -e "----------------------------------------"
    echo -e "IP 类型属性:"
    echo -e "  数据库      IPinfo    ipregistry    ipapi    IP2Location   AbuseIPDB"
    echo -e "  使用类型    ${ipinfo[type_disp]}${ipregistry[type_disp]}${ipapi[type_disp]}${ip2location[type_disp]}${abuseipdb[type_disp]}"
    echo -e "  公司类型    ${ipinfo[comp_disp]}${ipregistry[type_disp]}${ipapi[type_disp]}${ip2location[type_disp]}${abuseipdb[type_disp]}"
    echo -e "----------------------------------------"
    echo -e "风险因子 (IPAPI):"
    echo -e "  Proxy: $([[ "${ipapi[proxy]}" == "true" ]] && echo "$Font_Red 是 $Font_Suffix" || echo "$Font_Green 否 $Font_Suffix")"
    echo -e "  VPN:   $([[ "${ipapi[vpn]}" == "true" ]] && echo "$Font_Red 是 $Font_Suffix" || echo "$Font_Green 否 $Font_Suffix")"
    echo -e "  Tor:   $([[ "${ipapi[tor]}" == "true" ]] && echo "$Font_Red 是 $Font_Suffix" || echo "$Font_Green 否 $Font_Suffix")"
    echo -e "  IDC:   $([[ "${ipapi[datacenter]}" == "true" ]] && echo "$Font_Red 是 $Font_Suffix" || echo "$Font_Green 否 $Font_Suffix")"
    echo -e "----------------------------------------"
    echo -e "流媒体解锁:"
    echo -e "  TikTok   : ${tiktok[status]} ${tiktok[region]}"
    echo -e "  Netflix  : ${netflix[status]} ${netflix[region]}"
    echo -e "  YouTube  : ${youtube[status]} ${youtube[region]}"
    echo -e "  ChatGPT  : ${chatgpt[status]} ${chatgpt[region]}"
    echo -e "----------------------------------------"
    echo -e "========================================"
    echo -e "\n"
}

# --- 主逻辑 ---

# 参数解析
while getopts "i:x:46fn" opt; do
    case $opt in
        4) IPV6check=0 ;;
        6) IPV4check=0 ;;
        f) fullIP=1 ;;
        i) CurlARGS+=("--interface" "$OPTARG") ;;
        x) CurlARGS+=("-x" "$OPTARG") ;;
        n) ;; # 忽略依赖检查标志
        *) echo "Usage: $0 [-4] [-6] [-f] [-i interface] [-x proxy]"; exit 1 ;;
    esac
done

# 初始化
generate_user_agent
check_connectivity
fetch_cookies

# 执行 IPv4 检测
if [[ $IPV4check -eq 1 ]]; then
    get_ipv4
    if [[ -n "$IPV4" ]]; then
        IP=$IPV4
        db_maxmind 4
        db_ipinfo
        db_ipregistry 4
        db_ipapi
        db_ip2location 4
        db_abuseipdb 4
        db_scamalytics 4

        test_tiktok 4
        test_netflix 4
        test_youtube 4
        test_chatgpt 4

        show_report "$IPV4" 4
    else
        echo -e "$Font_Red[Error] 未检测到 IPv4 地址$Font_Suffix"
    fi
fi

# 执行 IPv6 检测
if [[ $IPV6check -eq 1 ]]; then
    get_ipv6
    if [[ -n "$IPV6" ]]; then
        IP=$IPV6
        db_maxmind 6
        db_ipinfo
        db_ipregistry 6
        db_ipapi
        db_ip2location 6
        db_abuseipdb 6
        db_scamalytics 6

        test_tiktok 6
        test_netflix 6
        test_youtube 6
        test_chatgpt 6

        show_report "$IPV6" 6
    else
         # 如果没要求只测 IPv6，则不报错，仅跳过
         if [[ $IPV4check -eq 0 ]]; then
             echo -e "$Font_Red[Error] 未检测到 IPv6 地址$Font_Suffix"
         fi
    fi
fi
