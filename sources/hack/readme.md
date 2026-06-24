# hack 工具集

存放与 sb-xray 配套使用的辅助脚本，运行在客户端侧（如 OpenWrt 路由器）。

> **cdn-speedtest 已迁出本目录**：Cloudflare CDN IP 优选已并入 OpenWrt 一键初始化脚本（内嵌生成 `/usr/bin/cdn-speedtest`），配置与使用见 [`../openwrt/README.md`](../openwrt/README.md)。

## check_ip_type.sh — IP 质量体检

对一台节点的出口 IP 做体检：基础归属信息、风险评分、流媒体/AI 服务解锁情况。基于 xykt 的 IP 质量检测脚本改写，适配容器环境（精简依赖、curl 参数数组化避免注入）。常用于上线一台 VPS 后判断该出口 IP 是「家宽 / 机房 / 商业」类型、是否被风控、能否解锁 Netflix / YouTube / TikTok / ChatGPT 等。

### 功能

- **基础信息与类型**：聚合 Maxmind、IPinfo、IPregistry、IP2Location、AbuseIPDB 等多个数据库，判定 IP 用途类型（家宽 / 机房 / 商业 / 教育 / CDN / 手机 等）。
- **风险评分**：Scamalytics 欺诈分数、IPAPI 风险等级。
- **流媒体 / AI 解锁**：TikTok、Netflix、YouTube Premium、Disney+、ChatGPT 等区域解锁检测。
- 同时检测 IPv4 / IPv6，可指定出口网卡或代理。

### 使用

```bash
# 默认同时体检 IPv4 + IPv6
./check_ip_type.sh

# 仅 IPv4 / 仅 IPv6
./check_ip_type.sh -4
./check_ip_type.sh -6

# 显示完整 IP（默认打码）
./check_ip_type.sh -f

# 指定出口网卡（多线路机器选某条腿体检）
./check_ip_type.sh -i eth0

# 经指定代理体检（验证落地出口而非本机出口）
./check_ip_type.sh -x socks5://127.0.0.1:1080
```

| 参数 | 说明 |
|------|------|
| `-4` | 只检测 IPv4 |
| `-6` | 只检测 IPv6 |
| `-f` | 显示完整 IP（默认对 IP 打码） |
| `-i interface` | 指定 curl 出口网卡 |
| `-x proxy` | 指定 curl 代理 |

> 依赖 `curl` 与 `jq`；联网调用多个第三方 IP 数据库 API，结果受网络与各 API 可用性影响。

---

## rename.js — 节点重命名

基于 [Keywos/rule](https://github.com/Keywos/rule) 的节点重命名脚本。

```
https://hk.gh-proxy.org/https://raw.githubusercontent.com/currycan/sb-xray/main/sources/hack/rename.js
```

过滤正则：`._(距离|套餐|国内|剩余|到期)._`

使用示例：
```
https://raw.githubusercontent.com/Keywos/rule/main/rename.js#name=Nexitally&fgf=|&blkey=Emby+GPT>OpenAI&nm
```

**标旗：名称优先 + IP 地理兜底。** 默认按节点名称识别国旗；名称无地理线索（纯主机名 / 纯 IP）而识别为 🏳️ 时，自动按节点 `server` 的真实 IP 经 `ip-api.com` 兜底判国。兜底为异步、可选、带缓存，失败/无网时优雅保持 🏳️。机制与排障见 [docs/03 §3.3「IP 地理兜底」](../../docs/03-routing-and-clients.md#ip-地理兜底名称识别失败时)。

自测（零依赖，node 直跑，期望 `9 passed`）：
```
node sources/hack/rename.test.js
```
