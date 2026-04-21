# 08. VLESS Reverse Proxy 内网穿透指南

> 把你家里的路由器 / NAS / 本地 Web 服务通过 sb-xray 公网节点**反向暴露**出来，零公网 IP、零端口映射、复用已有 REALITY 通道。
>
> 能力来自 Xray-core v25.10.15 [PR #5101](https://github.com/XTLS/Xray-core/pull/5101) + v26.3.27 [#5837](https://github.com/XTLS/Xray-core/pull/5837)（reverse + sniffing）。

---

## 0. 适用场景

- 家里跑了 **NAS / HomeAssistant / 软路由 web 界面**，想在外网访问却没有公网 IP
- 已有 sb-xray 公网节点（`bracknerd.ansandy.com` 等），不想再跑 frp / cloudflared tunnel
- 希望**外部访问流量直接通过已有 REALITY 隧道**，不开新端口、不暴露家宽 IP

---

## 1. 架构

```
[外部用户] --https--> [公网 sb-xray (portal)]
                           ^
                           | REALITY 反向隧道（UUID=XRAY_REVERSE_UUID）
                           |
                    [家宽落地机 (bridge)]
                           |
                           v freedom 出站
                    [nas.lan / router.lan / 任意内网资源]
```

- **portal**（公网端 sb-xray）：在 REALITY 入站的 `clients[]` 加一个带 `reverse.tag=r-tunnel` 标记的 UUID；routing.rules 里把 `REVERSE_DOMAINS`（如 `domain:home.lan`）的流量 `outboundTag: r-tunnel`
- **bridge**（家宽落地机）：跑一个 xray 客户端进程，以该 UUID 主动连接 portal 并保持长连接；portal 推过来的流量用 freedom 直出到内网
- **流量方向**：所有 TCP 连接都由 bridge → portal 发起（家宽只需要**出**443，不需要开端口）

---

## 2. 公网端启用（sb-xray）

### 2.1 修改 `docker-compose.yml`

```yaml
services:
  sb-xray:
    environment:
      # ... 已有变量 ...
      - ENABLE_REVERSE=true
      - REVERSE_DOMAINS=domain:home.lan,domain:nas.lan,domain:router.lan
```

- `ENABLE_REVERSE=true` 触发 entrypoint 的 reverse 注入逻辑
- `REVERSE_DOMAINS` 是逗号分隔的 geosite / domain: 前缀匹配列表，命中后才走 reverse 隧道
  - 留空也可以，此时隧道会建立但没有任何流量路由进来（诊断用）

### 2.2 重启容器

```bash
docker compose up -d --force-recreate
```

entrypoint 自动：
1. 生成 `XRAY_REVERSE_UUID`（首次）持久化到 `.envs/sb-xray`
2. 用 `jq` 往 `/sb-xray/xray/01_reality_inbounds.json` 的 `clients` 追加带 `reverse.tag=r-tunnel` 的条目
3. 用 `jq` 往 `/sb-xray/xray/xr.json` 的 `routing.rules` 顶部插入 `outboundTag: r-tunnel` 规则
4. xray 重启加载新配置

### 2.3 获取 bridge 端所需参数

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

六个值要原样给到落地机。

---

## 3. 家宽落地机部署（bridge）

### 3.1 拉模板

```bash
wget https://raw.githubusercontent.com/currycan/sb-xray/main/templates/reverse_bridge/client.json \
     -O /etc/xray/client.json
```

或从仓库 `templates/reverse_bridge/client.json` 复制。

### 3.2 填参数

编辑 `/etc/xray/client.json`，把 6 个 `${...}` 占位符替换成上一步拿到的真值（脚本示例）：

```bash
sed -i \
  -e "s|\${DOMAIN}|bracknerd.ansandy.com|g" \
  -e "s|\${LISTENING_PORT}|443|g" \
  -e "s|\${DEST_HOST}|speed.cloudflare.com|g" \
  -e "s|\${XRAY_REVERSE_UUID}|<你的 UUID>|g" \
  -e "s|\${XRAY_REALITY_PUBLIC_KEY}|<你的 pubkey>|g" \
  -e "s|\${XRAY_REALITY_SHORTID}|<你的 shortid>|g" \
  /etc/xray/client.json
```

### 3.3 跑 xray

最简单：前台测试

```bash
xray run -c /etc/xray/client.json
```

正式部署用 systemd：

```ini
# /etc/systemd/system/xray-reverse-bridge.service
[Unit]
Description=Xray Reverse Bridge (sb-xray)
After=network-online.target

[Service]
ExecStart=/usr/local/bin/xray run -c /etc/xray/client.json
Restart=always
RestartSec=5
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now xray-reverse-bridge
journalctl -u xray-reverse-bridge -f
```

看到 `[Info] app/reverse: got connection` 类日志即建立成功。

---

## 4. 验证

### 4.1 portal 侧看连接建立

```bash
docker exec sb-xray tail -f /var/log/xray/access.log | grep -i reverse
```

bridge 刚连上时会看到类似：
```
from 1.2.3.4:xxx accepted reverse [REALITY_IN -> r-tunnel] email: reverse@portal.bridge
```

### 4.2 端到端

在外网机（非家宽）用 curl 走 sb-xray 的 socks5 代理访问 `http://nas.lan/`（或你配的 `REVERSE_DOMAINS` 里的任意域名）：

```bash
curl -x socks5h://<sb-xray-socks5>:1080 http://nas.lan/
```

应能返回内网 NAS 的页面。

### 4.3 故障告警

bridge 断线时，公网端 Xray 的 observatory 会在 1 分钟内标记隧道 dead；配合 `rules.webhook` 事件总线，可在 xr.json 里追加一条：

```json
{
  "type": "field",
  "ruleTag": "reverse-down",
  "outboundTag": "r-tunnel",
  "webhook": {
    "url": "http://127.0.0.1:18085/reverse_down",
    "deduplication": 300,
    "headers": {"X-Event": "reverse_tunnel_event"}
  }
}
```

则隧道有任何命中流量失败时都会推送到 shoutrrr。

---

## 5. 故障排查

### bridge 一直连不上

| 症状 | 排查 |
|---|---|
| `REALITY: processed invalid connection` | UUID / shortId 填错 |
| `tls: handshake failure` | `XRAY_REALITY_PUBLIC_KEY` 填错，或 portal 侧 `DEST_HOST` 不一致 |
| `i/o timeout` | 家宽禁出 443？先 `curl -v https://bracknerd.ansandy.com:443` |
| `server rejects account` | reverse UUID 跟公网端 `XRAY_REVERSE_UUID` 不同 |

### portal 侧 route 命中但不走 reverse

1. `docker exec sb-xray cat /sb-xray/xray/xr.json | jq .routing.rules[0]` 确认 `outboundTag: r-tunnel` 规则已注入且在正确位置（靠前）
2. `docker exec sb-xray cat /sb-xray/xray/01_reality_inbounds.json | jq .inbounds[0].settings.clients` 确认第二个 client 带 `reverse: {tag: r-tunnel}` 且 UUID = `${XRAY_REVERSE_UUID}`

### 安全边界

Xray v25.12.8 commit `a83253f` 引入：**带 `reverse` 标记的 UUID 禁止用作正向代理**。所以 `XRAY_REVERSE_UUID` 与 `XRAY_UUID` 必须独立 —— entrypoint 已按此设计自动生成两个 UUID，不要合并成一个。

---

## 6. 关闭 reverse

```yaml
- ENABLE_REVERSE=false
```

重启容器，entrypoint 下次渲染会重新从原始模板出发（不再注入 reverse client），`01_reality_inbounds.json` / `xr.json` 的孤儿条目会被覆盖清理。

bridge 客户端端无需特殊处理，它会持续重试连接并失败 —— 可自行 `systemctl stop xray-reverse-bridge`。
