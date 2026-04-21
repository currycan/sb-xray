# 新特性使用指南

> 本指南覆盖 [CHANGELOG.md](../CHANGELOG.md) [Unreleased] 段里列出的全部新增 / 强化能力。
>
> 按"**做什么 / 何时用 / 怎么开 / 如何验证 / 故障排查**"五段式组织。
>
> 默认路径（不改任何 env）的容器行为：Hy2 由 Xray 接管但客户端参数无感；所有实验性入站关闭；Reverse Proxy 关闭；webhook 事件总线以 dry-run 模式运行（仅本地日志，不发外部通知）。

## 目录

1. [事件总线（webhook → shoutrrr）](#1-事件总线-webhook--shoutrrr)
2. [两轨订阅（v2rayn + v2rayn-compat，adv 轨已并入主轨）](#2-两轨订阅v2rayn--v2rayn-compatadv-轨已并入主轨)
3. [VLESS Reverse Proxy 内网穿透](#3-vless-reverse-proxy-内网穿透)
4. [Xray 原生 Hysteria2](#4-xray-原生-hysteria2)
5. [XHTTP/3 + BBR 主轨入站（永久启用）](#5-xhttp3--bbr-主轨入站永久启用)
6. [XICMP 紧急通道](#6-xicmp-紧急通道)
7. [XDNS 紧急通道](#7-xdns-紧急通道)
8. [ECH 占位开关](#8-ech-占位开关)
9. [Feature Flag 与 Env 变量速查表](#9-feature-flag-与-env-变量速查表)
10. [通用故障排查](#10-通用故障排查)

---

## 1. 事件总线（webhook → shoutrrr）

**做什么**：把 Xray 路由命中 `ban_bt` / `ban_geoip_cn` / `ban_ads` / `private-ip` 四条规则时的事件（含 protocol / source / destination / email / inboundTag / outboundTag / ts 元数据）通过 HTTP POST 推送到容器内 sidecar `shoutrrr-forwarder.py`，再由 `shoutrrr` CLI 转发到 Telegram / Discord / Slack / Gotify / 等 20+ 通道。

**何时用**：想实时知道节点被 BT / 广告流量骚扰、有客户端触发私网 IP 防护、或需要对国内回源做审计告警。相比 `tail access.log | grep` 方式，元数据更完整且零延迟。

**怎么开**：在 `docker-compose.yml` 的 `environment:` 段添加 `shoutrrr` URL（[完整 URL 语法见 shoutrrr docs](https://containrrr.dev/shoutrrr/v0.8/services/overview/)）：

```yaml
    environment:
      # Telegram bot
      - SHOUTRRR_URLS=telegram://BOT_TOKEN@telegram?chats=@YOUR_CHANNEL
      # 也支持多通道并发（逗号分隔）
      # - SHOUTRRR_URLS=telegram://...,discord://TOKEN@WEBHOOK_ID
      - SHOUTRRR_TITLE_PREFIX=[sb-xray-bracknerd]
```

`SHOUTRRR_URLS=""`（默认）时 forwarder 进入 dry-run 模式，事件只写入 `/var/log/supervisor/shoutrrr-forwarder.out.log`，不发外部通知。

**如何验证**：

```bash
# 1. forwarder 健康探针
docker exec sb-xray curl -s http://127.0.0.1:18085/healthz
# 期望：{"ok":true}

# 2. 用 xray 的 webhook 测试工具触发一次（或直接访问 bt 站点）
docker exec sb-xray curl -X POST http://127.0.0.1:18085/shoutrrr/ban_bt \
  -H 'Content-Type: application/json' \
  -d '{"ruleTag":"ban_bt","source":"198.51.100.1","destination":"1.1.1.1"}'
# 期望日志里看到 "forwarded to N channels"
```

**故障排查**：
- Telegram 没收到：检查 `docker logs sb-xray` 的 `shoutrrr-forwarder.err.log`；最常见错误是 BOT_TOKEN 格式错或 bot 没加入频道
- 事件完全没产生：测 `SHOUTRRR_URLS` 没配置时是否也写日志；写了说明链路通，只是外部通道未启用
- 减噪：`webhook.deduplication` 默认 5 分钟去重（`xr.json` 里配），同一源的连续事件只推一次

---

## 2. 两轨订阅（v2rayn + v2rayn-compat，adv 轨已并入主轨）

**做什么**：2026-04 起，**原 `v2rayn-adv` 独立订阅轨已退役**，adv 能力（XHTTP obfuscation 新字段 `xPaddingQueryParam` / `xPaddingPlacement` / `UplinkDataPlacement` + Finalmask `tcp.fragment`）**直接合并到 `02_xhttp_inbounds.json` 主轨**。最终订阅结构简化为两轨：

| 轨道 | URL 路径 | 目标客户端 | 包含能力 |
|---|---|---|---|
| **v2rayn** (主轨) | `https://${CDNDOMAIN}/sb-xray/v2rayn?token=${SUBSCRIBE_TOKEN}` | **Xray-core 26.3.27+**（v2rayN / Xray CLI / Shadowrocket / Stash 等） | ML-KEM-768 + adv obfs 新字段 + Finalmask fragment + **XHTTP-H3 + BBR 节点优先** |
| **v2rayn-compat** | `https://${CDNDOMAIN}/sb-xray/v2rayn-compat?token=${SUBSCRIBE_TOKEN}` | **mihomo / OpenClash / Karing / 低版 Xray-core（<26.3.27）** | 无 VLESS 加密 + TCP xhttp（无 H3、无 fragment） |

**为什么退役 adv 轨**：
- XHTTP-H3 客户端必然 Xray-core 26.3.27+，已在 `02_xhttp_h3_inbounds.json` 内嵌 adv obfs 字段
- v2rayN 自 2024 起跟着 Xray-core 最新发，2026-04 所有 v2rayN 用户几乎都在 26.3.27+ 范围内
- Karing / OpenClash 本就走 `v2rayn-compat`（sing-box / mihomo 不认 ML-KEM）
- **三轨简化为两轨**：一套主轨吃最新 Xray-core，一套 compat 吃其他

**何时用 compat 而非主轨**：
- 客户端核心是 **sing-box / mihomo**（Karing / OpenClash）
- 或 Xray-core 版本 **< 26.3.27**（碰到 obfs 新字段会握手失败）

**如何验证**：

```bash
docker exec sb-xray sh -c 'echo v2rayn:;        base64 -d /sb-xray/subscribe/v2rayn        | wc -l'
docker exec sb-xray sh -c 'echo v2rayn-compat:; base64 -d /sb-xray/subscribe/v2rayn-compat | wc -l'
# v2rayn-adv 不再生成：
docker exec sb-xray sh -c 'ls /sb-xray/subscribe/ | grep -c v2rayn-adv'
# 期望：0（adv 订阅产物已不生成）
```

**故障排查**：
- 客户端原订阅是 `/v2rayn-adv`：**改订阅 URL 为 `/v2rayn`** —— 主轨已吸收全部 adv 能力
- 主轨节点全部延迟 -1：客户端版本 < 26.3.27，**改订阅 `/v2rayn-compat`**
- Vmess-Adv 节点：历史上曾存在，因 v2rayN URL 标准不支持 `fm=` 字段承载 Finalmask header-custom 而彻底移除，详见 CHANGELOG

---

## 3. VLESS Reverse Proxy 内网穿透

**做什么**：让**零公网 IP 的家宽落地机**通过 REALITY 隧道反向挂载到 VPS，在 VPS 侧按域名路由把流量回源到家宽内网。相当于 frp / cloudflared tunnel 的替代，但用同一套 Xray 二进制 + 同一条 REALITY 隧道。

**何时用**：
- 想在家宽暴露 NAS / Home Assistant / 家庭 IoT 服务，不想走 cloudflared tunnel 或买公网 IP
- 想让流媒体（Netflix / Disney）走家宽真实 ISP 出口以匹配 residential IP 需求
- VPS 要让部分业务"落地"到家宽以获得更好的国内访问路径

**怎么开**：完整部署步骤见 [`docs/06-reverse-proxy-guide.md`](./06-reverse-proxy-guide.md)。核心 env 配置：

```yaml
    environment:
      # VPS（portal）侧
      - ENABLE_REVERSE=true
      - REVERSE_DOMAINS=domain:home.lan,domain:nas.example.com,domain:jellyfin.example.com
```

落地机（bridge）侧部署时用 `templates/reverse_bridge/client.json` 作为 Xray outbound 模板，把自己注册为 `r-tunnel` 的反向连接源。

**如何验证**：

```bash
# 1. 确认 VPS 侧注入成功
docker exec sb-xray cat /sb-xray/xray/01_reality_inbounds.json \
  | jq '.inbounds[0].settings.clients | length'
# 启用 reverse 后应 = 2（原主 UUID + reverse UUID）

docker exec sb-xray cat /sb-xray/xray/01_reality_inbounds.json \
  | jq '.inbounds[0].settings.clients[] | select(.reverse)'
# 应看到 reverse: {tag: "r-tunnel"} 的条目

# 2. 确认 routing 规则
docker exec sb-xray cat /sb-xray/xray/xr.json \
  | jq '.routing.rules[0]'
# 应看到 ruleTag: "reverse-bridge" 指向 r-tunnel outbound

# 3. 从 VPS 访问 REVERSE_DOMAINS 列出的任一域名，应能穿透到家宽内网
```

**故障排查**：
- `jq: error: object .reverse not a bool`：Xray v26.4.17 里 `reverse` 是 `{tag:string}` 对象而非 bool；落地机模板用**扁平化 simplified 格式**，嵌套 `servers[]` 会被静默忽略
- bridge 断连：检查家宽侧 xray 日志，常见原因是出口网络封 REALITY；换条链路再试
- 撤销 reverse：`ENABLE_REVERSE=false` + `docker compose up -d` 重建容器；entrypoint 从原始模板重新渲染，孤儿条目被覆盖清理

---

## 4. Xray 原生 Hysteria2

**做什么**：Hy2 入站后端**永久**从 sing-box 切到 Xray，**无 feature flag**。端口 / 密码 / obfs / ALPN 完全等价，客户端订阅 URL 完全不变。

**何时用**：**无任何操作**。升级到本 release 即永久生效 —— sing-box 不再承载 Hy2，仅保留 TUIC + AnyTLS。

**怎么开**：不需要开关。Hy2 永久由 xray 接管，没有回退路径（见 [CHANGELOG](../CHANGELOG.md)）。

**如何验证**：

```bash
# 端口由 xray pid 绑定（不是 sing-box）
docker exec sb-xray ss -ulnp | grep 6443
# 期望：users:(("xray",pid=...))

# entrypoint 启动日志
docker logs sb-xray 2>&1 | grep '阶段 1'
# 期望：[阶段 1] 完成 hy2=6443(xray) tuic=8443 anytls=4433

# sing-box 目录只剩 tuic + anytls
docker exec sb-xray ls /sb-xray/sing-box/
# 期望：01_tuic_inbounds.json  02_anytls_inbounds.json  cache.db  sb.json

# 端到端握手测试（容器内用 sing-box 作 client 连 xray 服务端）
# 期望：http_code=200，time_total < 0.1s
```

**故障排查**：
- 客户端原本能连、升级后连不上：确认 `PORT_HYSTERIA2=6443` 没被外部覆盖；xray `-test` 输出是否 `Configuration OK`
- 想短期回退：`docker tag currycan/sb-xray:before-m4 currycan/sb-xray:latest && docker compose up -d`（要求升级前在本地保留 before-m4 tag）

---

## 5. XHTTP/3 + BBR 主轨入站（永久启用）

**做什么**：纯 UDP + HTTP/3 + BBR 拥塞控制的 XHTTP 入站（`templates/xray/02_xhttp_h3_inbounds.json`），**永久启用无开关**。与 `02_xhttp_inbounds.json`（TCP / 经 nginx / CDN 友好）**互补不替换**。模板内置 adv 字段（`xPaddingQueryParam` / `xPaddingPlacement` / `UplinkDataPlacement`）—— 因 H3 客户端必然 Xray-core 26.3.27+，直接上 adv，不分 h3-base / h3-adv 两份。

对应节点 `Xhttp-H3+BBR` **进入 `v2rayn` 主轨**（排第一位，性能优先；客户端按实测 RTT 重排，显示序保留 H3 优先）。`v2rayn-compat` 不含 H3 节点（sing-box / mihomo 不支持 xhttp-h3 transport）。

**何时用**：
- 所有 `v2rayN 26.3.27+` / `Xray CLI 26.3.27+` 客户端**自动拿到 H3 节点**，BBR/QUIC 弱网吞吐优势直接生效
- 低版本 Xray-core 客户端命中 H3 节点会延迟 -1 自动跳过，其他节点不受影响

**不能用的场景**（仍需 02_xhttp 兜底）：
- 走 CDN 的链路：Cloudflare 到源站**不支持 H3 upstream**（截至 2026-04），CDN-fronted 部署必须走 `02_xhttp` 的 TCP/443 链路
- 企业 wifi / 部分运营商封锁 UDP 非 443：`02_xhttp` 的 TCP/443 能过，`02_xhttp_h3` 的 UDP/4443 不能过
- Xray-core 低于 26.3.27 的客户端：不识别 xhttp-h3

**不支持的客户端（走 compat 轨）**：**mihomo（OpenClash）和 sing-box（Karing）的 xhttp transport 是 TCP-only**，不支持 QUIC / H3。这类客户端继续走 `02_xhttp_compat_inbounds.json` 的 `v2rayn-compat` 订阅轨（`decryption: "none"` + TCP xhttp，不含任何 H3 节点）。

**怎么开**：**无需任何操作**，升级到本 release 即永久启用。仅需确认宿主机防火墙放行 UDP `${PORT_XHTTP_H3:-4443}`：

```bash
# 宿主机放行 UDP 4443（host 网络模式下 docker-compose ports: 段被忽略，直接操作 iptables/nftables）
iptables -I INPUT -p udp --dport 4443 -j ACCEPT
# 持久化（Debian/Ubuntu）
iptables-save > /etc/iptables/rules.v4
```

若需改端口：在 `docker-compose.yml` 环境变量覆盖 `PORT_XHTTP_H3=xxxx`（避开 TUIC 8443、Hy2 6443）。

**如何验证**：

```bash
# 配置渲染
docker exec sb-xray ls /sb-xray/xray/ | grep xhttp_h3
# 期望：02_xhttp_h3_inbounds.json

# 端口监听（默认 4443）
docker exec sb-xray ss -ulnp | grep 4443
# 期望：users:(("xray",...))

# v2rayn 主轨订阅里包含 Xhttp-H3+BBR
docker exec sb-xray sh -c 'base64 -d /sb-xray/subscribe/v2rayn | grep Xhttp-H3'
# 期望：vless://...@domain:4443?...alpn=h3&...#Xhttp-H3+BBR...
```

**故障排查**：
- xray 启动崩溃：最常见是 `PORT_XHTTP_H3` 被其他协议占用（与 TUIC 8443、Hy2 6443 冲突）
- v2rayN 里 Xhttp-H3 延迟 -1：检查客户端版本（需 26.3.27+）；检查宿主机 UDP/4443 防火墙；对于走 CDN 的链路放弃 H3 走 02_xhttp
- 吞吐没提升：BBR 在高质量链路上与 CUBIC 差别不大；在丢包率 >1% 的链路上效果才明显
- Karing / OpenClash 看不到 H3 节点：符合预期 —— compat 轨不含 H3（sing-box / mihomo 不支持）

---

## 6. XICMP 紧急通道

**做什么**：用 ICMP echo 报文载荷承载代理流量（类似 icmptunnel / hans），走 mKCP transport。配置文件命名带 `_emergency_` 特征以示区分。

**何时用**：**抗封锁备选 / 极端场景专用**。常规通道（REALITY / XHTTP-TCP / XHTTP-H3 / Hy2 / TUIC / AnyTLS）性能和隐蔽性都远超 XICMP；**只有当常规通道全部被封、仅 ICMP 可通时才启用**。牺牲带宽换可达性。

**怎么开**：

1. **容器必须有 NET_RAW 能力**（默认没有，需要在 `docker-compose.yml` 取消注释）：

```yaml
    cap_add:
      - NET_RAW
```

2. **env 启用**：

```yaml
    environment:
      - ENABLE_XICMP=true
      - PORT_XICMP_ID=12345   # 16-bit ICMP id，默认 12345，可改
```

3. **客户端配置**：XICMP 没有标准化 URL 分享格式，需要**手动拼 xray 客户端 JSON**（模板参考 `templates/xray/05_xicmp_emergency_inbounds.json`，交换 inbound → outbound 即可）。

**如何验证**：

```bash
# 配置渲染 + 权限
docker exec sb-xray sh -c 'ls /sb-xray/xray/ | grep xicmp && cat /proc/self/status | grep CapEff'
# 期望：05_xicmp_emergency_inbounds.json 存在；CapEff 含 cap_net_raw

# 客户端测试（需要有 XICMP client 工具或自建 xray client）
ping bracknerd.ansandy.com  # 确认 ICMP 可达
# 然后用 xray client 通过 XICMP outbound 访问任一 HTTPS 站
```

**故障排查**：
- `ENABLE_XICMP=true` 但 xray 启动报 permission denied：忘了加 `cap_add: NET_RAW`
- ICMP 被路由器 rate-limit：XICMP 默认 `mtu=1400 / tti=100ms`，与 Linux 默认 ICMP rate limit 容易冲突，吞吐被限到几 KB/s。这是 protocol inherent 限制，不是 bug

---

## 7. XDNS 紧急通道

**做什么**：用 DNS 查询载荷承载代理流量（类似 DNSTT），走 mKCP 高 TTI transport。配置文件命名带 `_emergency_` 特征以示区分。

**何时用**：**抗封锁备选 / 比 XICMP 更极端的场景**——连 ICMP 都不通但 DNS 能用（例如某些企业 wifi 只放过 DNS）。同样属于牺牲带宽保可达性的备选通道，不要用作常规协议。

**怎么开**：

1. **必须有用户控制的 NS 域名**。例如你控制 `ns1.example.com` 并把它的权威 NS 记录指向你的 VPS（`XDNS_DOMAIN` 设为这个域名）：

```yaml
    environment:
      - ENABLE_XDNS=true
      - XDNS_DOMAIN=ns1.example.com
      - PORT_XDNS=5353    # 默认 5353 UDP，避开 53 的 systemd-resolved
```

2. **DNS 权威服务器配置**：`ns1.example.com` 的 A 记录指向 VPS IP；`PORT_XDNS` 端口允许 UDP 入站；（可选）在上游 DNS 提供商配置 delegation glue。

**如何验证**：

```bash
docker exec sb-xray ls /sb-xray/xray/ | grep xdns
# 期望：06_xdns_emergency_inbounds.json

docker exec sb-xray ss -ulnp | grep 5353

# 客户端侧用 dig 测试 NS 能响应（不通就说明 delegation 没做好）
dig @ns1.example.com TXT probe.ns1.example.com
```

**故障排查**：
- `XDNS_DOMAIN=""` 但 `ENABLE_XDNS=true`：xray 仍会启动入站但 schema 里 `domains: [""]` 无效，后续请求都会被丢弃；必须配
- 延迟极高（>600ms）：XDNS 的 `tti=600`（模板默认），用 600ms 的 RTT 做通讯是**故意的**——这是极端场景下的换速保通
- 被防火墙识别：XDNS 把载荷塞到 DNS 查询里，深度 DPI 能识别非标准查询模式；只对穷举式封锁有效

---

## 8. ECH 占位开关

**做什么**：**目前仅占位**。Dockerfile 注册了 `ENABLE_ECH=false` env，但 TLS 层的 `tlsSettings.echConfigList` 尚未接入任何入站模板。**启用暂无实际效果**。

**何时用**：**暂时不要依赖**。留作下次 release 真正实现 TLS ECH 后再启用。

**追踪**：
- TLS ECH 集成仍在计划中，当前版本仅为 env 占位
- 上游 Xray v26.3.27 [#5725](https://github.com/XTLS/Xray-core/pull/5725) 提供 ECH 字段

---

## 9. Feature Flag 与 Env 变量速查表

| 变量 | 默认 | 作用 | 相关文件 |
|---|---|---|---|
| `ENABLE_XICMP` | `false` | XICMP **紧急通道**（需 `cap_add=NET_RAW`；极端封锁场景备选） | `templates/xray/05_xicmp_emergency_inbounds.json` |
| `ENABLE_XDNS` | `false` | XDNS **紧急通道**（需 `XDNS_DOMAIN`；极端封锁场景备选） | `templates/xray/06_xdns_emergency_inbounds.json` |
| `ENABLE_ECH` | `false` | ❗占位，暂无效果 | - |
| `ENABLE_REVERSE` | `false` | VLESS Reverse Proxy 内网穿透 | `templates/reverse_bridge/client.json` |
| `PORT_XHTTP_H3` | `4443` | XHTTP/3 监听 UDP 端口 | - |
| `PORT_XICMP_ID` | `12345` | XICMP 的 16-bit ICMP id | - |
| `PORT_XDNS` | `5353` | XDNS 监听 UDP 端口 | - |
| `PORT_HYSTERIA2` | `6443` | Hy2 监听 UDP 端口（xray 接管后不变） | - |
| `PORT_TUIC` | `8443` | TUIC UDP（sing-box） | - |
| `PORT_ANYTLS` | `4433` | AnyTLS TCP（sing-box） | - |
| `XDNS_DOMAIN` | `""` | XDNS 权威 NS 域名 | - |
| `REVERSE_DOMAINS` | `""` | 逗号分隔域名列表，命中走 reverse 隧道 | - |
| `SHOUTRRR_URLS` | `""` | shoutrrr 推送 URL（dry-run 留空） | `scripts/shoutrrr-forwarder.py` |
| `SHOUTRRR_FORWARDER_PORT` | `18085` | forwarder HTTP 端口 | - |
| `SHOUTRRR_TITLE_PREFIX` | `[sb-xray]` | 推送标题前缀 | - |
| `LOG_LEVEL` | `warning` | xray + sing-box 日志级别（debug/info/warning/error） | - |

---

## 10. 通用故障排查

### 10.1 确认容器健康

```bash
docker ps --filter name=sb-xray --format "{{.Names}}\t{{.Status}}"
# 期望：Up ... (healthy)

docker exec sb-xray supervisorctl status
# 期望：10 program 全部 RUNNING
```

### 10.2 配置渲染失败

```bash
docker exec sb-xray xray -test -confdir /sb-xray/xray/ 2>&1 | tail -20
# 期望：Configuration OK

# 查哪个模板 envsubst 留了字面量
docker exec sb-xray sh -c 'grep -rn "\\\${[A-Z_]*}" /sb-xray/xray/ || echo "all clean"'
# 出现字面量说明 env 变量未设置；检查 Dockerfile ENV 或 docker-compose environment
```

### 10.3 smoke test 运行

```bash
cd /path/to/sb-xray
SKIP_COMPOSE=1 bash scripts/test_smoke.sh   # 仅静态（CI 用）
bash scripts/test_smoke.sh                  # 含 docker 运行时检查（需要本地跑着 sb-xray 容器）
# 期望：通过: 52  失败: 0
```

### 10.4 订阅输出

```bash
docker exec sb-xray show-config
# 交互式打印两轨订阅 URL（v2rayn / v2rayn-compat）+ QR 码（qrencode 可用时）
```

### 10.5 快速回滚到 before-m4

```bash
# 前提：升级时本地保留了 before-m4 tag
ssh <vps> "docker tag currycan/sb-xray:before-m4 currycan/sb-xray:latest && cd /root/sb-xray && docker compose up -d"
# 30 秒内回滚到上一版镜像
```

### 10.6 日志定位

```bash
docker exec sb-xray sh -c 'ls /var/log/supervisor/'
# xray.out.log / xray.err.log / sing-box.out.log / nginx.err.log / shoutrrr-forwarder.out.log 等

docker exec sb-xray tail -100 /var/log/supervisor/xray.err.log
```

---

## 相关资源

- [CHANGELOG.md](../CHANGELOG.md) —— 版本发布日志
- [docs/01-architecture-and-traffic.md](./01-architecture-and-traffic.md) —— 架构与流量拓扑
- [docs/02-protocols-and-security.md](./02-protocols-and-security.md) —— 协议与安全设计
- [docs/03-routing-and-clients.md](./03-routing-and-clients.md) —— 路由与客户端
- [docs/04-ops-and-troubleshooting.md](./04-ops-and-troubleshooting.md) —— 运维与故障排查
- [docs/05-build-release.md](./05-build-release.md) —— 构建与发布流程
- [docs/06-reverse-proxy-guide.md](./06-reverse-proxy-guide.md) —— VLESS Reverse Proxy 部署指南
- [references/implementation-notes.md](../references/implementation-notes.md) —— 实施过程笔记（本地维护）
