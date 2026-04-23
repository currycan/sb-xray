# 04. 运维管理与故障排查手册

> 覆盖系统管理面板导航、环境变量完整参考、订阅端点安全防护、证书运维、GeoIP 数据更新与常见故障排查。

---

## 目录

1. [系统控制面板导航](#1-系统控制面板导航)
2. [环境变量完整参考](#2-环境变量完整参考)
3. [订阅端点安全体系](#3-订阅端点安全体系)
4. [证书管理运维](#4-证书管理运维)
5. [GeoIP/GeoSite 数据更新](#5-geoipgeosite-数据更新)
6. [故障排查实战手册](#6-故障排查实战手册)
7. [小内存节点部署指引（内存不超过 512 MB）](#7-小内存节点部署指引内存不超过-512-mb)

---

## 1. 系统控制面板导航

SB-Xray 集成了多个可视化管理面板，全部通过 CDN 域名的子路径访问。

### 1.1 控制面板一览

| 面板 | 入口路径 | 功能 | 默认凭据来源 |
|:---|:---|:---|:---|
| **X-UI (3x-ui)** | `https://${CDNDOMAIN}/${XUI_WEBBASEPATH}` | Xray 协议与用户管理 | `/.env/secret` 中 `PUBLIC_USER/PASSWORD` |
| **S-UI** | `https://${CDNDOMAIN}/${SUI_WEBBASEPATH}` | Sing-box 入站与出站监控 | 同上 |
| **Sub-Store** | `https://${CDNDOMAIN}/sub-store` | 订阅源管理与节点清洗 | 无需认证 |
| **Dufs** | `https://${CDNDOMAIN}/${DUFS_PATH_PREFIX}` | 文件上传/下载网盘 | HTTP Basic 认证（同上凭据） |
| **Yacd/Zashboard** | `https://${CDNDOMAIN}:9090/ui` | 实时流量与策略组监控 | Secret: `yyds666` |

> **安全提醒**：所有面板路径均通过 Nginx 的 `location` 指令保护，建议首次登录后立即修改默认 WebBasePath。

### 1.2 环境变量配置示例

```yaml
environment:
  # X-UI
  - XUI_WEBBASEPATH=3xadmin      # 自定义面板路径

  # S-UI
  - SUI_WEBBASEPATH=sui           # 自定义面板路径

  # Dufs 文件服务
  - DUFS_PATH_PREFIX=/myfiles     # 文件网盘的 URL 前缀
  - DUFS_SERVE_PATH=/data         # 文件存储路径
```

---

## 2. 环境变量完整参考

### 2.1 优先级模型

容器启动时，变量按以下顺序加载，**序号越小优先级越高**（后加载的覆盖先设的值）：

| 优先级 | 来源 | 加载时机 | 典型内容 |
|:---|:---|:---|:---|
| **1（最高）** | `/.env/status` | `main_init` 步骤 3，最后 source | 流媒体/AI 检测结果（`*_OUT` 变量）；当 ISP_TAG 需要重新评估时，所有 `*_OUT` 行会被联动清除后重新写入 |
| **2** | `/.env/sb-xray` | `main_init` 步骤 3 | UUID、端口、密钥、`ISP_TAG` 等 |
| **3** | `/.env/secret` | `main_init` 步骤 2 | 面板凭据、ISP 节点凭据 |
| **4** | `docker-compose environment` | 容器启动时注入 | 用户显式配置 |
| **5（最低）** | `Dockerfile ENV` | 镜像构建时烘焙 | 安全默认值 |

> **实践说明**：各层通常包含不同的变量，实际覆盖冲突极少。`ensure_var` 的三分支逻辑确保自动生成的变量首次计算后即永久缓存，不会被重复生成。

### 2.2 用户配置变量（在 docker-compose 设置）

#### 必填（无默认值，缺少则容器退出）

| 变量 | 说明 |
|:---|:---|
| `DOMAIN` | 主域名（Reality TLS 伪装目标所在域） |
| `CDNDOMAIN` | CDN 域名（Cloudflare 代理，Nginx Web 层监听此 SNI） |
| `DECODE` | 远端密钥库解密密钥（由 `crypctl` 使用） |

#### 核心可选

| 变量 | Dockerfile 默认 | 说明 |
|:---|:---|:---|
| `LISTENING_PORT` | `443` | Nginx 主监听端口 |
| `DEST_HOST` | `www.microsoft.com` | Reality SNI 伪装目标（建议改为 `speed.cloudflare.com`） |
| `DEFAULT_ISP` | `LA_ISP` | ISP 出口模式：非空=锁定到指定前缀出口（跳过测速）；**显式置空=启用测速自动选路**。Dockerfile 默认 `LA_ISP`，不覆盖则永远锁定 LA 出口 |
| `GEMINI_DIRECT` | `""` | Gemini 路由：`true`=强制直连，`false`=代理，空=自动判断 |
| `NODE_SUFFIX` | `""` | 订阅节点名称后缀（如 ` ✈ 高速`） |
| `PROVIDERS` | `""` | 外部订阅源，多行格式 |
| `TZ` | `Asia/Singapore` | 容器时区 |

#### ACME 证书

| 变量 | Dockerfile 默认 | 说明 |
|:---|:---|:---|
| `ACMESH_SERVER_NAME` | `zerossl` | ACME CA：`zerossl` / `google` |
| `ACMESH_REGISTER_EMAIL` | `""` | ACME 注册邮箱 |
| `ACMESH_DEBUG` | `2` | ACME 调试级别（生产可改为 `1`） |
| `ACMESH_EAB_KID` | — | Google CA 专用 EAB Key ID |
| `ACMESH_EAB_HMAC_KEY` | — | Google CA 专用 EAB HMAC Key |
| `SSL_PATH` | `/pki` | 证书存储路径 |

#### X-UI 面板

| 变量 | Dockerfile 默认 | 说明 |
|:---|:---|:---|
| `XUI_WEBBASEPATH` | `xui` | 面板访问路径 |
| `XUI_LOG_LEVEL` | `info` | 日志级别 |
| `XUI_DEBUG` | `false` | 调试模式 |

#### S-UI 面板

| 变量 | Dockerfile 默认 | 说明 |
|:---|:---|:---|
| `SUI_WEBBASEPATH` | `sui` | 面板访问路径 |
| `SUI_SUB_PATH` | `sub` | 订阅子路径 |
| `SUI_PORT` | `3095` | S-UI 内部端口 |
| `SUI_SUB_PORT` | `3096` | S-UI 订阅内部端口 |
| `SUI_LOG_LEVEL` | `info` | 日志级别 |

#### Dufs 文件服务

| 变量 | Dockerfile 默认 | 说明 |
|:---|:---|:---|
| `DUFS_PATH_PREFIX` | `/dufs` | URL 前缀 |
| `DUFS_SERVE_PATH` | `/data` | 文件存储根目录 |
| `DUFS_ALLOW_UPLOAD` | `true` | 允许上传 |
| `DUFS_ALLOW_DELETE` | `true` | 允许删除 |

#### Entrypoint 日志（Python stdlib logging）

| 变量 | 默认值 | 说明 |
|:---|:---|:---|
| `SB_LOG_LEVEL` | `INFO` | Python entrypoint 日志级别：`DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL`（大小写不敏感，`WARN` 作为 `WARNING` 的别名）。**与给 xray/sing-box 用的 `LOG_LEVEL` 分离**，避免 xray 的 `warning` 字符串意外屏蔽阶段进度 INFO 日志。 |
| `NO_COLOR` | *(空)* | 设为任意非空值 → 关闭 entrypoint 日志的 ANSI 彩色。容器 stdout 非 TTY 时自动关闭，无需手动配置。 |

**格式**：`[ISO-8601 时区时间戳] [LEVEL] [模块名] 消息`

```
[2026-04-22T13:59:33.123+08:00] [INFO] [sb_xray.entrypoint] ▶ Stage 5/17 speed: ISP 测速与选路
[2026-04-22T13:59:33.876+08:00] [INFO] [sb_xray.routing.isp] 注入出站: proxy-us-isp (999.00 Mbps)
[2026-04-22T13:59:33.891+08:00] [INFO] [sb_xray.entrypoint] ✓ Stage 5/17 speed ok in 768ms
```

- 每个阶段用 `▶ / ✓ / ⋯ / ✗` 标识 start / ok / skipped / failed，带毫秒级耗时。
- 日志走 **stderr**；`SYSTEM STRATEGY SUMMARY` 方框与订阅 banner 是 **stdout** 的一次性报告，不属于日志流。
- 日志流末尾会留一行锚点 `handing over to supervisord; subsequent lines come from supervisord / xray / nginx`，之后的 supervisord / xray / nginx 日志保留其原生格式。
- 排查启动问题时推荐 `SB_LOG_LEVEL=DEBUG`；稳定后改回 `INFO`。

### 2.3 远端密钥变量（`/.env/secret`，由 `DECODE` 解密注入）

这些变量从加密的远端密钥库中读取，**不应出现在 `docker-compose.yml`** 中：

| 变量 | 说明 |
|:---|:---|
| `PUBLIC_USER` | 统一用户名（X-UI / S-UI / HTTP Basic Auth 共用） |
| `PUBLIC_PASSWORD` | 统一密码 |
| `<PREFIX>_ISP_IP` | ISP 落地节点 IP（如 `LA_ISP_IP`） |
| `<PREFIX>_ISP_PORT` | ISP 落地节点端口 |
| `<PREFIX>_ISP_USER` | ISP 落地节点用户名 |
| `<PREFIX>_ISP_SECRET` | ISP 落地节点密码 |

> `<PREFIX>` 可自定义（如 `LA`、`KR`），需在 `DEFAULT_ISP` 中指定全局兜底前缀。

### 2.4 自动生成变量（`/.env/sb-xray`，首次启动后永久缓存）

> ⚠️ **禁止在 docker-compose 中手动设置**，否则会锁死随机值，重建容器无法刷新。

| 变量 | 生成方式 | 说明 |
|:---|:---|:---|
| `XRAY_UUID` | `uuidgen` | Xray VLESS 用户 ID |
| `SB_UUID` | `uuidgen` | Sing-box 用户 ID |
| `PASSWORD` | 随机 16 位 | 通用密码 |
| `SUBSCRIBE_TOKEN` | 随机 32 位 | 订阅 URL 鉴权 Token |
| `XRAY_REALITY_SHORTID` | `openssl rand -hex 8` | Reality ShortId |
| `XRAY_REALITY_PRIVATE_KEY` | `xray x25519` | Reality 私钥（配对生成） |
| `XRAY_REALITY_PUBLIC_KEY` | `xray x25519` | Reality 公钥 |
| `XRAY_MLKEM768_SEED` | `xray mlkem768` | ML-KEM 种子（配对生成） |
| `XRAY_MLKEM768_CLIENT` | `xray mlkem768` | ML-KEM 客户端密钥 |
| `XRAY_URL_PATH` | 随机 32 位 | XHTTP 路径 |
| `PORT_HYSTERIA2` | `6443`（Dockerfile ENV 固定值） | Hysteria2 UDP 端口（Xray 承载） |
| `PORT_TUIC` | `8443`（Dockerfile ENV 固定值） | TUIC UDP 端口（Sing-box 承载） |
| `PORT_ANYTLS` | `4433`（Dockerfile ENV 固定值） | AnyTLS TCP 端口（Sing-box 承载） |
| `PORT_XHTTP_H3` | `4443`（Dockerfile ENV 固定值） | XHTTP/3 UDP 端口 |
| `PORT_XICMP_ID` | `12345` | XICMP 紧急通道 ICMP id（默认关，见 `docs/07-new-features-guide.md §6`） |
| `PORT_XDNS` | `5353` | XDNS 紧急通道 UDP 端口（默认关，见 `docs/07-new-features-guide.md §7`） |
| `ENABLE_XICMP` / `ENABLE_XDNS` / `ENABLE_ECH` / `ENABLE_REVERSE` | `false` | 实验性 feature flag；开启条件与效果详见 [新特性使用指南](./07-new-features-guide.md) |
| `ENABLE_SUBSTORE` / `ENABLE_XUI` / `ENABLE_SUI` / `ENABLE_SHOUTRRR` | `true`（Dockerfile ENV 注册） | **小内存节点降载开关**；设 `false` 在 `createConfig` 后由 `python3 /scripts/entrypoint.py trim` 过滤对应 `[program:*]` 段；详见 §7 |
| `GOMEMLIMIT` / `GOGC` | _未设置_ | Go GC 硬上限 + 回收激进度；推荐小内存节点 `GOMEMLIMIT=320MiB` + `GOGC=50` |
| `XDNS_DOMAIN` | _空_ | XDNS 紧急通道的 NS 域名（`ENABLE_XDNS=true` 时必填） |
| `REVERSE_DOMAINS` | _空_ | VLESS Reverse 需要穿透的域名列表（逗号分隔，`ENABLE_REVERSE=true` 时生效） |
| `SHOUTRRR_URLS` / `SHOUTRRR_FORWARDER_PORT` / `SHOUTRRR_TITLE_PREFIX` | `"" / 18085 / [sb-xray]` | 事件总线推送通道（留空 = dry-run 仅本地日志） |
| `XUI_LOCAL_PORT` | 随机端口 | X-UI 实际监听端口 |
| `DUFS_PORT` | 随机高位端口 | Dufs 内部监听端口 |
| `SUB_STORE_FRONTEND_BACKEND_PATH` | 随机 32 位路径 | Sub-Store 后端 API 路径（每次部署唯一，防扫描） |
| `STRATEGY` | API 检测 | 双栈 / 纯 IPv4 / 纯 IPv6 |
| `GEOIP_INFO` | API 检测 | GeoIP 归属字符串 |
| `IS_BRUTAL` | 内核探测 | BBR/Brutal 支持状态 |
| `IP_TYPE` | API 检测 | `isp` / `hosting` 等 |

### 2.5 自动检测变量（`/.env/status`，可清除重新检测）

删除 `/.env/status` 并重启容器即可强制重新探测所有网络状态（含测速选路）：

```bash
docker exec sb-xray rm -f /.env/status
docker compose restart
```

| 变量 | 含义 |
|:---|:---|
| `ISP_TAG` | 最快 ISP 代理 tag（测速选路结果）；`direct` 表示无代理回退直连。用于 IS_8K_SMOOTH 计算和节点标签生成 |
| `IS_8K_SMOOTH` | `true`/`false`，实际出口速度是否 ≥ 100 Mbps；驱动 `show` 子命令生成 `✈ good`（代理出口）或 `✈ super`（住宅直出）节点标签 |
| `HAS_ISP_NODES` | `true`/空，标识是否存在可用 ISP 节点。有节点时路由函数返回 `isp-auto`（健康选优），无节点返回 `direct` |
| `CHATGPT_OUT` | ChatGPT 出口策略 tag |
| `NETFLIX_OUT` | Netflix 出口策略 tag |
| `DISNEY_OUT` | Disney+ 出口策略 tag |
| `YOUTUBE_OUT` | YouTube 出口策略 tag |
| `GEMINI_OUT` | Gemini 出口策略 tag |
| `CLAUDE_OUT` | Claude 出口策略 tag |
| `SOCIAL_MEDIA_OUT` | 社交媒体出口策略 tag |
| `TIKTOK_OUT` | TikTok 出口策略 tag |
| `ISP_OUT` | ISP 首选策略 tag |
| `ISP_LAST_RETEST_TS` | 上次周期重测 Unix 时间戳（用于冷启动缓存 TTL 判定） |
| `ISP_LAST_RETEST_DELTA_PCT` | 上次重测触发 reload 的最大速率变化百分比 |
| `ISP_LAST_RETEST_TOP_TAG` | 上次重测后选中的最快 tag |

### 2.6 `isp-auto` 优化控制变量（可选）

所有变量都有保守默认值,**不设置任何一个**也能正常工作；需要调优时再逐项开启,随时可通过取消覆盖单变量回滚。

| 变量 | 默认 | 作用 |
|:---|:---|:---|
| `ISP_PROBE_URL` | `https://speed.cloudflare.com/__down?bytes=1048576` | urltest / observatory 探测 URL；默认携带带宽信号,可切 `https://www.gstatic.com/generate_204` 仅测 RTT |
| `ISP_PROBE_INTERVAL` | `1m` | 探测周期；小内存节点建议 `5m` |
| `ISP_PROBE_TOLERANCE_MS` | `300` | sing-box `urltest` 切换最低 RTT 差阈值（毫秒） |
| `ISP_EVENTS_ENABLED` | `true` | 结构化事件 `event=... payload=...` 是否写 stdout + POST 到 shoutrrr |
| `ISP_RETEST_INTERVAL_HOURS` | `6` | 周期性带宽重测 cron 间隔；`0` 禁用 |
| `ISP_RETEST_DELTA_PCT` | `15` | 重测后触发 daemon restart 的最小速率变化百分比；`999` 实质禁用重启 |
| `ISP_RETEST_ENABLED` | `true` | 即使 cron 已安装,也可以通过此开关让子命令 no-op |
| `ISP_PER_SERVICE_SB` | `false` | 开启后 sing-box 为 Netflix / OpenAI / Claude / Gemini / Disney / YouTube 生成独立 `isp-auto-<service>` urltest balancer,各自用该服务的真实域名探测;xray 因 observatory 单例不受影响 |
| `ISP_FALLBACK_STRATEGY` | `direct` | `direct`(静默直连) / `block`(fail-closed,CN / HK / RU 建议) |
| `ISP_SPEED_CACHE_TTL_MIN` | `60` | 冷启动缓存 TTL（分钟）；`0` 禁用,每次 boot 强制实测 |
| `ISP_SPEED_CACHE_ASYNC` | `true` | 缓存命中时是否后台线程异步刷新速度;`false` 仅用于调试 |

#### v2 带宽采样器（流式 + 预热丢弃 + 时间窗 + 诊断）

v1 单次 GET + 1 MiB 文件 + 5s 超时的采样在跨境 SOCKS5 链路上受 TCP slow-start、TLS/SOCKS5 握手开销和小文件管道填不满三重因素叠加影响，系统性低估节点带宽 5–20 倍。v2 采样器改用 `httpx.stream()` 流式读取 + 时间窗 + 首字节后计时 + 结构化失败码。

| 变量 | 默认 | 作用 |
|:---|:---|:---|
| `ISP_SPEED_WINDOW_SEC` | `8.0` | 稳态测速窗口秒数（加长可减少噪声,建议 ≥ 5） |
| `ISP_SPEED_WARMUP_SEC` | `1.5` | TCP 慢启动预热丢弃秒数；高延迟链路可拉长到 2–3 |
| `ISP_SPEED_MAX_BYTES` | `268435456` | 单样本字节封顶（256 MiB 默认足够覆盖 1 Gbps 链路在 8s 窗口内跑满） |
| `ISP_SPEED_CHUNK_BYTES` | `65536` | `iter_bytes` 分块大小；默认 64 KiB 兼顾内存与精度 |
| `ISP_SPEED_SAMPLES` | `3` | 每个节点外层采样次数；≥ 3 时启用 drop-min/drop-max 截断均值 |
| `ISP_SPEED_TIMEOUT_SEC` | `20` | 单样本硬超时（应 > warmup + window + 网络缓冲） |
| `ISP_SPEED_URL_MAP` | `""` | JSON `{"proxy-kr-isp":"https://kr-speed.example/100mb"}`,按 tag 覆盖探针 URL；缺项回退到 `ISP_PROBE_URL` |
| `ISP_SPEED_DIAG_ENABLED` | `true` | 是否把 `_ISP_SPEEDS_DIAG_JSON`（每 tag 诊断）写入 STATUS_FILE 和 event payload |
| `ISP_SPEED_LEGACY` | `false` | **Kill switch** — 置 `true` 回退到 v1 单次 GET 采样器（保留至少一个 release 以防新算法在某台机器上水土不服） |

**读懂 `_ISP_SPEEDS_DIAG_JSON`**

每次测速后，`/.env/status` 多写一行：

```
export _ISP_SPEEDS_DIAG_JSON='{"proxy-la-isp":{"status":"ok","ok":3,"total":3,"statuses":["ok","ok","ok"],"bytes":100663296,"window_sec":24.0},"proxy-kr-isp":{"status":"connect_fail","ok":0,"total":3,"statuses":["connect_fail","connect_fail","connect_fail"],"bytes":0,"window_sec":0.0}}'
```

字段含义：

| 字段 | 含义 |
|:---|:---|
| `status` | 整体分类：`ok` = 全部采样成功；`mixed` = 部分成功；单一失败码（`connect_fail` / `timeout` / `low_speed` / `zero_body` / `proxy_dep_missing`）= 全部同类失败 |
| `ok` | 成功采样数（status=="ok"） |
| `total` | 总采样数 |
| `statuses` | 每次采样的原始状态列表，便于排查偶发抖动 |
| `bytes` | 所有采样的 metered 字节总和（排除 warmup） |
| `window_sec` | 所有采样的 metered 时长总和（排除 warmup） |

失败码对照：

| 状态码 | 含义 | 排查方向 |
|:---|:---|:---|
| `ok` | 成功且速率 ≥ 1 KiB/s | 正常 |
| `connect_fail` | SOCKS5 / TLS / HTTP 握手失败 或 4xx/5xx | 节点 IP:PORT 不通 / 凭据错 / 探针 URL 被节点屏蔽 |
| `timeout` | httpx 读取超时 | RTT 过高 / 节点严重拥塞；尝试拉长 `ISP_SPEED_TIMEOUT_SEC` |
| `low_speed` | 测得速率 < 1 KiB/s | 节点真的很慢 / 链路被限速 |
| `zero_body` | 流开启但零字节 | 节点接受连接后立即关闭 — 检查认证或节点状态 |
| `proxy_dep_missing` | 镜像缺 `socksio` | 升级镜像或 `pip install httpx[socks]` |
| `mixed` | 采样间结果不一致 | 节点状态抖动，观察 `statuses` 找规律 |

> **典型组合**
> - **小内存节点（OOM 敏感）**: `ISP_PROBE_INTERVAL=5m`, `ISP_PER_SERVICE_SB=false`, `ISP_RETEST_INTERVAL_HOURS=12`
> - **CN / HK / RU 受限地区 fail-closed**: `ISP_FALLBACK_STRATEGY=block`
> - **追求极致解锁命中率**: `ISP_PER_SERVICE_SB=true` + 保持 `ISP_PROBE_URL` 默认

**升级后验证周期性重测已注册**（从旧镜像升级过来的部署,首次启动会注入 `isp-retest` cron entry）:

```bash
docker exec sb-xray crontab -l | grep isp-retest
# 期望: 0 */6 * * * /scripts/entrypoint.py isp-retest >> /var/log/isp_retest.log 2>&1
```

若无输出,说明 pipeline stage 16 未跑到(容器未完成 boot) — 等待或查 `docker logs sb-xray | grep 'Cron 定时任务'`。禁用后确认: `ISP_RETEST_INTERVAL_HOURS=0` 重启,该 entry 应消失。

**Entrypoint 事件流**（`SHOUTRRR_URLS` 未设置时仅 stdout）:
- `isp.speed_test.result` — 每次速度测试完成
- `isp.speed_test.cache_hit` — 冷启动缓存命中
- `isp.retest.completed` — 周期重测触发了 daemon restart
- `isp.retest.noop` — 周期重测结果无变化
- `isp.retest.error` / `isp.speed_test.error` — 失败路径

---

## 3. 订阅端点安全体系

订阅配置文件（`/sb-xray/`）包含所有代理连接信息，安全性至关重要。系统对此端点设计了多层防御策略。

### 3.1 安全体系总览

```mermaid
flowchart TD
    A["访问请求"] --> B{"携带 Token?"}
    B -- "是" --> C{"Token 正确?"}
    C -- "是" --> D["允许访问"]
    C -- "否" --> E["要求 HTTP 基础认证"]
    B -- "否" --> E
    E -- "凭据正确" --> D
    E -- "凭据错误" --> F["返回 404"]

    style D fill:#55efc4,stroke:#00b894,stroke-width:2px
    style F fill:#ff7675,stroke:#d63031,stroke-width:2px
```

### 3.2 认证方式一：Token 认证（推荐）

Token 在容器首次启动时**自动生成**。

**查看当前 Token**：

```bash
docker exec sb-xray grep SUBSCRIBE_TOKEN /.env/sb-xray
```

**使用方法**：在订阅 URL 后添加 `?token=YOUR_TOKEN` 参数：

```
https://your-domain.com/sb-xray/MihomoPro.yaml?token=a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6
```

**自定义 Token**（可选）：

```yaml
environment:
  - SUBSCRIBE_TOKEN=your_custom_secure_token_32_chars
```

### 3.3 认证方式二：HTTP 基础认证（备用）

使用与 X-UI / S-UI 相同的用户名和密码（来自 `/.env/secret`）：

**使用方法**：

* 浏览器：访问链接时会弹出认证对话框
* 客户端：`https://admin:password@your-domain.com/sb-xray/MihomoPro.yaml`

### 3.4 安全加固措施

| 措施 | 实现方式 | 效果 |
|:---|:---|:---|
| **防扫描设计** | 所有非法请求统一返回 `404` | 攻击者无法判断端点是否存在 |
| **严格路径验证** | 仅允许字母数字+`.yaml`后缀 | 阻止目录遍历攻击 (`../etc/passwd`) |
| **禁止目录浏览** | Nginx `autoindex off` | 防止列出全部订阅文件 |
| **速率限制** | 每 IP 每分钟 10 次 | 超限也返回 `404`（防暴力破解） |
| **安全响应头** | `X-Content-Type-Options: nosniff` | 防 MIME 嗅探、点击劫持 |
| **禁止缓存** | `Cache-Control: no-store` | 防敏感配置被中间节点缓存 |

### 3.5 监控与日志

```bash
# 查看成功的订阅访问
docker exec sb-xray tail -f /var/log/nginx/subscribe_access.log

# 实时查看扫描尝试
docker exec sb-xray tail -f /var/log/nginx/subscribe_scan.log

# 统计前 10 个攻击者 IP
docker exec sb-xray awk '{print $1}' /var/log/nginx/subscribe_scan.log | sort | uniq -c | sort -rn | head -10
```

---

## 4. 证书管理运维

### 4.1 日常运维命令

```bash
# 查看证书状态
docker exec sb-xray openssl x509 -in /pki/sb_xray_bundle.crt -text -noout | grep -E "Not (Before|After)"

# 强制重新签发（删除旧证书后重启）
rm -rf ./pki/* ./acmecerts/*
docker compose restart

# 手动续期
docker exec sb-xray /acme.sh/acme.sh --renew -d ${DOMAIN} -d ${CDNDOMAIN} --force
```

### 4.2 多域名证书策略

系统默认申请**泛域名 + 主域名**双SAN证书：

```
SAN[0]: *.example.com     (泛域名，覆盖所有子域名)
SAN[1]: example.com       (主域名)
```

### 4.3 DH 参数安全加固

首次启动时，系统自动生成 2048-bit DH 密钥参数：

```bash
# 存储路径：挂载卷 ./nginx/dhparam/dhparam.pem
# 耗时约 30 秒至 2 分钟（取决于 CPU）
# 生成后缓存，后续重启直接复用
```

---

## 5. GeoIP/GeoSite 数据更新

`scripts/sb_xray/geo.py` 负责下载 Xray 和 Sing-box 使用的规则库,并通过 `entrypoint.py geo-update` 子命令暴露给 cron。规则库落在持久化卷 `./geo:/geo`,容器重启不会重新下载。

### 5.1 自动更新

- **启动阶段**: entrypoint 第 9 段 (`stages/geoip.py`) 调用 `geo.refresh(on_startup=True)`。文件 <7 天视为新鲜,直接跳过下载;仅维护 `/usr/local/bin/*.dat` 符号链接。
- **每日任务**: cron 每天 03:00 (容器时区) 执行 `/scripts/entrypoint.py geo-update`,强制刷新并通过 `supervisorctl` 重启 xray 让新规则生效。
- **日志**: cron 输出重定向到 `/var/log/geo_update.log`;启动阶段输出走 entrypoint 的 stderr。

### 5.2 手动更新

```bash
# 手动触发更新 (与 cron 等价:强制刷新 + 重启 xray)
docker exec sb-xray /scripts/entrypoint.py geo-update

# 查看缓存的规则库
docker exec sb-xray ls -lh /geo/
docker exec sb-xray ls -l /usr/local/bin/ | grep dat   # 符号链接 → /geo/*.dat
```

### 5.3 清空并重新下载

```bash
# 宿主机删除缓存,重启容器触发全量下载
rm -rf ./geo/*.dat
docker compose restart sb-xray
```

### 5.4 持久化卷要求

`docker-compose.yml` 需挂载 `./geo:/geo`。早期版本(无此卷)仍可运行,但每次 `docker compose down/up` 都会丢失缓存并重新下载 6 个 ~10-30 MB 的文件。升级现网节点只需在 compose 里追加一行然后 `docker compose up -d` 即可。

### 5.5 数据源

| 文件 | 用途 | 来源 |
|:---|:---|:---|
| `geoip.dat` / `geosite.dat` | IP/域名分类库 (Xray, Sing-box) | Loyalsoldier/v2ray-rules-dat |
| `geoip_IR.dat` / `geosite_IR.dat` | 伊朗区域规则 | chocolate4u/Iran-v2ray-rules |
| `geoip_RU.dat` / `geosite_RU.dat` | 俄罗斯区域规则 | runetfreedom/russia-v2ray-rules-dat |

---

## 6. 故障排查实战手册

### 6.1 快速诊断流程

```mermaid
flowchart TD
    Start(("连接出问题")) --> Q1{"能 ping 通服务器 IP 吗?"}
    Q1 -- "不能" --> A1["检查服务器防火墙和网络"]
    Q1 -- "能" --> Q2{"浏览器打开域名能出页面吗?"}
    Q2 -- "出 SSL 报错 / 无响应" --> A2["检查 Nginx 和证书配置"]
    Q2 -- "出伪装页面 / 404" --> Q3{"Proxy 客户端连接超时?"}
    Q3 -- "Vision 超时" --> A3["检查 UUID、Reality 参数"]
    Q3 -- "VMess/XHTTP 超时" --> A4["检查 Path、CDN域名匹配"]
    Q3 -- "Hysteria2 超时" --> A5["检查 UDP 端口、防火墙"]
```

### 6.2 常见故障排查表

#### ❌ 故障一：客户端连接 502 Bad Gateway

**现象**：浏览器或客户端返回 502 错误

**排查步骤**：

```bash
# 1. 检查 Xray 核心是否存活
docker exec sb-xray supervisorctl status xray

# 2. 查看 Xray 错误日志
docker exec sb-xray tail -50 /var/log/xray/access.log

# 3. 检查 Nginx 连接 UDS 是否正常
docker exec sb-xray ls -la /dev/shm/*.sock

# 4. 检查 Nginx 错误日志
docker exec sb-xray tail -50 /var/log/nginx/error.log
```

**常见原因**：
| 原因 | 解决方案 |
|:---|:---|
| Xray 配置 JSON 语法错误导致崩溃 | 检查日志中的 JSON parse error |
| UDS Socket 文件不存在 | 重启容器：`docker compose restart` |
| 内存不足导致 OOM | 检查 `dmesg` |

#### ❌ 故障二：订阅文件返回 404

**排查顺序**：

1. **Token 错误**：检查 URL 中的 Token 与 `/.env/sb-xray` 中的是否一致
2. **认证失败**：未携带 Token 时，输入的用户名/密码可能错误
3. **文件名大小写**：文件名大小写敏感
4. **触发限流**：请求过于频繁，等待一分钟

#### ❌ 故障三：证书申请失败

**排查步骤**：

```bash
# 检查 DNS 解析
nslookup ${DOMAIN}

# 检查 443 端口是否开放
nc -zv ${SERVER_IP} 443

# 检查 acme.sh 日志
docker exec sb-xray cat /var/log/acme.sh.log
```

**常见原因**：
| 原因 | 解决方案 |
|:---|:---|
| DNS A 记录未指向服务器 | 修正 DNS 记录并等待传播 |
| Cloudflare 小黄云未关闭 | DNS-only 模式下申请后再开启 |
| CA 频率限制 | 切换 CA 或等待后重试 |
| EAB 凭据过期 (Google CA) | 重新生成 EAB 凭据 |

#### ❌ 故障四：Hysteria2/TUIC 连接失败

**排查步骤**：

```bash
# 检查 Sing-box 状态
docker exec sb-xray supervisorctl status sing-box

# 检查 UDP 端口是否监听
docker exec sb-xray ss -ulnp | grep ${PORT_HYSTERIA2}

# 外部测试 UDP 连通性
nc -zuv ${SERVER_IP} ${PORT_HYSTERIA2}
```

**常见原因**：
| 原因 | 解决方案 |
|:---|:---|
| 服务器防火墙未放行 UDP | `ufw allow ${PORT}/udp` 或安全组放行 |
| ISP 封锁 UDP | 切换其他协议 |
| 客户端 UUID 错误 | 注意 Sing-box 使用 `SB_UUID` 而非 `XRAY_UUID` |

### 6.3 日志检查速查表

| 日志位置 | 用途 |
|:---|:---|
| `docker logs sb-xray` | **entrypoint 启动日志**（测速、选路、证书、配置渲染全流程） |
| `/var/log/xray/access.log` | Xray 访问日志 |
| `/var/log/xray/error.log` | Xray 错误日志 |
| `/var/log/sing-box/sing-box.log` | Sing-box 日志 |
| `/var/log/nginx/error.log` | Nginx 错误日志 |
| `/var/log/nginx/access.log` | Nginx 访问日志 |
| `/var/log/nginx/subscribe_access.log` | 订阅成功访问 |
| `/var/log/nginx/subscribe_scan.log` | 扫描/攻击记录 |
| `/var/log/supervisord.log` | Supervisor 主日志 |
| `/var/log/acme.sh.log` | 证书申请/续期日志 |

### 6.4 快速运维命令汇总

```bash
# 容器状态
docker compose ps
docker exec sb-xray supervisorctl status

# 重启所有服务
docker compose restart

# 仅重启某个核心
docker exec sb-xray supervisorctl restart xray
docker exec sb-xray supervisorctl restart sing-box
docker exec sb-xray supervisorctl restart nginx

# 查看实时配置
docker exec sb-xray show

# 进入容器终端
docker exec -it sb-xray bash
```

### 6.5 解读测速选路启动日志

entrypoint 启动时会在 `docker logs sb-xray` 中打印完整的测速与选路决策链路，方便运维人员判断选路是否符合预期。

#### 阶段 2 日志结构

```
[阶段 2] 测速与选路...
[阶段 2] 环境: IP_TYPE=<类型> | 地区=<国家> | DEFAULT_ISP=<值>    ← 决策上下文
[阶段 2] 直连基准: XX.XX Mbps（不参与选路；无代理时用于 IS_8K_SMOOTH 判定）
[阶段 2] 发现 ISP 节点: N 个，开始逐节点测速（采样=2次）...

[测速] <节点> | 第 1/2 轮: XXXX KB/s → XX.XX Mbps    ← 每轮单次结果
[测速] <节点> | 第 2/2 轮: ...
[测速] <节点>: 2/2 有效样本，截断均值 XX.XX Mbps      ← 有效样本均值（单次 < 1 KB/s 不计入）
  或
[测速] <节点>: 全部 2 次采样失败，返回 0               ← 全部采样低于 1 KB/s 阈值

[测速] <tag>: XX.XX Mbps → 新最优                     ← 速度超过当前最优
  或
[测速] <tag>: XX.XX Mbps (最优仍: <tag> XX.XX Mbps)   ← 未超过最优

[选路] ════════════════════════════════════════════
[选路] 决策输入:
[选路]   IP_TYPE       = <值> (<描述>)
[选路]   地区          = <国家>
[选路]   DEFAULT_ISP   = <值>
[选路]   直连速度      = XX.XX Mbps（不参与选路）
[选路]   最优 ISP 代理 = <tag> (XX.XX Mbps)
[选路] <路由判断结果>
[选路] IS_8K_SMOOTH: 出口=<tag> | 参考速度=XX.XX Mbps | 阈值=100 Mbps → <true/false>  → <标签提示>
[选路] ✓ 最终决策: ISP_TAG=<tag> | IS_8K_SMOOTH=<true/false>
[选路] ════════════════════════════════════════════
```

#### 阶段 4 日志结构（健康检测配置生成）

```
[阶段 4] 生成客户端/服务端配置片段...
[ISP] 注入出站: proxy-kr-isp (76.56 Mbps)              ← 按速度降序注入全部 ISP
[ISP] 注入出站: proxy-jp-isp (70.00 Mbps)
[ISP] Sing-box urltest 已生成: outbounds=[...]          ← urltest 包含所有 ISP + direct
[ISP] Xray observatory + balancer 已生成: selector=[...]← balancer 精确匹配 ISP tags
[阶段 4] 完成
```

#### 常见选路场景对照

| 日志关键字 | 含义 |
|:---|:---|
| `DEFAULT_ISP=未设置（自动选路）` | docker-compose 已置空，测速自动决策 |
| `DEFAULT_ISP=LA_ISP（锁定模式）` | 强制使用 LA 出口，测速跳过 |
| `未发现 ISP 节点` | `/.env/secret` 中无 `*_ISP_IP` 变量，检查密钥库 |
| `ISP_TAG 已缓存` | `/.env/status` 存有旧结果，删除后重启可重新测速 |
| `清除服务路由缓存（与 ISP_TAG 同步刷新）` | ISP_TAG 未命中缓存，触发重新测速；同时联动清除所有 `*_OUT` 旧缓存，确保流媒体/AI 路由基于新 ISP_TAG 重新评估 |
| `受限地区 (中国\|香港)，强制使用代理` | GeoIP 检测到受限地区，无论速度必须走代理 |
| `→ 新最优` | 该节点速度超过当前最优，成为新的 FASTEST_PROXY_TAG |
| `最优仍: proxy-xx` | 该节点速度未超过当前最优 |
| `注入出站: proxy-xx (XX Mbps)` | 全部 ISP 节点按速度降序注入出站配置 |
| `Sing-box urltest 已生成` | urltest 健康选优出站已构建（含所有 ISP + direct 回退） |
| `Xray observatory + balancer 已生成` | observatory 探测 + balancer 选优已构建（fallbackTag: direct） |
| `IS_8K_SMOOTH → true → ✈ good 标签` | 代理均值 > 100 Mbps，节点将附加 good 标签 |
| `IS_8K_SMOOTH → false → 无质量标签` | 速度不足 100 Mbps，节点无 good/super 标签 |

#### ❌ 故障五：ISP 测速结果不符预期 / 选路错误

**现象**：选路走了不期望的出口，或 IS_8K_SMOOTH 结果与实际网速不符

**排查步骤**：

```bash
# 查看完整启动日志，重点看 [阶段 2] 和 [选路] 段
docker logs sb-xray 2>&1 | grep -E "\[阶段 2\]|\[测速\]|\[选路\]"

# 强制重新测速：清空运行时状态后重启
docker exec sb-xray rm /.env/status
docker compose restart
```

**常见原因**：

| 原因 | 日志特征 | 解决方案 |
|:---|:---|:---|
| `DEFAULT_ISP` 非空导致跳过测速 | `ISP_TAG 已缓存` 或 `手动覆盖 DEFAULT_ISP=xxx` | docker-compose 中显式设置 `DEFAULT_ISP=` |
| 缓存未清除 | `ISP_TAG 已缓存 (proxy-xxx)，跳过测速` | `rm /.env/status` 后重启 |
| 无 ISP 节点注入 | `未发现 ISP 节点` | 检查 `/.env/secret` 中的 `*_ISP_IP` 变量 |
| 节点速度均低于 100 Mbps | `IS_8K_SMOOTH → false` | 正常现象，速度不足则无 good 标签 |
| 全部采样显示 0 Mbps | `全部 N 次采样失败，返回 0` | curl 下载速度低于 1 KB/s 阈值（连接失败）；检查节点连通性：`curl -x socks5h://IP:PORT https://speed.cloudflare.com/__down?bytes=1000 -o /dev/null -w '%{speed_download}'` |

#### ❌ 故障六：ISP 代理运行中突然失效，流媒体/AI 不可用

**现象**：容器已正常运行一段时间后，ChatGPT/Netflix 等突然无法访问

**运行时保障**：系统内置健康检测（Sing-box `urltest` / Xray `observatory`），按 `ISP_PROBE_INTERVAL`（默认 1 分钟）探测所有 ISP 节点。ISP 故障后：
- **Sing-box**：urltest 在下次探测后自动切换到存活节点，全部故障时回退 `direct`
- **Xray**：observatory 标记不健康节点，balancer 选择存活节点或回退 `direct`（`fallbackTag`）

**排查步骤**：

```bash
# 查看 Sing-box 日志确认 urltest 切换行为
docker exec sb-xray tail -100 /var/log/sing-box/sing-box.log | grep -i "urltest\|outbound"

# 查看 Xray 日志确认 observatory 探测结果
docker exec sb-xray tail -100 /var/log/xray/access.log | grep -i "observatory\|balancer"

# 手动测试 ISP 节点连通性
docker exec sb-xray curl -x socks5h://ISP_IP:PORT -o /dev/null -w '%{http_code}' https://www.gstatic.com/generate_204
```

**常见原因**：

| 原因 | 解决方案 |
|:---|:---|
| ISP 提供商临时维护 | 等待恢复，系统自动回退 direct 兜底 |
| ISP 凭据过期 | 更新 `/.env/secret` 中对应 `*_ISP_*` 变量后重启 |
| 回退 direct 也无法访问 | VPS 本身 IP 被目标服务封锁，需更换 ISP 节点 |

> **关键特性**：健康检测是系统内置能力，ISP 故障后最多一个探测周期（默认 1 分钟）即可完成切换，无需手动干预。

---

#### ❌ 故障七：ISP_TAG 已更新但流媒体/AI 路由仍走旧出口

**现象**：删除 `/.env/status` 重启后，`[选路]` 段显示已选出新的最优 ISP（如 `proxy-jp-isp`），但实际 ChatGPT、Netflix 等流量仍走上次的旧代理（如 `proxy-us-isp`）

**根本原因**：早期版本中，`*_OUT` 服务路由缓存（`CHATGPT_OUT`、`NETFLIX_OUT` 等）写入 `/.env/status` 后不会随 ISP_TAG 重置而联动清除。下次启动时 `analyze_ai_routing_env()` 检测到这些变量非空，直接复用旧值，导致路由不一致。

此问题已在 Bug #025 中修复：`run_speed_tests_if_needed()` 现在在 ISP_TAG 未命中缓存时，会立即清除所有 `*_OUT` 行及对应 shell 变量，确保后续流媒体/AI 路由基于新 ISP_TAG 重新评估。启动日志中会出现：

```
[阶段 2] 清除服务路由缓存（与 ISP_TAG 同步刷新）...
```

**排查步骤**（仅针对未升级到修复版本的环境）：

```bash
# 同时清除 ISP_TAG 与所有 *_OUT 缓存
docker exec sb-xray sed -i '/^export ISP_TAG=/d; /^export ISP_OUT=/d; /^export CHATGPT_OUT=/d; /^export NETFLIX_OUT=/d; /^export DISNEY_OUT=/d; /^export YOUTUBE_OUT=/d; /^export GEMINI_OUT=/d; /^export CLAUDE_OUT=/d; /^export SOCIAL_MEDIA_OUT=/d; /^export TIKTOK_OUT=/d' /.env/status
docker compose restart
```

> **注意**：升级到修复版本后，仅删除 `/.env/status` 并重启即可触发完整的缓存联动清除，无需手动执行上述命令。

---

## 7. 小内存节点部署指引（内存不超过 512 MB）

镜像默认启用全部 10 个 supervisord 进程（xray / sing-box / nginx / x-ui / s-ui / sub-store / http-meta / shoutrrr-forwarder / dufs / cron），常驻 RSS ~390–650 MB，启动峰值可达 650–870 MB。低于 1 GB 物理内存的节点需通过以下开关组合降载至 ~300–430 MB 常驻。

**作用机制**：`scripts/sb_xray/config_builder.py:create_config` 用 `string.Template` envsubst 语义渲染出完整 10 段 `/etc/supervisor.d/daemon.ini` 后，`scripts/entrypoint.py:run_pipeline` 在步 11b 直接调用同模块的 `trim_runtime_configs` 按环境变量 `ENABLE_*` 对 `daemon.ini` 做幂等 in-place 过滤（也可通过 `docker exec sb-xray python3 /scripts/entrypoint.py trim` 重跑一次）。所有开关都是 opt-out：仅显式设 `false` 生效，未设置或为空保持默认启用。

### 7.1 docker-compose.yml（宿主层硬约束）

```yaml
services:
  sb-xray:
    mem_limit: 460m
    mem_reservation: 380m
    memswap_limit: 900m
    oom_kill_disable: false
    environment:
      # Go 四件套（xray / sing-box / x-ui / s-ui）共享上限
      GOMEMLIMIT: "320MiB"
      GOGC: "50"
      # 容器级进程精简
      ENABLE_SUBSTORE: "false"       # 关掉 sub-store + http-meta（省 ~130–200 MB）
      ENABLE_XUI: "false"            # 关掉 x-ui 面板（省 ~35–55 MB）
      ENABLE_SUI: "false"            # 关掉 s-ui 面板（省 ~35–55 MB）
      ENABLE_SHOUTRRR: "false"       # 关掉 shoutrrr-forwarder（省 ~20–30 MB）
```

### 7.2 开关语义

| 变量 | 默认 | 设 `false` 的副作用 |
|------|-----|--------------------|
| `ENABLE_SUBSTORE` | true（保留） | 本节点无法对外托管订阅聚合/转换，客户端改从主节点订阅 |
| `ENABLE_XUI` | true（保留） | 无 X-UI 面板；xray 仍正常运行，改用 S-UI 或直接编辑 JSON |
| `ENABLE_SUI` | true（保留） | 无 S-UI 面板；改用 X-UI 或 sub-store |
| `ENABLE_SHOUTRRR` | true（保留） | xray `rules.webhook` 事件不再转发到 Telegram/Discord |

> Opt-out 语义：以上开关**仅**在显式设为字符串 `"false"` 时才禁用功能；未设置、空字符串、`"0"` 等均保持默认启用。

### 7.3 VPS 宿主层建议

- 确认 `swapon --show` 显示 ≥ 512 MB swap；无 swap 时 `fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile`
- `sysctl vm.swappiness=60`（激进换入 swap 保物理内存）
- `sysctl vm.overcommit_memory=1`（允许 VSZ 超分，避免 xray 启动瞬间 1.4 GB VSZ 被内核拒绝）

### 7.4 验证

部署后 30 分钟内观察：

```bash
docker stats --no-stream sb-xray   # MEM USAGE 应稳定 < 400 MB
free -h                            # available 稳定 > 80 MB
dmesg | grep -i "killed process"   # 无新 OOM kill 事件
```

若仍触发 OOM，进一步措施：

- 主节点接管订阅服务，小内存节点仅作纯转发
- 保留 `before-m2` 回滚标签作为紧急兜底：`docker pull currycan/sb-xray:before-m2`
