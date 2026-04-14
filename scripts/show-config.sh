#!/usr/bin/env bash
set -eou pipefail

# Colors
RED="\033[31m"; GREEN="\033[32m"; YELLOW="\033[33m"; BLUE="\033[34m"; MAGENTA="\033[35m"; CYAN="\033[36m"; PURPLE="\033[0;35m"; RESET="\033[0m"

# Env
ENV_FILE="/.env/sb-xray"
STATUS_FILE="/.env/status"
SECRET_FILE="/.env/secret"
[ -f "$ENV_FILE" ] || { echo -e "${RED}Error: $ENV_FILE missing${RESET}"; exit 1; }
source "$ENV_FILE"
[ -f "$STATUS_FILE" ] && source "$STATUS_FILE"
[ -f "$SECRET_FILE" ] && source "$SECRET_FILE"

# Ensure output directory exists immediately
mkdir -p "${WORKDIR}/subscribe"

# Node Info
export NODE_NAME="${DOMAIN%%.*}"
export NODE_IP="${GEOIP_INFO#*|}"
export REGION_INFO="${GEOIP_INFO%%|*}"

get_flag_emoji() {
    case "$1" in
        *"香港"*|*"Hong Kong"*) echo "🇭🇰" ;;
        *"台湾"*|*"Taiwan"*) echo "🇹🇼" ;;
        *"日本"*|*"Japan"*) echo "🇯🇵" ;;
        *"新加坡"*|*"Singapore"*) echo "🇸🇬" ;;
        *"美国"*|*"United States"*) echo "🇺🇸" ;;
        *"韩国"*|*"Korea"*) echo "🇰🇷" ;;
        *"英国"*|*"United Kingdom"*) echo "🇬🇧" ;;
        *"德国"*|*"Germany"*) echo "🇩🇪" ;;
        *"法国"*|*"France"*) echo "🇫🇷" ;;
        *"加拿大"*|*"Canada"*) echo "🇨🇦" ;;
        *"澳大利亚"*|*"Australia"*) echo "🇦🇺" ;;
        *"俄罗斯"*|*"Russia"*) echo "🇷🇺" ;;
        *"印度"*|*"India"*) echo "🇮🇳" ;;
        *"荷兰"*|*"Netherlands"*) echo "🇳🇱" ;;
        *"菲律宾"*|*"Philippines"*) echo "🇵🇭" ;;
        *"马来西亚"*|*"Malaysia"*) echo "🇲🇾" ;;
        *"泰国"*|*"Thailand"*) echo "🇹🇭" ;;
        *"越南"*|*"Vietnam"*) echo "🇻🇳" ;;
        *"印尼"*|*"印度尼西亚"*|*"Indonesia"*) echo "🇮🇩" ;;
        *"土耳其"*|*"Turkey"*) echo "🇹🇷" ;;
        *"阿根廷"*|*"Argentina"*) echo "🇦🇷" ;;
        *"巴西"*|*"Brazil"*) echo "🇧🇷" ;;
        *"南非"*|*"South Africa"*) echo "🇿🇦" ;;
        *"澳门"*|*"Macao"*|*"Macau"*) echo "🇲🇴" ;;
        *"瑞士"*|*"Switzerland"*) echo "🇨🇭" ;;
        *"瑞典"*|*"Sweden"*) echo "🇸🇪" ;;
        *"意大利"*|*"Italy"*) echo "🇮🇹" ;;
        *"爱尔兰"*|*"Ireland"*) echo "🇮🇪" ;;
        *"土库曼斯坦"*) echo "🇹🇲" ;;
        *"中国"*|*"China"*) echo "🇨🇳" ;;
        *) echo "" ;;
    esac
}

export FLAG_INFO="$(get_flag_emoji "$REGION_INFO")"
if [ -n "$FLAG_INFO" ]; then
    export FLAG_PREFIX="${FLAG_INFO} "
else
    export FLAG_PREFIX=""
fi

# Preserve NODE_SUFFIX from env, default to empty if not set
: "${NODE_SUFFIX:=}"

# Auto-detect suffix logic (supports stacking)
# 0. 域名硬编码匹配 (dmit/dc/jp) -> ✈高速
if [[ "$DOMAIN" =~ ^(dmit|dc|jp) ]]; then
    export NODE_SUFFIX="${NODE_SUFFIX} ✈ 高速"
fi
# 1. 8K 能力场景分发
# good: 使用了 sock5 代理后支持 8k 流媒体 (优先级最高，无论宿主是否为住宅，只要被收编进代理统统算 good)
if [[ -n "${ISP_TAG:-}" && "${ISP_TAG}" != "direct" && "${IS_8K_SMOOTH:-}" == "true" ]]; then
    export NODE_SUFFIX+=" ✈ good"
# super: 宿主是纯净的住宅 ip 且全链路直出支持 8k 流媒体
elif [[ "${IP_TYPE:-}" == "isp" && "${IS_8K_SMOOTH:-}" == "true" ]]; then
    export NODE_SUFFIX+=" ✈ super"
fi

# Add IP_TYPE to NODE_SUFFIX
if [ -n "${IP_TYPE:-}" ]; then
    export NODE_SUFFIX+=" ✈ ${IP_TYPE}"
fi

# Helpers
print_colored() { echo -e "$1$2${RESET}\n"; }

show_qrcode() {
    local content="$1" remark="$2"
    local qr_params="-s 8 -m 4 -l H -v 10 -d 300 -k 2"
    qrencode $qr_params -o "/tmp/qr_${remark}.png" "$content"
    echo -e "${GREEN}== ${remark} QR Code ==${RESET}"
    echo "$content" | qrencode -o - -t utf8 $qr_params -f 0 -b 255
}

generate_links() {
    local region_name="${NODE_NAME}" h2_alpn="alpn=h3"
    local vmes_json="{\"v\":\"2\",\"ps\":\"${FLAG_PREFIX}Vmess ✈ ${region_name}${NODE_SUFFIX}\",\"add\":\"${CDNDOMAIN}\",\"port\":\"${LISTENING_PORT}\",\"id\":\"${XRAY_UUID}\",\"aid\":\"0\",\"scy\":\"auto\",\"net\":\"ws\",\"type\":\"none\",\"host\":\"${CDNDOMAIN}\",\"path\":\"/${XRAY_URL_PATH}-vmessws\",\"tls\":\"tls\",\"sni\":\"${CDNDOMAIN}\",\"alpn\":\"h2\",\"fp\":\"chrome\"}"

    # 基础链接 (Clash 支持部分)
    local link_hysteria2="hysteria2://${SB_UUID}@${DOMAIN}:${PORT_HYSTERIA2}/?sni=${DOMAIN}&obfs=salamander&obfs-password=${SB_UUID}&${h2_alpn}#${FLAG_PREFIX}Hysteria2 ✈ ${region_name}${NODE_SUFFIX}"
    local link_tuic="tuic://${SB_UUID}:${SB_UUID}@${DOMAIN}:${PORT_TUIC}?sni=${DOMAIN}&${h2_alpn}&congestion_control=bbr#${FLAG_PREFIX}TUIC ✈ ${region_name}${NODE_SUFFIX}"
    local link_anytls="anytls://${SB_UUID}@${DOMAIN}:${PORT_ANYTLS}?security=tls&type=tcp#${FLAG_PREFIX}AnyTLS ✈ ${region_name}${NODE_SUFFIX}"
    local link_vmess="vmess://$(echo -n "$vmes_json" | base64 -w0)"
    local link_vless_vision="vless://${XRAY_UUID}@${DOMAIN}:${LISTENING_PORT}?encryption=none&flow=xtls-rprx-vision&security=reality&sni=${DEST_HOST}&fp=chrome&pbk=${XRAY_REALITY_PUBLIC_KEY}&sid=${XRAY_REALITY_SHORTID}&spx=%2F&type=tcp&headerType=none#${FLAG_PREFIX}XTLS-Reality ✈ ${region_name}${NODE_SUFFIX}"

    # 高级/Xhttp 链接 (Mihomo / V2rayN / Sing-box 支持)
    local xhttp_base="encryption=mlkem768x25519plus.native.0rtt.${XRAY_MLKEM768_CLIENT}&security=reality&sni=${DEST_HOST}&fp=chrome&pbk=${XRAY_REALITY_PUBLIC_KEY}&sid=${XRAY_REALITY_SHORTID}&type=xhttp&path=%2F${XRAY_URL_PATH}-xhttp&mode=auto"
    local link_xhttp_reality="vless://${XRAY_UUID}@${DOMAIN}:${LISTENING_PORT}?${xhttp_base}#${FLAG_PREFIX}Xhttp+Reality直连 ✈ ${region_name}${NODE_SUFFIX}"

    # 复杂的 CDN/Reality 混合模式 json
    local down_settings="%7B%22downloadSettings%22%3A%7B%22address%22%3A%22${DOMAIN}%22%2C%22port%22%3A${LISTENING_PORT}%2C%22network%22%3A%22xhttp%22%2C%22security%22%3A%22reality%22%2C%22realitySettings%22%3A%7B%22show%22%3Afalse%2C%22serverName%22%3A%22${DEST_HOST}%22%2C%22fingerprint%22%3A%22chrome%22%2C%22publicKey%22%3A%22${XRAY_REALITY_PUBLIC_KEY}%22%2C%22shortId%22%3A%22${XRAY_REALITY_SHORTID}%22%2C%22spiderX%22%3A%22%2F%22%7D%2C%22xhttpSettings%22%3A%7B%22host%22%3A%22%22%2C%22path%22%3A%22%2F${XRAY_URL_PATH}-xhttp%22%2C%22mode%22%3A%22auto%22%7D%7D%7D"

    local link_up_cdn_down_reality="vless://${XRAY_UUID}@${CDNDOMAIN}:${LISTENING_PORT}?encryption=mlkem768x25519plus.native.0rtt.${XRAY_MLKEM768_CLIENT}&security=tls&sni=${CDNDOMAIN}&alpn=h2&fp=chrome&type=xhttp&host=${CDNDOMAIN}&path=%2F${XRAY_URL_PATH}-xhttp&mode=auto&extra=${down_settings}#${FLAG_PREFIX}上行Xhttp+TLS+CDN下行Xhttp+Reality ✈ ${region_name}${NODE_SUFFIX}"

    local tls_settings="%7B%22downloadSettings%22%3A%7B%22address%22%3A%22${DOMAIN}%22%2C%22port%22%3A${LISTENING_PORT}%2C%22network%22%3A%22xhttp%22%2C%22security%22%3A%22tls%22%2C%22tlsSettings%22%3A%7B%22serverName%22%3A%22${CDNDOMAIN}%22%2C%22alpn%22%3A%5B%22h2%22%5D%2C%22fingerprint%22%3A%22chrome%22%7D%2C%22xhttpSettings%22%3A%7B%22host%22%3A%22${CDNDOMAIN}%22%2C%22path%22%3A%22%2F${XRAY_URL_PATH}-xhttp%22%2C%22mode%22%3A%22auto%22%7D%7D%7D"

    local link_up_reality_down_cdn="vless://${XRAY_UUID}@${DOMAIN}:${LISTENING_PORT}?encryption=mlkem768x25519plus.native.0rtt.${XRAY_MLKEM768_CLIENT}&security=reality&sni=${DEST_HOST}&fp=chrome&pbk=${XRAY_REALITY_PUBLIC_KEY}&sid=${XRAY_REALITY_SHORTID}&type=xhttp&path=%2F${XRAY_URL_PATH}-xhttp&mode=auto&extra=${tls_settings}#${FLAG_PREFIX}上行Xhttp+Reality下行Xhttp+TLS+CDN ✈ ${region_name}${NODE_SUFFIX}"

    local link_mix="vless://${XRAY_UUID}@${CDNDOMAIN}:${LISTENING_PORT}?encryption=mlkem768x25519plus.native.0rtt.${XRAY_MLKEM768_CLIENT}&security=tls&sni=${CDNDOMAIN}&alpn=h2&fp=chrome&pbk=${XRAY_REALITY_PUBLIC_KEY}&sid=${XRAY_REALITY_SHORTID}&type=xhttp&host=${CDNDOMAIN}&path=%2F${XRAY_URL_PATH}-xhttp&mode=auto#${FLAG_PREFIX}Xhttp+TLS+CDN上下行不分离 ✈ ${region_name}${NODE_SUFFIX}"

    # 加上注释 (保留原样格式)
    local part1="${link_hysteria2}
${link_tuic}
${link_anytls}
${link_vmess}
${link_vless_vision}"

    local part2="${link_xhttp_reality}
${link_up_cdn_down_reality}
${link_up_reality_down_cdn}
${link_mix}"

    CLASH_SUBSCRIBE="${part1}
${part2}"

    V2RAYN_SUBSCRIBE="${part1}
${part2}"
    print_colored ${PURPLE} "V2RAYN 订阅链接内容如下:\n${V2RAYN_SUBSCRIBE}"
    echo -n "$V2RAYN_SUBSCRIBE" | base64 -w0 > ${WORKDIR}/subscribe/v2rayn
    echo -n "$CLASH_SUBSCRIBE" | base64 -w0 > ${WORKDIR}/subscribe/clashsub
}

show_info_links() {
    local token_param=""
    if [ -n "${SUBSCRIBE_TOKEN:-}" ]; then
        token_param="?token=${SUBSCRIBE_TOKEN}"
    fi

    print_colored ${GREEN} "\n******************************************************************\n  *        Sing-box / Xray 多协议多传输客户端配置文件汇总         *\n"
    print_colored ${RED} "Index:\nhttps://${CDNDOMAIN}/sb-xray/show-config${token_param}"
    print_colored ${YELLOW} "全部订阅:\nhttps://${CDNDOMAIN}/sb-xray/proxies${token_param}"
    print_colored ${MAGENTA} "Clash 订阅:\nhttps://${CDNDOMAIN}/sb-xray/clashsub${token_param}"
    print_colored ${BLUE} "Stash 订阅:\nhttps://${CDNDOMAIN}/sb-xray/stash${token_param}"
    print_colored ${CYAN} "V2rayN 订阅:\nhttps://${CDNDOMAIN}/sb-xray/v2rayn${token_param}"

    # Client Templates
    for c in /templates/client_template/*.yaml; do
        if [ -f "$c" ]; then
            local filename=$(basename "$c")
            local name="${filename%.yaml}"
            print_colored ${PURPLE} "${name} 订阅:\nhttps://${CDNDOMAIN}/sb-xray/${filename}${token_param}"
        fi
    done

    if [ -f "/templates/client_template/surge.conf" ]; then
        print_colored ${PURPLE} "Surge 订阅:\nhttps://${CDNDOMAIN}/sb-xray/surge.conf${token_param}"
    fi

    # 提示用户
    if [ -n "$token_param" ]; then
        echo -e "💡 \033[33m已自动附加安全认证 Token，可直接导入客户端使用。\033[0m"
        echo -e "🔒 \033[33m基础认证用户: ${PUBLIC_USER:-未设置} / ${PUBLIC_PASSWORD:-未设置}\033[0m"
    fi

    print_colored ${GREEN} "\n*                                                                *\n *        Sing-box / Xray 多协议多传输客户端配置文件汇总         *\n******************************************************************"
}

main() {
    mkdir -p ${WORKDIR}/subscribe
    generate_links

    # 批量处理简单模板
    for t in all:proxies clash stash surge surge.conf; do
        local src="${t%:*}" dst="${t#*:}"
        local path="/templates/proxies/${src}"
        [[ "$src" == "surge.conf" ]] && path="/templates/client_template/surge.conf"

        [ -f "$path" ] && envsubst < "$path" > "${WORKDIR}/subscribe/${dst}"
    done

    # Client Templates
    for c in /templates/client_template/*.yaml; do
        [ -f "$c" ] && envsubst < "$c" > "${WORKDIR}/subscribe/$(basename "$c")"
    done

    cp -a /sources/* ${WORKDIR}/subscribe 2>/dev/null || true

    show_info_links
}

main | tee >(sed 's/\x1b\[[0-9;]*m//g' > ${WORKDIR}/subscribe/show-config)
