#!/bin/sh
. /usr/share/openclash/ruby.sh
. /usr/share/openclash/log.sh
. /lib/functions.sh

# This script is called by /etc/init.d/openclash
# Add your custom overwrite scripts here, they will be take effict after the OpenClash own srcipts
#
# 安全说明：本脚本只为「server 字段为纯 IP 地址」的 AnyTLS 节点
#   设置 skip-cert-verify=true（IP 节点没有 CN/SAN 可供校验，否则 TLS 握手一定失败）。
# 对「server 字段为域名」的节点，**保留证书校验**，避免 MITM 时 AnyTLS 载荷被解密。
# 若需对域名节点也跳过校验，请在节点配置层显式声明，不要在此脚本无条件改写。

LOG_OUT "Tip: Start Running Custom Overwrite Scripts..."
LOGTIME=$(echo $(date "+%Y-%m-%d %H:%M:%S"))
LOG_FILE="/tmp/openclash.log"
#Config Path
CONFIG_FILE="$1"

if [ -f "$CONFIG_FILE" ]; then
    LOG_OUT "Custom Overwrite: 正在处理 AnyTLS 节点跳过证书验证..."

    # 导出变量给 Ruby 使用
    export CONFIG_FILE

    ruby -r yaml -r ipaddr -e "
    begin
        # 兼容性处理：开启 aliases 允许解析 YAML 锚点，permitted_classes 允许解析日期等
        # 使用 YAML.load_file 在新版 Ruby 中需要传参，或者使用 YAML.safe_load
        config = YAML.load_file(ENV['CONFIG_FILE'], aliases: true)

        # 判断字符串是否为合法 IPv4/IPv6 地址
        is_ip = lambda { |s| IPAddr.new(s.to_s); true rescue false }

        modified = false
        if config['proxies'].is_a?(Array)
            config['proxies'].each do |proxy|
                # 仅处理 anytls 协议节点
                next unless proxy['type'].to_s.downcase == 'anytls'
                # 仅当 server 是 IP 时跳过证书校验；域名节点保留 TLS 校验以防 MITM
                server = proxy['server']
                next unless is_ip.call(server)
                if proxy['skip-cert-verify'] != true
                    proxy['skip-cert-verify'] = true
                    modified = true
                end
            end
        end

        if modified
            File.open(ENV['CONFIG_FILE'], 'w') { |f| f.write(config.to_yaml) }
            puts 'SUCCESS'
        else
            puts 'NO_CHANGE'
        end
    rescue Exception => e
        puts 'ERROR: ' + e.message
    end
    " >> $LOG_FILE 2>&1

    # 根据 Ruby 的输出记录日志
    if grep -q "SUCCESS" $LOG_FILE; then
        LOG_OUT "Custom Overwrite: AnyTLS 节点配置已成功修改。"
    elif grep -q "NO_CHANGE" $LOG_FILE; then
        LOG_OUT "Custom Overwrite: 未发现 AnyTLS 节点或无需修改。"
    else
        LOG_OUT "Custom Overwrite: Ruby 脚本运行出错，请检查 /tmp/openclash.log"
    fi
else
    LOG_OUT "Custom Overwrite: 错误，找不到配置文件: $CONFIG_FILE"
fi
exit 0
