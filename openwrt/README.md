# sb-xray OpenWrt 客户端安装脚本

把回国代理客户端在 OpenWrt 上的全部配置固化成一个幂等脚本。服务端（VPS）由 `docker-compose` 单独部署，不在本脚本范围内。

## 这个脚本做什么

| 步骤 | 内容 |
|------|------|
| 1. Tailscale | 下载二进制 + 写 `/etc/init.d/tailscale`，**固定 UDP 端口 41641** + kernel TUN 模式；配置 tailscale 防火墙 zone 与转发（lan 双向 + wan 出口）；`tailscale up` 上线并通告 subnet routes + exit node |
| 2. 防火墙放行 | 写 OpenClash 原生钩子，把 Tailscale UDP 在 nftables mangle 链顶 `return`，绕过 tproxy；OpenClash 每次重启自动重跑 |
| 3. 解耦 | 给 OpenClash 加 `DOMAIN,<VPS>,DIRECT` + fake-ip 过滤，让 reverse bridge 直连 VPS 真实 IP，不依赖 OpenClash 在线 |
| 3b. skip-auth | 经 overwrite 钩子把 `100.64.0.0/10` 注入 mihomo `skip-auth-prefixes`，让 VPS 经 Tailscale 访问本机 SOCKS5(7891) 免认证（机场开了 SOCKS 认证时 cn-exit 的必要条件）|
| 4. xray bridge | 下载 xray + 带 token 拉取已渲染的落地机 `client.json` + 写 `/etc/init.d/xray-bridge` |
| 5. keepalive | 每分钟 ping 对端 Tailscale IP，缓解双重 NAT 空闲掉线 |
| 6. 自检 | 12 项端到端验证 |

## 前置条件

- OpenWrt，使用 **fw4 / nftables**（不是旧 iptables）
- **OpenClash 已安装并运行**（脚本只注入钩子与规则，不装 OpenClash 本体）
- 能访问公网（下载 Tailscale / Xray / 落地机配置）
- VPS 服务端已 `ENABLE_REVERSE=true` 并能 `docker exec sb-xray show` 拿到下载链接

## 用法

```sh
# 1. 复制脚本和配置到路由器
scp openwrt/install.sh openwrt/config.env.example root@<路由器IP>:/root/sb-xray-openwrt/

# 2. 在路由器上填配置
ssh root@<路由器IP>
cd /root/sb-xray-openwrt
cp config.env.example config.env
vi config.env          # 填 VPS_DOMAIN / SUBSCRIBE_TOKEN / PEER_TS_IP / TS_HOSTNAME

# 3. 运行
sh install.sh
```

`config.env` 里所有值都来自 VPS：在 VPS 上运行 `docker exec sb-xray show`，输出里有域名、`?token=` 后的订阅 token、以及 reverse bridge 下载链接。`PEER_TS_IP` 用 VPS 上 `tailscale ip -4` 获取。

### Tailscale 授权（仅首次）

脚本跑到 `tailscale up` 时，若本机未登录会**打印一个登录 URL 并停下等待**。用浏览器打开它、授权一次即可，脚本随后继续。

### 批准 subnet routes 与 exit node（仅首次）

脚本通告的内网网段（`TS_ADVERTISE_ROUTES`）和 exit node 需要手动批准才生效：
[Tailscale 管理后台](https://login.tailscale.com/admin/machines) → 找到本机 → Edit route settings → 勾选 subnet routes 和 Use as exit node。

## 配置项

见 `config.env.example`。必填：`VPS_DOMAIN`、`SUBSCRIBE_TOKEN`、`PEER_TS_IP`、`TS_HOSTNAME`、`TS_VERSION`、`XRAY_VERSION`。
常用可选：`RELOAD_OPENCLASH=1`（安装末尾自动重载 OpenClash 使解耦规则生效）、`ARCH_OVERRIDE`、`TS_PORT`、`TS_ADVERTISE_ROUTES`（subnet router 通告网段，默认 `172.18.18.0/23`）。

## 幂等

脚本可反复执行：

- 二进制按版本比对，命中则跳过下载
- cron / OpenClash 规则用 grep 守卫，不产生重复行
- 防火墙钩子自带去重，OpenClash 反复重启不叠加
- 每次覆盖前生成 `.bak.<时间戳>` 备份

## 验证

脚本结尾自动跑 12 项自检。也可手动复测：

```sh
# 重启鲁棒性：OpenClash 重启后链路应快速恢复
/etc/init.d/openclash restart && sleep 5 && tailscale ping -c1 <PEER_TS_IP>

# 重启后服务自恢复
reboot   # 起来后 tailscale status 已登录、xray-bridge 在跑
```

VPS 侧确认 bridge 流量：

```sh
docker exec sb-xray sh -c 'grep r-tunnel /var/log/xray/access.log | tail'
# 应见 accepted ... -> r-tunnel
```

## 回滚

每个被改文件都有 `.bak.<时间戳>` 备份。手动恢复示例：

```sh
ls -t /etc/init.d/tailscale.bak.*        # 找最近备份
cp /etc/init.d/tailscale.bak.<ts> /etc/init.d/tailscale
/etc/init.d/tailscale restart
```

回退到 userspace-networking 模式（不需要 TUN / 防火墙 zone 的旧形态）：

```sh
# 仓库侧取回旧版脚本重跑
git checkout 10ce8f9 -- openwrt/install.sh
# 重新 scp 到路由器后 sh install.sh

# （可选）清理 kernel TUN 模式遗留的防火墙配置——留着也无害
uci delete network.tailscale
# 按 uci show firewall | grep tailscale 输出的索引逐个删除 zone/forwarding 后：
uci commit network && uci commit firewall
/etc/init.d/network reload && /etc/init.d/firewall reload
```

停用整套：

```sh
/etc/init.d/xray-bridge stop && /etc/init.d/xray-bridge disable
/etc/init.d/tailscale  stop && /etc/init.d/tailscale  disable
# 删 keepalive cron 行 + OpenClash custom 规则后重载 OpenClash
```

## 为什么固定 41641 端口

OpenWrt 多在双重 NAT 后、上游无 UPnP/NAT-PMP，Tailscale 直连靠 STUN 临时映射，随机端口空闲即老化掉线。固定端口 + 两侧 keepalive + 防火墙放行三者配合，让 OpenClash 重启后直连快速恢复，也为日后在上游路由器做 41641/UDP 端口转发铺路（端口转发后直连彻底稳定）。
