# 回国代理部署指南

让境外设备访问大陆限定服务（爱奇艺、优酷、哔哩哔哩、网银、支付宝等），流量经由大陆家宽出口。

---

## 方案一（推荐）：Tailscale + OpenClash

适用于 OpenWrt 上已安装 OpenClash 的场景，无需公网 IP，无需手动管理 xray 密钥。

### 架构

```
[ClashMi / Karing / 任何 Clash 客户端]
  规则: GEOSITE,CN / GEOIP,CN → 国内流量组 → 选择 VPS 节点

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

### 1.3 OpenClash 配置确认

CN exit 依赖 OpenClash 将国内目标流量放行为 DIRECT，通常无需额外操作。

**确认 CN→DIRECT 规则存在（默认已有）**

OpenClash 默认规则包含 `GEOSITE,CN,DIRECT` 和 `GEOIP,CN,DIRECT`，覆盖绝大部分国内域名和 IP。
若自定义过规则导致 CN 流量走代理节点，CN exit 将失效（出口 IP 变成代理节点 IP 而非家宽 IP）。

**可选：加 Tailscale 网段直连规则**

在 OpenClash → 覆写设置 → 规则（Rules）最顶部添加：

```yaml
- IP-CIDR,100.64.0.0/10,DIRECT,no-resolve
```

此规则让访问 Tailscale 节点自身（100.64.x.x）的流量直连，防止健康检查等场景形成环路。

> **注意**：直接在 OpenWrt 上执行 `curl ip.sb` 会显示代理节点 IP，这是**正常现象**——`ip.sb` 是非国内域名，OpenClash 代理它是预期行为。CN exit 仅对 `geosite:cn` + `geoip:cn` 目标生效。

### 1.4 VPS：安装 Tailscale 并加入同一账号

```sh
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up --auth-key=<tskey>

tailscale ip E3845-op   # 查询 OpenWrt 节点（1.1 中设置的 hostname）的 Tailscale IP，填入 CN_EXIT_SOCKS5_HOST
```

同一账号下所有 VPS（JP/US/SG 等）均可访问同一个 OpenWrt Tailscale IP，无需额外配置。

### 1.5 VPS：配置 docker-compose.yml

在 `docker-compose.yml` 中取消注释并填值：

```yaml
- CN_EXIT_SOCKS5_HOST=100.x.x.x   # 替换为 tailscale ip 输出的 OpenWrt IP
- CN_EXIT_SOCKS5_PORT=7891         # OpenClash 默认 SOCKS5 端口
```

重启容器：

```sh
docker compose down && docker compose up -d
```

### 1.6 验证

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
# arm64（大多数 OpenWrt 路由器，uname -m 显示 aarch64）
wget -O /tmp/xray.zip https://github.com/XTLS/Xray-core/releases/download/v26.3.27/Xray-linux-arm64-v8a.zip
# x86 软路由（uname -m 显示 x86_64）改用：
# wget -O /tmp/xray.zip https://github.com/XTLS/Xray-core/releases/download/v26.3.27/Xray-linux-64.zip

opkg update && opkg install unzip   # OpenWrt 默认没有 unzip
unzip /tmp/xray.zip xray -d /usr/bin/
chmod +x /usr/bin/xray
```

### 2.2 VPS 开启 reverse

在 `docker-compose.yml` 中取消注释并填值：

```yaml
- ENABLE_REVERSE=true
- REVERSE_DOMAINS=domain:lan       # 通过 bridge 访问的内网域名（覆盖所有 .lan）；只做回国可留空
- REVERSE_CN_EXIT=true             # 国内流量（geosite/geoip:cn）经 bridge 回家宽出口
```

> ⚠️ **关键**：`CN_EXIT_SOCKS5_HOST` 的优先级高于 `REVERSE_CN_EXIT`——只要它有值，国内流量就走 SOCKS5（方案一）而不进 reverse 隧道。用方案二回国时，请把 `CN_EXIT_SOCKS5_HOST` / `CN_EXIT_SOCKS5_PORT` 注释掉。

`XRAY_REVERSE_UUID` 无需手填：容器首次启动会自动生成并持久化（也可以自己指定一个）。

重启容器并验证：

```sh
docker compose down && docker compose up -d
docker compose logs sb-xray | grep -E "CN-exit|r-tunnel"
```

### 2.3 获取 bridge 参数

运行下面的命令，把配置变量输出**完整抄下来**，下一步要用：

```bash
docker exec sb-xray bash -c '. /.env/sb-xray; cat <<EOF
DOMAIN=$DOMAIN
LISTENING_PORT=$LISTENING_PORT
DEST_HOST=$DEST_HOST
XRAY_REVERSE_UUID=$XRAY_REVERSE_UUID
XRAY_REALITY_PUBLIC_KEY=$XRAY_REALITY_PUBLIC_KEY
XRAY_REALITY_SHORTID=$XRAY_REALITY_SHORTID
EOF'
```

| 参数 | 它是什么 |
|------|---------|
| `DOMAIN` | 你 VPS 的域名，例如 `vpn.example.com` |
| `LISTENING_PORT` | VPS 对外端口，一般是 `443` |
| `DEST_HOST` | Reality 伪装目标网站（照抄即可，不用懂） |
| `XRAY_REVERSE_UUID` | 隧道专用身份证，**只给隧道用**，和你平时翻墙的 UUID 不是同一个 |
| `XRAY_REALITY_PUBLIC_KEY` | Reality 公钥（照抄即可） |
| `XRAY_REALITY_SHORTID` | Reality Short ID（照抄即可） |

### 2.4 配置 OpenWrt bridge

#### 2.4.1 下载配置文件模板并填入参数

```sh
mkdir -p /etc/xray
wget https://raw.githubusercontent.com/currycan/sb-xray/main/templates/reverse_bridge/client.json -O /etc/xray/client.json
```

先把 2.3 抄下来的那几行（`DOMAIN=...`、`XRAY_REVERSE_UUID=...` 等）**原样粘贴到 OpenWrt 的命令行里回车**（这样它们就变成了 shell 变量），然后直接执行：

```sh
sed -i \
  -e "s|\${DOMAIN}|${DOMAIN}|g" \
  -e "s|\${XRAY_REVERSE_UUID}|${XRAY_REVERSE_UUID}|g" \
  -e "s|\${XRAY_REALITY_PUBLIC_KEY}|${XRAY_REALITY_PUBLIC_KEY}|g" \
  -e "s|\${XRAY_REALITY_SHORTID}|${XRAY_REALITY_SHORTID}|g" \
  -e "s|\${DEST_HOST}|${DEST_HOST}|g" \
  /etc/xray/client.json
```

确认没有占位符残留（无输出即正确）：

```sh
grep '\${' /etc/xray/client.json
```

> 模板里出站端口固定写的 `443`。如果你的 `LISTENING_PORT` 不是 443，再手动编辑 `/etc/xray/client.json`，把 `"port": 443` 改成你的端口。

#### 2.4.2 设置开机自启（OpenWrt procd）

```sh
cat > /etc/init.d/xray-bridge << 'EOF'
#!/bin/sh /etc/rc.common
START=99
USE_PROCD=1
start_service() {
    procd_open_instance
    procd_set_param command /usr/bin/xray run -config /etc/xray/client.json
    procd_set_param respawn
    procd_set_param stdout 1
    procd_set_param stderr 1
    procd_close_instance
}
EOF
chmod +x /etc/init.d/xray-bridge
/etc/init.d/xray-bridge enable && /etc/init.d/xray-bridge start
```

#### 2.4.3 配置需要外网访问的内网站点/服务（如 NAS）

举例来说：我们约定外网用 `nas.lan` 访问 NAS，现在要让路由器知道 `nas.lan` 指的是谁。把下面命令里的 `192.168.1.10` 换成你 NAS 的真实内网 IP：

```sh
echo "192.168.1.10 nas.lan" >> /etc/hosts
```

（喜欢图形界面的话，LuCI → 网络 → 主机名 → 添加，效果相同。）

```sh
ping -c 1 nas.lan
```

> ✅ **检查点**：ping 通，且显示的是 NAS 的内网 IP。

### 2.5 路由器上跑了 OpenClash？注意

隧道客户端是路由器**自己发出**的流量（目标是你的 VPS 443 端口）。OpenClash 默认只接管局域网设备的流量、不动路由器自身的连接，所以两者通常相安无事。

只有当你在 OpenClash 里开启了「代理路由器本机流量」之类的选项时，隧道流量可能被它截走、出现反复重连。处理办法任选其一：

- 在 OpenClash 的绕过列表（访问控制/黑名单）中加入你的 VPS 域名或 IP；
- 关闭「代理路由器本机流量」选项。

### 2.6 在外面用手机 / 电脑访问内网服务（如 NAS）

回到你的手机或电脑（连手机流量或其他网络，**不要连家里 WiFi**，否则测不出效果）。

#### 2.6.1 核心概念：`nas.lan` 必须"走代理"

平时我们让国内流量直连、国外流量走代理；而 `nas.lan` 恰恰相反——它**必须走 sb-xray 节点**，因为只有 VPS 知道怎么把它送进回家的隧道。

好消息是：sb-xray 自带的订阅模板里，所有没被规则匹配的"陌生域名"默认都交给 **`兜底流量`** 这个策略组，`nas.lan` 正属于此类。所以你只需要做一件事：**确保 `兜底流量` 组选中的是 sb-xray 节点**（而不是 DIRECT 或别家机场的节点）。

#### 2.6.2 手机（ClashMi / Karing）

1. 打开 App，确认 sb-xray 的订阅是当前生效配置，启动代理；
2. 在策略组列表里找到 **`兜底流量`**，把它切换到你的 sb-xray 节点（或指向 sb-xray 的自动选择组）;
3. 浏览器打开 `http://nas.lan/`（NAS 网页端口不是 80 的话带上端口，如 `http://nas.lan:5000/`）。

#### 2.6.3 电脑（Clash Verge / mihomo）

操作与手机相同：导入订阅 → 开启系统代理 → `兜底流量` 组选中 sb-xray 节点 → 浏览器访问 `http://nas.lan/`。

想要"一劳永逸"、不依赖兜底组的选择，可以在客户端的覆写/自定义规则里**置顶**加一条（`节点选择` 换成你实际指向 sb-xray 的策略组名）：

```yaml
rules:
  - DOMAIN-SUFFIX,lan,节点选择
```

> ✅ **检查点**：浏览器成功打开 NAS 登录页。

---

## 客户端配置：让国内流量走回国链路

方案一 / 方案二的客户端操作相同。重新拉取订阅后，模板已把 `GEOSITE,CN` / `GEOIP,CN` 等规则指向 **`国内流量`** 策略组（默认选中「直接连接」）。

身在境外想用爱奇艺、网银、支付宝等大陆限定服务时，把 **`国内流量`** 组切换到你的 sb-xray VPS 节点：

1. 客户端策略组列表里找到 **`国内流量`**；
2. 从「直接连接」切换为 sb-xray 节点（或指向它的自动选择组）。

之后国内流量先到 VPS，再由 VPS 端 xray 按 `geosite:cn` / `geoip:cn` 规则转入回国链路——方案一经 SOCKS5 到 OpenClash 直连出去，方案二经 r-tunnel 由 bridge 直连出去——最终从大陆家宽 IP 访问目标服务。

验证：切换后浏览器访问 `https://ip.cn` 等国内 IP 查询站，显示的应是你家宽的 IP。

> 人在国内时记得把该组切回「直接连接」，否则国内流量会白白绕道 VPS。

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

常见原因：UUID 不一致、Reality 公钥或 shortId 有误、VPS 防火墙未放行 443/TCP。
