# 回国代理部署指南

让境外设备访问大陆限定服务（爱奇艺、优酷、哔哩哔哩、网银、支付宝等），流量经由大陆家宽出口。

---

## 方案一（推荐）：Tailscale + OpenClash

适用于 OpenWrt 上已安装 OpenClash 的场景，无需公网 IP，无需手动管理 xray 密钥。

### 架构

```
[ClashMi / Karing / 任何 Clash 客户端]
  规则: iqiyi / youku / bilibili / 银行 → 🇨🇳 回国组 → 选择 VPS 节点

[VPS · xray 路由]
  geosite:cn / geoip:cn → cn-exit outbound (SOCKS5)
  ↓ Tailscale 加密隧道（自动 NAT 穿透）

[OpenWrt · OpenClash (Mihomo)]
  接受 SOCKS5 连接（port 7891）
  国内流量 → DIRECT → 中国 ISP → 目标服务
```

### 1.1 OpenWrt 安装 Tailscale

```sh
VERSION=1.98.4
ARCH=amd64
# ARCH=arm64

wget https://pkgs.tailscale.com/stable/tailscale_${VERSION}_${ARCH}.tgz
tar -zxvf tailscale_${VERSION}_${ARCH}.tgz
mv tailscale_${VERSION}_${ARCH}/tailscale /usr/sbin/
mv tailscale_${VERSION}_${ARCH}/tailscaled /usr/sbin/

cat > /etc/init.d/tailscale << EOF
#!/bin/sh /etc/rc.common

# START=95
# STOP=10

USE_PROCD=0

start_service() {
    procd_set_param pidfile /var/run/tailscaled.pid
    procd_set_param file /etc/config/tailscale # procd 需要一个文件来监控，此处可指向任意相关文件
    procd_set_param stdout 1
    procd_set_param stderr 1

    # 定义 tailscaled 启动命令
    # --state: 状态文件路径，重要！用于持久化认证信息和网络状态。
    # --socket: socket 文件路径，tailscale 客户端通过此与守护进程通信。
    # --tun=userspace-networking: 使用用户空间网络模式，在 OpenWrt 上兼容性更好。
    procd_open_instance
    procd_set_param command /usr/sbin/tailscaled \
        --state=/var/lib/tailscale/tailscaled.state \
        --socket=/var/run/tailscale/tailscaled.sock \
        --tun=userspace-networking
    # 崩溃自动拉起
    procd_set_param respawn
    procd_set_param stdout 1
    procd_set_param stderr 1
    procd_close_instance
}

stop_service() {
    /usr/sbin/tailscale down # 优雅地关闭 Tailscale 连接
    killall tailscaled     # 确保守护进程完全停止
}
EOF
chmod +x /etc/init.d/tailscale
/etc/init.d/tailscale enable && /etc/init.d/tailscale restart

tailscale up --accept-dns=false --accept-routes --advertise-exit-node --advertise-routes=172.18.18.0/23 --hostname=E3845-op
```

### 1.2 防火墙：允许 Tailscale 接口访问 OpenClash SOCKS5

```sh
# 1. 创建名为 tailscale 的网络接口，绑定虚拟网卡 tailscale0
uci set network.tailscale=interface
uci set network.tailscale.proto='none'
uci set network.tailscale.device='tailscale0'
uci commit network

# 2. 新建 tailscale 防火墙区域，并开启 IP 动态伪装 (masq)
uci add firewall zone
uci set firewall.@zone[-1].name='tailscale'
uci set firewall.@zone[-1].input='ACCEPT'
uci set firewall.@zone[-1].output='ACCEPT'
uci set firewall.@zone[-1].forward='ACCEPT'
uci set firewall.@zone[-1].masq='1'
uci add_list firewall.@zone[-1].network='tailscale'
uci commit firewall

# 3. 允许 tailscale 区域与 lan 区域的双向转发
uci add firewall forwarding
uci set firewall.@forwarding[-1].src='tailscale'
uci set firewall.@forwarding[-1].dest='lan'
uci add firewall forwarding
uci set firewall.@forwarding[-1].src='lan'
uci set firewall.@forwarding[-1].dest='tailscale'
uci commit firewall

# 4. 重启网络与防火墙服务使配置生效
/etc/init.d/network restart
/etc/init.d/firewall restart
```

### 1.3 VPS：安装 Tailscale 并加入同一账号

```sh
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up --auth-key=<tskey>

tailscale up --auth-key=<tskey> --hostname=home-openwrt
tailscale ip   # 记录此 IP，后面填入 CN_EXIT_SOCKS5_HOST
```

同一账号下所有 VPS（JP/US/SG 等）均可访问同一个 OpenWrt Tailscale IP，无需额外配置。

### 1.4 VPS：配置 docker-compose.yml

在 `docker-compose.yml` 中取消注释并填值：

```yaml
- CN_EXIT_SOCKS5_HOST=100.x.x.x   # 替换为 tailscale ip 输出的 OpenWrt IP
- CN_EXIT_SOCKS5_PORT=7891         # OpenClash 默认 SOCKS5 端口
```

重启容器：

```sh
docker compose down && docker compose up -d
```

### 1.5 验证

```sh
# 确认 SOCKS5 outbound 已注入
docker compose exec sb-xray cat /sb-xray/xray/xr.json | python3 -m json.tool | grep -A5 '"cn-exit"'
# 预期：protocol=socks，address=100.x.x.x

# 通过 OpenWrt SOCKS5 访问国内内容
curl --socks5 100.x.x.x:7891 https://www.iqiyi.com -o /dev/null -w "%{http_code}"
```

---

## 方案二（备用）：xray reverse bridge

适用于无法使用 Tailscale 的环境（如 OpenWrt 无 kmod-tun）。需要大陆软路由能主动向 VPS 建立长连接。

### 前提条件

- 大陆 OpenWrt 可访问 VPS 的 443 端口
- 已部署 sb-xray（参见 [06-reverse-proxy-guide.md](06-reverse-proxy-guide.md)）

### 2.1 OpenWrt 安装 xray

```sh
# arm64（大多数 OpenWrt 路由器）
wget -O /tmp/xray.zip https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-arm64-v8a.zip
unzip /tmp/xray.zip xray -d /usr/bin/
chmod +x /usr/bin/xray
```

### 2.2 生成 bridge 专用 UUID

```sh
cat /proc/sys/kernel/random/uuid
```

### 2.3 获取 VPS Reality 公钥

```sh
docker compose logs sb-xray | grep "public key"
```

### 2.4 创建 bridge 配置

将 `templates/reverse_bridge/client.json` 复制到 OpenWrt，填入以下值并保存为 `/etc/xray/bridge.json`：

| 占位符 | 说明 |
|--------|------|
| `${XRAY_REVERSE_UUID}` | 上面生成的 UUID |
| `${DOMAIN}` | VPS 域名 |
| `${LISTENING_PORT}` | 443 |
| `${DEST_HOST}` | Reality 伪装域名 |
| `${XRAY_REALITY_PUBLIC_KEY}` | VPS Reality 公钥 |
| `${XRAY_REALITY_SHORTID}` | VPS Reality shortId |

### 2.5 设置开机自启（OpenWrt procd）

```sh
cat > /etc/init.d/xray-bridge << 'EOF'
#!/bin/sh /etc/rc.common
START=99
USE_PROCD=1
start_service() {
    procd_open_instance
    procd_set_param command /usr/bin/xray run -config /etc/xray/bridge.json
    procd_set_param respawn
    procd_set_param stdout 1
    procd_set_param stderr 1
    procd_close_instance
}
EOF
chmod +x /etc/init.d/xray-bridge
/etc/init.d/xray-bridge enable && /etc/init.d/xray-bridge start
```

### 2.6 VPS 配置

在 `docker-compose.yml` 中取消注释并填值：

```yaml
- ENABLE_REVERSE=true
- XRAY_REVERSE_UUID=<与 bridge 相同的 UUID>
- REVERSE_DOMAINS=<bridge 的访问域名，可留空>
- REVERSE_CN_EXIT=true
```

重启容器后验证：

```sh
docker compose logs sb-xray | grep -E "CN-exit|r-tunnel"
```

---

## 客户端配置

重新拉取订阅后，所有支持的客户端（Mihomo / Stash / Surge）会自动出现 **🇨🇳 回国** 策略组。

该策略组在 `include-all: true` 模式下列出所有节点。**将其切换至对应 VPS 节点**，国内流量即从该 VPS 出口经大陆落地。

已路由到 🇨🇳 回国 的规则：
- `geosite:iqiyi` — 爱奇艺
- `geosite:youku` — 优酷
- `geosite:bilibili` — 哔哩哔哩
- `geosite:category-bank-cn` — 国内银行
- `geosite:category-payment-cn` — 支付宝、微信支付

---

## 排查

### SOCKS5 模式：cn-exit outbound 未注入

检查容器启动日志：

```sh
docker compose logs sb-xray | grep "CN-exit"
# 预期：CN-exit(socks5): 100.x.x.x:7891
```

若无，检查 `CN_EXIT_SOCKS5_HOST` 是否已取消注释且无拼写错误。

### SOCKS5 模式：连接失败

```sh
# 在 VPS 上测试 Tailscale 连通性
tailscale ping <OpenWrt Tailscale IP>
# 在 VPS 上测试 SOCKS5 端口
curl --socks5 <OpenWrt Tailscale IP>:7891 https://www.baidu.com -o /dev/null -w "%{http_code}"
```

常见原因：
- Tailscale 未加入同一账号
- OpenWrt 防火墙未开放 7891 端口给 tailscale0
- OpenClash 未启动或 SOCKS5 端口配置有误

### reverse bridge：连接不上 VPS

```sh
# 在 OpenWrt 上检查日志
logread | grep xray
```

常见原因：UUID 不一致、Reality 公钥或 shortId 有误、VPS 防火墙未放行 443/TCP。
