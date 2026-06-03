# 回国代理部署指南

通过现有的 VLESS reverse bridge 功能，让境外设备能访问大陆限定服务（爱奇艺、优酷、哔哩哔哩、网银、支付宝等）。大陆家宽 OpenWrt 软路由作为落地 bridge，海外 VPS 上的 sb-xray 作为 portal。

## 流量路径

```
[境外设备]
  │ geosite:iqiyi / youku / bilibili / category-bank-cn 等
  ▼
[海外 VPS · sb-xray portal]
  │ VLESS reverse bridge（xr.json 路由）
  ▼
[OpenWrt bridge · 大陆家宽]
  │ freedom outbound → 直接出境
  ▼
[国内应用服务器]
```

访问 Google、Twitter 等境外服务不走任何代理，直接走节点或 DIRECT。

## 前提条件

- 已部署 sb-xray 海外 VPS（参见 [06-reverse-proxy-guide.md](06-reverse-proxy-guide.md)）
- 大陆有一台 OpenWrt 软路由，可出访 VPS 的 443 端口
- 大陆 ISP 宽带有公网 IP（或软路由可正常向外建立长连接）

## 一、OpenWrt bridge 配置

### 1.1 下载 xray 二进制

在 OpenWrt 上安装或下载 xray（建议版本与 VPS 相同）：

```sh
# amd64
wget -O /usr/bin/xray https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip
# arm64（OpenWrt 路由器通常用此架构）
wget -O /tmp/xray.zip https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-arm64-v8a.zip
unzip /tmp/xray.zip xray -d /usr/bin/
chmod +x /usr/bin/xray
```

### 1.2 生成 bridge 专用 UUID

在任意机器上运行：

```sh
xray uuid
# 或
cat /proc/sys/kernel/random/uuid
```

记录此 UUID，下面两处都要用到（VPS 环境变量 `XRAY_REVERSE_UUID` 和 bridge 配置）。

### 1.3 获取 VPS Reality 公钥

在 VPS 上查看：

```sh
grep XRAY_REALITY_PUBLIC_KEY /path/to/.env
# 或从 sb-xray 日志获取
docker compose logs sb-xray | grep "public key"
```

### 1.4 创建 bridge 配置文件

将 `templates/reverse_bridge/client.json` 复制到 OpenWrt，填入以下值并保存为 `/etc/xray/bridge.json`：

| 占位符 | 说明 |
|--------|------|
| `${XRAY_REVERSE_UUID}` | 上面生成的 UUID |
| `${DOMAIN}` | VPS 域名（如 `jp.example.com`） |
| `${LISTENING_PORT}` | 443 |
| `${DEST_HOST}` | Reality 目标伪装域名（同 VPS 配置） |
| `${XRAY_REALITY_PUBLIC_KEY}` | VPS Reality 公钥 |
| `${XRAY_REALITY_SHORTID}` | VPS Reality shortId |

```sh
# 测试配置是否有效
xray run -config /etc/xray/bridge.json
```

### 1.5 设置开机自启（OpenWrt procd）

创建 `/etc/init.d/xray-bridge`：

```sh
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
```

```sh
chmod +x /etc/init.d/xray-bridge
/etc/init.d/xray-bridge enable
/etc/init.d/xray-bridge start
```

## 二、海外 VPS 配置

在 `docker-compose.yml` 中启用以下环境变量（取消注释并填值）：

```yaml
- ENABLE_REVERSE=true
- XRAY_REVERSE_UUID=<与 bridge 相同的 UUID>
- REVERSE_DOMAINS=<bridge 的访问域名，可留空>
- REVERSE_CN_EXIT=true
```

`REVERSE_CN_EXIT=true` 会在容器启动时自动将 `xr.json` 里的 `cn-ip` 规则从 `block` 改为 `r-tunnel`，并插入 `geosite:cn → r-tunnel` 规则。

重启容器使配置生效：

```sh
docker compose down && docker compose up -d
```

验证生效：

```sh
docker compose exec sb-xray cat /sb-xray/xray/xr.json | grep -A3 '"cn-ip"\|"cn-geosite"'
# 预期：outboundTag 为 "r-tunnel"，且存在 cn-geosite 规则
```

## 三、客户端订阅更新

重新拉取订阅后，所有支持的客户端（mihomo / Stash / Surge）会自动出现 **🇨🇳 回国** 策略组。

该策略组默认指向 DIRECT（bypass），在 `include-all: true` 模式下列出所有节点。**要启用回国功能，需手动将其切换至海外 VPS 对应的节点**（即运行 sb-xray 的 VPS 节点），这样爱奇艺、优酷等流量才会经由 reverse bridge 从大陆出。

已路由到 🇨🇳 回国 的规则：
- `geosite:iqiyi` — 爱奇艺
- `geosite:youku` — 优酷
- `geosite:bilibili` — 哔哩哔哩
- `geosite:category-bank-cn` — 国内银行
- `geosite:category-payment-cn` — 支付宝、微信支付等

`geosite:cn` 和 `geoip:cn` 仍然路由到 **国内流量**（DIRECT），不影响已有行为。

## 四、排查

### bridge 连接不上 VPS

```sh
# 在 OpenWrt 上检查 xray 日志
logread | grep xray
# 或直接运行查看错误
xray run -config /etc/xray/bridge.json
```

常见原因：
- UUID 不一致（VPS 与 bridge 必须相同）
- Reality 公钥或 shortId 填写有误
- VPS 防火墙未放行 443/TCP

### 回国流量仍被 block（outboundTag 未改变）

检查 VPS 容器启动日志中是否有 `CN-exit:` 字样：

```sh
docker compose logs sb-xray | grep -E "CN-exit|REVERSE_CN_EXIT"
```

若无，说明 `REVERSE_CN_EXIT=true` 未生效，检查 docker-compose.yml 中该行是否被注释。

### 访问爱奇艺仍显示"您所在地区无法观看"

1. 确认 🇨🇳 回国 策略组已切换到 VPS 节点（非 DIRECT）
2. 在 VPS 上确认 bridge 已连接：`docker compose logs sb-xray | grep "r-tunnel"`
3. 检查 OpenWrt bridge 进程是否在运行
